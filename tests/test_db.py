import sqlite3

from app.db import conectar, migrar

TABELAS = {
    "contas", "categorias", "lancamentos", "importacoes", "regras_categoria",
    "recorrentes", "chaves_quitadas", "configuracoes",
}
CATEGORIAS = {"Moradia", "Mercado", "Transporte", "Saúde", "Lazer", "Assinaturas", "Educação", "Outros"}


def _tabelas(con: sqlite3.Connection) -> set[str]:
    return {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_migrar_cria_tabelas_e_categorias(con: sqlite3.Connection) -> None:
    assert TABELAS <= _tabelas(con)
    nomes = {r["nome"] for r in con.execute("SELECT nome FROM categorias")}
    assert nomes == CATEGORIAS
    cols = {r["name"] for r in con.execute("PRAGMA table_info(lancamentos)")}
    assert {"data_compensacao", "compensado", "estimado", "grupo_parcela_uuid",
            "parcela_atual", "parcela_total", "recorrente_id"} <= cols
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrar_idempotente(con: sqlite3.Connection) -> None:
    migrar(con)
    assert con.execute("SELECT COUNT(*) FROM categorias").fetchone()[0] == 8
    assert con.execute("PRAGMA user_version").fetchone()[0] == 1
