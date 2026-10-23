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
MSG_LINHA_NO_EXTRATO = ("Existe uma linha no extrato que parece ser este pagamento; confirme em "
                        "Possíveis correspondências para não contar duas vezes.")


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


def _pago_a_mao(row: Any) -> bool:
    """Compensado pelo usuário (não importado e ainda não casado com o extrato)."""
    return bool(row["compensado"]) and row["origem"] != "importado" and row["importacao_id"] is None


def _data_efetiva(row: Any) -> str:
    """Pago à mão: data do pagamento; pendente: data prevista."""
    return str(row["data_compensacao"] or row["data"]) if row["compensado"] else str(row["data"])


def _pontuar(pendente: Any, linha: Any) -> float | None:
    vp, vl = int(pendente["valor_centavos"]), int(linha["valor_centavos"])
    if (vp < 0) != (vl < 0) or vp == 0 or vl == 0:
        return None
    tolerancia = max(abs(vp) * TOLERANCIA_PERCENTUAL, TOLERANCIA_MINIMA_CENTAVOS)
    dif_valor = abs(vl - vp)
    dias = abs((date.fromisoformat(linha["data"])
                - date.fromisoformat(_data_efetiva(pendente))).days)
    if dif_valor > tolerancia or dias > JANELA_DIAS:
        return None
    return round(
        0.4 * (1 - dias / (JANELA_DIAS + 1))
        + 0.3 * (1 - dif_valor / (tolerancia + 1))
        + 0.3 * _similaridade(pendente["descricao"], linha["descricao"]),
        6,
    )


def propor_casamentos(con: Any, conta_id: int, importacao_id: int | None = None) -> list[Casamento]:
    """Propõe pares candidato×linha; cada candidato e cada linha aparece em no máximo um par.
    Candidatos: pendentes e pagos à mão (compensados sem importação) da conta. Linhas: as
    importadas livres da importação dada, ou de todas as importações da conta (None)."""
    pendentes = lancamentos.candidatos_casamento_da_conta(con, conta_id)
    linhas = (lancamentos.importados_da_conta(con, conta_id) if importacao_id is None
              else lancamentos.importados_da_importacao(con, importacao_id))
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


@dataclass(frozen=True)
class PropostaCasamento:
    pendente_id: int
    lancamento_id: int
    pendente_descricao: str
    pendente_data: str
    pendente_valor: int
    linha_descricao: str
    linha_data: str
    linha_valor: int


def propostas_com_detalhes(con: Any, conta_id: int,
                           importacao_id: int | None = None) -> list[PropostaCasamento]:
    """Propostas de casamento com os dados de cada lado, para exibição."""
    out: list[PropostaCasamento] = []
    for c in propor_casamentos(con, conta_id, importacao_id):
        p = lancamentos.obter(con, c.pendente_id)
        l = lancamentos.obter(con, c.lancamento_id)
        if p is None or l is None:
            continue
        out.append(PropostaCasamento(
            c.pendente_id, c.lancamento_id, str(p["descricao"]), _data_efetiva(p),
            int(p["valor_centavos"]), str(l["descricao"]), str(l["data"]),
            int(l["valor_centavos"])))
    return out


def linha_do_extrato_para(con: Any, lancamento_id: int) -> bool:
    """True se alguma linha importada livre da conta é proposta para este lançamento."""
    row = lancamentos.obter(con, lancamento_id)
    if row is None:
        return False
    return any(c.pendente_id == lancamento_id
               for c in propor_casamentos(con, int(row["conta_id"])))


def confirmar_casamento(con: Any, pendente_id: int, lancamento_id: int) -> None:
    """Quita o pendente (ou o pago à mão) com os dados reais da linha e remove a linha
    (chave vai a chaves_quitadas com o estado anterior, para o desfazer restaurar)."""
    with transacao(con):
        pend = lancamentos.obter(con, pendente_id)
        if pend is None:
            raise ValueError(f"Lançamento pendente {pendente_id} não encontrado.")
        linha = lancamentos.obter(con, lancamento_id)
        if linha is None:
            raise ValueError(f"Linha importada {lancamento_id} não encontrada.")
        if pend["compensado"] != 0 and not _pago_a_mao(pend):
            raise ValueError(f"O lançamento {pendente_id} não está mais pendente.")
        if linha["origem"] != "importado":
            raise ValueError(f"O lançamento {lancamento_id} não é uma linha importada livre.")
        if pend["conta_id"] != linha["conta_id"]:
            raise ValueError("Pendente e linha importada são de contas diferentes.")
        lancamentos.excluir(con, lancamento_id)
        lancamentos.registrar_chave_quitada(con, linha["chave_deduplicacao"], pendente_id,
                                            linha["importacao_id"],
                                            int(pend["valor_centavos"]), int(pend["estimado"]),
                                            int(pend["compensado"]), pend["data_compensacao"])
        lancamentos.quitar_pendente(con, pendente_id, linha["data"], int(linha["valor_centavos"]),
                                    linha["importacao_id"])
