"""Totais com fatura importada: caixa pela linha da fatura, gasto/categoria pelos itens."""
import sqlite3
from datetime import date

from app.dominio import faturas as dom
from app.dominio import faturas_pagamento as pag
from app.dominio.categorias import (
    consumo_do_mes, criar_categoria, despesas_do_mes, entradas_do_mes)
from app.dominio.contas_pagar import abrir_painel
from app.dominio.lancamentos import Filtros, listar
from app.dominio.painel import SEM_CATEGORIA, gastos_por_categoria, resumo_inicio
from app.dominio.projecao import projetar
from app.importacao.fatura.modelo import FaturaLida, ItemFatura
from app.repo import contas as repo_contas
from app.repo import lancamentos as repo_lanc
from app.repo import painel as repo_painel

HOJE = date(2026, 10, 27)


def _item(data: str, desc: str, valor: int, tipo: str = "compra",
          parcela: tuple[int, int] | None = None) -> ItemFatura:
    return ItemFatura(data=data, descricao=desc, valor_centavos=valor, tipo=tipo,
                      parcela_atual=parcela[0] if parcela else None,
                      parcela_total=parcela[1] if parcela else None,
                      final_cartao="9999", categoria_banco=None)


def _setup(con: sqlite3.Connection, parcelado: bool = False) -> dict[str, int]:
    """Fatura venc. 2026-10-10, total 300,00: compra 200,00 (categoria), compra 150,00
    (sem categoria), crédito 50,00 — todos em setembro."""
    cc = repo_contas.criar_conta(con, "Corrente Teste", "corrente", saldo_inicial_centavos=100000,
                                 data_saldo_inicial="2026-09-01")
    cartao = repo_contas.criar_conta(con, "Cartao Teste", "cartao", final_numero="9999")
    cat = criar_categoria(con, "Loja Inventada")
    itens = [_item("2026-09-15", "LOJA A", -20000, parcela=(1, 3) if parcelado else None),
             _item("2026-09-20", "LOJA B", -15000),
             _item("2026-09-25", "ESTORNO C", 5000, tipo="credito")]
    f = FaturaLida(emissor="Banco Teste", bandeira="Visa", produto="Gold", final_cartao="9999",
                   data_vencimento="2026-10-10", data_fechamento=None,
                   total_impresso_centavos=30000, ajustes_resumo_centavos=0, itens=itens)
    r = dom.importar_fatura(con, f, "h1", "f.pdf", cartao, HOJE, [cat, None, None])
    return {"cc": cc, "cartao": cartao, "cat": cat, "fatura": r.fatura_id,
            "linha": r.lancamento_id}


def _pagto(con: sqlite3.Connection, conta: int, data: str = "2026-10-09") -> int:
    lid = repo_lanc.inserir_manual(con, conta_id=conta, data=data, descricao="PAG FATURA X",
                                   valor_centavos=-30000, tipo="pagamento_fatura",
                                   categoria_id=None, compensado=True, observacao=None)
    con.commit()
    return lid


def _outubro(con: sqlite3.Connection, conta_id: int | None = None) -> object:
    params = {} if conta_id is None else {"conta_id": str(conta_id)}
    return listar(con, Filtros.de_parametros(params, date(2026, 10, 23)))


# --- Regra 1: extrato ------------------------------------------------------------------------

def test_extrato_fatura_pendente_soma_em_pendentes_mesmo_em_cartao(con: sqlite3.Connection) -> None:
    ids = _setup(con)
    _pagto(con, ids["cc"])
    p = _outubro(con)
    assert (p.saidas_centavos, p.pendentes_centavos) == (30000, -30000)  # sem casar: conta


def test_extrato_casado_conta_a_fatura_e_nao_o_pagamento(con: sqlite3.Connection) -> None:
    ids = _setup(con)
    lid = _pagto(con, ids["cc"])
    pag.confirmar_pagamento(con, ids["fatura"], lid)
    p = _outubro(con)
    assert (p.saidas_centavos, p.pendentes_centavos) == (30000, 0)
    assert _outubro(con, ids["cc"]).saidas_centavos == 0
    assert _outubro(con, ids["cartao"]).saidas_centavos == 30000
    rows = repo_lanc.listar_extrato(con, de=None, ate=None, conta_id=None, categoria_id=None,
                                    sem_categoria=False, tipo=None, texto=None, compensado=None,
                                    limite=50, deslocamento=0)
    marca = {int(r["id"]): r["fatura_casada_id"] for r in rows}
    assert marca[lid] == ids["fatura"] and marca[ids["linha"]] is None


# --- Regra 2: painel -------------------------------------------------------------------------

def test_painel_exclui_pagamento_casado_e_mostra_fatura_pendente(con: sqlite3.Connection) -> None:
    ids = _setup(con)
    lid = _pagto(con, ids["cc"])
    pendentes = [int(r["id"]) for r in repo_painel.saidas_pendentes_proximas(con, 50)]
    assert ids["linha"] in pendentes
    assert lid in [int(r["id"]) for r in repo_painel.despesas_detalhadas(
        con, "2026-10-01", "2026-11-01")]
    pag.confirmar_pagamento(con, ids["fatura"], lid)
    assert ids["linha"] not in [int(r["id"]) for r in repo_painel.saidas_pendentes_proximas(con, 50)]
    assert repo_painel.despesas_detalhadas(con, "2026-10-01", "2026-11-01") == []
    r = resumo_inicio(con, date(2026, 10, 27), 2026, 10, 3)
    assert r.saidas_centavos == 30000 and r.saldo_total_centavos == 70000  # caixa: a fatura
    assert entradas_do_mes(con, 2026, 10) == 0


