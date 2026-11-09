"""Cheque especial configurado junto das contas (Contas a pagar)."""
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
    with TestClient(app) as c:
        yield c


def _nova(db: Path, nome: str = "Itaú CC", tipo: str = "corrente") -> int:
    con = conectar(db)
    try:
        return contas.criar_conta(con, nome, tipo, saldo_inicial_centavos=0, data_saldo_inicial="2026-10-01")
    finally:
        con.close()


def _config(db: Path) -> ce.ConfigChequeEspecial | None:
    con = conectar(db)
    try:
        return ce.carregar_config(con)
    finally:
        con.close()


def _form(limite: str = "4.410,00") -> dict[str, str]:
    return {"limite": limite, "dias_isentos": "10", "alerta_a_partir_de": "8", "horizonte": "6"}


def test_configura_conta_existente(cliente: TestClient, db: Path) -> None:
    cid = _nova(db)
    r = cliente.post(f"/contas-a-pagar/conta/{cid}/cheque-especial", data=_form())
    assert r.status_code == 200 and "Cheque especial configurado" in r.text
    assert _config(db) == ce.ConfigChequeEspecial(conta_id=cid, limite_centavos=441000)


def test_limite_vazio_remove_quando_e_a_conta_configurada(cliente: TestClient, db: Path) -> None:
    cid = _nova(db)
    cliente.post(f"/contas-a-pagar/conta/{cid}/cheque-especial", data=_form())
    r = cliente.post(f"/contas-a-pagar/conta/{cid}/cheque-especial", data=_form(""))
    assert r.status_code == 200 and "removido" in r.text and _config(db) is None


def test_limite_vazio_em_outra_conta_nao_mexe_na_config(cliente: TestClient, db: Path) -> None:
    a, b = _nova(db), _nova(db, "Outra")
    cliente.post(f"/contas-a-pagar/conta/{a}/cheque-especial", data=_form())
    r = cliente.post(f"/contas-a-pagar/conta/{b}/cheque-especial", data=_form(""))
    assert r.status_code == 400 and _config(db).conta_id == a  # type: ignore[union-attr]


@pytest.mark.parametrize("limite", ["abc", "-5", "0"])
def test_limite_invalido_nao_grava(cliente: TestClient, db: Path, limite: str) -> None:
    cid = _nova(db)
    r = cliente.post(f"/contas-a-pagar/conta/{cid}/cheque-especial", data=_form(limite))
    assert r.status_code == 400 and _config(db) is None


def test_so_conta_corrente(cliente: TestClient, db: Path) -> None:
    cid = _nova(db, "Cartão", "cartao")
    r = cliente.post(f"/contas-a-pagar/conta/{cid}/cheque-especial", data=_form())
    assert r.status_code == 400 and "conta corrente" in r.text and _config(db) is None


def test_conta_inexistente(cliente: TestClient) -> None:
    r = cliente.post("/contas-a-pagar/conta/9999/cheque-especial", data=_form())
    assert r.status_code == 404


def _cadastro(limite: str, tipo: str = "corrente") -> dict[str, str]:
    return {"nome": "Itaú Novo", "tipo": tipo, "instituicao": "", "final_numero": "",
            "saldo_inicial": "0,00", "data_saldo_inicial": "2026-10-01", "horizonte": "6",
            "limite_cheque_especial": limite}


def test_cadastro_com_limite_configura_a_conta_nova(cliente: TestClient, db: Path) -> None:
    r = cliente.post("/contas-a-pagar/conta", data=_cadastro("1.000,00"))
    assert r.status_code == 200
    con = conectar(db)
    try:
        cid = int(con.execute("SELECT id FROM contas WHERE nome='Itaú Novo'").fetchone()[0])
    finally:
        con.close()
    assert _config(db) == ce.ConfigChequeEspecial(conta_id=cid, limite_centavos=100000)


def test_cadastro_sem_limite_nao_configura(cliente: TestClient, db: Path) -> None:
    assert cliente.post("/contas-a-pagar/conta", data=_cadastro("")).status_code == 200
    assert _config(db) is None


def test_cadastro_com_limite_em_tipo_errado_nao_cria_conta(cliente: TestClient, db: Path) -> None:
    r = cliente.post("/contas-a-pagar/conta", data=_cadastro("1.000,00", "poupanca"))
    assert r.status_code == 400
    con = conectar(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM contas WHERE nome='Itaú Novo'").fetchone()[0] == 0
    finally:
        con.close()


def test_tela_mostra_formulario_so_para_conta_corrente(cliente: TestClient, db: Path) -> None:
    cc, cartao = _nova(db), _nova(db, "Cartão", "cartao")
    html = cliente.get("/contas-a-pagar").text
    assert f"/contas-a-pagar/conta/{cc}/cheque-especial" in html
    assert f"/contas-a-pagar/conta/{cartao}/cheque-especial" not in html
