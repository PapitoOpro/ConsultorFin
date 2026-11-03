"""Titular do holerite: migração v12 sem perda de dados, unicidade por titular e telas."""
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import MIGRACOES, conectar, migrar
from app.main import create_app
from app.repo import holerites as repo
from app.repo import titulares


def _conta(con: sqlite3.Connection) -> int:
    return int(con.execute("INSERT INTO contas (uuid, nome, tipo) VALUES ('c1', 'Conta', 'corrente')").lastrowid)


def _holerite(con: sqlite3.Connection, conta: int, titular: int, competencia: str = "2026-09",
              liquido: int = 400000) -> int:
    return repo.criar(con, conta_id=conta, competencia=competencia, tipo="mensal", empresa="ACME",
                      total_vencimentos=liquido + 100000, total_descontos=100000, liquido=liquido,
                      arquivo_hash=f"h{titular}{competencia}", arquivo_nome=None, lancamento_id=None,
                      criado_em="2026-10-01T00:00:00", titular_id=titular)


def test_migracao_12_preserva_holerites_e_itens() -> None:
    c = conectar(":memory:")
    for numero in range(11):
        c.execute("BEGIN")
        MIGRACOES[numero](c)
        c.execute(f"PRAGMA user_version = {numero + 1}")
        c.execute("COMMIT")
    conta = _conta(c)
    c.execute("INSERT INTO holerites (uuid, conta_id, competencia, tipo, empresa, total_vencimentos_centavos,"
              " total_descontos_centavos, liquido_centavos, arquivo_hash, criado_em)"
              " VALUES ('u1', ?, '2026-09', 'mensal', 'ACME', 500000, 100000, 400000, 'h', '2026-10-01')", (conta,))
    c.execute("INSERT INTO holerite_itens (uuid, holerite_id, ordem, descricao, natureza, valor_centavos)"
              " VALUES ('i1', 1, 1, 'SALARIO', 'provento', 500000), ('i2', 1, 2, 'IMPOSTO', 'desconto', 100000)")
    c.commit()
    migrar(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == len(MIGRACOES)
    h = c.execute("SELECT id, titular_id, liquido_centavos FROM holerites").fetchall()
    assert [(r["id"], r["titular_id"], r["liquido_centavos"]) for r in h] == [(1, 1, 400000)]
    assert c.execute("SELECT COUNT(*) FROM holerite_itens WHERE holerite_id = 1").fetchone()[0] == 2
    assert c.execute("SELECT nome FROM titulares WHERE id = 1").fetchone()[0] == "Eu"
    idx = {r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"ux_holerites_lancamento", "idx_holerite_itens_holerite"} <= idx
    c.close()


def test_mesma_competencia_para_titulares_diferentes_mas_nao_para_o_mesmo(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    outro = titulares.criar(con, "Outra pessoa")
    _holerite(con, conta, 1)
    _holerite(con, conta, outro)  # mesma competência e tipo, outro titular: permitido
    with pytest.raises(sqlite3.IntegrityError):
        _holerite(con, conta, 1)


def test_listar_junta_todos_os_titulares_e_traz_o_nome(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    outro = titulares.criar(con, "Outra pessoa")
    _holerite(con, conta, 1, liquido=100000)
    _holerite(con, conta, outro, liquido=250000)
    assert sorted(r["titular_nome"] for r in repo.listar(con, 2026)) == ["Eu", "Outra pessoa"]
    assert titulares.ultimo_usado(con) == outro


@pytest.fixture
def cliente(tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    db = tmp_path / "t.db"
    with TestClient(create_app(db)) as c:
        yield c, db


def test_tela_holerites_e_lista_unica_com_selo_do_titular(cliente: tuple[TestClient, Path]) -> None:
    c, db = cliente
    antes = c.get("/holerites").text
    assert "Titular dos holerites" not in antes and "selo neutro\">Eu" not in antes
    con = conectar(db)
    conta = _conta(con)
    outro = titulares.criar(con, "Outra pessoa")
    _holerite(con, conta, 1, liquido=111100)
    _holerite(con, conta, outro, liquido=222200)
    con.commit()
    con.close()
    html = c.get("/holerites?ano=2026").text
    assert "R$ 1.111,00" in html and "R$ 2.222,00" in html  # tudo junto
    assert "Titular dos holerites" not in html and "líquido em 2026" not in html  # sem filtro nem cartões
    assert ">Eu</span>" in html and ">Outra pessoa</span>" in html  # só o selo distingue as linhas


def test_formulario_de_importacao_pede_titular_com_mais_de_um(cliente: tuple[TestClient, Path]) -> None:
    c, db = cliente
    assert 'name="titular_id" value="1"' in c.get("/importar").text  # um só: campo oculto
    con = conectar(db)
    titulares.criar(con, "Outra pessoa")
    con.commit()
    con.close()
    html = c.get("/importar").text
    assert 'id="holerite-titular"' in html and "Outra pessoa" in html
