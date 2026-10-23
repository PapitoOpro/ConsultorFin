from datetime import date
from pathlib import Path

import pytest

from app.dominio.saldo import Conciliacao, conciliar, saldo_atual, saldo_em
from app.importacao.itau_csv import ParserItauCsv
from app.importacao.servico import importar
from app.repo.contas import criar_conta

FIXTURE = Path(__file__).parent / "fixtures" / "extrato_itau_exemplo.csv"
HOJE = date(2026, 10, 23)


def _preparar(con) -> tuple[int, dict[str, int]]:
    conta = criar_conta(con, "Itaú", "corrente", saldo_inicial_centavos=214808,
                        data_saldo_inicial="2026-07-24")
    conteudo = FIXTURE.read_bytes()
    importar(con, conta, "extrato.csv", conteudo, ParserItauCsv(), HOJE)
    return conta, ParserItauCsv().ler(conteudo).saldos_dia


def test_aceite_saldo_e_conciliacao(con) -> None:
    conta, saldos = _preparar(con)
    assert saldo_em(con, conta, "2026-10-22") == 140914
    assert saldo_atual(con, conta, HOJE) == 140914
    r = conciliar(con, conta, saldos)
    assert r == Conciliacao(True, None, None, None)


def test_divergencia_aponta_primeiro_dia_errado(con) -> None:
    conta, saldos = _preparar(con)
    dia = sorted(saldos)[len(saldos) // 2]
    con.execute(
        "UPDATE lancamentos SET valor_centavos = valor_centavos + 100 WHERE id = ("
        "SELECT id FROM lancamentos WHERE data_compensacao = ? LIMIT 1)", (dia,))
    r = conciliar(con, conta, saldos)
    assert r.ok is False
    assert r.primeira_divergencia == dia
    assert r.esperado_centavos == saldos[dia]
    assert r.calculado_centavos == saldos[dia] + 100


def test_saldo_antes_da_data_inicial_levanta_erro(con) -> None:
    conta, _ = _preparar(con)
    with pytest.raises(ValueError, match="anterior"):
        saldo_em(con, conta, "2026-07-23")
    assert saldo_em(con, conta, "2026-07-24") >= -10**9


def test_dias_anteriores_ao_saldo_inicial_sao_ignorados(con) -> None:
    conta, saldos = _preparar(con)
    r = conciliar(con, conta, {**saldos, "2026-01-01": 1})
    assert r.ok is True


def test_conciliar_ignora_nao_importados(con) -> None:
    conta, saldos = _preparar(con)
    con.execute(
        "INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
        " chave_deduplicacao, origem, data_compensacao, compensado)"
        " VALUES ('u', ?, '2026-08-01', 'x', -500, 'saida', 'k', 'manual', '2026-08-01', 1)",
        (conta,))
    assert conciliar(con, conta, saldos).ok is True
    assert saldo_em(con, conta, "2026-10-22") == 140914 - 500


def test_conta_inexistente(con) -> None:
    with pytest.raises(ValueError, match="não encontrada"):
        saldo_em(con, 999, "2026-10-22")
