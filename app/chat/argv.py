"""Linha de comando do `claude -p` usada pelo chat.

Verificado com chamadas reais (Claude Code 2.1.317, 2026-11-03), rodando na raiz do repositório:
- A mensagem vai por stdin (nunca no argv); `-p` lê o prompt da entrada.
- `--permission-prompts none`: o que pediria aprovação é negado na hora e aparece em
  `permission_denials` do evento `result` (com `tool_input.command`); nada é executado.
- Regra de prefixo `Bash(<prefixo> -m app.consultor <consulta>:*)` libera a consulta com
  argumentos (`permission_decision.source=config`). Regra exata `Bash(<comando>)` libera só ele.
- Heredoc com JSON (`relatorio --spec - <<'EOF' {...} EOF`) é NEGADO mesmo com a regra de prefixo:
  "Contains brace with quote character (expansion obfuscation)". Quem libera é o porteiro
  (`app/chat/guarda.py`, hook PreToolUse via `--settings`), só no formato exato.
- `--tools Bash` tira Read/Glob/Grep/Edit/Write, mas comandos de shell "somente leitura"
  (`head -c 30 README.md`, `echo`, e até `<consulta>; head ...`) rodam sem regra nenhuma. Por isso o
  porteiro nega todo Bash que não seja um subcomando do consultor com argumentos simples.
- Sem `--setting-sources`, os hooks/plugins/MCP do perfil do usuário carregam (`hook_started`
  no stream). `--setting-sources project,local --strict-mcp-config` corta hooks, plugins e MCP do
  usuário; o `CLAUDE.md` e as skills de `.claude/skills` do projeto continuam carregando.
- `--resume <id>` exige sessão gravada: não usar `--no-session-persistence`.
"""
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from app import config
from app.chat.guarda import CAMINHO, CONSULTAS, PREFIXOS, subcomando_escrita

CONSULTAS_PERMITIDAS: tuple[str, ...] = CONSULTAS
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_PROIBIDO_EM_REGRA = re.compile(r"[()*\n\r]")


def sessao_valida(sessao: str) -> bool:
    return isinstance(sessao, str) and _UUID.fullmatch(sessao) is not None


def _settings() -> str:
    """Liga o porteiro em todo uso do Bash (aspas e barras normais: funciona no bash e no cmd)."""
    comando = f'"{Path(sys.executable).as_posix()}" "{CAMINHO.as_posix()}"'
    return json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash",
                                                 "hooks": [{"type": "command", "command": comando}]}]}})


def _regra_extra(comando: str) -> str:
    if _PROIBIDO_EM_REGRA.search(comando) or subcomando_escrita(comando) is None:
        raise ValueError("Comando de confirmação fora do formato permitido.")
    return f"Bash({comando})"


def montar_argv(*, sessao: str | None, extras_permitidos: Sequence[str] = ()) -> list[str]:
    if sessao is not None and not sessao_valida(sessao):
        raise ValueError("Sessão inválida.")
    regras = [f"Bash({p} -m app.consultor {c}:*)" for c in CONSULTAS_PERMITIDAS for p in PREFIXOS]
    regras += [_regra_extra(c) for c in extras_permitidos]
    argv = [config.caminho_claude(), "-p", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--model", config.MODELO_CHAT, "--permission-prompts", "none",
            "--tools", "Bash", "--setting-sources", "project,local", "--strict-mcp-config",
            "--settings", _settings(), "--allowedTools", *regras]
    if sessao is not None:
        argv += ["--resume", sessao]
    return argv
