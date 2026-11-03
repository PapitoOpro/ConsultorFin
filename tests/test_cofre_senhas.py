"""Cofre de senhas de PDF: cifra AES-GCM por cartão (dados inventados)."""
import logging
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from app import config
from app.db import conectar, migrar
from app.dominio import cofre_senhas as cofre

SENHA = "segredo-ficticio-123"


@pytest.fixture
def chave(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "dados" / "senhas.key"
    monkeypatch.setenv("CONSULTORFIN_SENHAS_KEY", str(p))
    return p


def test_roundtrip(con: sqlite3.Connection, chave: Path) -> None:
    cofre.guardar(con, "Itaú", "1234", SENHA)
    assert cofre.candidatas(con, "Itaú", "1234") == [SENHA]


def test_sem_linhas_nao_cria_chave(con: sqlite3.Connection, chave: Path) -> None:
    assert cofre.candidatas(con, "Itaú", "1234") == []
    assert not chave.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="permissões POSIX")
def test_chave_criada_0600(con: sqlite3.Connection, chave: Path) -> None:
    cofre.guardar(con, "Itaú", "1234", SENHA)
    assert (chave.stat().st_mode & 0o777) == 0o600
    assert (chave.parent.stat().st_mode & 0o777) == 0o700
    assert len(chave.read_bytes()) == 32


def test_adulteracao_e_aad_errado_sao_ignorados(con: sqlite3.Connection, chave: Path) -> None:
    cofre.guardar(con, "Itaú", "1234", SENHA)
    cofre.guardar(con, "Itaú", "9999", "outra")
    # troca a cifra de 1234 por bytes adulterados; 9999 recebe a cifra de 1234 (AAD diferente)
    c1 = con.execute("SELECT nonce, cifra FROM senhas_pdf WHERE cartao_final='1234'").fetchone()
    adulterada = bytes([c1["cifra"][0] ^ 1]) + c1["cifra"][1:]
    con.execute("UPDATE senhas_pdf SET cifra=? WHERE cartao_final='1234'", (adulterada,))
    con.execute("UPDATE senhas_pdf SET nonce=?, cifra=? WHERE cartao_final='9999'",
                (c1["nonce"], c1["cifra"]))
    assert cofre.candidatas(con, "Itaú", "1234") == []


def test_chave_ausente_com_linhas(con: sqlite3.Connection, chave: Path,
                                  caplog: pytest.LogCaptureFixture) -> None:
    cofre.guardar(con, "Itaú", "1234", SENHA)
    chave.unlink()
    with caplog.at_level(logging.INFO):
        assert cofre.candidatas(con, "Itaú", "1234") == []
    assert "event=senhas_chave_ausente" in caplog.text
    assert SENHA not in caplog.text


def test_ordem_exato_emissor_demais(con: sqlite3.Connection, chave: Path) -> None:
    cofre.guardar(con, "Porto", "1111", "p1")
    cofre.guardar(con, "Itaú", "2222", "i2")
    cofre.guardar(con, "Itaú", "1234", "i1")
    assert cofre.candidatas(con, "Itaú", "1234") == ["i1", "i2", "p1"]
    assert cofre.candidatas(con, "Itaú") == ["i1", "i2", "p1"]
    assert sorted(cofre.candidatas(con)) == ["i1", "i2", "p1"]


def test_upsert_substitui(con: sqlite3.Connection, chave: Path) -> None:
    cofre.guardar(con, "Itaú", "1234", "antiga")
    cofre.guardar(con, "Itaú", "1234", "nova")
    assert cofre.candidatas(con, "Itaú", "1234") == ["nova"]
    assert len(cofre.listar(con)) == 1


def test_listar_e_esquecer_sem_segredo(con: sqlite3.Connection, chave: Path) -> None:
    cofre.guardar(con, "Itaú", "1234", SENHA)
    itens = cofre.listar(con)
    assert [set(i) for i in itens] == [{"id", "emissor", "cartao_final"}]
    assert cofre.esquecer(con, int(itens[0]["id"])) is True
    assert cofre.listar(con) == [] and cofre.esquecer(con, 999) is False


def test_chave_invalida_nao_e_sobrescrita(con: sqlite3.Connection, chave: Path) -> None:
    chave.parent.mkdir(parents=True)
    chave.write_bytes(b"curta")
    with pytest.raises(ValueError):
        cofre.guardar(con, "Itaú", "1234", SENHA)
    assert chave.read_bytes() == b"curta"


def test_migracao_14_e_senha_nao_aparece_no_arquivo_do_banco(tmp_path: Path, chave: Path) -> None:
    db = tmp_path / "t.db"
    c = conectar(db)
    migrar(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] >= 14
    cols = [r[1] for r in c.execute("PRAGMA table_info(senhas_pdf)")]
    assert cols == ["id", "emissor", "cartao_final", "nonce", "cifra", "criado_em"]
    cofre.guardar(c, "Itaú", "1234", SENHA)
    c.commit()
    c.close()
    assert SENHA.encode() not in db.read_bytes()
    assert config.caminho_chave_senhas() == chave
    assert os.path.exists(chave)
