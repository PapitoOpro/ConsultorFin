"""Detecção do emissor da fatura: cada parser registrado diz se reconhece o texto."""

from typing import Protocol

from app.importacao.fatura.itau import ParserItau
from app.importacao.fatura.modelo import EmissorNaoSuportado, FaturaLida
from app.importacao.fatura.texto import PaginaTexto


class ParserFatura(Protocol):
    emissor: str

    def reconhece(self, paginas: list[PaginaTexto]) -> bool: ...

    def ler(self, paginas: list[PaginaTexto]) -> FaturaLida: ...


# Novo emissor = uma linha aqui.
PARSERS: list[ParserFatura] = [
    ParserItau(),
]


def detectar(paginas: list[PaginaTexto]) -> ParserFatura:
    for parser in PARSERS:
        if parser.reconhece(paginas):
            return parser
    raise EmissorNaoSuportado("Emissor da fatura não reconhecido (suportados: Itaú).")
