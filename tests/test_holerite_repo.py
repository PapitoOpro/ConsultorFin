import sqlite3

import pytest

from app.db import MIGRACOES, conectar, migrar
from app.repo import holerites as repo
from app.repo import lancamentos as lanc


def _conta(con: sqlite3.Connection) -> int:
    cur = con.execute("INSERT INTO contas (uuid, nome, tipo) VALUES ('c1', 'Conta', 'corrente')")
    return int(cur.lastrowid)


def _holerite(con: sqlite3.Connection, conta_id: int, competencia: str = "2026-09",
              tipo: str = "mensal", lancamento_id: int | None = None) -> int:
    return repo.criar(
        con, conta_id=conta_id, competencia=competencia, tipo=tipo, empresa="ACME",
        total_vencimentos=500000, total_descontos=100000, liquido=400000, arquivo_hash="h",
        arquivo_nome="a.pdf", lancamento_id=lancamento_id, criado_em="2026-10-01T00:00:00")


def _entrada(con: sqlite3.Connection, conta_id: int, chave: str, data: str, valor: int) -> int:
    cur = con.execute(
        "INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
        " chave_deduplicacao, origem, compensado) VALUES (?,?,?,?,?,'entrada',?,'importado',1)",
        (chave, conta_id, data, "SALARIO", valor, chave))
    return int(cur.lastrowid)


def test_migracao_10_a_partir_da_v9_populada() -> None:
    c = conectar(":memory:")
    for numero in range(9):
        c.execute("BEGIN")
        MIGRACOES[numero](c)
        c.execute(f"PRAGMA user_version = {numero + 1}")
        c.execute("COMMIT")
    conta = _conta(c)
    c.execute("INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
              " chave_deduplicacao, origem) VALUES ('l1', ?, '2026-01-01', 'X', 100, 'entrada',"
              " 'k1', 'manual')", (conta,))
    c.commit()
    migrar(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 10
    tabelas = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"holerites", "holerite_itens"} <= tabelas
    assert c.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 1
    assert c.execute("SELECT COUNT(*) FROM contas").fetchone()[0] == 1
    c.close()


def test_criar_e_listar_por_ano(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    _holerite(con, conta, "2025-12")
    _holerite(con, conta, "2026-09", "adiantamento")
    _holerite(con, conta, "2026-09", "mensal")
    todos = repo.listar(con)
    assert [(r["competencia"], r["tipo"]) for r in todos] == [
        ("2026-09", "adiantamento"), ("2026-09", "mensal"), ("2025-12", "mensal")]
    assert [r["competencia"] for r in repo.listar(con, 2025)] == ["2025-12"]
    achado = repo.por_competencia_tipo(con, "2026-09", "mensal")
    assert achado is not None and repo.obter(con, achado["id"])["liquido_centavos"] == 400000
    assert repo.por_competencia_tipo(con, "2024-01", "mensal") is None


def test_unique_competencia_tipo(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    _holerite(con, conta)
    with pytest.raises(sqlite3.IntegrityError):
        _holerite(con, conta)


def test_vinculo_unico_de_lancamento(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    lid = _entrada(con, conta, "e1", "2026-09-30", 400000)
    _holerite(con, conta, "2026-09", "mensal", lid)
    with pytest.raises(sqlite3.IntegrityError):
        _holerite(con, conta, "2026-10", "mensal", lid)
    _holerite(con, conta, "2026-10", "adiantamento", None)
    _holerite(con, conta, "2026-11", "adiantamento", None)


def test_itens_em_ordem_e_cascade(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    hid = _holerite(con, conta)
    for ordem, desc in ((2, "INSS"), (1, "SALARIO")):
        repo.inserir_item(con, hid, ordem=ordem, codigo=None, descricao=desc, referencia=None,
                          natureza="provento" if ordem == 1 else "desconto",
                          valor_centavos=1000, categoria_id=None)
    lista = repo.itens(con, hid)
    assert [i["descricao"] for i in lista] == ["SALARIO", "INSS"]
    repo.atualizar_categoria_item(con, lista[0]["id"], None)
    assert repo.obter_item(con, lista[1]["id"])["descricao"] == "INSS"
    con.execute("DELETE FROM holerites WHERE id = ?", (hid,))
    assert con.execute("SELECT COUNT(*) FROM holerite_itens").fetchone()[0] == 0


def test_excluir_itens(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    hid = _holerite(con, conta)
    repo.inserir_item(con, hid, ordem=1, codigo="1", descricao="A", referencia=None,
                      natureza="provento", valor_centavos=1, categoria_id=None)
    repo.excluir_itens(con, hid)
    assert repo.itens(con, hid) == []


def test_total_por_descricao(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    h25 = _holerite(con, conta, "2025-05")
    h26 = _holerite(con, conta, "2026-05")
    for hid, desc, nat, valor in (
        (h25, "Previdência Privada", "desconto", 1000),
        (h26, "PREVIDENCIA PRIVADA", "desconto", 2000),
        (h26, "Previdência Complementar", "desconto", 500),
        (h26, "Previdência Privada", "provento", 9999),
        (h26, "INSS", "desconto", 7000),
    ):
        repo.inserir_item(con, hid, ordem=1, codigo=None, descricao=desc, referencia=None,
                          natureza=nat, valor_centavos=valor, categoria_id=None)
    assert repo.total_por_descricao(con, 2026, "previdencia privada") == 2000
    assert repo.total_por_descricao(con, 2026, "previdencia") == 2500
    assert repo.total_por_descricao(con, 2025, "previdencia") == 1000
    assert repo.total_por_descricao(con, 2024, "previdencia") == 0


def test_entradas_candidatas_ignora_vinculadas_e_fora_da_janela(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    livre = _entrada(con, conta, "e1", "2026-09-30", 400000)
    vinc = _entrada(con, conta, "e2", "2026-09-30", 400000)
    _entrada(con, conta, "e3", "2026-08-01", 400000)
    _entrada(con, conta, "e4", "2026-09-30", 399999)
    _holerite(con, conta, "2026-09", "mensal", vinc)
    r = lanc.entradas_candidatas(con, conta, 400000, "2026-09-25", "2026-10-05")
    assert [x["id"] for x in r] == [livre]


def test_inserir_holerite_previsto_e_pendente_estimado(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    lid = lanc.inserir_holerite_previsto(con, conta_id=conta, data="2026-10-05",
                                         descricao="Holerite 2026-10", valor_centavos=400000,
                                         categoria_id=None)
    row = lanc.obter(con, lid)
    assert (row["tipo"], row["origem"], row["compensado"], row["estimado"]) == (
        "entrada", "holerite", 0, 1)
    assert row["chave_deduplicacao"] == f"holerite:{row['uuid']}"
