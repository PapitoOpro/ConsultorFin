"""Saldo por data, conciliação com os saldos do extrato e ajuste do saldo inicial."""
from typing import Any
from dataclasses import dataclass
from datetime import date

from app.db import transacao
from app.dominio.dinheiro import ValorMuitoAlto, verificar_magnitude
from app.repo import contas, lancamentos


@dataclass(frozen=True)
class Conciliacao:
    ok: bool
    primeira_divergencia: str | None
    esperado_centavos: int | None
    calculado_centavos: int | None
    dias_comparados: int = 0


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
    """Compara cada saldo do extrato (ordem crescente) com o calculado só dos importados.

    ok só é True se ao menos um dia foi comparado (dias anteriores ao saldo inicial
    não são comparáveis) e nenhum divergiu."""
    inicial, data_inicial = contas.saldo_inicial_da_conta(con, conta_id)
    somas = lancamentos.somas_diarias_conciliacao(con, conta_id)
    idx = 0
    acumulado = 0
    comparados = 0
    for dia in sorted(saldos_dia):
        if data_inicial is not None and dia < data_inicial:
            continue
        while idx < len(somas) and somas[idx][0] <= dia:
            acumulado += somas[idx][1]
            idx += 1
        calculado = inicial + acumulado
        comparados += 1
        if calculado != saldos_dia[dia]:
            return Conciliacao(False, dia, saldos_dia[dia], calculado, comparados)
    return Conciliacao(comparados > 0, None, None, None, comparados)


def ajustar_saldo_inicial(con: Any, conta_id: int, saldo_inicial_centavos: int,
                          data_iso: str) -> None:
    """Redefine saldo inicial e data da conta (validados: teto de valor e data real)."""
    try:
        verificar_magnitude(saldo_inicial_centavos)
    except ValorMuitoAlto as exc:
        raise ValueError("Valor muito alto. O máximo é R$ 10.000.000.000,00.") from exc
    try:
        data = date.fromisoformat(data_iso).isoformat()
    except (TypeError, ValueError) as exc:
        raise ValueError("Data do saldo inicial inválida.") from exc
    if contas.obter(con, conta_id) is None:
        raise ValueError("Conta não encontrada.")
    with transacao(con):
        contas.atualizar_saldo_inicial(con, conta_id, saldo_inicial_centavos, data)


@dataclass(frozen=True)
class SaldoInformado:
    """Saldo do banco em data posterior ao fim do extrato, comparado ao calculado (informativo)."""
    data: str
    banco_centavos: int
    calculado_centavos: int

    @property
    def diferenca_centavos(self) -> int:
        return self.banco_centavos - self.calculado_centavos


def comparar_saldo_informado(con: Any, conta_id: int, informado: tuple[str, int]) -> SaldoInformado | None:
    """None se a data é anterior ao saldo inicial da conta (não há como calcular)."""
    data_iso, banco = informado
    try:
        return SaldoInformado(data_iso, banco, saldo_em(con, conta_id, data_iso))
    except ValueError:
        return None
