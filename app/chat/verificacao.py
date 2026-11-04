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


def _amplia_permissao(dados: Any) -> bool:
    if not isinstance(dados, dict):
        return True
    permissoes = dados.get("permissions") or {}
    if not isinstance(permissoes, dict):
        return True
    modo = permissoes.get("defaultMode")
    return bool(permissoes.get("allow")) or modo not in (None, "default") or bool(dados.get("hooks"))


def settings_do_projeto_seguros(raiz: Path | None = None) -> bool:
    """`.claude/settings*.json` do projeto sem `permissions.allow`, `defaultMode` diferente ou `hooks`."""
    for arquivo in sorted(((raiz or config.RAIZ) / ".claude").glob("settings*.json")):
        try:
            dados = json.loads(arquivo.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if _amplia_permissao(dados):
            return False
    return True


def chat_pode_iniciar() -> bool:
    if not settings_do_projeto_seguros():
        log.error("event=chat_settings_inseguros")
        return False
    if not porteiro_responde():
        log.error("event=chat_porteiro_indisponivel")
        return False
    return True
