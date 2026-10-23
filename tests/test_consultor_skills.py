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
PADROES_CREDENCIAL = [
    r"password\s*=", r"senha\s*[:=]\s*\S+", r"sk-[A-Za-z0-9]{10,}", r"ghp_[A-Za-z0-9]{10,}",
    r"api[_-]?key", r"secret\s*[:=]", r"Bearer\s+[A-Za-z0-9._-]{16,}", r"AKIA[0-9A-Z]{16}",
    r"token\s*[:=]\s*\S{12,}"]
COMANDO = re.compile(r"python -m app\.consultor\s+(?:--(?:db|hoje)\s+\S+\s+)*([a-z][a-z-]*)")


COMANDO_COMPLETO = re.compile(
    r"python -m app\.consultor\s+(?:--(?:db|hoje)\s+\S+\s+)*([a-z][a-z-]*)([^`\n]*)")


def _subparsers() -> dict[str, argparse.ArgumentParser]:
    for acao in construir_parser()._actions:
        if isinstance(acao, argparse._SubParsersAction):
            return dict(acao.choices)
    raise AssertionError("parser sem subcomandos")


def _subcomandos() -> set[str]:
    return set(_subparsers())


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


def _ler(arq: Path) -> str | None:
    try:
        return arq.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None  # binário ou ilegível: ignorado


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
    texto = _ler(arq)
    if texto is None:
        pytest.skip("arquivo binário ou ilegível")
    for padrao in PADROES_CREDENCIAL:
        assert not re.search(padrao, texto, re.I), (arq.name, padrao)


def test_opcoes_citadas_existem() -> None:
    subs = _subparsers()
    for arq in _arquivos_texto():
        texto = _ler(arq)
        if arq.suffix != ".md" or texto is None:
            continue
        for sub, resto in COMANDO_COMPLETO.findall(texto):
            validas = {o for a in subs[sub]._actions for o in a.option_strings}
            for flag in re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", resto):
                assert flag in validas, (arq.name, sub, flag)


def test_opcoes_citadas_detectam_erro_de_digitacao() -> None:
    subs = _subparsers()
    _, resto = COMANDO_COMPLETO.findall("python -m app.consultor projecao --mes 6")[0]
    flags = re.findall(r"(--[a-z][a-z-]*)", resto)
    assert flags == ["--mes"]
    assert "--mes" not in {o for a in subs["projecao"]._actions for o in a.option_strings}


def test_sem_barra_invertida_no_caminho_do_venv() -> None:
    for arq in _arquivos_texto():
        texto = _ler(arq)
        if texto is not None:
            assert ".venv\\" not in texto, arq.name
