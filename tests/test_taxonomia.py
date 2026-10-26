from pathlib import Path

from app.dominio.taxonomia import PADRAO

DOC = Path(__file__).resolve().parent.parent / "docs" / "categorias-padrao.md"


def _linhas_do_markdown() -> list[tuple[str, str, str]]:
    saida: list[tuple[str, str, str]] = []
    for linha in DOC.read_text(encoding="utf-8").splitlines():
        if not linha.startswith("| ") or linha.startswith("| Tipo"):
            continue
        tipo, cat, sub = (c.strip() for c in linha.strip().strip("|").split("|"))
        saida.append(("entrada" if tipo == "Entrada" else "saida", cat, sub))
    return saida


def test_padrao_igual_ao_markdown_na_mesma_ordem() -> None:
    assert list(PADRAO) == _linhas_do_markdown()


def test_contagens_e_unicidade() -> None:
    entradas = [r for r in PADRAO if r[0] == "entrada"]
    saidas = [r for r in PADRAO if r[0] == "saida"]
    assert (len(entradas), len(saidas)) == (21, 121)
    assert len({r[1] for r in entradas}) == 6
    assert len({r[1] for r in saidas}) == 23
    assert len(set(PADRAO)) == len(PADRAO)
    assert {r[0] for r in PADRAO} == {"entrada", "saida"}
