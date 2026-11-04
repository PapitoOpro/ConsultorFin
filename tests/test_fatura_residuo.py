"""Resíduo: saldo da fatura anterior carregado na seguinte (pagamento parcial)."""
import sqlite3
from datetime import date

import pytest

from app.consultor import consultas
from app.dominio import faturas as dom
from app.dominio import faturas_pagamento as pag
from app.dominio.faturas import residuo_da_fatura
from app.dominio.painel import gastos_por_categoria
from app.importacao.fatura.modelo import FaturaLida, ItemFatura
from app.repo import contas as repo_contas
from app.repo import faturas as repo
from app.repo import lancamentos as repo_lanc

HOJE = date(2026, 11, 5)


def _fatura(venc: str, itens: list[int], ajustes: int, total: int) -> FaturaLida:
    its = [ItemFatura(data=venc[:7] + "-01", descricao=f"LOJA {n}", valor_centavos=-v, tipo="compra",
                      parcela_atual=None, parcela_total=None, final_cartao="9999",
                      categoria_banco=None) for n, v in enumerate(itens)]
    return FaturaLida(emissor="Banco Teste", bandeira="Visa", produto=None, final_cartao="9999",
                      data_vencimento=venc, data_fechamento=None, total_impresso_centavos=total,
                      ajustes_resumo_centavos=ajustes, itens=its)


def _cenario(con: sqlite3.Connection, pago: int | None, ajustes: int = 10000) -> tuple[int, int]:
    """Fatura 1 (total 300,00) paga parcialmente com `pago` (None: sem casar); fatura 2 com itens
    de 400,00 + `ajustes`. Devolve (id da fatura 1, id da fatura 2)."""
    cc = repo_contas.criar_conta(con, "Corrente Teste", "corrente", saldo_inicial_centavos=100000,
                                 data_saldo_inicial="2026-08-01")
    cartao = repo_contas.criar_conta(con, "Cartao Teste", "cartao", final_numero="9999")
    r1 = dom.importar_fatura(con, _fatura("2026-09-10", [30000], 0, 30000), "h1", "a.pdf",
                             cartao, HOJE, [None])
    r2 = dom.importar_fatura(con, _fatura("2026-10-10", [40000], ajustes, 40000 + ajustes), "h2",
                             "b.pdf", cartao, HOJE, [None])
    if pago is not None:
        lid = repo_lanc.inserir_manual(con, conta_id=cc, data="2026-09-10", descricao="PAG FATURA",
                                       valor_centavos=-pago, tipo="pagamento_fatura",
                                       categoria_id=None, compensado=True, observacao=None)
        con.commit()
        pag.confirmar_pagamento(con, r1.fatura_id, lid)
    return r1.fatura_id, r2.fatura_id


def _residuo(con: sqlite3.Connection, fid: int):  # type: ignore[no-untyped-def]
    return residuo_da_fatura(con, repo.obter(con, fid))


def test_pagamento_parcial_confirma_residuo_e_nao_conta_como_consumo(con: sqlite3.Connection) -> None:
    f1, f2 = _cenario(con, pago=20000)
    r = _residuo(con, f2)
    assert (r.centavos, r.esperado_centavos, r.situacao) == (10000, 10000, "confirmado")
    assert r.origem_competencia == "2026-09"
    # itens (400,00) + resíduo (100,00) = total impresso (500,00)
    fat = repo.obter(con, f2)
    assert fat["total_calculado_centavos"] + r.centavos == fat["total_impresso_centavos"]
    # resíduo fora das categorias: só os itens da fatura 2 (ainda não paga => nada em outubro)
    assert sum(c.total_centavos for c in gastos_por_categoria(con, 2026, 10)) == 0


def test_fatura_paga_por_inteiro_nao_tem_residuo(con: sqlite3.Connection) -> None:
    _, f2 = _cenario(con, pago=30000, ajustes=0)
    assert _residuo(con, f2) is None


def test_residuo_diferente_do_esperado_diverge(con: sqlite3.Connection) -> None:
    _, f2 = _cenario(con, pago=20000, ajustes=7000)
    r = _residuo(con, f2)
    assert (r.situacao, r.esperado_centavos) == ("diverge", 10000)
    assert r.origem_competencia is None


def test_pagamento_nao_casado_nao_presume_residuo(con: sqlite3.Connection) -> None:
    _, f2 = _cenario(con, pago=None)
    r = _residuo(con, f2)
    assert (r.situacao, r.esperado_centavos, r.centavos) == ("sem_origem", None, 10000)


def test_reimportar_mesmo_arquivo_nao_duplica_residuo(con: sqlite3.Connection) -> None:
    _, f2 = _cenario(con, pago=20000)
    cartao = int(repo.obter(con, f2)["conta_id"])
    with pytest.raises(dom.FaturaJaImportada):
        dom.importar_fatura(con, _fatura("2026-10-10", [40000], 10000, 50000), "h2", "b.pdf",
                            cartao, HOJE, [None])
    assert len(repo.listar(con, cartao)) == 2
    assert _residuo(con, f2).centavos == 10000


def test_consultor_expoe_residuo_e_a_origem(con: sqlite3.Connection) -> None:
    f1, f2 = _cenario(con, pago=20000)
    ver = consultas.fatura_ver(con, HOJE, f2)["fatura"]["residuo"]
    assert ver["valor_centavos"] == 10000 and ver["origem_competencia"] == "2026-09"
    assert consultas.fatura_ver(con, HOJE, f1)["fatura"]["residuo"] is None
