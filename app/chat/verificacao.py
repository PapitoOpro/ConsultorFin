"""Checagens de partida do chat: sem porteiro funcionando ou com settings que ampliam permissão,
o chat não é registrado (o balão do terminal continua)."""
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from app import config
from app.chat.argv import comando_hook

log = logging.getLogger(__name__)

_SONDA = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                     "tool_input": {"command": "cat data/senhas.key"}})


def porteiro_responde() -> bool:
    """Roda o comando exato do hook (pelo shell, como o Claude Code) e exige a negação."""
    try:
        r = subprocess.run(comando_hook(), shell=True, input=_SONDA, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30, cwd=config.RAIZ)
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode == 2:
        return True
    try:
        return bool(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny")
    except (ValueError, KeyError, TypeError):
        return False


def _chave_insegura(dados: Any) -> str | None:
    """Nome da chave que amplia permissão ou enfraquece o porteiro; None se for neutro."""
    if not isinstance(dados, dict):
        return "conteudo"
    permissoes = dados.get("permissions") or {}
    if not isinstance(permissoes, dict):
        return "permissions"
    if permissoes.get("allow"):
        return "permissions.allow"
    if permissoes.get("defaultMode") not in (None, "default"):
        return "permissions.defaultMode"
    if dados.get("hooks"):
        return "hooks"
    if dados.get("disableAllHooks"):
        return "disableAllHooks"
    if dados.get("env"):
        return "env"
    return None


def settings_inseguros(raiz: Path | None = None) -> tuple[str, str] | None:
    """(arquivo, chave) do primeiro `.claude/settings*.json` inseguro; None se todos são neutros."""
    for arquivo in sorted(((raiz or config.RAIZ) / ".claude").glob("settings*.json")):
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return arquivo.name, "ilegivel"
        chave = _chave_insegura(dados)
        if chave:
            return arquivo.name, chave
    return None


def settings_do_projeto_seguros(raiz: Path | None = None) -> bool:
    """`.claude/settings*.json` sem `permissions.allow`, `defaultMode` diferente, `hooks`,
    `disableAllHooks` ou `env`."""
    return settings_inseguros(raiz) is None


def chat_pode_iniciar() -> bool:
    problema = settings_inseguros()
    if problema:
        log.error("event=chat_settings_inseguros arquivo=%s chave=%s", *problema)
        return False
    if not porteiro_responde():
        log.error("event=chat_porteiro_indisponivel")
        return False
    return True
