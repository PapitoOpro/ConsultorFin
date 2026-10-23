"""Vencimentos (vencidas / a vencer) e projeção de saldo por mês."""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.dominio.dinheiro import somar_meses
from app.dominio.saldo import saldo_atual
from app.repo import contas, lancamentos

HORIZONTES = (3, 6, 12)


@dataclass(frozen=True)
class Vencimentos:
    vencidas: list[Any]
    a_vencer: list[Any]


@dataclass(frozen=True)
class PontoProjecao:
    mes: str
    parcelas_centavos: int
    saida_pendente_centavos: int
    entrada_pendente_centavos: int
    saldo_final_centavos: int


def vencimentos(con: Any, hoje: date, janela_dias: int = 7) -> Vencimentos:
    """Saídas pendentes: vencidas (data < hoje) e a vencer (hoje <= data <= hoje+janela, inclusive)."""
    hoje_iso = hoje.isoformat()
    limite = (hoje + timedelta(days=janela_dias)).isoformat()
    todas = lancamentos.saidas_pendentes_entre(con, None, limite)
    return Vencimentos(
        vencidas=[r for r in todas if r["data"] < hoje_iso],
        a_vencer=[r for r in todas if r["data"] >= hoje_iso],
    )


def projetar(con: Any, hoje: date, meses: int) -> list[PontoProjecao]:
    """Projeta o saldo mês a mês (`meses` pontos, o primeiro é o mês de `hoje`).

    Saldo inicial = soma dos saldos atuais das contas não-cartão. Pendentes (inclusive
    estimados e os de contas cartão) subtraem/somam no mês da data; os anteriores ao
    mês de hoje (vencidos) entram no primeiro ponto. Compensados e pendentes além do
    horizonte ficam de fora.
    """
    if meses not in HORIZONTES:
        raise ValueError(f"Horizonte inválido: {meses} (use 3, 6 ou 12 meses).")
    primeiro = hoje.replace(day=1)
    meses_iso = [somar_meses(primeiro, i).isoformat()[:7] for i in range(meses)]
    fim = somar_meses(primeiro, meses).isoformat()  # exclusivo
    saldo = sum(saldo_atual(con, c, hoje) for c in contas.ids_nao_cartao(con))
    acum: dict[str, list[int]] = {m: [0, 0, 0] for m in meses_iso}  # parcelas, saídas, entradas
    for r in lancamentos.pendentes_ate(con, fim):
        if r["data"] >= fim:
            continue
        mes = max(r["data"][:7], meses_iso[0])
        v = int(r["valor_centavos"])
        if v < 0:
            acum[mes][1] += -v
            if r["grupo_parcela_uuid"]:
                acum[mes][0] += -v
        else:
            acum[mes][2] += v
    pontos: list[PontoProjecao] = []
    for m in meses_iso:
        par, sai, ent = acum[m]
        saldo += ent - sai
        pontos.append(PontoProjecao(m, par, sai, ent, saldo))
    return pontos
