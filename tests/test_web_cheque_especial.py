"""Rotas do cheque especial: configurar limite e corrigir dias de uso."""
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import conectar
from app.dominio import cheque_especial as ce
from app.main import create_app
from app.repo import contas

HOJE = date(2026, 11, 10)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "t.db"


@pytest.fixture
def cliente(db: Path) -> Iterator[TestClient]:
    app = create_app(db)
    app.state.hoje_fn = lambda: HOJE
    with TestClient(app, follow_redirects=False) as c:
        yield c


@pytest.fixture
def conta(cliente: TestClient, db: Path) -> int:
    con = conectar(db)
    try:
        return contas.criar_conta(con, "Itaú CC", "corrente", saldo_inicial_centavos=0,
                                  data_saldo_inicial="2026-10-01")
    finally:
        con.close()


def _config(db: Path) -> ce.ConfigChequeEspecial | None:
    con = conectar(db)
    try:
        return ce.carregar_config(con)
    finally:
        con.close()


def _form(conta_id: int | str, limite: str = "4.410,00") -> dict[str, str]:
    return {"conta_id": str(conta_id), "limite": limite, "dias_isentos": "10", "alerta_a_partir_de": "8"}


def test_configurar_grava_e_redireciona(cliente: TestClient, db: Path, conta: int) -> None:
    r = cliente.post("/cheque-especial", data=_form(conta))
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert _config(db) == ce.ConfigChequeEspecial(conta_id=conta, limite_centavos=441000)


@pytest.mark.parametrize("limite", ["", "-5", "abc", "0"])
def test_configurar_rejeita_limite_invalido(cliente: TestClient, db: Path, conta: int, limite: str) -> None:
    r = cliente.post("/cheque-especial", data=_form(conta, limite))
    assert r.status_code == 303 and "erro_cheque=" in r.headers["location"]
    assert _config(db) is None


def test_configurar_rejeita_conta_inexistente_e_campos_nao_numericos(cliente: TestClient, db: Path,
                                                                    conta: int) -> None:
    for form in (_form(9999), _form("x"), {**_form(conta), "dias_isentos": "x"}):
        r = cliente.post("/cheque-especial", data=form)
        assert r.status_code == 303 and "erro_cheque=" in r.headers["location"]
    assert _config(db) is None


def test_ajuste_de_dias(cliente: TestClient, db: Path, conta: int) -> None:
    cliente.post("/cheque-especial", data=_form(conta))
    r = cliente.post("/cheque-especial/dias", data={"dias": "7"})
    assert r.status_code == 303 and r.headers["location"] == "/"
    con = conectar(db)
    try:
        assert ce.calcular(con, HOJE).dias_usados == 7  # type: ignore[union-attr]
    finally:
        con.close()


@pytest.mark.parametrize("dias", ["40", "abc", "-1", ""])
def test_ajuste_rejeita_valor_invalido(cliente: TestClient, conta: int, dias: str) -> None:
    r = cliente.post("/cheque-especial/dias", data={"dias": dias})
    assert r.status_code == 303 and "erro_cheque=" in r.headers["location"]
