import sqlite3
from datetime import date

import pytest

from pathlib import Path

from app.dominio.categorias import criar_subcategoria, despesas_do_mes, entradas_do_mes
from app.dominio.lancamentos import ErroLancamento, criar_lancamento_manual
from app.dominio.painel import (
    comparativo_mensal, fluxo_caixa, formatar_variacao, gastos_por_categoria, interpretar_mes,
    proximos_vencimentos, top_com_outros, ultimos_lancamentos, variacao_percentual)
from app.importacao.deteccao import detectar
from app.importacao.servico import importar
from app.repo import contas

FIXTURE = Path(__file__).parent / "fixtures" / "extrato_itau_exemplo.csv"
HOJE = date(2026, 10, 23)


def test_interpretar_mes() -> None:
    hoje = date(2026, 10, 23)
    assert interpretar_mes(None, hoje) == (2026, 10)
    assert interpretar_mes("2027-01", hoje) == (2027, 1)
    for ruim in ("2026-13", "2026-00", "abc", "2026-1", "", " 2026-10", "2026-10\n"):
        with pytest.raises(ErroLancamento):
            interpretar_mes(ruim, hoje)


def test_top_com_outros_junta_categoria_outros() -> None:
    itens = [("A", 50), ("B", 40), ("Outros", 30), ("C", 20), ("D", 10)]
    assert top_com_outros(itens, 2) == [("A", 50), ("B", 40), ("Outros", 60)]
    assert top_com_outros(itens[:2], 5) == [("A", 50), ("B", 40)]


def test_gastos_por_categoria_soma_subcategorias_no_pai(con: sqlite3.Connection) -> None:
    cid = contas.criar_conta(con, "C", "corrente", saldo_inicial_centavos=0,
                             data_saldo_inicial="2026-01-01")
    pai = int(con.execute("SELECT id FROM categorias WHERE nome='Lazer e Entretenimento'").fetchone()[0])
    sub = criar_subcategoria(con, pai, "Sub teste")
    for cat, v in ((pai, -300), (sub, -200)):
        criar_lancamento_manual(con, conta_id=cid, data=date(2026, 10, 3), descricao="x",
                                valor_centavos=v, categoria_id=cat, compensado=True)
    g = gastos_por_categoria(con, 2026, 10)
    assert [(x.nome, x.total_centavos) for x in g] == [("Lazer e Entretenimento", 500)]
    assert g[0].total_centavos == despesas_do_mes(con, 2026, 10)


def test_comparativo_mensal_janeiro(con: sqlite3.Connection) -> None:
    cid = contas.criar_conta(con, "C", "corrente", saldo_inicial_centavos=0,
                             data_saldo_inicial="2025-01-01")
    criar_lancamento_manual(con, conta_id=cid, data=date(2025, 12, 31), descricao="x",
                            valor_centavos=-5000, compensado=True)
    c = comparativo_mensal(con, 2026, 1)
    assert [(p["mes"], p["saidas_centavos"]) for p in c] == [("2025-12", 5000), ("2026-01", 0)]


def _importar_fixture(con: sqlite3.Connection) -> int:
    cid = contas.criar_conta(con, "Itaú", "corrente", saldo_inicial_centavos=214808,
                             data_saldo_inicial="2026-07-24")
    dados = FIXTURE.read_bytes()
    parser = detectar(dados, "extrato.csv")
    assert parser is not None
    importar(con, cid, "extrato.csv", dados, parser, HOJE)
    return cid


def test_fluxo_caixa_seis_meses_cronologicos_iguais_ao_dominio(con: sqlite3.Connection) -> None:
    _importar_fixture(con)
    f = fluxo_caixa(con, 2026, 10)
    assert [p["mes"] for p in f] == ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    assert [p["rotulo"] for p in f] == ["mai/26", "jun/26", "jul/26", "ago/26", "set/26", "out/26"]
    for p in f:
        a, m = int(p["mes"][:4]), int(p["mes"][5:])
        assert p["entradas_centavos"] == entradas_do_mes(con, a, m)
        assert p["saidas_centavos"] == despesas_do_mes(con, a, m)
    assert f[-1]["saidas_centavos"] == 323329 and f[-1]["entradas_centavos"] == 40000
    assert f[-1]["saidas_fmt"] == "R$ 3.233,29"


