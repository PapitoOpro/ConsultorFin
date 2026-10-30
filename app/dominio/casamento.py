"""Casamento de lançamentos pendentes com linhas importadas do extrato."""
import bisect
import difflib
import heapq
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from typing import Any

from app.db import transacao
from app.dominio.parcelas import MSG_FATURA_PAGAR, TIPO_FATURA
from app.repo import faturas, holerites, lancamentos, recorrentes

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


@lru_cache(maxsize=65536)
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


def _similaridade_teto(a: str, b: str) -> float:
    """Limite superior de _similaridade (difflib.quick_ratio no lugar de ratio)."""
    na, nb = _normalizar(a), _normalizar(b)
    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb:
        return 0.0
    contido = len(ta & tb) / min(len(ta), len(tb))
    return max(difflib.SequenceMatcher(None, na, nb).quick_ratio(), contido)


def _pago_a_mao(row: Any) -> bool:
    """Compensado pelo usuário (não importado e ainda não casado com o extrato)."""
    return bool(row["compensado"]) and row["origem"] != "importado" and row["importacao_id"] is None


def _data_efetiva(row: Any) -> str:
    """Pago à mão: data do pagamento; pendente: data prevista."""
    return str(row["data_compensacao"] or row["data"]) if row["compensado"] else str(row["data"])


def _parte_sem_texto(pendente: Any, linha: Any) -> tuple[float, float] | None:
    """Parcelas de data e de valor da pontuação; None se o par não é elegível."""
    vp, vl = int(pendente["valor_centavos"]), int(linha["valor_centavos"])
    if (vp < 0) != (vl < 0) or vp == 0 or vl == 0:
        return None
    tolerancia = max(abs(vp) * TOLERANCIA_PERCENTUAL, TOLERANCIA_MINIMA_CENTAVOS)
    dif_valor = abs(vl - vp)
    if dif_valor > tolerancia:  # barato primeiro: evita o parse de datas
        return None
    dias = abs((date.fromisoformat(linha["data"])
                - date.fromisoformat(_data_efetiva(pendente))).days)
    if dias > JANELA_DIAS:
        return None
    return 0.4 * (1 - dias / (JANELA_DIAS + 1)), 0.3 * (1 - dif_valor / (tolerancia + 1))


def _pontuar(pendente: Any, linha: Any) -> float | None:
    partes = _parte_sem_texto(pendente, linha)
    if partes is None:
        return None
    return round(partes[0] + partes[1] + 0.3 * _similaridade(pendente["descricao"],
                                                             linha["descricao"]), 6)


