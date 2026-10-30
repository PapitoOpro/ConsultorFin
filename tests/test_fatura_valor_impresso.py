"""Linha da fatura vale o que o banco cobra (total impresso); pagamento parcial vale o pago."""
import sqlite3
from datetime import date

from app.db import MIGRACOES, conectar, migrar
from app.dominio import faturas as dom
from app.dominio import faturas_pagamento as pag
from app.importacao.fatura.modelo import FaturaLida, ItemFatura
from app.repo import contas as repo_contas
from app.repo import faturas as repo_faturas
from app.repo import lancamentos as repo_lanc

HOJE = date(2026, 10, 27)


def _fatura(venc: str = "2026-11-01", itens: int = -40000, ajustes: int = 8000) -> FaturaLida:
    """Itens somam 400,00; saldo anterior financiado de 80,00 => impresso 480,00."""
    item = ItemFatura(data="2026-10-05", descricao="LOJA INVENTADA", valor_centavos=itens,
                      tipo="compra", parcela_atual=None, parcela_total=None,
                      final_cartao="9999", categoria_banco=None)
    f = FaturaLida(emissor="Banco Teste", bandeira="Visa", produto="Gold", final_cartao="9999",
                   data_vencimento=venc, data_fechamento=None, total_impresso_centavos=0,
                   ajustes_resumo_centavos=ajustes, itens=[item])
    f.total_impresso_centavos = f.calculado_centavos + ajustes
    return f


def _setup(con: sqlite3.Connection, **kw: int) -> tuple[int, int]:
    cc = repo_contas.criar_conta(con, "Corrente Teste", "corrente")
    cartao = repo_contas.criar_conta(con, "Cartao Teste", "cartao", final_numero="9999")
    r = dom.importar_fatura(con, _fatura(**kw), "h1", "f.pdf", cartao, HOJE, [None])
    return cc, r.fatura_id


def _valor_linha(con: sqlite3.Connection, fid: int) -> int:
    fat = repo_faturas.obter(con, fid)
    return int(repo_lanc.obter(con, int(fat["lancamento_id"]))["valor_centavos"])


def _pagto(con: sqlite3.Connection, conta: int, data: str, valor: int) -> int:
    lid = repo_lanc.inserir_manual(con, conta_id=conta, data=data, descricao="PAG FATURA X",
                                   valor_centavos=valor, tipo="pagamento_fatura",
                                   categoria_id=None, compensado=True, observacao=None)
    con.commit()
    return lid


def test_linha_da_fatura_vale_o_total_impresso(con: sqlite3.Connection) -> None:
    _, fid = _setup(con)
    assert _valor_linha(con, fid) == -48000  # e não -40000 (soma dos itens)


def test_pagamento_parcial_deixa_a_linha_com_o_valor_pago(con: sqlite3.Connection) -> None:
    cc, fid = _setup(con)
    lid = _pagto(con, cc, "2026-11-01", -30000)
    pag.confirmar_pagamento(con, fid, lid)
    assert _valor_linha(con, fid) == -30000
    pag.desfazer_pagamento(con, fid)
    assert _valor_linha(con, fid) == -48000


def test_pagamento_exato_mantem_o_total_impresso(con: sqlite3.Connection) -> None:
    cc, fid = _setup(con)
    lid = _pagto(con, cc, "2026-11-01", -48000)
    pag.confirmar_pagamento(con, fid, lid)
    assert _valor_linha(con, fid) == -48000


def test_reimportar_fatura_paga_parcialmente_mantem_o_valor_pago(con: sqlite3.Connection) -> None:
    cc, fid = _setup(con)
    pag.confirmar_pagamento(con, fid, _pagto(con, cc, "2026-11-01", -30000))
    cartao = int(repo_faturas.obter(con, fid)["conta_id"])
    novo = _fatura()
    novo.itens.append(ItemFatura("2026-10-06", "OUTRA LOJA", -1000, "compra", None, None,
                                 "9999", None))
    novo.total_impresso_centavos = novo.calculado_centavos + novo.ajustes_resumo_centavos
    dom.importar_fatura(con, novo, "h2", "f2.pdf", cartao, HOJE, [None, None], substituir=True)
    assert _valor_linha(con, fid) == -30000
    assert int(repo_faturas.obter(con, fid)["total_impresso_centavos"]) == 49000


def test_reimportar_fatura_pendente_usa_o_novo_total_impresso(con: sqlite3.Connection) -> None:
    _, fid = _setup(con)
    cartao = int(repo_faturas.obter(con, fid)["conta_id"])
    novo = _fatura(ajustes=20000)
    dom.importar_fatura(con, novo, "h2", "f2.pdf", cartao, HOJE, [None], substituir=True)
    assert _valor_linha(con, fid) == -60000


def test_migracao_11_corrige_faturas_existentes() -> None:
    c = conectar(":memory:")
    for numero in range(10):
        c.execute("BEGIN")
        MIGRACOES[numero](c)
        c.execute(f"PRAGMA user_version = {numero + 1}")
        c.execute("COMMIT")
    cc = repo_contas.criar_conta(c, "Corrente Teste", "corrente")
    cartao = repo_contas.criar_conta(c, "Cartao Teste", "cartao", final_numero="9999")
    # Simula o banco antigo: linha da fatura com a soma dos itens (-400,00), impresso 480,00.
    f = _fatura()
    fid = repo_faturas.criar(
        c, conta_id=cartao, competencia="2026-11", data_vencimento="2026-11-01",
        data_fechamento=None, emissor="X", bandeira=None, produto=None,
        total_impresso_centavos=48000, total_calculado_centavos=40000,
        ajustes_resumo_centavos=8000, arquivo_hash="h", arquivo_nome=None, criado_em="2026-10-27")
    lid = repo_lanc.inserir_fatura(c, conta_id=cartao, data="2026-11-01", descricao="Fatura X",
                                   valor_centavos=-40000)
    repo_faturas.definir_lancamento(c, fid, lid)
    # Fatura paga parcialmente: linha -400,00 mas o banco pagou 300,00.
    fid2 = repo_faturas.criar(
        c, conta_id=cartao, competencia="2026-10", data_vencimento="2026-10-01",
        data_fechamento=None, emissor="X", bandeira=None, produto=None,
        total_impresso_centavos=40000, total_calculado_centavos=40000,
        ajustes_resumo_centavos=0, arquivo_hash="h2", arquivo_nome=None, criado_em="2026-10-01")
    lid2 = repo_lanc.inserir_fatura(c, conta_id=cartao, data="2026-10-01", descricao="Fatura Y",
                                    valor_centavos=-40000)
    repo_faturas.definir_lancamento(c, fid2, lid2)
    pg = repo_lanc.inserir_manual(c, conta_id=cc, data="2026-10-01", descricao="PAG",
                                  valor_centavos=-30000, tipo="pagamento_fatura",
                                  categoria_id=None, compensado=True, observacao=None)
    repo_faturas.marcar_pagamento(c, fid2, pg)
    repo_lanc.definir_compensacao_fatura(c, lid2, "2026-10-01")
    c.commit()
    del f
    migrar(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 11
    assert repo_lanc.obter(c, lid)["valor_centavos"] == -48000
    assert repo_lanc.obter(c, lid2)["valor_centavos"] == -30000
    c.close()
