import sqlite3

import pytest

from app import db
from app.db import MIGRACOES, conectar, migrar

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
    assert con.execute("PRAGMA user_version").fetchone()[0] == len(MIGRACOES)


def _banco_v1() -> sqlite3.Connection:
    c = conectar(":memory:")
    c.execute("BEGIN")
    MIGRACOES[0](c)
    c.execute("PRAGMA user_version = 1")
    c.execute("COMMIT")
    return c


def test_migracao_incremental_v1_aplica_so_v2() -> None:
    c = _banco_v1()
    assert "recorrentes_pulados" not in _tabelas(c)
    c.execute("INSERT INTO categorias (uuid, nome) VALUES ('x', 'Extra')")
    c.commit()
    migrar(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == 2
    assert "recorrentes_pulados" in _tabelas(c)
    # v1 não foi reaplicada: categoria extra preservada, iniciais não duplicadas
    assert c.execute("SELECT COUNT(*) FROM categorias").fetchone()[0] == 9
    migrar(c)  # no-op
    assert c.execute("PRAGMA user_version").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(*) FROM categorias").fetchone()[0] == 9
    c.close()


def test_migracao_com_falha_faz_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    def falha(con: sqlite3.Connection) -> None:
        con.execute("CREATE TABLE tmp_falha (id INTEGER)")
        raise RuntimeError("boom")

    c = conectar(":memory:")
    migrar(c)
    versao = c.execute("PRAGMA user_version").fetchone()[0]
    monkeypatch.setattr(db, "MIGRACOES", [*MIGRACOES, falha])
    with pytest.raises(RuntimeError):
        migrar(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == versao
    assert "tmp_falha" not in _tabelas(c)
    assert not c.in_transaction
    c.close()
