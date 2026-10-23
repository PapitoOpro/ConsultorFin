import argparse
import re
from pathlib import Path

import pytest

from app.consultor.cli import construir_parser

RAIZ = Path(__file__).resolve().parent.parent
SKILLS = RAIZ / ".claude" / "skills"
CLAUDE_MD = RAIZ / "CLAUDE.md"
ESPERADAS = {"saldo", "vencimentos", "projecao", "gastos", "importar", "categorizar", "painel"}
REGRA_CREDENCIAIS = (
    "Nunca peça, receba, repita, grave ou versione senha, token ou chave de acesso de banco. "
    "Se o usuário colar uma, avise para trocá-la e não a use. "
    "Este projeto só importa arquivos de extrato.")
PADROES_CREDENCIAL = [r"password\s*=", r"senha\s*[:=]\s*\S+", r"sk-[A-Za-z0-9]{10,}",
                      r"ghp_[A-Za-z0-9]{10,}"]
COMANDO = re.compile(r"python -m app\.consultor\s+(?:--(?:db|hoje)\s+\S+\s+)*([a-z][a-z-]*)")


def _subcomandos() -> set[str]:
    for acao in construir_parser()._actions:
        if isinstance(acao, argparse._SubParsersAction):
            return set(acao.choices)
    raise AssertionError("parser sem subcomandos")


def _frontmatter(texto: str) -> dict[str, str]:
    m = re.match(r"---\r?\n(.*?)\r?\n---\r?\n", texto, re.S)
    assert m, "frontmatter ausente"
    campos: dict[str, str] = {}
    for linha in m.group(1).splitlines():
        chave, sep, valor = linha.partition(":")
        assert sep, f"linha inválida no frontmatter: {linha!r}"
        campos[chave.strip()] = valor.strip()
    return campos


def _arquivos_texto() -> list[Path]:
    return [CLAUDE_MD, *sorted(p for p in (RAIZ / ".claude").rglob("*") if p.is_file())]


def test_skills_esperadas_existem() -> None:
    assert {p.name for p in SKILLS.iterdir() if p.is_dir()} == ESPERADAS


@pytest.mark.parametrize("nome", sorted(ESPERADAS))
def test_frontmatter_valido(nome: str) -> None:
    campos = _frontmatter((SKILLS / nome / "SKILL.md").read_text(encoding="utf-8"))
    assert campos["name"] == nome
    assert campos["description"]


def test_comandos_citados_existem() -> None:
    validos = _subcomandos()
    citados: set[str] = set()
    for arq in _arquivos_texto():
        if arq.suffix == ".md":
            citados.update(COMANDO.findall(arq.read_text(encoding="utf-8")))
    assert citados, "nenhum comando citado"
    assert citados <= validos, citados - validos
    for nome in ESPERADAS - {"painel", "gastos"}:
        assert COMANDO.search((SKILLS / nome / "SKILL.md").read_text(encoding="utf-8")), nome


def test_claude_md_tem_modos_e_regra_de_credenciais() -> None:
    texto = CLAUDE_MD.read_text(encoding="utf-8")
    assert re.search(r"^#+ .*Modo consultor", texto, re.M)
    assert re.search(r"^#+ .*Modo desenvolvimento", texto, re.M)
    assert texto.index("Modo consultor") < texto.index("Modo desenvolvimento")
    assert REGRA_CREDENCIAIS in [p.strip() for p in re.split(r"\n\s*\n", texto)]
    assert "## 12. Ordem de trabalho da v0.1" in texto


@pytest.mark.parametrize("arq", _arquivos_texto(), ids=lambda p: p.name)
def test_sem_padrao_de_credencial(arq: Path) -> None:
    texto = arq.read_text(encoding="utf-8")
    for padrao in PADROES_CREDENCIAL:
        assert not re.search(padrao, texto, re.I), (arq.name, padrao)
