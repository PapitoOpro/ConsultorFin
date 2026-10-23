import sqlite3
from datetime import date

import pytest

from app.dominio.lancamentos import (
    ErroLancamento, Filtros, alterar_tipo, categorizar_em_lote, criar_lancamento_manual,
    excluir_manual, interpretar_data, interpretar_id, listar, pagar_pendente,
)
from app.dominio.saldo import saldo_atual
from app.repo import contas


@pytest.fixture
def conta(con: sqlite3.Connection) -> int:
    return contas.criar_conta(con, "C", "corrente", saldo_inicial_centavos=100000,
                              data_saldo_inicial="2026-01-01")


def _m(con: sqlite3.Connection, conta: int, dia: int, desc: str, valor: int, ok: bool = True) -> int:
    return criar_lancamento_manual(con, conta_id=conta, data=date(2026, 3, dia), descricao=desc,
                                   valor_centavos=valor, compensado=ok)


def test_totais_so_compensados_e_pendentes_separados(con: sqlite3.Connection, conta: int) -> None:
    _m(con, conta, 1, "Salario", 300000)
    _m(con, conta, 2, "Mercado", -50000)
    _m(con, conta, 3, "Boleto", -20000, ok=False)
    t = _m(con, conta, 4, "Para poupanca", -10000)
    alterar_tipo(con, t, "transferencia")
    f = Filtros(de="2026-03-01", ate="2026-03-31")
    p = listar(con, f)
    assert (p.entradas_centavos, p.saidas_centavos, p.liquido_centavos) == (300000, 50000, 250000)
    assert p.pendentes_centavos == -20000
    assert p.total == 4


def test_saldo_acumulado_por_conta_e_pendente_sem_saldo(con: sqlite3.Connection, conta: int) -> None:
    _m(con, conta, 1, "A", 1000)
    _m(con, conta, 2, "B", -300)
    _m(con, conta, 3, "C", -50, ok=False)
    p = listar(con, Filtros())
    assert [x.saldo_acumulado_centavos for x in p.linhas] == [101000, 100700, None]
    assert p.linhas[1].saldo_acumulado_centavos == saldo_atual(con, conta, date(2026, 3, 2))


def test_categorizar_em_lote_valida(con: sqlite3.Connection, conta: int) -> None:
    a = _m(con, conta, 1, "A", -100)
    with pytest.raises(ErroLancamento):
        categorizar_em_lote(con, [], 1)
    with pytest.raises(ErroLancamento):
        categorizar_em_lote(con, [a, 9999], 1)
    with pytest.raises(ErroLancamento):
        categorizar_em_lote(con, [a], 9999)
    assert categorizar_em_lote(con, [a, a], 1) == 1


def test_excluir_manual_recusa_importado(con: sqlite3.Connection, conta: int) -> None:
    a = _m(con, conta, 1, "A", -100)
    con.execute("UPDATE lancamentos SET origem = 'importado' WHERE id = ?", (a,))
    con.commit()
    with pytest.raises(ErroLancamento, match="importados não podem ser excluídos"):
        excluir_manual(con, a)


def test_pagar_pendente_recusa_compensado(con: sqlite3.Connection, conta: int) -> None:
    a = _m(con, conta, 1, "A", -100)
    with pytest.raises(ErroLancamento, match="já está compensado"):
        pagar_pendente(con, a, 50, date(2026, 3, 5), hoje=date(2026, 3, 5))


def test_filtros_parametros_invalidos_e_padrao() -> None:
    f = Filtros.de_parametros({}, date(2026, 12, 15))
    assert (f.de, f.ate) == ("2026-12-01", "2026-12-31")
    assert Filtros.de_parametros({"de": "", "ate": ""}, date(2026, 12, 15)).de is None
    with pytest.raises(ErroLancamento):
        Filtros.de_parametros({"conta_id": "1 OR 1=1"}, date(2026, 1, 1))
    assert interpretar_data("05/03/2026") == "2026-03-05"
    with pytest.raises(ErroLancamento):
        interpretar_id("١٢")  # dígitos não-ASCII


def test_totais_excluem_cartao_como_o_inicio(con: sqlite3.Connection) -> None:
    from app.dominio.categorias import despesas_do_mes
    cc = contas.criar_conta(con, "CC", "corrente", saldo_inicial_centavos=100000,
                            data_saldo_inicial="2026-10-01")
    card = contas.criar_conta(con, "Cartão", "cartao", saldo_inicial_centavos=0,
                              data_saldo_inicial="2026-10-01")
    criar_lancamento_manual(con, conta_id=card, data=date(2026, 10, 5), descricao="Restaurante",
                            valor_centavos=-8000, compensado=True)
    criar_lancamento_manual(con, conta_id=cc, data=date(2026, 10, 6), descricao="Mercado",
                            valor_centavos=-5000, compensado=True)
    criar_lancamento_manual(con, conta_id=card, data=date(2026, 10, 25), descricao="Parcela",
                            valor_centavos=-3000)
    todas = listar(con, Filtros.de_parametros({}, date(2026, 10, 23)))
    assert todas.saidas_centavos == despesas_do_mes(con, 2026, 10) == 5000
    assert (todas.pendentes_centavos, todas.total, todas.conta_cartao) == (0, 3, False)
    so_cartao = listar(con, Filtros.de_parametros({"conta_id": str(card)}, date(2026, 10, 23)))
    assert (so_cartao.saidas_centavos, so_cartao.pendentes_centavos, so_cartao.total,
            so_cartao.conta_cartao) == (8000, -3000, 2, True)
    so_cc = listar(con, Filtros.de_parametros({"conta_id": str(cc)}, date(2026, 10, 23)))
    assert (so_cc.saidas_centavos, so_cc.conta_cartao) == (5000, False)


def test_excluir_manual_recusa_conta_fixa(con: sqlite3.Connection, conta: int) -> None:
    from app.dominio.recorrentes import criar_recorrente, gerar_recorrentes
    criar_recorrente(con, nome="Luz", tipo="saida", dia=10, valor_estimado_centavos=1000,
                     conta_id=conta, categoria_id=None)
    gerar_recorrentes(con, date(2026, 3, 1), 1)
    rid = con.execute("SELECT id FROM lancamentos WHERE recorrente_id IS NOT NULL").fetchone()[0]
    with pytest.raises(ErroLancamento, match="Pular este mês"):
        excluir_manual(con, rid)


def test_pagar_pendente_recusa_data_futura(con: sqlite3.Connection, conta: int) -> None:
    a = _m(con, conta, 5, "Boleto", -5000, ok=False)
    with pytest.raises(ErroLancamento, match="não pode ser no futuro"):
        pagar_pendente(con, a, None, date(2026, 3, 6), hoje=date(2026, 3, 5))
    pagar_pendente(con, a, None, date(2026, 3, 5), hoje=date(2026, 3, 5))
