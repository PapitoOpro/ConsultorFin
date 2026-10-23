import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import conectar
from app.importacao.base import LinhaImportada, ResultadoLeitura
from app.importacao.servico import importar
from app.main import create_app
from app.repo import contas


class _P:
    def ler(self, conteudo: bytes) -> ResultadoLeitura:
        return ResultadoLeitura(linhas=[LinhaImportada("2026-10-01", "X", -100)])


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "t.db"


@pytest.fixture
def cliente(db: Path) -> Iterator[TestClient]:
    with TestClient(create_app(db)) as c:
        con = conectar(db)
        cid = contas.criar_conta(con, "C", "corrente")
        importar(con, cid, "a.csv", b"a", _P(), date(2026, 10, 23))
        con.close()
        yield c


def _n(db: Path) -> int:
    con = conectar(db)
    try:
        return int(con.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0])
    finally:
        con.close()


def test_origin_estrangeira_bloqueada(cliente: TestClient, db: Path) -> None:
    r = cliente.post("/importar/1/desfazer", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert "Requisição bloqueada por segurança (origem diferente)." in r.text
    assert _n(db) == 1


def test_origin_mesma_origem_passa(cliente: TestClient, db: Path) -> None:
    r = cliente.post("/importar/1/desfazer", headers={"Origin": "http://testserver"})
    assert r.status_code == 200
    assert _n(db) == 0


def test_sec_fetch_site(cliente: TestClient, db: Path) -> None:
    assert cliente.post("/importar/1/desfazer", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert _n(db) == 1
    assert cliente.post("/importar/1/desfazer", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_host_invalido(cliente: TestClient) -> None:
    r = cliente.get("/importar", headers={"Host": "evil.example"})
    assert r.status_code == 400
    assert "host não permitido" in r.text


def test_get_host_permitido(cliente: TestClient) -> None:
    assert cliente.get("/importar", headers={"Host": "localhost:8000"}).status_code == 200
    assert cliente.get("/importar", headers={"Host": "[::1]:8000"}).status_code == 200


def test_conta_id_nao_ascii_sem_500(cliente: TestClient) -> None:
    r = cliente.post("/importar/confirmar", data={"token": "x", "conta_id": "²"})
    assert r.status_code == 400


def test_confirmar_excecao_inesperada(cliente: TestClient, db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.web.rotas import importar as rota

    def boom(*a: Any, **k: Any) -> None:
        raise OverflowError("x")

    monkeypatch.setattr(rota, "importar", boom)
    csv = b"Dia;Hist;Valor\n01/10/2026;A;-1,00\n"
    r = cliente.post("/importar/previa", files={"arquivo": ("b.csv", csv)})
    m = re.search(r'name="token" value="([^"]+)"', r.text)
    assert m
    r = cliente.post("/importar/confirmar", data={"token": m.group(1), "conta_id": "1", "mapa_data": "Dia",
                                                  "mapa_descricao": "Hist", "mapa_valor": "Valor"})
    assert r.status_code == 500
    assert "Não foi possível concluir a importação. Nada foi gravado." in r.text
    assert "Traceback" not in r.text
    assert _n(db) == 1


def test_conta_com_token_expirado_nao_cria(cliente: TestClient, db: Path) -> None:
    r = cliente.post("/importar/conta", data={"nome": "Nova", "tipo": "corrente", "saldo_inicial": "0,00",
                                              "data_saldo_inicial": "2026-07-24", "token": "expirado"})
    assert r.status_code == 400 and "Envio expirado" in r.text
    con = conectar(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM contas").fetchone()[0] == 1
    finally:
        con.close()
