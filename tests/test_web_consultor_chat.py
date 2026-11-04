"""Rotas SSE do chat com um `claude` falso que devolve o stream real capturado."""
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.chat.guarda import VAR_CONFIRMADO, VAR_MARCA
from app.main import create_app

FIXTURE = Path(__file__).parent / "fixtures" / "stream_claude.jsonl"
SESSAO = "5b1f6a8e-2c3d-4e5f-8a9b-0c1d2e3f4a5b"
OUTRA = "0f1e2d3c-4b5a-4968-8776-655443322110"
CATEGORIZAR = ".venv/Scripts/python -m app.consultor categorizar --lancamento 1 --categoria 5"
MSG_ERRO = "Não consegui responder agora. Tente de novo."


class FalsoClaude:
    def __init__(self) -> None:
        self.chamadas: list[tuple[list[str], str]] = []
        self.ambientes: list[dict[str, str]] = []
        self.marca_existia: list[bool] = []
        self.linhas = FIXTURE.read_text(encoding="utf-8").splitlines()
        self.falha: Exception | None = None

    async def __call__(self, argv: list[str], entrada: str, ambiente: dict[str, str]) -> AsyncIterator[str]:
        self.chamadas.append((argv, entrada))
        self.ambientes.append(dict(ambiente))
        marca = ambiente.get(VAR_MARCA)
        self.marca_existia.append(bool(marca) and Path(str(marca)).parent.is_dir())
        for i, linha in enumerate(self.linhas):
            if self.falha is not None and i == 5:
                raise self.falha
            yield linha


@pytest.fixture
def falso() -> FalsoClaude:
    return FalsoClaude()


@pytest.fixture
def cliente(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, falso: FalsoClaude) -> Iterator[TestClient]:
    monkeypatch.setenv("CONSULTORFIN_CHAT", "1")
    app = create_app(tmp_path / "t.db")
    app.state.claude_fn = falso
    with TestClient(app) as c:
        yield c


def _eventos(texto: str) -> list[dict[str, Any]]:
    blocos = [b for b in texto.split("\n\n") if b.strip()]
    assert all(b.startswith("data: ") for b in blocos)
    return [json.loads(b[len("data: "):]) for b in blocos]


def _mensagem(c: TestClient, texto: str = "qual meu saldo?", sessao: str | None = None) -> Any:
    return c.post("/consultor/mensagem", json={"texto": texto, "sessao": sessao})


def test_stream_de_eventos(cliente: TestClient, falso: FalsoClaude) -> None:
    r = _mensagem(cliente)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    eventos = _eventos(r.text)
    tipos = [e["tipo"] for e in eventos]
    assert {"texto", "status", "relatorio", "confirmar", "fim"} <= set(tipos)
    assert tipos[-1] == "fim" and eventos[-1]["sessao"] == SESSAO
    assert "python" not in r.text and "app.consultor" not in r.text
    argv, entrada = falso.chamadas[0]
    assert entrada == "qual meu saldo?"
    assert not any("qual meu saldo" in a for a in argv)
    assert "--resume" not in argv
    assert falso.ambientes[0] == {}


def test_sessao_existente_vai_no_resume(cliente: TestClient, falso: FalsoClaude) -> None:
    assert _mensagem(cliente, sessao=SESSAO).status_code == 200
    argv = falso.chamadas[0][0]
    assert argv[argv.index("--resume") + 1] == SESSAO


def _token(c: TestClient) -> str:
    eventos = _eventos(_mensagem(c, "categorize o lançamento 1 como Saúde").text)
    return next(e["token"] for e in eventos if e["tipo"] == "confirmar")


def test_confirmar_executa_com_o_comando_negado_liberado(cliente: TestClient, falso: FalsoClaude) -> None:
    token = _token(cliente)
    r = cliente.post("/consultor/confirmar", json={"sessao": SESSAO, "token": token, "aceitar": True})
    assert r.status_code == 200
    assert _eventos(r.text)[-1]["tipo"] == "fim"
    argv, entrada = falso.chamadas[-1]
    assert not any("categorizar" in a for a in argv)  # nada de regra para o turno inteiro
    assert argv[argv.index("--resume") + 1] == SESSAO
    assert entrada == "Confirmado. Execute agora a ação que foi bloqueada e conte o resultado."
    ambiente = falso.ambientes[-1]
    assert ambiente[VAR_CONFIRMADO] == CATEGORIZAR  # aprovação única, feita pelo porteiro
    assert falso.marca_existia[-1]
    assert not Path(ambiente[VAR_MARCA]).parent.exists()  # pasta do turno apagada no fim


