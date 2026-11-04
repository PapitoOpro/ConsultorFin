"""Leitura de fatura de cartão em PDF (local, sem rede)."""

from app.importacao.fatura.deteccao import detectar
from app.importacao.fatura.modelo import EmissorNaoSuportado, FaturaLida
from app.importacao.fatura.texto import TOLERANCIA_APERTADA, abrir_pdf


def ler_fatura(conteudo: bytes, senha: str | None = None) -> FaturaLida:
    """Abre o PDF em memória, detecta o emissor e lê a fatura. Se o emissor não for
    reconhecido, tenta de novo com espaçamento apertado (PDFs que perdem os espaços)."""
    paginas = abrir_pdf(conteudo, senha)
    try:
        return detectar(paginas).ler(paginas)
    except EmissorNaoSuportado:
        paginas = abrir_pdf(conteudo, senha, TOLERANCIA_APERTADA)
        return detectar(paginas).ler(paginas)
