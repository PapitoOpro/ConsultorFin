"""Abertura local do PDF do holerite (única parte que usa pdfplumber)."""

import io
from dataclasses import dataclass

import pdfplumber

from app.importacao.holerite.modelo import PdfComSenha, PdfInvalido, PdfSemTexto

MSG_COM_SENHA = ("PDF com senha não é suportado para holerite; remova a senha antes de "
                 "enviar.")


@dataclass(frozen=True)
class Palavra:
    x0: float
    x1: float
    topo: float
    texto: str


def _cortar_via_duplicada(palavras: list[Palavra]) -> list[Palavra]:
    """A via do funcionário vem repetida: descarta tudo a partir do 2º cabeçalho da tabela."""
    cabecalhos = sorted(p.topo for p in palavras if p.texto == "Descrição")
    if len(cabecalhos) < 2:
        return palavras
    corte = cabecalhos[1] - 1
    return [p for p in palavras if p.topo < corte]


def _abrir(conteudo: bytes) -> list[list[Palavra]]:
    with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
        paginas: list[list[Palavra]] = []
        for pagina in pdf.pages:
            palavras = [
                Palavra(float(w["x0"]), float(w["x1"]), float(w["top"]), str(w["text"]))
                for w in pagina.extract_words()
            ]
            paginas.append(_cortar_via_duplicada(palavras))
        return paginas


def abrir_pdf(conteudo: bytes) -> list[list[Palavra]]:
    """Lê o PDF em memória: uma lista de palavras por página. Erros nunca repassam conteúdo."""
    try:
        paginas = _abrir(conteudo)
    except Exception as e:  # mensagem original nunca é repassada (pode ter conteúdo)
        nomes = [type(e).__name__] + [type(a).__name__ for a in e.args]
        if any("Password" in n for n in nomes):
            raise PdfComSenha(MSG_COM_SENHA) from None
        raise PdfInvalido("O arquivo não é um PDF válido.") from None
    if not any(p.texto.strip() for pagina in paginas for p in pagina):
        raise PdfSemTexto("O PDF não tem texto (pode ser imagem escaneada).")
    return paginas