def test_fluxo_caixa_virada_de_ano_e_tamanho(con: sqlite3.Connection) -> None:
    f = fluxo_caixa(con, 2027, 2, meses=4)
    assert [p["mes"] for p in f] == ["2026-11", "2026-12", "2027-01", "2027-02"]
    assert f[2]["rotulo"] == "jan/27"


def test_ultimos_lancamentos_ordem_limite_e_rotulos(con: sqlite3.Connection) -> None:
    cid = _importar_fixture(con)
    cartao = contas.criar_conta(con, "Nubank", "cartao", saldo_inicial_centavos=0,
                                data_saldo_inicial="2026-01-01")
    pai = int(con.execute("SELECT id FROM categorias WHERE nome='Alimentação'").fetchone()[0])
    sub = criar_subcategoria(con, pai, "Feira")
    criar_lancamento_manual(con, conta_id=cid, data=date(2026, 10, 22), descricao="Feira do Zé",
                            valor_centavos=-1000, categoria_id=sub, compensado=True)
    criar_lancamento_manual(con, conta_id=cartao, data=date(2026, 10, 22), descricao="Compra cartão",
                            valor_centavos=-5000, categoria_id=pai, compensado=True)
    criar_lancamento_manual(con, conta_id=cid, data=date(2026, 10, 25), descricao="Pendente",
                            valor_centavos=-7000, compensado=False)
    u = ultimos_lancamentos(con, limite=4)
    assert len(u) == 4
    assert "Pendente" not in [x["descricao"] for x in u]
    # empate de data: id maior primeiro
    assert [x["descricao"] for x in u[:3]] == ["Compra cartão", "Feira do Zé", "PIX TRANSF RR Supo22/10"]
    assert u[0]["conta"] == "Nubank" and u[0]["categoria"] == "Alimentação"
    assert u[1]["categoria"] == "Alimentação › Feira"
    assert u[2]["categoria"] == "Sem categoria" and u[2]["valor_centavos"] == -34354
    assert u[2]["tipo"] == "saida" and u[2]["data"] == "2026-10-22"
    assert len(ultimos_lancamentos(con)) == 5


def test_ultimos_lancamentos_banco_vazio(con: sqlite3.Connection) -> None:
    assert ultimos_lancamentos(con) == []


def test_proximos_vencimentos_ordem_situacao_limite(con: sqlite3.Connection) -> None:
    cid = contas.criar_conta(con, "C", "corrente", saldo_inicial_centavos=0,
                             data_saldo_inicial="2026-01-01")
    for d, desc, v, comp in ((date(2026, 10, 30), "Luz", -21000, False),
                             (date(2026, 10, 20), "Internet", -11990, False),
                             (date(2026, 10, 23), "Hoje", -100, False),
                             (date(2026, 10, 24), "Amanhã", -200, False),
                             (date(2026, 10, 25), "Entrada futura", 5000, False),
                             (date(2026, 10, 21), "Já paga", -300, True)):
        criar_lancamento_manual(con, conta_id=cid, data=d, descricao=desc, valor_centavos=v,
                                compensado=comp)
    p = proximos_vencimentos(con, HOJE)
    assert [x["descricao"] for x in p] == ["Internet", "Hoje", "Amanhã", "Luz"]
    assert [x["situacao"] for x in p] == ["vencida", "vence hoje", "em 1 dia", "em 7 dias"]
    assert [x["vencida"] for x in p] == [True, False, False, False]
    assert p[0]["valor_centavos"] == 11990 and p[0]["data"] == "2026-10-20"
    assert len(proximos_vencimentos(con, HOJE, limite=2)) == 2


def test_variacao_percentual() -> None:
    assert variacao_percentual(100, 0) is None
    assert variacao_percentual(100, -5) is None
    assert variacao_percentual(120, 100) == 20.0
    assert variacao_percentual(80, 100) == -20.0
    assert variacao_percentual(0, 100) == -100.0
    assert variacao_percentual(1, 3) == -66.7
    assert variacao_percentual(4, 3) == 33.3


def test_formatar_variacao() -> None:
    assert formatar_variacao(8.2) == "+8,2%"
    assert formatar_variacao(-12.0) == "-12,0%"
    assert formatar_variacao(0.0) == "0,0%"
