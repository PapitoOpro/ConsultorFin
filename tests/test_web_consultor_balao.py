from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

URL = "https://consultor.exemplo.com.br"


def _html(tmp_path: Path) -> str:
    with TestClient(create_app(tmp_path / "t.db")) as c:
        return c.get("/").text


def test_sem_variavel_nao_ha_balao(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONSULTORFIN_TERMINAL_URL", raising=False)
    assert "consultor-balao" not in _html(tmp_path)


def test_url_nao_https_e_ignorada(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONSULTORFIN_TERMINAL_URL", "javascript:alert(1)")
    assert "consultor-balao" not in _html(tmp_path)


def test_com_variavel_renderiza_balao_e_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONSULTORFIN_TERMINAL_URL", URL)
    html = _html(tmp_path)
    assert 'id="consultor-balao"' in html
    assert f'data-url="{URL}"' in html
    assert "Abrir terminal em nova aba" in html
    assert "<iframe" not in html  # iframe só nasce no primeiro clique