# --- Regra 3: categoria / gasto do mês pelos itens -------------------------------------------

def test_caixa_do_mes_conta_fatura_uma_vez_e_sem_itens(con: sqlite3.Connection) -> None:
    ids = _setup(con)
    lid = _pagto(con, ids["cc"], "2026-10-04")
    # sem casar: o pagamento conta uma vez; a linha pendente da fatura não
    assert (despesas_do_mes(con, 2026, 9), despesas_do_mes(con, 2026, 10)) == (0, 30000)
    pag.confirmar_pagamento(con, ids["fatura"], lid)
    # casado: a linha da fatura conta uma vez, na data do pagamento; o pagamento sai
    assert (despesas_do_mes(con, 2026, 9), despesas_do_mes(con, 2026, 10)) == (0, 30000)
    assert entradas_do_mes(con, 2026, 10) == 0


def test_caixa_usa_data_do_pagamento_e_ignora_outras_linhas_do_cartao(
        con: sqlite3.Connection) -> None:
    ids = _setup(con)
    repo_lanc.inserir_manual(con, conta_id=ids["cartao"], data="2026-09-30", descricao="X",
                             valor_centavos=-700, tipo="saida", categoria_id=None,
                             compensado=True, observacao=None)
    lid = _pagto(con, ids["cc"], "2026-10-03")
    con.execute("UPDATE lancamentos SET data_compensacao = '2026-09-30' WHERE id = ?", (lid,))
    con.commit()
    pag.confirmar_pagamento(con, ids["fatura"], lid)
    assert repo_lanc.obter(con, ids["linha"])["data_compensacao"] == "2026-09-30"
    assert (despesas_do_mes(con, 2026, 9), despesas_do_mes(con, 2026, 10)) == (30000, 0)


def test_consumo_do_mes_soma_itens_pela_data_da_compra(con: sqlite3.Connection) -> None:
    ids = _setup(con)
    lid = _pagto(con, ids["cc"])
    assert consumo_do_mes(con, 2026, 9) == 30000  # 200 + 150 - 50
    assert consumo_do_mes(con, 2026, 10) == 30000  # pagamento sem casar ainda aparece
    pag.confirmar_pagamento(con, ids["fatura"], lid)
    assert (consumo_do_mes(con, 2026, 9), consumo_do_mes(con, 2026, 10)) == (30000, 0)


def test_categorias_usam_itens_e_sem_categoria(con: sqlite3.Connection) -> None:
    ids = _setup(con)
    g = gastos_por_categoria(con, 2026, 9)
    totais = {c.nome: c.total_centavos for c in g}
    assert totais == {"Loja Inventada": 20000, SEM_CATEGORIA: 10000}
    assert sum(c.total_centavos for c in g) == consumo_do_mes(con, 2026, 9)
    assert ids["cat"] in [c.id for c in g]


# --- Regra 4: saldo total e projeção ---------------------------------------------------------

def test_projecao_abate_fatura_pendente_e_previstas(con: sqlite3.Connection) -> None:
    _setup(con, parcelado=True)
    pontos = projetar(con, date(2026, 10, 1), 3)
    assert [p.saida_pendente_centavos for p in pontos] == [30000, 20000, 20000]
    assert [p.parcelas_centavos for p in pontos] == [0, 20000, 20000]
    assert [p.saldo_final_centavos for p in pontos] == [70000, 50000, 30000]
    r = resumo_inicio(con, date(2026, 10, 1), 2026, 10, 3)
    assert r.saldo_total_centavos == 100000  # conta-cartão fora do saldo


def test_projecao_com_pagamento_nao_casado_abate_duas_vezes(con: sqlite3.Connection) -> None:
    """Esperado até casar: o pagamento já baixou o saldo e a fatura pendente ainda é abatida
    no vencimento (a tela pede para casar)."""
    ids = _setup(con)
    lid = _pagto(con, ids["cc"])
    hoje = date(2026, 10, 27)
    pontos = projetar(con, hoje, 3)
    assert [p.saldo_final_centavos for p in pontos] == [40000, 40000, 40000]
    pag.confirmar_pagamento(con, ids["fatura"], lid)
    assert [p.saldo_final_centavos for p in projetar(con, hoje, 3)] == [70000, 70000, 70000]


# --- Regra 5: contas a pagar -----------------------------------------------------------------

def test_contas_a_pagar_lista_fatura_pendente_e_some_quando_paga(con: sqlite3.Connection) -> None:
    ids = _setup(con)
    lid = _pagto(con, ids["cc"])
    painel = abrir_painel(con, date(2026, 10, 5), 3)
    linha = next(r for r in painel.a_vencer if int(r["id"]) == ids["linha"])
    assert (linha["data"], linha["valor_centavos"], linha["conta_id"]) == (
        "2026-10-10", -30000, ids["cartao"])
    pag.confirmar_pagamento(con, ids["fatura"], lid)
    painel = abrir_painel(con, date(2026, 10, 5), 3)
    todas = painel.vencidas + painel.a_vencer + painel.proximos
    assert ids["linha"] not in [int(r["id"]) for r in todas]
