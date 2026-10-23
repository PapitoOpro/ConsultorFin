"""Casamento de lançamentos pendentes com linhas importadas do extrato."""
import difflib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.db import transacao
from app.repo import lancamentos

JANELA_DIAS = 10
# Brief dizia 5%, mas o próprio exemplo (21000 x 23000 = 9,5%) exige >= 9,53%: usado 10%.
TOLERANCIA_PERCENTUAL = 0.10
TOLERANCIA_MINIMA_CENTAVOS = 500


@dataclass(frozen=True)
class Casamento:
    pendente_id: int
    lancamento_id: int
    pontuacao: float


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", sem_acento.lower())


def _similaridade(a: str, b: str) -> float:
    na, nb = _normalizar(a), _normalizar(b)
    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb:
        return 0.0
    contido = len(ta & tb) / min(len(ta), len(tb))
    return max(difflib.SequenceMatcher(None, na, nb).ratio(), contido)


def _pontuar(pendente: Any, linha: Any) -> float | None:
    vp, vl = int(pendente["valor_centavos"]), int(linha["valor_centavos"])
    if (vp < 0) != (vl < 0) or vp == 0 or vl == 0:
        return None
    tolerancia = max(abs(vp) * TOLERANCIA_PERCENTUAL, TOLERANCIA_MINIMA_CENTAVOS)
    dif_valor = abs(vl - vp)
    dias = abs((date.fromisoformat(linha["data"]) - date.fromisoformat(pendente["data"])).days)
    if dif_valor > tolerancia or dias > JANELA_DIAS:
        return None
    return round(
        0.4 * (1 - dias / (JANELA_DIAS + 1))
        + 0.3 * (1 - dif_valor / (tolerancia + 1))
        + 0.3 * _similaridade(pendente["descricao"], linha["descricao"]),
        6,
    )


def propor_casamentos(con: Any, conta_id: int, importacao_id: int) -> list[Casamento]:
    """Propõe pares pendente×linha; cada pendente e cada linha aparece em no máximo um par."""
    pendentes = lancamentos.pendentes_da_conta(con, conta_id)
    linhas = lancamentos.importados_da_importacao(con, importacao_id)
    candidatos: list[Casamento] = []
    for p in pendentes:
        for l in linhas:
            pont = _pontuar(p, l)
            if pont is not None:
                candidatos.append(Casamento(int(p["id"]), int(l["id"]), pont))
    candidatos.sort(key=lambda c: (-c.pontuacao, c.pendente_id, c.lancamento_id))
    usados_p: set[int] = set()
    usadas_l: set[int] = set()
    propostas: list[Casamento] = []
    for c in candidatos:
        if c.pendente_id in usados_p or c.lancamento_id in usadas_l:
            continue
        usados_p.add(c.pendente_id)
        usadas_l.add(c.lancamento_id)
        propostas.append(c)
    return propostas


def confirmar_casamento(con: Any, pendente_id: int, lancamento_id: int) -> None:
    """Quita o pendente com os dados reais da linha e remove a linha (chave vai a chaves_quitadas)."""
    with transacao(con):
        pend = lancamentos.obter(con, pendente_id)
        if pend is None:
            raise ValueError(f"Lançamento pendente {pendente_id} não encontrado.")
        linha = lancamentos.obter(con, lancamento_id)
        if linha is None:
            raise ValueError(f"Linha importada {lancamento_id} não encontrada.")
        if pend["compensado"] != 0:
            raise ValueError(f"O lançamento {pendente_id} não está mais pendente.")
        if linha["origem"] != "importado":
            raise ValueError(f"O lançamento {lancamento_id} não é uma linha importada livre.")
        if pend["conta_id"] != linha["conta_id"]:
            raise ValueError("Pendente e linha importada são de contas diferentes.")
        lancamentos.excluir(con, lancamento_id)
        lancamentos.registrar_chave_quitada(con, linha["chave_deduplicacao"], pendente_id,
                                            linha["importacao_id"])
        lancamentos.quitar_pendente(con, pendente_id, linha["data"], int(linha["valor_centavos"]),
                                    linha["importacao_id"])
