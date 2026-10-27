"""Abertura local do PDF da fatura (única parte que usa pdfplumber)."""

import io
from dataclasses import dataclass

import pdfplumber

from app.importacao.fatura.modelo import PdfInvalido, PdfProtegido, PdfSemTexto, SenhaIncorreta

DIVISAO_COLUNAS = 0.57  # Itaú: 2 colunas; corte em 57% da largura


@dataclass(frozen=True)
class PaginaTexto:
    numero: int
    texto: str
    esquerda: str
    direita: str


def _recorte(pagina: "pdfplumber.page.Page", x0: float, x1: float) -> str:
    _, topo, _, base = pagina.bbox
    return pagina.crop((x0, topo, x1, base)).extract_text() or ""


def _abrir(conteudo: bytes, senha: str | None) -> list[PaginaTexto]:
    with pdfplumber.open(io.BytesIO(conteudo), password=senha) as pdf:
        paginas: list[PaginaTexto] = []
        for i, p in enumerate(pdf.pages, 1):
            x0, _, x1, _ = p.bbox  # mediabox pode não começar em 0
            corte = x0 + (x1 - x0) * DIVISAO_COLUNAS
            paginas.append(PaginaTexto(
                numero=i,
                texto=p.extract_text() or "",
                esquerda=_recorte(p, x0, corte),
                direita=_recorte(p, corte, x1),
            ))
        return paginas


def abrir_pdf(conteudo: bytes, senha: str | None = None) -> list[PaginaTexto]:
    """Lê o PDF em memória. A senha nunca entra em mensagens de erro."""
    try:
        paginas = _abrir(conteudo, senha or None)
    except Exception as e:  # mensagem original nunca é repassada (pode ter conteúdo)
        nomes = [type(e).__name__] + [type(a).__name__ for a in e.args]
        if any("Password" in n for n in nomes):
            if senha:
                raise SenhaIncorreta("Senha do PDF incorreta.") from None
            raise PdfProtegido("O PDF é protegido; informe a senha.") from None
        raise PdfInvalido("O arquivo não é um PDF válido.") from None
    if not any(p.texto.strip() for p in paginas):
        raise PdfSemTexto("O PDF não tem texto (pode ser imagem escaneada).")
    return paginas
