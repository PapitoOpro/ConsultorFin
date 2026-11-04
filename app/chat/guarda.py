"""Porteiro dos comandos Bash do chat (hook PreToolUse do `claude -p`).

Por que existe: com `--tools Bash`, o Claude Code ainda aprova sozinho comandos "somente leitura"
(`cat`, `head`, `echo`, inclusive encadeados após um comando permitido), então
`cat data/senhas.key` rodaria sem passar pelas regras. Este hook nega tudo que não seja um
subcomando do consultor com argumentos simples, e aprova o `relatorio --spec - <<'EOF'` (o
heredoc com JSON é bloqueado pela checagem "brace with quote character" do Claude Code, que
nenhuma regra de prefixo libera). Consultas e escritas bem formadas seguem para as regras do
`--allowedTools` (escritas só passam com a regra exata dada após o "Confirmar").

Roda como script (`python app/chat/guarda.py`): só biblioteca padrão. Qualquer falha => exit 2
(bloqueia a ferramenta).
"""
import json
import re
import sys
from pathlib import Path

CAMINHO: Path = Path(__file__).resolve()
CONSULTAS: tuple[str, ...] = ("saldos", "resumo", "vencimentos", "projecao", "gastos", "lancamentos",
                              "faturas", "holerites", "contas", "categorias", "relatorio")
ESCRITAS: tuple[str, ...] = ("importar", "casar", "categorizar", "regra", "pagar", "criar-conta", "gmail-buscar")
PREFIXOS: tuple[str, ...] = (".venv/Scripts/python", ".venv/bin/python")
PERMITIR = "allow"
NEGAR = "deny"
LIMITE_COMANDO = 20_000

_PREFIXO = r"\.venv/(?:Scripts|bin)/python -m app\.consultor "
# Palavra sem metacaractere de shell; aspas duplas sem expansão; aspas simples literais.
_TOKEN = r"""(?:[\w./:,=+@%-]|"[^"$`\\!\n]*"|'[^'\n]*')+"""
_SIMPLES = re.compile(rf"{_PREFIXO}(?P<sub>[a-z-]+)(?P<resto>(?: {_TOKEN})*)")
_HEREDOC = re.compile(rf"{_PREFIXO}relatorio --spec - <<'(?P<tag>[A-Z_]{{1,20}})'\n(?P<corpo>.*)\n(?P=tag)\n?",
                      re.DOTALL)
_OPCOES_PROIBIDAS = re.compile(r"(?:^| )--(?:db|hoje)\b")


def _simples(comando: str) -> str | None:
    """Subcomando de um comando do consultor bem formado (sem shell extra), ou None."""
    m = None if "\r" in comando or "\x00" in comando else _SIMPLES.fullmatch(comando)
    if m is None or m["sub"] not in CONSULTAS + ESCRITAS or _OPCOES_PROIBIDAS.search(m["resto"]):
        return None
    return m["sub"]


def _heredoc_ok(comando: str) -> bool:
    m = _HEREDOC.fullmatch(comando)
    return m is not None and all(linha.strip() != m["tag"] for linha in m["corpo"].split("\n"))


def decidir(comando: str) -> str | None:
    """`allow` (relatório por heredoc), `deny` ou None (deixa para as regras do --allowedTools)."""
    if len(comando) > LIMITE_COMANDO or "\r" in comando or "\x00" in comando:
        return NEGAR
    if _heredoc_ok(comando):
        return PERMITIR
    return None if _simples(comando) is not None else NEGAR


def subcomando_escrita(comando: str) -> str | None:
    """Nome da escrita se `comando` é uma escrita do consultor bem formada (candidata a cartão)."""
    sub = _simples(comando)
    return sub if sub in ESCRITAS else None


def _resposta(decisao: str) -> str:
    motivo = ("Relatório (somente leitura)." if decisao == PERMITIR
              else "O chat só pode rodar os comandos do consultor.")
    return json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decisao,
                                              "permissionDecisionReason": motivo}})


def main() -> int:
    try:
        dados = json.load(sys.stdin)
        if dados.get("tool_name") != "Bash":
            decisao: str | None = NEGAR
        else:
            comando = dados["tool_input"]["command"]
            decisao = decidir(comando) if isinstance(comando, str) else NEGAR
    except Exception as exc:  # noqa: BLE001 - falha do porteiro bloqueia a ferramenta
        sys.stderr.write(f"event=chat_porteiro_falhou erro={type(exc).__name__}\n")
        return 2
    if decisao is not None:
        sys.stdout.write(_resposta(decisao) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