def propor_casamentos(con: Any, conta_id: int, importacao_id: int | None = None) -> list[Casamento]:
    """Propõe pares candidato×linha; cada candidato e cada linha aparece em no máximo um par.
    Candidatos: pendentes e pagos à mão (compensados sem importação) da conta. Linhas: as
    importadas livres da importação dada, ou de todas as importações da conta (None).
    Só pares com datas a até JANELA_DIAS são comparados (busca binária por data) e a
    similaridade de texto, a parte cara, é calculada sob demanda: cada par entra numa fila
    com o teto da pontuação (similaridade 1) e só é pontuado de fato se chegar ao topo com
    ambos os lados ainda livres. O resultado é o mesmo da varredura completa."""
    pendentes = lancamentos.candidatos_casamento_da_conta(con, conta_id)
    if not pendentes:
        return []
    # Valor zero nunca casa: nem a data é lida (como no algoritmo antigo).
    datas = [None if int(p["valor_centavos"]) == 0 else date.fromisoformat(_data_efetiva(p))
             for p in pendentes]
    validas = [d for d in datas if d is not None]
    if not validas:
        return []
    janela = timedelta(days=JANELA_DIAS)
    minimo, maximo = (min(validas) - janela).isoformat(), (max(validas) + janela).isoformat()
    linhas = (lancamentos.importados_da_conta(con, conta_id, minimo, maximo)
              if importacao_id is None
              else lancamentos.importados_da_importacao(con, importacao_id))
    linhas = sorted(linhas, key=lambda l: str(l["data"]))
    chaves = [str(l["data"]) for l in linhas]
    valores = [int(l["valor_centavos"]) for l in linhas]
    # (-pontuação ou teto, pendente_id, lancamento_id, parcelas sem texto | None se já exata)
    fila: list[tuple[float, int, int, tuple[float, float] | None, int, int, int]] = []
    for pi, (p, dia) in enumerate(zip(pendentes, datas, strict=True)):
        vp = int(p["valor_centavos"])
        if vp == 0 or dia is None:
            continue
        tol = max(abs(vp) * TOLERANCIA_PERCENTUAL, TOLERANCIA_MINIMA_CENTAVOS)
        ini = bisect.bisect_left(chaves, (dia - janela).isoformat())
        fim = bisect.bisect_right(chaves, (dia + janela).isoformat())
        for li in range(ini, fim):
            vl = valores[li]
            if abs(vl - vp) > tol or (vl < 0) != (vp < 0):  # pré-filtro barato
                continue
            partes = _parte_sem_texto(p, linhas[li])
            if partes is not None:
                teto = round(partes[0] + partes[1] + 0.3 * 1.0, 6)
                fila.append((-teto, int(p["id"]), int(linhas[li]["id"]), partes, pi, li, 0))
    # Linha e pendente ligados cada um a um holerite: casar deixaria dois holerites no mesmo
    # lançamento; esse par nunca é proposto.
    com_holerite = holerites.lancamentos_vinculados(con) if fila else set()
    fila = [f for f in fila if not (f[1] in com_holerite and f[2] in com_holerite)]
    heapq.heapify(fila)
    usados_p: set[int] = set()
    usadas_l: set[int] = set()
    propostas: list[Casamento] = []
    while fila:
        neg, pid, lid, partes, pi, li, refinado = heapq.heappop(fila)
        if pid in usados_p or lid in usadas_l:
            continue
        if partes is not None:  # ainda é só um teto: refina (teto justo, depois exato)
            dp, dl = pendentes[pi]["descricao"], linhas[li]["descricao"]
            if refinado == 0:
                sim = _similaridade_teto(dp, dl)
                nxt = 1
            else:
                sim = _similaridade(dp, dl)
                nxt = 2
            novo = round(partes[0] + partes[1] + 0.3 * sim, 6)
            heapq.heappush(fila, (-novo, pid, lid, partes if nxt == 1 else None, pi, li, nxt))
            continue
        usados_p.add(pid)
        usadas_l.add(lid)
        propostas.append(Casamento(pid, lid, -neg))
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
    (chave vai a chaves_quitadas com o estado anterior, para o desfazer restaurar). Holerite
    ligado à linha passa ao pendente, que assume o crédito."""
    with transacao(con):
        pend = lancamentos.obter(con, pendente_id)
        if pend is None:
            raise ValueError(f"Lançamento pendente {pendente_id} não encontrado.")
        if pend["tipo"] == TIPO_FATURA:
            raise ValueError(MSG_FATURA_PAGAR)
        linha = lancamentos.obter(con, lancamento_id)
        if linha is None:
            raise ValueError(f"Linha importada {lancamento_id} não encontrada.")
        if faturas.por_pagamento(con, lancamento_id) is not None:
            raise ValueError("Esta linha do extrato é o pagamento de uma fatura; desfaça o "
                             "pagamento da fatura antes.")
        if pend["compensado"] != 0 and not _pago_a_mao(pend):
            raise ValueError(f"O lançamento {pendente_id} não está mais pendente.")
        if linha["origem"] != "importado":
            raise ValueError(f"O lançamento {lancamento_id} não é uma linha importada livre.")
        if pend["conta_id"] != linha["conta_id"]:
            raise ValueError("Pendente e linha importada são de contas diferentes.")
        hol = holerites.por_lancamento(con, lancamento_id)
        if hol is not None:
            if holerites.por_lancamento(con, pendente_id) is not None:
                raise ValueError("Pendente e linha do extrato já estão ligados a holerites "
                                 "diferentes; não é possível casá-los.")
            holerites.definir_lancamento(con, int(hol["id"]), pendente_id)
        lancamentos.excluir(con, lancamento_id)
        lancamentos.registrar_chave_quitada(con, linha["chave_deduplicacao"], pendente_id,
                                            linha["importacao_id"],
                                            int(pend["valor_centavos"]), int(pend["estimado"]),
                                            int(pend["compensado"]), pend["data_compensacao"])
        lancamentos.quitar_pendente(con, pendente_id, linha["data"], int(linha["valor_centavos"]),
                                    linha["importacao_id"])
        if pend["recorrente_id"] is not None:
            # Mesma propagação de confirmar_recorrente: o valor real vira a estimativa futura.
            recorrentes.atualizar_futuros_estimados(con, int(pend["recorrente_id"]),
                                                    str(pend["data"]), int(linha["valor_centavos"]))