def test_token_reutilizado_da_404(cliente: TestClient) -> None:
    token = _token(cliente)
    corpo = {"sessao": SESSAO, "token": token, "aceitar": True}
    assert cliente.post("/consultor/confirmar", json=corpo).status_code == 200
    assert cliente.post("/consultor/confirmar", json=corpo).status_code == 404


def test_token_de_outra_sessao_da_404(cliente: TestClient, falso: FalsoClaude) -> None:
    token = _token(cliente)
    n = len(falso.chamadas)
    r = cliente.post("/consultor/confirmar", json={"sessao": OUTRA, "token": token, "aceitar": True})
    assert r.status_code == 404
    assert len(falso.chamadas) == n
    # o token continua valendo na sessão certa
    assert cliente.post("/consultor/confirmar",
                        json={"sessao": SESSAO, "token": token, "aceitar": True}).status_code == 200


def test_cancelar_nao_libera_comando(cliente: TestClient, falso: FalsoClaude) -> None:
    token = _token(cliente)
    r = cliente.post("/consultor/confirmar", json={"sessao": SESSAO, "token": token, "aceitar": False})
    assert r.status_code == 200
    argv, entrada = falso.chamadas[-1]
    assert entrada == "Cancelado. Não faça essa alteração."
    assert not any("categorizar" in a for a in argv)
    assert falso.ambientes[-1] == {}


@pytest.mark.parametrize("corpo", [
    {"texto": "oi", "sessao": "--help"},
    {"texto": "oi", "sessao": SESSAO + "\n--help"},
    {"texto": ""},
    {"texto": "x" * 2001},
    {"texto": 5},
    {},
])
def test_mensagem_invalida_da_422(cliente: TestClient, falso: FalsoClaude, corpo: dict[str, Any]) -> None:
    assert cliente.post("/consultor/mensagem", json=corpo).status_code == 422
    assert falso.chamadas == []


def test_mensagem_no_limite_e_aceita(cliente: TestClient) -> None:
    assert _mensagem(cliente, "x" * 2000).status_code == 200
    assert _mensagem(cliente, "--help").status_code == 200  # vai por stdin, nunca vira flag


@pytest.mark.parametrize("corpo", [
    {"sessao": "--help", "token": "t", "aceitar": True},
    {"sessao": SESSAO, "token": "", "aceitar": True},
    {"sessao": SESSAO, "token": "t" * 200, "aceitar": True},
    {"sessao": SESSAO, "token": "t"},
])
def test_confirmar_invalido_da_422(cliente: TestClient, corpo: dict[str, Any]) -> None:
    assert cliente.post("/consultor/confirmar", json=corpo).status_code == 422


def test_sessao_ocupada_da_409(cliente: TestClient, falso: FalsoClaude) -> None:
    estado = cliente.app.state.chat  # type: ignore[attr-defined]
    assert estado.reservar(SESSAO)
    r = _mensagem(cliente, sessao=SESSAO)
    assert r.status_code == 409
    assert r.json() == {"erro": "Aguarde a resposta anterior."}
    assert falso.chamadas == []
    estado.liberar(SESSAO)
    assert _mensagem(cliente, sessao=SESSAO).status_code == 200


def test_confirmar_em_sessao_ocupada_da_409_e_preserva_token(cliente: TestClient) -> None:
    token = _token(cliente)
    estado = cliente.app.state.chat  # type: ignore[attr-defined]
    assert estado.reservar(SESSAO)
    corpo = {"sessao": SESSAO, "token": token, "aceitar": True}
    assert cliente.post("/consultor/confirmar", json=corpo).status_code == 409
    estado.liberar(SESSAO)
    assert cliente.post("/consultor/confirmar", json=corpo).status_code == 200


