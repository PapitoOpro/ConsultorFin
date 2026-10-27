"""Armazéns temporários em memória entre a prévia e a confirmação.

A aplicação nunca grava os bytes enviados em disco nem em log (o Starlette pode usar
arquivo temporário para corpos multipart grandes). Cada item recebe um token aleatório
(secrets.token_urlsafe); expira em TTL_SEGUNDOS e cada armazém guarda no máximo
MAX_ITENS itens (os mais antigos são descartados). Processo único/local.

- ArmazemUploads: arquivo enviado (nome + bytes).
- ArmazemFaturas: fatura de cartão já lida (FaturaLida + hash do arquivo); nunca guarda
  os bytes do PDF nem a senha.
"""
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from app.importacao.fatura.modelo import FaturaLida

TTL_SEGUNDOS = 15 * 60
MAX_ITENS = 20


@dataclass(frozen=True)
class Envio:
    nome: str
    conteudo: bytes
    criado_em: float


@dataclass(frozen=True)
class FaturaPendente:
    nome: str
    arquivo_hash: str
    fatura: FaturaLida
    criado_em: float


class _ComData(Protocol):
    @property
    def criado_em(self) -> float: ...


T = TypeVar("T", bound=_ComData)


class _Armazem(Generic[T]):
    def __init__(self) -> None:
        self._itens: dict[str, T] = {}
        self._trava = threading.Lock()

    def _limpar(self, agora: float) -> None:
        for t in [t for t, e in self._itens.items() if agora - e.criado_em > TTL_SEGUNDOS]:
            del self._itens[t]
        while len(self._itens) > MAX_ITENS:
            del self._itens[next(iter(self._itens))]

    def _guardar(self, item: T) -> str:
        token = secrets.token_urlsafe(24)
        with self._trava:
            self._itens[token] = item
            self._limpar(item.criado_em)
        return token

    def obter(self, token: str) -> T | None:
        with self._trava:
            self._limpar(time.monotonic())
            return self._itens.get(token)

    def descartar(self, token: str) -> None:
        with self._trava:
            self._itens.pop(token, None)


class ArmazemUploads(_Armazem[Envio]):
    def guardar(self, nome: str, conteudo: bytes) -> str:
        return self._guardar(Envio(nome, conteudo, time.monotonic()))


class ArmazemFaturas(_Armazem[FaturaPendente]):
    def guardar(self, nome: str, arquivo_hash: str, fatura: FaturaLida) -> str:
        return self._guardar(FaturaPendente(nome, arquivo_hash, fatura, time.monotonic()))
