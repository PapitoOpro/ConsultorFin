"""Linha de comando do `claude -p` usada pelo chat.

Verificado com chamadas reais (Claude Code 2.1.317, 2026-11-03/04), rodando na raiz do repositório:
- A mensagem vai por stdin (nunca no argv); `-p` lê o prompt da entrada.
- `--permission-prompts none`: o que pediria aprovação é negado na hora e aparece em
  `permission_denials` do evento `result` (com `tool_input.command`); nada é executado.
- Regra de prefixo `Bash(<prefixo> -m app.consultor <consulta>:*)` libera a consulta com
  argumentos (`permission_decision.source=config`).
- Heredoc com JSON (`relatorio --spec - <<'EOF' {...} EOF`) é NEGADO mesmo com a regra de prefixo:
  "Contains brace with quote character (expansion obfuscation)". Quem libera é o porteiro
  (`app/chat/guarda.py`, hook PreToolUse via `--settings`), só no formato exato.
- `--tools Bash` tira Read/Glob/Grep/Edit/Write, mas comandos de shell "somente leitura"
  (`head -c 30 README.md`, `echo`, e até `<consulta>; head ...`) rodam sem regra nenhuma. Por isso o
  porteiro (matcher "*") nega tudo que não seja um subcomando do consultor da tabela dele.
- Um `allow` do hook executa um comando que não está em `--allowedTools` (assim roda a escrita
  confirmada, uma única vez; o hook recebe o comando pelo ambiente do processo).
- Sem `--setting-sources`, os hooks/plugins/MCP do perfil do usuário carregam (`hook_started`
  no stream). `--setting-sources project --strict-mcp-config` corta hooks, plugins e MCP do usuário
  e ignora `.claude/settings.local.json`; o `CLAUDE.md` e as skills de `.claude/skills` do projeto
  continuam carregando.
- `--tools Bash` remove a ferramenta Skill, por isso o `--append-system-prompt` com as instruções do
  relatório (observado em produção em 2026-11-04).
- `--resume <id>` exige sessão gravada: não usar `--no-session-persistence`.
"""
import json
import re
import sys
from pathlib import Path

from app import config
from app.chat.guarda import CAMINHO, CONSULTAS, PREFIXOS
from app.chat.prompt import prompt_sistema

CONSULTAS_PERMITIDAS: tuple[str, ...] = CONSULTAS
PYTHON_HOOK: Path = Path(sys.executable)  # o mesmo Python do servidor (venv)
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def sessao_valida(sessao: str) -> bool:
    return isinstance(sessao, str) and _UUID.fullmatch(sessao) is not None


def comando_hook() -> str:
    """Comando do porteiro (aspas e barras normais: funciona no bash e no cmd)."""
    return f'"{PYTHON_HOOK.as_posix()}" "{CAMINHO.as_posix()}"'


def _settings() -> str:
    return json.dumps({"hooks": {"PreToolUse": [{"matcher": "*",
                                                 "hooks": [{"type": "command", "command": comando_hook()}]}]}})


def montar_argv(*, sessao: str | None) -> list[str]:
    if sessao is not None and not sessao_valida(sessao):
        raise ValueError("Sessão inválida.")
    regras = [f"Bash({p} -m app.consultor {c}:*)" for c in CONSULTAS_PERMITIDAS for p in PREFIXOS]
    argv = [config.caminho_claude(), "-p", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--model", config.MODELO_CHAT, "--permission-prompts", "none",
            "--tools", "Bash", "--setting-sources", "project", "--strict-mcp-config",
            "--settings", _settings(), "--append-system-prompt", prompt_sistema(),
            "--allowedTools", *regras]
    if sessao is not None:
        argv += ["--resume", sessao]
    return argv
