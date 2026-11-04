"""SQL somente leitura dos relatórios: fronteira de segurança."""
import sqlite3
from pathlib import Path

import pytest

from app.relatorio.consulta import Resultado, executar_select
from app.relatorio.espec import ErroRelatorio
from tests._consultor_base import criar_banco


@pytest.fixture()
def banco(tmp_path: Path) -> Path:
    caminho = criar_banco(tmp_path / "t.db", com_extrato=True)
    con = sqlite3.connect(caminho)
    con.execute(
        "INSERT INTO senhas_pdf (emissor, cartao_final, nonce, cifra, criado_em)"
        " VALUES ('x', '1234', X'00', X'DEADBEEF', '2026-01-01')"
    )
    con.commit()
    con.close()
    return caminho


def _contar(caminho: Path, tabela: str) -> int:
    con = sqlite3.connect(caminho)
    try:
        return int(con.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0])
    finally:
        con.close()


def test_select_simples_devolve_colunas_e_linhas(banco: Path) -> None:
    r = executar_select(banco, "SELECT COUNT(*) AS n FROM lancamentos")
    assert isinstance(r, Resultado)
    assert r.colunas == ("n",)
    assert r.linhas[0][0] == _contar(banco, "lancamentos") > 0
    assert r.truncado is False


def test_limite_marca_truncado(banco: Path) -> None:
    r = executar_select(banco, "SELECT id FROM lancamentos", limite=3)
    assert len(r.linhas) == 3
    assert r.truncado is True


HOSTIS = [
    "SELECT 1; SELECT 2",
    "DELETE FROM lancamentos",
    "PRAGMA user_version",
    "ATTACH DATABASE ':memory:' AS x",
    "SELECT * FROM senhas_pdf",
    "SELECT (SELECT COUNT(*) FROM senhas_pdf) AS n",
    "SELECT * FROM lancamentos JOIN senhas_pdf",
    "SELECT cifra FROM SENHAS_PDF",
    "SELECT load_extension('x')",
    "INSERT INTO lancamentos (id) VALUES (999999)",
    "DROP TABLE lancamentos",
    "SELECT 1; DELETE FROM lancamentos",
    "",
]


@pytest.mark.parametrize("sql", HOSTIS)
def test_recusa_hostil(banco: Path, sql: str) -> None:
    antes = _contar(banco, "lancamentos")
    with pytest.raises(ErroRelatorio) as exc:
        executar_select(banco, sql)
    msg = str(exc.value)
    assert msg.startswith("Consulta recusada")
    assert sql.strip() == "" or sql not in msg
    assert _contar(banco, "lancamentos") == antes


def test_timeout_em_consulta_infinita(banco: Path) -> None:
    sql = ("WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r)"
           " SELECT COUNT(*) FROM r")
    with pytest.raises(ErroRelatorio) as exc:
        executar_select(banco, sql, timeout_s=0.2)
    assert str(exc.value).startswith("Consulta recusada")


def test_banco_intacto_apos_tentativas(banco: Path) -> None:
    antes = {t: _contar(banco, t) for t in ("lancamentos", "senhas_pdf")}
    for sql in HOSTIS:
        with pytest.raises(ErroRelatorio):
            executar_select(banco, sql)
    assert {t: _contar(banco, t) for t in antes} == antes
    assert executar_select(banco, "SELECT COUNT(*) FROM lancamentos").linhas[0][0] == antes["lancamentos"]
