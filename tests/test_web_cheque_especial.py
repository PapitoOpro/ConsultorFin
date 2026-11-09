"""Rota do cheque especial: corrigir os dias de uso do mês."""
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


def test_ajuste_de_dias(cliente: TestClient, db: Path, conta: int) -> None:
    con = conectar(db)
    try:
        ce.salvar_config(con, ce.ConfigChequeEspecial(conta_id=conta, limite_centavos=441000))
    finally:
        con.close()
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
