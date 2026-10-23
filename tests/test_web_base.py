import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.db import conectar
from app.main import create_app
from app.web import deps

WEB = Path(__file__).resolve().parent.parent / "app" / "web"


@pytest.fixture
def cliente(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(tmp_path / "t.db")) as c:
        yield c


def test_inicio_ok_e_lang(cliente: TestClient) -> None:
    r = cliente.get("/")
    assert r.status_code == 200
    assert 'lang="pt-BR"' in r.text
    for rota in ("/lancamentos", "/importar", "/contas-a-pagar"):
        assert f'href="{rota}"' in r.text


def test_tema_variaveis_exatas(cliente: TestClient) -> None:
    css = cliente.get("/static/css/tema.css").text
    for v in (
        "--fundo: #121212", "--superficie: #1E1E1E", "--borda: #2C2C2C",
        "--primaria: #38BDF8", "--alerta: #F59E0B", "--entrada: #4ADE80",
        "--saida: #F87171", "--texto: #F3F4F6", "--texto-secundario: #9CA3AF",
        '--fonte-texto: "Inter", system-ui, sans-serif',
        '--fonte-dados: "JetBrains Mono", ui-monospace, monospace',
    ):
        assert v in css
    assert "box-shadow" not in css
    assert "#FFFFFF" not in css.upper()


def test_assets_locais_servidos(cliente: TestClient) -> None:
    for p in ("js/htmx.min.js", "js/echarts.min.js", "fontes/inter-latin-400-normal.woff2",
              "fontes/jetbrains-mono-latin-400-normal.woff2"):
        assert cliente.get(f"/static/{p}").status_code == 200


def test_sem_cdn_nos_arquivos_proprios() -> None:
    for p in (WEB / "templates" / "base.html", WEB / "static" / "css" / "tema.css"):
        assert not re.search(r"https?://", p.read_text(encoding="utf-8")), p


def test_filtros(tmp_path: Path) -> None:
    app = create_app(tmp_path / "t.db")
    env = app.state.templates.env
    assert env.from_string("{{ 140914 | brl }}").render() == "R$ 1.409,14"
    assert env.from_string("{{ '2026-10-22' | data_br }}").render() == "22/10/2026"


def test_get_con_commit_e_rollback(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    app = create_app(db)

    @app.post("/_ok")
    def ok(con: Any = Depends(deps.get_con)) -> dict[str, bool]:
        con.execute("INSERT INTO configuracoes (chave, valor) VALUES ('a', '1')")
        return {"ok": True}

    @app.post("/_erro")
    def erro(con: Any = Depends(deps.get_con)) -> None:
        con.execute("INSERT INTO configuracoes (chave, valor) VALUES ('b', '1')")
        raise HTTPException(status_code=400, detail="x")

    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.post("/_ok").status_code == 200
        assert c.post("/_erro").status_code == 400
    con = conectar(db)
    chaves = {r["chave"] for r in con.execute("SELECT chave FROM configuracoes")}
    con.close()
    assert chaves == {"a"}


def test_create_app_nao_toca_data(tmp_path: Path) -> None:
    assert isinstance(create_app(tmp_path / "x.db"), FastAPI)