def test_falha_no_claude_vira_erro_generico_e_libera(cliente: TestClient, falso: FalsoClaude,
                                                    caplog: pytest.LogCaptureFixture) -> None:
    falso.falha = RuntimeError("stderr secreto /home/user saldo 1234")
    r = _mensagem(cliente, "meu segredo financeiro", sessao=SESSAO)
    assert r.status_code == 200
    eventos = _eventos(r.text)
    assert eventos[-1] == {"tipo": "erro", "texto": MSG_ERRO}
    assert "secreto" not in r.text
    assert "meu segredo" not in caplog.text and "secreto" not in caplog.text
    assert "event=chat_falhou" in caplog.text
    assert cliente.app.state.chat.reservar(SESSAO)  # type: ignore[attr-defined]


def test_stream_sem_resultado_vira_erro(cliente: TestClient, falso: FalsoClaude) -> None:
    falso.linhas = falso.linhas[:10]
    eventos = _eventos(_mensagem(cliente, sessao=SESSAO).text)
    assert eventos[-1] == {"tipo": "erro", "texto": MSG_ERRO}
    assert cliente.app.state.chat.reservar(SESSAO)  # type: ignore[attr-defined]


def test_origem_estrangeira_bloqueada(cliente: TestClient, falso: FalsoClaude) -> None:
    r = cliente.post("/consultor/mensagem", json={"texto": "oi"}, headers={"origin": "https://mal.example"})
    assert r.status_code == 403
    assert falso.chamadas == []


def test_chat_desativado_da_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONSULTORFIN_CHAT", raising=False)
    with TestClient(create_app(tmp_path / "t.db")) as c:
        assert c.post("/consultor/mensagem", json={"texto": "oi"}).status_code == 404
        assert c.post("/consultor/confirmar",
                      json={"sessao": SESSAO, "token": "t", "aceitar": True}).status_code == 404
    monkeypatch.setenv("CONSULTORFIN_CHAT", "true")
    with TestClient(create_app(tmp_path / "t.db")) as c:
        assert c.post("/consultor/mensagem", json={"texto": "oi"}).status_code == 404


def _cliente_sobe_com_chat(tmp_path: Path) -> int:
    with TestClient(create_app(tmp_path / "t.db")) as c:
        return c.post("/consultor/mensagem", json={"texto": "oi"}).status_code


def test_porteiro_quebrado_desliga_o_chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                          caplog: pytest.LogCaptureFixture) -> None:
    from app.chat import argv as argv_mod
    monkeypatch.setenv("CONSULTORFIN_CHAT", "1")
    monkeypatch.setattr(argv_mod, "PYTHON_HOOK", tmp_path / "nao-existe" / "python.exe")
    assert _cliente_sobe_com_chat(tmp_path) == 404
    assert "event=chat_porteiro_indisponivel" in caplog.text


@pytest.mark.parametrize("conteudo", [
    {"permissions": {"allow": ["Bash(.venv/Scripts/python -m app.consultor pagar:*)"]}},
    {"permissions": {"defaultMode": "bypassPermissions"}},
    {"hooks": {"PreToolUse": []}},
    "isto não é json",
])
def test_settings_do_projeto_que_ampliam_permissao_desligam_o_chat(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, conteudo: Any) -> None:
    from app import config
    raiz = tmp_path / "raiz"
    (raiz / ".claude").mkdir(parents=True)
    texto = conteudo if isinstance(conteudo, str) else json.dumps(conteudo)
    (raiz / ".claude" / "settings.local.json").write_text(texto, encoding="utf-8")
    monkeypatch.setattr(config, "RAIZ", raiz)
    monkeypatch.setenv("CONSULTORFIN_CHAT", "1")
    assert _cliente_sobe_com_chat(tmp_path) == 404
    assert "event=chat_settings_inseguros" in caplog.text


def test_settings_neutros_mantem_o_chat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import config
    raiz = tmp_path / "raiz"
    (raiz / ".claude").mkdir(parents=True)
    (raiz / ".claude" / "settings.json").write_text(json.dumps({"permissions": {"deny": ["Read"]}}),
                                                    encoding="utf-8")
    monkeypatch.setattr(config, "RAIZ", raiz)
    monkeypatch.setenv("CONSULTORFIN_CHAT", "1")
    with TestClient(create_app(tmp_path / "t.db")) as c:
        c.app.state.claude_fn = FalsoClaude()  # type: ignore[attr-defined]
        assert c.post("/consultor/mensagem", json={"texto": "oi"}).status_code == 200


def test_settings_reais_do_repositorio_sao_seguros() -> None:
    from app.chat.verificacao import settings_do_projeto_seguros
    assert settings_do_projeto_seguros()
