import sqlite3

import pytest

from app.repo import faturas as repo


def _conta(con: sqlite3.Connection) -> int:
    cur = con.execute("INSERT INTO contas (uuid, nome, tipo) VALUES ('c-u', 'Cartao', 'cartao')")
    return int(cur.lastrowid)


def _fatura(con: sqlite3.Connection, conta: int, competencia: str = "2026-10") -> int:
    return repo.criar(
        con, conta_id=conta, competencia=competencia, data_vencimento=competencia + "-10",
        data_fechamento=competencia + "-03", emissor="X", bandeira="Y", produto="Z",
        total_impresso_centavos=10000, total_calculado_centavos=10000,
        ajustes_resumo_centavos=0, arquivo_hash="h", arquivo_nome="a.pdf",
        criado_em="2026-10-27T00:00:00")


def _item(con: sqlite3.Connection, fid: int, ordem: int) -> int:
    return repo.inserir_item(
        con, fid, ordem=ordem, data="2026-10-01", descricao=f"item {ordem}",
        valor_centavos=-500, tipo="compra", categoria_id=None, categoria_banco=None,
        parcela_atual=None, parcela_total=None, grupo_parcela_uuid=None, final_cartao="0000")


def test_unicidade_conta_competencia(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    _fatura(con, conta)
    with pytest.raises(sqlite3.IntegrityError):
        _fatura(con, conta)
    assert repo.por_conta_competencia(con, conta, "2026-10") is not None
    assert repo.por_conta_competencia(con, conta, "2026-11") is None


def test_excluir_itens_e_cascata(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    fid = _fatura(con, conta)
    _item(con, fid, 2)
    _item(con, fid, 1)
    assert [r["ordem"] for r in repo.itens(con, fid)] == [1, 2]
    repo.excluir_itens(con, fid)
    assert repo.itens(con, fid) == []
    _item(con, fid, 1)
    con.execute("DELETE FROM faturas WHERE id = ?", (fid,))
    assert con.execute("SELECT COUNT(*) FROM fatura_itens").fetchone()[0] == 0


def test_atualizar_categoria_item(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    fid = _fatura(con, conta)
    a, b, c = _item(con, fid, 1), _item(con, fid, 2), _item(con, fid, 3)
    cat = con.execute("SELECT id FROM categorias LIMIT 1").fetchone()[0]
    assert repo.atualizar_categoria_item(con, [a, b], cat) == 2
    assert repo.obter_item(con, a)["categoria_id"] == cat
    assert repo.obter_item(con, c)["categoria_id"] is None
    assert repo.atualizar_categoria_item(con, [a], None) == 1
    assert repo.atualizar_categoria_item(con, [], cat) == 0


def test_lancamento_pagamento_totais_listar(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    fid = _fatura(con, conta)
    con.execute(
        "INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
        " chave_deduplicacao, origem) VALUES ('l1', ?, '2026-10-10', 'F', -10000, 'fatura',"
        " 'k1', 'fatura')", (conta,))
    lid = int(con.execute("SELECT id FROM lancamentos").fetchone()[0])
    repo.definir_lancamento(con, fid, lid)
    assert repo.por_lancamento(con, lid)["id"] == fid
    repo.marcar_pagamento(con, fid, lid)
    assert repo.obter(con, fid)["pagamento_lancamento_id"] == lid
    repo.marcar_pagamento(con, fid, None)
    assert repo.obter(con, fid)["pagamento_lancamento_id"] is None
    repo.atualizar_totais(con, fid, 1, 2, 3, "h2", "b.pdf")
    r = repo.obter(con, fid)
    assert (r["total_impresso_centavos"], r["total_calculado_centavos"],
            r["ajustes_resumo_centavos"], r["arquivo_hash"], r["arquivo_nome"]) == (
        1, 2, 3, "h2", "b.pdf")
    assert len(repo.listar(con)) == 1 and len(repo.listar(con, conta)) == 1
    assert repo.listar(con, conta + 1) == []


def test_lancamento_da_fatura_e_previstas(con: sqlite3.Connection) -> None:
    from app.repo import lancamentos as repo_l

    conta = _conta(con)
    lid = repo_l.inserir_fatura(con, conta_id=conta, data="2026-10-10", descricao="Fatura X",
                                valor_centavos=-900)
    r = repo_l.obter(con, lid)
    assert (r["tipo"], r["origem"], r["compensado"], r["data_compensacao"], r["categoria_id"]) == (
        "fatura", "fatura", 0, None, None)
    assert str(r["chave_deduplicacao"]).startswith("fatura:")
    repo_l.atualizar_fatura(con, lid, data="2026-10-11", valor_centavos=-1000)
    assert (repo_l.obter(con, lid)["data"], repo_l.obter(con, lid)["valor_centavos"]) == (
        "2026-10-11", -1000)
    p = repo_l.inserir_prevista(con, conta_id=conta, data="2026-11-10", descricao="TV",
                                valor_centavos=-500, tipo="saida", categoria_id=None,
                                grupo_parcela_uuid="g1", parcela_atual=2, parcela_total=3)
    prev = repo_l.obter(con, p)
    assert (prev["origem"], prev["estimado"], prev["compensado"]) == ("fatura_prevista", 1, 0)
    assert [int(x["id"]) for x in repo_l.previstas_pendentes_da_conta(con, conta)] == [p]
    assert repo_l.previstas_pendentes_da_conta(con, conta + 1) == []
    assert repo_l.parcela_existe_no_grupo(con, "g1", 2)
    assert not repo_l.parcela_existe_no_grupo(con, "g1", 3)
    assert repo_l.previstas_orfas(con) == []
    _fatura(con, conta, "2026-11")  # vencimento 2026-11-10 >= data da prevista
    assert [int(x["id"]) for x in repo_l.previstas_orfas(con)] == [p]
    assert repo_l.excluir_previstas_dos_grupos(con, []) == 0
    assert repo_l.excluir_previstas_dos_grupos(con, ["g1"]) == 1


def test_contar_itens_e_vencimento(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    fid = _fatura(con, conta)
    a = _item(con, fid, 1)
    assert repo.contar_itens(con, [a, a + 99]) == 1
    assert repo.contar_itens(con, []) == 0
    repo.atualizar_vencimento(con, fid, "2026-10-12", None)
    f = repo.obter(con, fid)
    assert (f["data_vencimento"], f["data_fechamento"]) == ("2026-10-12", None)


def test_categoria_por_nome_e_regras_ignoram_fatura(con: sqlite3.Connection) -> None:
    from app.dominio.categorias import aplicar_regras, criar_categoria, criar_regra
    from app.repo import categorias as repo_c
    from app.repo import lancamentos as repo_l

    cat = criar_categoria(con, "Ótica Inventada")
    assert repo_c.id_por_nome(con, "  otica   INVENTADA ") == cat
    assert repo_c.id_por_nome(con, "nada disso") is None
    conta = _conta(con)
    lid = repo_l.inserir_fatura(con, conta_id=conta, data="2026-10-10",
                                descricao="Fatura Otica", valor_centavos=-1)
    con.commit()
    criar_regra(con, "OTICA", cat)
    assert aplicar_regras(con) == 0
    assert repo_l.obter(con, lid)["categoria_id"] is None


def test_parcela_existe_olha_itens_de_outras_faturas(con: sqlite3.Connection) -> None:
    from app.repo import lancamentos as repo_l

    conta = _conta(con)
    fid = _fatura(con, conta)
    repo.inserir_item(con, fid, ordem=1, data="2026-10-01", descricao="TV", valor_centavos=-1,
                      tipo="compra", categoria_id=None, categoria_banco=None, parcela_atual=4,
                      parcela_total=5, grupo_parcela_uuid="g9", final_cartao=None)
    assert repo_l.parcela_existe_no_grupo(con, "g9", 4)
    assert not repo_l.parcela_existe_no_grupo(con, "g9", 4, excluir_fatura_id=fid)
    assert not repo_l.parcela_existe_no_grupo(con, "g9", 5)
    assert [r["grupo_parcela_uuid"] for r in repo.itens_parcelados_da_conta(con, conta, None)
            ] == ["g9"]
    assert repo.itens_parcelados_da_conta(con, conta, fid) == []


def test_criar_conta_sem_commit(con: sqlite3.Connection) -> None:
    from app.repo import contas as repo_contas

    con.execute("BEGIN")
    repo_contas.criar_conta(con, "X", "cartao", commit=False)
    assert con.in_transaction
    con.execute("ROLLBACK")
    assert repo_contas.listar(con) == []
