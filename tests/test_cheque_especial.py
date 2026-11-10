"""Cheque especial: dias de uso no mês, estados e configuração."""
import sqlite3
from datetime import date

import pytest

from app.dominio import cheque_especial as ce
from app.repo import contas as repo_contas
from app.repo import lancamentos as repo_lanc

CFG = dict(limite_centavos=441000, dias_isentos=10, alerta_a_partir_de=8)


def _conta(con: sqlite3.Connection, data_inicial: str | None = "2026-10-01", inicial: int = 0) -> int:
    cid = repo_contas.criar_conta(con, "Itaú CC", "corrente", saldo_inicial_centavos=inicial,
                                  data_saldo_inicial=data_inicial)
    ce.salvar_config(con, ce.ConfigChequeEspecial(conta_id=cid, **CFG))
    return cid


def _lanc(con: sqlite3.Connection, conta: int, data: str, valor: int, compensado: bool = True) -> None:
    repo_lanc.inserir_manual(con, conta_id=conta, data=data, descricao="X", valor_centavos=valor,
                             tipo="entrada" if valor > 0 else "saida", categoria_id=None,
                             compensado=compensado, observacao=None)
    con.commit()


def test_contagem_acumula_e_nao_zera_ao_voltar_positivo(con: sqlite3.Connection) -> None:
    c = _conta(con)
    _lanc(con, c, "2026-11-03", -10000)
    _lanc(con, c, "2026-11-05", 20000)
    _lanc(con, c, "2026-11-08", -15000)
    r = ce.calcular(con, date(2026, 11, 10))
    assert r is not None and r.dias_calculados == 5  # 3,4 + 8,9,10


def test_virada_de_mes_zera(con: sqlite3.Connection) -> None:
    c = _conta(con)
    _lanc(con, c, "2026-10-28", -10000)
    _lanc(con, c, "2026-11-01", 20000)
    r = ce.calcular(con, date(2026, 11, 2))
    assert r is not None and r.dias_calculados == 0


def test_dia_primeiro_do_mes_negativo_conta_um(con: sqlite3.Connection) -> None:
    c = _conta(con)
    _lanc(con, c, "2026-10-31", -10000)
    r = ce.calcular(con, date(2026, 11, 1))
    assert r is not None and r.dias_calculados == 1


def test_dia_antes_do_saldo_inicial_nao_conta(con: sqlite3.Connection) -> None:
    _conta(con, data_inicial="2026-11-05", inicial=-50000)
    r = ce.calcular(con, date(2026, 11, 7))
    assert r is not None and r.dias_calculados == 3  # 5, 6, 7


def test_sem_lancamentos_nem_data_inicial(con: sqlite3.Connection) -> None:
    _conta(con, data_inicial=None)
    r = ce.calcular(con, date(2026, 11, 7))
    assert r is not None and r.dias_calculados == 0 and r.estado == "ok"


@pytest.mark.parametrize("dias,estado", [(7, "ok"), (8, "atencao"), (9, "atencao"), (10, "esgotado"), (11, "esgotado")])
def test_estado_nos_limites(dias: int, estado: str) -> None:
    assert ce.estado_de(dias, ce.ConfigChequeEspecial(conta_id=1, **CFG)) == estado


def test_excesso_e_percentual_limitado(con: sqlite3.Connection) -> None:
    c = _conta(con)
    ce.salvar_config(con, ce.ConfigChequeEspecial(conta_id=c, limite_centavos=100000))
    _lanc(con, c, "2026-11-03", -150000)
    r = ce.calcular(con, date(2026, 11, 4))
    assert r is not None
    assert (r.em_uso_centavos, r.excesso_centavos, r.pct_limite) == (150000, 50000, 100)


def test_pendente_nao_conta(con: sqlite3.Connection) -> None:
    c = _conta(con)
    _lanc(con, c, "2026-11-03", -10000, compensado=False)
    r = ce.calcular(con, date(2026, 11, 5))
    assert r is not None and r.dias_calculados == 0


def test_ajuste_manual_substitui_no_mes_e_expira_no_seguinte(con: sqlite3.Connection) -> None:
    _conta(con)
    ce.informar_dias(con, date(2026, 11, 10), 9)
    r = ce.calcular(con, date(2026, 11, 10))
    assert r is not None and (r.dias_usados, r.dias_informados) == (9, 9)
    r2 = ce.calcular(con, date(2026, 12, 1))
    assert r2 is not None and r2.dias_informados is None


def test_dias_informados_continuam_contando_nos_dias_seguintes(con: sqlite3.Connection) -> None:
    c = _conta(con)
    _lanc(con, c, "2026-11-09", -50000)  # saldo negativo desde antes
    ce.informar_dias(con, date(2026, 11, 9), 4)
    r = ce.calcular(con, date(2026, 11, 9))
    assert r is not None and r.dias_usados == 4
    r = ce.calcular(con, date(2026, 11, 12))
    assert r is not None and (r.dias_usados, r.dias_informados) == (7, 4)


def test_calcular_sem_config_retorna_none(con: sqlite3.Connection) -> None:
    assert ce.calcular(con, date(2026, 11, 10)) is None


@pytest.mark.parametrize("args", [(1, 0, 10, 8), (1, -5, 10, 8), (1, 1000, 10, 11), (1, 1000, 0, 1),
                                  (1, 1000, 32, 8)])
def test_validar_config_rejeita(args: tuple[int, int, int, int]) -> None:
    with pytest.raises(ValueError):
        ce.validar_config(*args)


def test_informar_dias_fora_da_faixa(con: sqlite3.Connection) -> None:
    for n in (-1, 32):
        with pytest.raises(ValueError):
            ce.informar_dias(con, date(2026, 11, 10), n)


def test_config_ida_e_volta(con: sqlite3.Connection) -> None:
    cfg = ce.ConfigChequeEspecial(conta_id=3, **CFG)
    ce.salvar_config(con, cfg)
    assert ce.carregar_config(con) == cfg
