"""Porteiro dos comandos do chat (hook PreToolUse do `claude -p`, matcher "*").

Por que existe: com `--tools Bash`, o Claude Code ainda aprova sozinho comandos "somente leitura"
(`cat`, `head`, `echo`, inclusive encadeados após um comando permitido), então
`cat data/senhas.key` rodaria sem passar pelas regras. Este hook nega tudo que não seja um
subcomando do consultor cujas opções estão na tabela `OPCOES` (cada token é conferido já sem
aspas: `--db`/`--hoje`, abreviações e opções estranhas são negados), e aprova o
`relatorio --spec - <<'EOF'` (o heredoc com JSON é bloqueado pela checagem "brace with quote
character" do Claude Code, que nenhuma regra de prefixo libera). Consultas bem formadas seguem
para as regras do `--allowedTools`; escritas não estão nas regras e viram cartão "Confirmar".

Escrita confirmada: o servidor passa o comando exato em CONSULTORFIN_CONFIRMADO e um arquivo-marca
(numa pasta só do turno) em CONSULTORFIN_CONFIRMADO_MARCA; o hook aprova esse comando uma única
vez (criação exclusiva da marca) e nega as repetições.

Roda como script (`python app/chat/guarda.py`): só biblioteca padrão. Qualquer falha => exit 2
(bloqueia a ferramenta).
"""
import json
import os
import re
import shlex
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

CAMINHO: Path = Path(__file__).resolve()
CONSULTAS: tuple[str, ...] = ("saldos", "resumo", "vencimentos", "projecao", "gastos", "lancamentos",
                              "faturas", "holerites", "contas", "categorias", "relatorio")
ESCRITAS: tuple[str, ...] = ("importar", "casar", "categorizar", "regra", "pagar", "criar-conta", "gmail-buscar")
PREFIXOS: tuple[str, ...] = (".venv/Scripts/python", ".venv/bin/python")
PERMITIR = "allow"
NEGAR = "deny"
LIMITE_COMANDO = 20_000
VAR_CONFIRMADO = "CONSULTORFIN_CONFIRMADO"
VAR_MARCA = "CONSULTORFIN_CONFIRMADO_MARCA"

# subcomando -> (opções com valor, opções booleanas). Sem --db/--hoje: o chat usa sempre o banco do app.
OPCOES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "saldos": ((), ()),
    "resumo": (("--mes",), ()),
    "vencimentos": (("--dias",), ()),
    "projecao": (("--meses",), ()),
    "gastos": (("--mes",), ()),
    "lancamentos": (("--de", "--ate", "--conta", "--categoria", "--tipo", "--status", "--texto", "--limite"), ()),
    "faturas": (("--conta",), ()),
    "holerites": (("--ano", "--item"), ()),
    "relatorio": (("--spec",), ()),  # no chat: só `exemplo` ou o heredoc (ver decidir)
    "contas": ((), ()),
    "categorias": ((), ()),
    "importar": (("--arquivo", "--conta"), ("--confirmar",)),
    "casar": (("--pendente", "--linha"), ()),
    "categorizar": (("--lancamento", "--categoria"), ()),
    "regra": (("--padrao", "--categoria"), ()),
    "pagar": (("--lancamento", "--valor", "--data"), ()),
    "criar-conta": (("--nome", "--tipo", "--saldo-inicial", "--data-saldo"), ()),
    "gmail-buscar": (("--mes", "--ate"), ()),
}
_VER_ID = re.compile(r"ver|\d{1,9}")
# subcomando -> (padrão de cada posicional, quantidade máxima)
POSICIONAIS: dict[str, tuple[re.Pattern[str], int]] = {
    "faturas": (_VER_ID, 2), "holerites": (_VER_ID, 2), "relatorio": (re.compile("exemplo"), 1)}

_PREFIXO = r"\.venv/(?:Scripts|bin)/python -m app\.consultor "
# Palavra sem metacaractere de shell; aspas duplas sem expansão; aspas simples literais.
_TOKEN = r"""(?:[\w./:,=+@%-]|"[^"$`\\!\n]*"|'[^'\n]*')+"""
_SIMPLES = re.compile(rf"{_PREFIXO}(?P<sub>[a-z-]+)(?P<resto>(?: {_TOKEN})*)")
_HEREDOC = re.compile(rf"{_PREFIXO}relatorio --spec - <<'(?P<tag>[A-Z_]{{1,20}})'\n(?P<corpo>.*)\n(?P=tag)\n?",
                      re.DOTALL)
