"""Fim da tela Contas a pagar: redirecionamento, menu com selo de vencidas e links do Início/painel."""
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import conectar
from app.dominio.lancamentos import criar_lancamento_manual
from app.main import create_app
from app.repo import contas

HOJE = date(2026, 10, 23)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "t.db"


@pytest.fixture
def cliente(db: Path) -> Iterator[TestClient]:
    app = create_app(db)
    app.state.hoje_fn = lambda: HOJE
    with TestClient(app) as c:
        yield c


@pytest.fixture
def conta(cliente: TestClient, db: Path) -> int:
    con = conectar(db)
    try:
        return contas.criar_conta(con, "Corrente Teste", "corrente")
    finally:
        con.close()


def _pendente(db: Path, conta: int, data: str, valor: int = -5000) -> int:
    con = conectar(db)
    try:
        return criar_lancamento_manual(con, conta_id=conta, data=date.fromisoformat(data),
                                       descricao="Boleto", valor_centavos=valor, compensado=False)
    finally:
        con.close()


def _menu(html: str) -> str:
    m = re.search(r'<nav class="menu-nav".*?</nav>', html, re.S)
    assert m
    return m.group(0)


def test_contas_a_pagar_redireciona(cliente: TestClient) -> None:
    r = cliente.get("/contas-a-pagar", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/lancamentos?aba=a-pagar"


def test_menu_nao_tem_contas_a_pagar(cliente: TestClient) -> None:
    menu = _menu(cliente.get("/").text)
    assert "Contas a pagar" not in menu and "/contas-a-pagar" not in menu


def test_menu_mostra_selo_de_vencidas(cliente: TestClient, db: Path, conta: int) -> None:
    _pendente(db, conta, "2026-10-01")
    _pendente(db, conta, "2026-09-20")
    menu = _menu(cliente.get("/").text)
    assert re.search(r'href="/lancamentos".*?class="selo-menu[^"]*"[^>]*>2<', menu, re.S)


def test_menu_sem_vencidas_nao_mostra_selo(cliente: TestClient, db: Path, conta: int) -> None:
    _pendente(db, conta, "2026-11-10")
    assert "selo-menu" not in _menu(cliente.get("/").text)


def test_menu_sobrevive_a_falha_ao_contar_vencidas(cliente: TestClient, db: Path, conta: int,
                                                   monkeypatch: pytest.MonkeyPatch,
                                                   caplog: pytest.LogCaptureFixture) -> None:
    def quebra(*_a: Any, **_k: Any) -> int:
        raise RuntimeError("falha")
    monkeypatch.setattr("app.main.contar_vencidas", quebra)
    r = cliente.get("/")
    assert r.status_code == 200 and "selo-menu" not in _menu(r.text)
    assert "event=n_vencidas_falhou" in caplog.text


def test_inicio_aponta_para_lancamentos_a_pagar(cliente: TestClient, db: Path, conta: int) -> None:
    _pendente(db, conta, "2026-10-01")
    _pendente(db, conta, "2026-10-25")
    html = cliente.get("/").text
    assert "/contas-a-pagar" not in html
    assert 'href="/lancamentos?aba=a-pagar"' in html


def test_painel_links_apontam_para_lancamentos() -> None:
    texto = (Path(__file__).resolve().parents[1] / "app" / "dominio" / "painel.py").read_text(
        encoding="utf-8")
    assert "/contas-a-pagar" not in texto and "Contas a pagar" not in texto
    assert texto.count('"link": "/lancamentos?aba=a-pagar", "link_rotulo": "Ver em Lançamentos"') == 2
