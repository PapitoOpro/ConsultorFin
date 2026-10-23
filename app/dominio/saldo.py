"""Saldo por data e conciliação com os saldos do extrato."""
from typing import Any
from dataclasses import dataclass
from datetime import date

from app.repo import contas, lancamentos


@dataclass(frozen=True)
class Conciliacao:
    ok: bool
    primeira_divergencia: str | None
    esperado_centavos: int | None
    calculado_centavos: int | None


def saldo_em(con: Any, conta_id: int, data_iso: str) -> int:
    """Saldo inicial + lançamentos compensados com data_compensacao <= data_iso.

    Levanta ValueError se data_iso for anterior à data do saldo inicial
    (o saldo não é conhecido antes dela).
    """
    inicial, data_inicial = contas.saldo_inicial_da_conta(con, conta_id)
    if data_inicial is not None and data_iso < data_inicial:
        raise ValueError(
            f"Data {data_iso} anterior à data do saldo inicial ({data_inicial})."
        )
    return inicial + lancamentos.soma_compensados_ate(con, conta_id, data_iso)


def saldo_atual(con: Any, conta_id: int, hoje: date) -> int:
    """Saldo em `hoje`; se `hoje` for anterior ao saldo inicial, retorna o saldo inicial."""
    inicial, data_inicial = contas.saldo_inicial_da_conta(con, conta_id)
    if data_inicial is not None and hoje.isoformat() < data_inicial:
        return inicial
    return saldo_em(con, conta_id, hoje.isoformat())


def conciliar(
    con: Any, conta_id: int, saldos_dia: dict[str, int]
) -> Conciliacao:
    """Compara cada saldo do extrato (ordem crescente) com o calculado só dos importados."""
    inicial, data_inicial = contas.saldo_inicial_da_conta(con, conta_id)
    somas = lancamentos.somas_diarias_conciliacao(con, conta_id)
    idx = 0
    acumulado = 0
    for dia in sorted(saldos_dia):
        if data_inicial is not None and dia < data_inicial:
            continue
        while idx < len(somas) and somas[idx][0] <= dia:
            acumulado += somas[idx][1]
            idx += 1
        calculado = inicial + acumulado
        if calculado != saldos_dia[dia]:
            return Conciliacao(False, dia, saldos_dia[dia], calculado)
    return Conciliacao(True, None, None, None)
