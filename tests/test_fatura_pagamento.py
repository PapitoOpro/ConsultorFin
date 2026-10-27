"""Casamento de pagamento bancário x fatura (dados inventados)."""
import sqlite3
from datetime import date

import pytest

from app.dominio import faturas as dom
from app.dominio import faturas_pagamento as pag
from app.importacao.fatura.modelo import FaturaLida, ItemFatura
from app.repo import contas as repo_contas
from app.repo import faturas as repo_faturas
from app.repo import lancamentos as repo_lanc

HOJE = date(2026, 10, 27)


def _fatura_lida(venc: str = "2026-10-10", valor: int = -30000) -> FaturaLida:
    item = ItemFatura(data="2026-09-15", descricao="LOJA INVENTADA", valor_centavos=valor,
                      tipo="compra", parcela_atual=None, parcela_total=None,
                      final_cartao="9999", categoria_banco=None)
    f = FaturaLida(emissor="Banco Teste", bandeira="Visa", produto="Gold", final_cartao="9999",
                   data_vencimento=venc, data_fechamento=None, total_impresso_centavos=0,
                   ajustes_resumo_centavos=0, itens=[item])
    f.total_impresso_centavos = f.calculado_centavos
    return f


def _setup(con: sqlite3.Connection) -> tuple[int, int, int]:
    cc = repo_contas.criar_conta(con, "Corrente Teste", "corrente")
    cartao = repo_contas.criar_conta(con, "Cartao Teste", "cartao", final_numero="9999")
    r = dom.importar_fatura(con, _fatura_lida(), "h1", "f.pdf", cartao, HOJE, [None])
    return cc, cartao, r.fatura_id


def _pagto(con: sqlite3.Connection, conta: int, data: str, valor: int = -30000,
           tipo: str = "pagamento_fatura") -> int:
    lid = repo_lanc.inserir_manual(con, conta_id=conta, data=data, descricao="PAG FATURA X",
                                   valor_centavos=valor, tipo=tipo, categoria_id=None,
                                   compensado=True, observacao=None)
    con.commit()
    return lid


def test_propor_so_valor_exato_e_janela_7_dias(con: sqlite3.Connection) -> None:
    cc, cartao, fid = _setup(con)
    perto = _pagto(con, cc, "2026-10-11")
    longe = _pagto(con, cc, "2026-10-03")  # -7 dias: ainda na janela
    _pagto(con, cc, "2026-10-18")  # +8 dias: fora
    _pagto(con, cc, "2026-10-10", valor=-29999)  # valor diferente
    _pagto(con, cc, "2026-10-10", tipo="saida")  # não é pagamento de fatura
    _pagto(con, cartao, "2026-10-10")  # conta-cartão
    props = pag.propor_pagamentos(con, fid)
    assert [p.lancamento_id for p in props] == [perto, longe]
    assert props[0].fatura_id == fid and props[0].data == "2026-10-11"
    assert props[0].valor_centavos == -30000
    assert props[0].pontuacao > props[1].pontuacao


def test_confirmar_marca_paga_e_nao_conta_duas_vezes(con: sqlite3.Connection) -> None:
    cc, _, fid = _setup(con)
    lid = _pagto(con, cc, "2026-10-09")
    pag.confirmar_pagamento(con, fid, lid)
    fat = repo_faturas.obter(con, fid)
    assert fat["pagamento_lancamento_id"] == lid
    linha = repo_lanc.obter(con, int(fat["lancamento_id"]))
    assert (linha["compensado"], linha["data_compensacao"]) == (1, "2026-10-09")
    assert pag.propor_pagamentos(con, fid) == []
    with pytest.raises(pag.PagamentoInvalido, match="já está paga"):
        pag.confirmar_pagamento(con, fid, lid)


def test_desfazer_volta_a_pendente_e_pagamento_conta_de_novo(con: sqlite3.Connection) -> None:
    cc, _, fid = _setup(con)
    lid = _pagto(con, cc, "2026-10-09")
    pag.confirmar_pagamento(con, fid, lid)
    pag.desfazer_pagamento(con, fid)
    fat = repo_faturas.obter(con, fid)
    assert fat["pagamento_lancamento_id"] is None
    linha = repo_lanc.obter(con, int(fat["lancamento_id"]))
    assert (linha["compensado"], linha["data_compensacao"]) == (0, None)
    assert [p.lancamento_id for p in pag.propor_pagamentos(con, fid)] == [lid]
    with pytest.raises(pag.PagamentoInvalido, match="não está paga"):
        pag.desfazer_pagamento(con, fid)


def test_pagamento_de_outra_fatura_rejeitado(con: sqlite3.Connection) -> None:
    cc, cartao, fid = _setup(con)
    r2 = dom.importar_fatura(con, _fatura_lida("2026-11-10"), "h2", "g.pdf", cartao, HOJE, [None])
    lid = _pagto(con, cc, "2026-10-10")
    pag.confirmar_pagamento(con, fid, lid)
    with pytest.raises(pag.PagamentoInvalido, match="outra fatura"):
        pag.confirmar_pagamento(con, r2.fatura_id, lid)
    # fora da janela / valor diferente / tipo errado
    longe = _pagto(con, cc, "2026-10-20")
    with pytest.raises(pag.PagamentoInvalido, match="vencimento"):
        pag.confirmar_pagamento(con, r2.fatura_id, longe)
    errado = _pagto(con, cc, "2026-11-10", valor=-1)
    with pytest.raises(pag.PagamentoInvalido, match="valor"):
        pag.confirmar_pagamento(con, r2.fatura_id, errado)
    saida = _pagto(con, cc, "2026-11-10", tipo="saida")
    with pytest.raises(pag.PagamentoInvalido, match="pagamento de fatura"):
        pag.confirmar_pagamento(con, r2.fatura_id, saida)
    with pytest.raises(pag.PagamentoInvalido, match="não encontrad"):
        pag.confirmar_pagamento(con, r2.fatura_id + 99, saida)
    with pytest.raises(pag.PagamentoInvalido, match="não encontrad"):
        pag.confirmar_pagamento(con, r2.fatura_id, saida + 99)
    assert repo_faturas.obter(con, r2.fatura_id)["pagamento_lancamento_id"] is None
