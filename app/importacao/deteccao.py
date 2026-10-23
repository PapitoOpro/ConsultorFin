"""Detecção do formato do arquivo de extrato."""

from app.importacao.base import Parser
from app.importacao.itau_csv import ParserItauCsv, tem_cabecalho_itau


def detectar(conteudo: bytes, nome: str) -> Parser | None:
    """Retorna o parser adequado ao arquivo, ou None se o formato não for reconhecido."""
    if nome.lower().endswith(".csv") and tem_cabecalho_itau(conteudo):
        return ParserItauCsv()
    return None