_NEGATIVO = re.compile(r"-\d+|-\d*\.\d+")  # o que o argparse aceita como valor começando com "-"


@dataclass(frozen=True)
class Analise:
    subcomando: str
    opcoes: dict[str, str | bool] = field(default_factory=dict)
    posicionais: tuple[str, ...] = ()


def _posicional_ok(sub: str, tok: str, ja_tem: int) -> bool:
    padrao, maximo = POSICIONAIS.get(sub, (None, 0))
    return padrao is not None and ja_tem < maximo and padrao.fullmatch(tok) is not None


def analisar(comando: str) -> Analise | None:
    """Comando do consultor bem formado (sem shell extra, opções da tabela), ou None."""
    if len(comando) > LIMITE_COMANDO or "\r" in comando or "\x00" in comando:
        return None
    m = _SIMPLES.fullmatch(comando)
    if m is None or m["sub"] not in OPCOES:
        return None
    try:
        tokens = shlex.split(m["resto"])
    except ValueError:
        return None
    com_valor, booleanas = OPCOES[m["sub"]]
    opcoes: dict[str, str | bool] = {}
    posicionais: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("-"):
            if tok in opcoes:
                return None
            if tok in booleanas:
                opcoes[tok] = True
            elif tok in com_valor and i + 1 < len(tokens) and \
                    (not tokens[i + 1].startswith("-") or _NEGATIVO.fullmatch(tokens[i + 1])):
                opcoes[tok] = tokens[i + 1]
                i += 1
            else:
                return None
        elif _posicional_ok(m["sub"], tok, len(posicionais)):
            posicionais.append(tok)
        else:
            return None
        i += 1
    return Analise(m["sub"], opcoes, tuple(posicionais))


def _heredoc_ok(comando: str) -> bool:
    m = _HEREDOC.fullmatch(comando)
    return m is not None and all(linha.strip() != m["tag"] for linha in m["corpo"].split("\n"))


def decidir(comando: str) -> str | None:
    """`allow` (relatório por heredoc), `deny` ou None (deixa para as regras do --allowedTools)."""
    if len(comando) > LIMITE_COMANDO or "\r" in comando or "\x00" in comando:
        return NEGAR
    if _heredoc_ok(comando):
        return PERMITIR
    a = analisar(comando)
    if a is None:
        return NEGAR
    if a.subcomando == "relatorio" and (a.opcoes or a.posicionais != ("exemplo",)):
        return NEGAR  # `--spec <arquivo>` abriria qualquer arquivo: no chat só `exemplo` ou o heredoc
    return None


def subcomando_escrita(comando: str) -> str | None:
    """Nome da escrita se `comando` é uma escrita do consultor bem formada (candidata a cartão)."""
    a = analisar(comando)
    return a.subcomando if a is not None and a.subcomando in ESCRITAS else None


def _marcar(caminho: str) -> bool:
    try:
        os.close(os.open(caminho, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except OSError:
        return False
    return True


def decidir_turno(comando: str, ambiente: Mapping[str, str]) -> str | None:
    """`decidir` + a escrita confirmada neste turno, aprovada uma única vez."""
    decisao = decidir(comando)
    confirmado, marca = ambiente.get(VAR_CONFIRMADO), ambiente.get(VAR_MARCA)
    if decisao is None and confirmado and marca and comando == confirmado and subcomando_escrita(comando):
        return PERMITIR if _marcar(marca) else NEGAR
    return decisao


def _resposta(decisao: str) -> str:
    motivo = ("Aprovado pelo porteiro do chat." if decisao == PERMITIR
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
            decisao = decidir_turno(comando, os.environ) if isinstance(comando, str) else NEGAR
    except Exception as exc:  # noqa: BLE001 - falha do porteiro bloqueia a ferramenta
        sys.stderr.write(f"event=chat_porteiro_falhou erro={type(exc).__name__}\n")
        return 2
    if decisao is not None:
        sys.stdout.write(_resposta(decisao) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
