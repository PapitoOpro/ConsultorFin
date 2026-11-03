"""Senhas de PDF guardadas por cartão: fluxo web (PDFs cifrados com pypdf, dados inventados)."""
import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import conectar
from app.dominio import cofre_senhas as cofre
from tests.test_web_fila import (  # noqa: F401 - fixtures reutilizadas
    _ParserFalso, _pdf_criptografado, _pdf_minimo, _semear, cliente, db, dir_fila,
)

SENHA = "senha-ficticia-4321"


@pytest.fixture(autouse=True)
def _parser(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.importacao.fatura.detectar", lambda paginas: _ParserFalso())


def _guardar(db: Path, senha: str = SENHA, emissor: str = "Itaú", final: str = "1234") -> None:
    con = conectar(db)
    try:
        cofre.guardar(con, emissor, final, senha)
        con.commit()
    finally:
        con.close()


def _linhas(db: Path) -> int:
    con = conectar(db)
    try:
        return int(con.execute("SELECT COUNT(*) FROM senhas_pdf").fetchone()[0])
    finally:
        con.close()


def _enviar(c: TestClient, pdf: bytes, **data: str):  # type: ignore[no-untyped-def]
    return c.post("/faturas/previa", files={"arquivo": ("f.pdf", pdf, "application/pdf")},
                  data=data)


def test_senha_guardada_vai_direto_para_a_previa(cliente: TestClient, db: Path) -> None:
    _guardar(db)
    r = _enviar(cliente, _pdf_criptografado(SENHA))
    assert r.status_code == 200 and "LOJA FICTICIA" in r.text
    assert 'name="senha"' not in r.text


def test_senha_guardada_errada_cai_no_formulario(cliente: TestClient, db: Path) -> None:
    _guardar(db, "outra-senha")
    r = _enviar(cliente, _pdf_criptografado(SENHA))
    assert "LOJA FICTICIA" not in r.text and 'name="senha"' in r.text


def test_lembrar_guarda_e_segunda_importacao_nao_pede_senha(cliente: TestClient, db: Path,
                                                            dir_fila: Path) -> None:
    pdf = _pdf_criptografado(SENHA)
    r = _enviar(cliente, pdf, senha=SENHA, lembrar="1")
    assert "LOJA FICTICIA" in r.text and _linhas(db) == 1
    item = _semear(db, dir_fila, pdf)
    r2 = cliente.post(f"/faturas/fila/{item}/importar")
    assert "LOJA FICTICIA" in r2.text and 'name="senha"' not in r2.text


def test_sem_lembrar_nao_guarda(cliente: TestClient, db: Path) -> None:
    r = _enviar(cliente, _pdf_criptografado(SENHA), senha=SENHA)
    assert "LOJA FICTICIA" in r.text and _linhas(db) == 0


def test_senha_errada_com_lembrar_nao_guarda(cliente: TestClient, db: Path) -> None:
    r = _enviar(cliente, _pdf_criptografado(SENHA), senha="errada-xyz", lembrar="1")
    assert "LOJA FICTICIA" not in r.text and _linhas(db) == 0
    assert "errada-xyz" not in r.text


def test_pdf_sem_senha_com_lembrar_nao_guarda(cliente: TestClient, db: Path) -> None:
    r = _enviar(cliente, _pdf_minimo("FATURA"), senha="qualquer", lembrar="1")
    assert "LOJA FICTICIA" in r.text and _linhas(db) == 0


def test_fila_passa_dica_e_prioriza_o_cartao(cliente: TestClient, db: Path,
                                             dir_fila: Path) -> None:
    _guardar(db, "do-porto", "Porto", "1111")
    _guardar(db, SENHA)
    item = _semear(db, dir_fila, _pdf_criptografado(SENHA))
    r = cliente.post(f"/faturas/fila/{item}/importar")
    assert "LOJA FICTICIA" in r.text


def test_fila_sem_senha_guardada_mostra_formulario_com_lembrar(cliente: TestClient, db: Path,
                                                               dir_fila: Path) -> None:
    item = _semear(db, dir_fila, _pdf_criptografado(SENHA))
    r = cliente.post(f"/faturas/fila/{item}/importar")
    assert 'name="senha"' in r.text
    assert re.search(r'<input[^>]*name="lembrar"[^>]*type="checkbox"|'
                     r'<input[^>]*type="checkbox"[^>]*name="lembrar"', r.text)
    assert "Lembrar a senha deste cartão" in r.text


def test_importar_lista_cartoes_sem_a_senha_e_esquecer(cliente: TestClient, db: Path) -> None:
    assert "Senhas guardadas" not in cliente.get("/importar").text
    _guardar(db)
    r = cliente.get("/importar")
    assert "Senhas guardadas" in r.text and "Itaú" in r.text and "1234" in r.text
    assert SENHA not in r.text
    con = conectar(db)
    try:
        sid = con.execute("SELECT id FROM senhas_pdf").fetchone()[0]
    finally:
        con.close()
    r2 = cliente.post(f"/senhas/{sid}/esquecer", follow_redirects=False)
    assert r2.status_code == 303 and r2.headers["location"] == "/importar"
    assert _linhas(db) == 0
    assert cliente.post("/senhas/999/esquecer", follow_redirects=False).status_code == 303
    assert cliente.post("/senhas/abc/esquecer").status_code == 422


def test_log_nao_tem_a_senha(cliente: TestClient, db: Path, dir_fila: Path,
                             caplog: pytest.LogCaptureFixture) -> None:
    pdf = _pdf_criptografado(SENHA)
    with caplog.at_level(logging.DEBUG):
        _enviar(cliente, pdf, senha="errada-xyz", lembrar="1")
        _enviar(cliente, pdf, senha=SENHA, lembrar="1")
        _enviar(cliente, pdf)
    assert SENHA not in caplog.text and "errada-xyz" not in caplog.text
