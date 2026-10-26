import sqlite3
from datetime import date

import pytest

from app.dominio.categorias import criar_subcategoria, despesas_do_mes
from app.dominio.lancamentos import ErroLancamento, criar_lancamento_manual
from app.dominio.painel import comparativo_mensal, gastos_por_categoria, interpretar_mes, top_com_outros
from app.repo import contas


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
