"""Auxiliar de testes: páginas de fatura a partir de fixtures de texto sintético."""

from pathlib import Path

from app.importacao.fatura.texto import PaginaTexto

PASTA = Path(__file__).parent / "fixtures" / "faturas"


def paginas_de_fixture(nome: str) -> list[PaginaTexto]:
    """Lê <nome>.txt com marcadores @@pagina, @@esq e @@dir."""
    paginas: list[PaginaTexto] = []
    blocos: dict[str, list[str]] = {}
    atual: str | None = None

    def fechar() -> None:
        if atual is None:
            return
        texto = "\n".join(blocos.get("texto", []))
        paginas.append(PaginaTexto(
            numero=len(paginas) + 1,
            texto=texto,
            esquerda="\n".join(blocos.get("esq", [])),
            direita="\n".join(blocos.get("dir", [])),
        ))

    conteudo = (PASTA / f"{nome}.txt").read_text(encoding="utf-8")
    for linha in conteudo.splitlines():
        marca = linha.strip()
        if marca == "@@pagina":
            fechar()
            atual, blocos = "texto", {}
        elif marca in ("@@esq", "@@dir"):
            atual = marca[2:]
            blocos.setdefault(atual, [])
        elif atual is not None:
            blocos.setdefault(atual, []).append(linha)
    fechar()
    return paginas
