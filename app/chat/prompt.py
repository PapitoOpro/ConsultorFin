"""Prompt de sistema do chat (`--append-system-prompt`): nota do modo chat + instruções do relatório.

Texto confiável (do repositório, não do usuário). `--tools Bash` remove a ferramenta Skill, então o
corpo de `.claude/skills/relatorio/SKILL.md` é injetado aqui. Falha ao ler => só a nota, nunca levanta.
"""
import sys

from app import config

NOTA_CHAT = """Modo chat: não há skills e as consultas ficam sem `--help`; só os subcomandos do consultor estão disponíveis. Nunca tente ler arquivos.
Pedido de gráfico ou relatório: siga as instruções de `relatorio` abaixo (somente a forma com heredoc); o chat mostra o cartão do relatório, então não repita o endereço.
Escritas (`importar --confirmar`, `casar`, `categorizar`, `regra`, `pagar`, `criar-conta`, `gmail-buscar`) o usuário confirma no painel, por um cartão Confirmar/Cancelar.
Se um comando de escrita for negado, diga que aguarda a confirmação do usuário no painel; nunca cite nem repita o comando.
Nunca mencione comandos, SQL nem JSON ao usuário."""


def _corpo_sem_frontmatter(texto: str) -> str:
    linhas = texto.splitlines()
    if linhas and linhas[0].strip() == "---":
        for i in range(1, len(linhas)):
            if linhas[i].strip() == "---":
                return "\n".join(linhas[i + 1:]).strip()
    return texto.strip()


def instrucoes_relatorio() -> str | None:
    caminho = config.RAIZ / ".claude" / "skills" / "relatorio" / "SKILL.md"
    try:
        return _corpo_sem_frontmatter(caminho.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        print("event=chat_skill_ausente skill=relatorio", file=sys.stderr)
        return None


def prompt_sistema() -> str:
    corpo = instrucoes_relatorio()
    return NOTA_CHAT if corpo is None else f"{NOTA_CHAT}\n\nInstruções de `relatorio`:\n\n{corpo}"
