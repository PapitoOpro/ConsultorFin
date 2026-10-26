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
        "--fundo: #080912", "--superficie: #111120", "--borda: #242438",
        "--primaria: #00D6A0", "--alerta: #FFB84D", "--entrada: #00D6A0",
        "--saida: #FF5470", "--texto: #F5F5FA", "--texto-secundario: #9292AB",
        '--fonte-texto: "Inter", system-ui, sans-serif',
        '--fonte-dados: "JetBrains Mono", ui-monospace, monospace',
    ):
        assert v in css
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


def test_create_app_nao_toca_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.main as main
    from app import config

    padrao = tmp_path / "padrao" / "consultorfin.db"
    monkeypatch.setattr(config, "DB_PATH", padrao)
    monkeypatch.chdir(tmp_path)
    assert not hasattr(main, "app")
    with TestClient(create_app(tmp_path / "x.db")) as c:
        assert c.get("/").status_code == 200
    assert not (tmp_path / "data").exists()
    assert not padrao.parent.exists() and not padrao.exists()


def test_get_con_concorrente(tmp_path: Path) -> None:
    from concurrent.futures import ThreadPoolExecutor

    app = create_app(tmp_path / "t.db")

    @app.get("/_conc/{n}")
    def conc(n: int, con: Any = Depends(deps.get_con)) -> dict[str, int]:
        con.execute("INSERT INTO configuracoes (chave, valor) VALUES (?, '1')", (f"k{n}",))
        return {"n": con.execute("SELECT count(*) FROM categorias").fetchone()[0]}

    with TestClient(app) as c:
        with ThreadPoolExecutor(max_workers=8) as ex:
            codigos = list(ex.map(lambda n: c.get(f"/_conc/{n}").status_code, range(40)))
    assert codigos == [200] * 40
    con = conectar(tmp_path / "t.db")
    total = con.execute("SELECT count(*) FROM configuracoes WHERE chave LIKE 'k%'").fetchone()[0]
    con.close()
    assert total == 40
