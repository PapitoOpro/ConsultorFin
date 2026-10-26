"""Tipos comuns da leitura de extratos."""

from dataclasses import dataclass, field
from typing import Protocol

MAX_ECO = 30


def eco(valor: object) -> str:
    """Valor de célula para mensagens de erro: no máximo 30 caracteres, com reticências."""
    t = str(valor).strip()
    return t if len(t) <= MAX_ECO else t[:MAX_ECO - 1] + "…"


class FormatoNaoSuportado(ValueError):
    """Formato de extrato reconhecido, mas não suportado (mensagem em português)."""


@dataclass
class LinhaImportada:
    data: str  # ISO AAAA-MM-DD
    descricao: str
    valor_centavos: int


@dataclass
class ErroLeitura:
    linha: int
    mensagem: str


@dataclass
class ResultadoLeitura:
    linhas: list[LinhaImportada] = field(default_factory=list)
    saldos_dia: dict[str, int] = field(default_factory=dict)
    saldo_anterior: tuple[str, int] | None = None
    erros: list[ErroLeitura] = field(default_factory=list)
    formato: str = ""
    periodo: tuple[str, str] | None = None  # (início, fim) ISO
    conta_numero: str | None = None
    saldo_informado: tuple[str, int] | None = None  # saldo do banco em data posterior ao fim do período
    saldo_anterior_estimado: tuple[str, int] | None = None


class Parser(Protocol):
    def ler(self, conteudo: bytes) -> ResultadoLeitura: ...
