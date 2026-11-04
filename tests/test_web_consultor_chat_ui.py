"""UI do chat no painel: markup condicionado ao chat realmente registrado."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

URL = "https://consultor.exemplo.com.br"
STATIC = Path(__file__).parent.parent / "app" / "web" / "static"


def _html(tmp_path: Path) -> str:
    with TestClient(create_app(tmp_path / "t.db")) as c:
        return c.get("/").text


@pytest.fixture
def chat_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONSULTORFIN_TERMINAL_URL", raising=False)
    monkeypatch.setenv("CONSULTORFIN_CHAT", "1")
    monkeypatch.setattr("app.main.chat_pode_iniciar", lambda: True)


def test_chat_ativo_renderiza_painel_de_chat(tmp_path: Path, chat_ok: None) -> None:
    html = _html(tmp_path)
    assert 'id="consultor-balao"' in html
    assert 'data-modo="chat"' in html
    assert "consultor-chat.js" in html and "chat-markdown.js" in html
    assert html.index("chat-markdown.js") < html.index("consultor-chat.js")
    assert "Nova conversa" in html and "<textarea" in html
    assert 'role="log"' in html and 'aria-live="polite"' in html
    assert "data-url" not in html and "<iframe" not in html
    assert "Abrir terminal em nova aba" not in html


def test_chat_ativo_com_terminal_mostra_fallback(tmp_path: Path, chat_ok: None,
                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONSULTORFIN_TERMINAL_URL", URL)
    html = _html(tmp_path)
    assert 'data-modo="chat"' in html
    assert "Abrir terminal em nova aba" in html
    assert "data-url" not in html


def test_porteiro_falhando_nao_renderiza_chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONSULTORFIN_CHAT", "1")
    monkeypatch.setattr("app.main.chat_pode_iniciar", lambda: False)
    monkeypatch.delenv("CONSULTORFIN_TERMINAL_URL", raising=False)
    html = _html(tmp_path)
    assert "consultor-balao" not in html and "<textarea" not in html and "consultor-chat.js" not in html
    monkeypatch.setenv("CONSULTORFIN_TERMINAL_URL", URL)
    html = _html(tmp_path)
    assert f'data-url="{URL}"' in html and 'data-modo="chat"' not in html and "<textarea" not in html


def test_chat_inativo_nao_renderiza_chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONSULTORFIN_CHAT", raising=False)
    monkeypatch.setenv("CONSULTORFIN_TERMINAL_URL", URL)
    html = _html(tmp_path)
    assert "consultor-chat.js" not in html and "data-modo" not in html


def test_chat_sem_urls_externas(tmp_path: Path, chat_ok: None) -> None:
    html = _html(tmp_path)
    painel = html[html.index('id="consultor-balao"'):]
    assert not re.search(r"""(?:src|href|action)=["']\s*(?:https?:)?//""", painel)


def test_js_do_chat_sem_sinks_perigosos() -> None:
    js = (STATIC / "js" / "consultor-chat.js").read_text(encoding="utf-8")
    for proibido in ("innerHTML", "insertAdjacentHTML", "outerHTML", "document.write", "eval(", "new Function"):
        assert proibido not in js
    caminhos = re.findall(r"""iniciarTurno\(\s*["']([^"']+)["']""", js)
    assert caminhos and all(c.startswith("/consultor/") for c in caminhos)
    assert "http://" not in js and "https://" not in js
