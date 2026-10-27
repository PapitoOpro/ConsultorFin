"""Leitura de fatura de cartão em PDF (local, sem rede)."""

from app.importacao.fatura.deteccao import detectar
from app.importacao.fatura.modelo import FaturaLida
from app.importacao.fatura.texto import abrir_pdf


def ler_fatura(conteudo: bytes, senha: str | None = None) -> FaturaLida:
    """Abre o PDF em memória, detecta o emissor e lê a fatura."""
    paginas = abrir_pdf(conteudo, senha)
    return detectar(paginas).ler(paginas)
