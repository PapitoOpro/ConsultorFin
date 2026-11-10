"""Tela Cadastros: contas e cartões, categorias e recorrentes."""
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

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


def _sql(db: Path, sql: str, *args: Any) -> list[Any]:
    con = conectar(db)
    try:
        return [tuple(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def _conta(db: Path, nome: str = "Itaú CC") -> int:
    con = conectar(db)
    try:
        return contas.criar_conta(con, nome, "corrente", saldo_inicial_centavos=0,
                                  data_saldo_inicial="2026-10-01")
    finally:
        con.close()


def test_cadastros_abre_na_secao_contas(cliente: TestClient) -> None:
    r = cliente.get("/cadastros")
    assert r.status_code == 200
    assert "Nova conta ou cartão" in r.text
    assert 'action="/cadastros/conta"' in r.text
    assert "Nova conta fixa ou receita recorrente" not in r.text


def test_secao_invalida_cai_em_contas(cliente: TestClient) -> None:
    r = cliente.get("/cadastros?secao=xx")
    assert r.status_code == 200 and "Nova conta ou cartão" in r.text


def test_secoes_categorias_e_recorrentes(cliente: TestClient) -> None:
    assert "Nova categoria de" in cliente.get("/cadastros?secao=categorias").text
    assert 'action="/cadastros/recorrente"' in cliente.get("/cadastros?secao=recorrentes").text


def test_categorias_get_redireciona_para_cadastros(cliente: TestClient) -> None:
    r = cliente.get("/categorias?aba=entrada", follow_redirects=False)
    assert r.status_code == 303
    assert "secao=categorias&aba=entrada" in r.headers["location"]
    r = cliente.get("/categorias", follow_redirects=False)
    assert r.status_code == 303 and "aba=saida" in r.headers["location"]


def test_criar_conta_em_cadastros(cliente: TestClient, db: Path) -> None:
    r = cliente.post("/cadastros/conta", data={
        "nome": "Itaú Novo", "tipo": "corrente", "instituicao": "", "final_numero": "",
        "saldo_inicial": "0,00", "data_saldo_inicial": "2026-10-01", "limite_cheque_especial": ""})
    assert r.status_code == 200 and "Itaú Novo" in r.text
    assert _sql(db, "SELECT COUNT(*) FROM contas WHERE nome='Itaú Novo'") == [(1,)]


def test_criar_conta_invalida_devolve_400(cliente: TestClient) -> None:
    r = cliente.post("/cadastros/conta", data={"nome": "", "tipo": "corrente"})
    assert r.status_code == 400


def test_configurar_cheque_especial_em_cadastros(cliente: TestClient, db: Path) -> None:
    cid = _conta(db)
    r = cliente.post(f"/cadastros/conta/{cid}/cheque-especial", data={
        "limite": "4.410,00", "dias_isentos": "10", "alerta_a_partir_de": "8"})
    assert r.status_code == 200
    con = conectar(db)
    try:
        assert ce.carregar_config(con) == ce.ConfigChequeEspecial(conta_id=cid, limite_centavos=441000)
    finally:
        con.close()
    assert f"/cadastros/conta/{cid}/cheque-especial" in cliente.get("/cadastros").text
    assert cliente.post("/cadastros/conta/9999/cheque-especial", data={"limite": "1,00"}).status_code == 404


def test_criar_e_desativar_recorrente_em_cadastros(cliente: TestClient, db: Path) -> None:
    cid = _conta(db)
    r = cliente.post("/cadastros/recorrente", data={
        "nome": "Aluguel", "tipo": "saida", "dia": "10", "valor_estimado": "1.500,00",
        "conta_id": str(cid), "categoria_saida": ""})
    assert r.status_code == 200 and "Aluguel" in r.text
    rid = _sql(db, "SELECT id FROM recorrentes")[0][0]
    assert f"/cadastros/recorrente/{rid}/desativar" in r.text
    r = cliente.post(f"/cadastros/recorrente/{rid}/desativar")
    assert r.status_code == 200 and "inativa" in r.text
    assert _sql(db, "SELECT ativa FROM recorrentes") == [(0,)]


def test_menu_mostra_cadastros_e_nao_categorias(cliente: TestClient) -> None:
    html = cliente.get("/cadastros").text
    assert 'href="/cadastros"' in html and ">Cadastros<" in html
    assert 'href="/categorias"' not in html
    assert 'href="/contas-a-pagar"' in html
