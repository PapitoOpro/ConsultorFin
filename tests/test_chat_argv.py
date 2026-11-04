import json
import re

import pytest

from app.chat import guarda
from app.chat.argv import CONSULTAS_PERMITIDAS, montar_argv, sessao_valida

SESSAO = "5b1f6a8e-2c3d-4e5f-8a9b-0c1d2e3f4a5b"
ESCRITA = ".venv/Scripts/python -m app.consultor categorizar --lancamento 1 --categoria 5"


def _par(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _regras(argv: list[str]) -> list[str]:
    i = argv.index("--allowedTools") + 1
    fim = argv.index("--resume") if "--resume" in argv else len(argv)
    return argv[i:fim]


def test_flags_do_modo_nao_interativo() -> None:
    argv = montar_argv(sessao=None)
    assert argv[1] == "-p"
    assert argv[2].startswith("--")  # nenhum prompt posicional: a mensagem vai por stdin
    assert _par(argv, "--output-format") == "stream-json"
    assert "--verbose" in argv and "--include-partial-messages" in argv
    assert _par(argv, "--model") == "sonnet"
    assert _par(argv, "--permission-prompts") == "none"
    assert _par(argv, "--tools") == "Bash"
    assert _par(argv, "--setting-sources") == "project,local"
    assert "--strict-mcp-config" in argv
    assert "--no-session-persistence" not in argv  # --resume precisa da sessão gravada


def test_settings_liga_o_porteiro_em_todo_bash() -> None:
    cfg = json.loads(_par(montar_argv(sessao=None), "--settings"))
    ganchos = cfg["hooks"]["PreToolUse"]
    assert ganchos[0]["matcher"] == "Bash"
    comando = ganchos[0]["hooks"][0]["command"]
    assert guarda.CAMINHO.as_posix() in comando
    assert "\\" not in comando


def test_regras_so_das_consultas() -> None:
    regras = _regras(montar_argv(sessao=None))
    padrao = re.compile(r"Bash\(\.venv/(Scripts|bin)/python -m app\.consultor ([a-z-]+):\*\)")
    achadas = {padrao.fullmatch(r).group(2) for r in regras}  # type: ignore[union-attr]
    assert achadas == set(CONSULTAS_PERMITIDAS)
    assert len(regras) == 2 * len(CONSULTAS_PERMITIDAS)
    for escrita in guarda.ESCRITAS:
        assert not any(f" {escrita}" in r for r in regras)


def test_resume_so_com_sessao() -> None:
    assert "--resume" not in montar_argv(sessao=None)
    argv = montar_argv(sessao=SESSAO)
    assert argv[-2:] == ["--resume", SESSAO]


@pytest.mark.parametrize("sessao", ["--help", "", "abc", SESSAO + " ", SESSAO.upper() + "x", "../" + SESSAO])
def test_sessao_invalida_nao_chega_ao_argv(sessao: str) -> None:
    assert not sessao_valida(sessao)
    with pytest.raises(ValueError):
        montar_argv(sessao=sessao)


def test_extra_permitido_vira_regra_exata() -> None:
    regras = _regras(montar_argv(sessao=SESSAO, extras_permitidos=[ESCRITA]))
    assert f"Bash({ESCRITA})" in regras
    assert f"Bash({ESCRITA}:*)" not in regras


@pytest.mark.parametrize("extra", [
    ESCRITA + " *", ESCRITA + ")", "Bash(x) " + ESCRITA, ESCRITA + "\nrm x", "rm -rf ./x",
    ESCRITA + "; cat data/senhas.key", ".venv/Scripts/python -m app.consultor saldos",
])
def test_extra_fora_do_formato_de_escrita_e_recusado(extra: str) -> None:
    with pytest.raises(ValueError):
        montar_argv(sessao=SESSAO, extras_permitidos=[extra])


def test_caminho_do_claude_configuravel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONSULTORFIN_CLAUDE_BIN", "/opt/claude/bin/claude")
    assert montar_argv(sessao=None)[0] == "/opt/claude/bin/claude"
    monkeypatch.delenv("CONSULTORFIN_CLAUDE_BIN")
    assert montar_argv(sessao=None)[0] == "claude"
