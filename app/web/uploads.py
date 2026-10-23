"""Armazém temporário em memória dos arquivos enviados entre a prévia e a confirmação.

Os bytes nunca vão para disco nem para log. Cada envio recebe um token aleatório
(secrets.token_urlsafe); expira em TTL_SEGUNDOS e o armazém guarda no máximo
MAX_ITENS envios (os mais antigos são descartados). Processo único/local.
"""
import secrets
import threading
import time
from dataclasses import dataclass

TTL_SEGUNDOS = 15 * 60
MAX_ITENS = 20


@dataclass(frozen=True)
class Envio:
    nome: str
    conteudo: bytes
    criado_em: float


class ArmazemUploads:
    def __init__(self) -> None:
        self._itens: dict[str, Envio] = {}
        self._trava = threading.Lock()

    def _limpar(self, agora: float) -> None:
        for t in [t for t, e in self._itens.items() if agora - e.criado_em > TTL_SEGUNDOS]:
            del self._itens[t]
        while len(self._itens) > MAX_ITENS:
            del self._itens[next(iter(self._itens))]

    def guardar(self, nome: str, conteudo: bytes) -> str:
        token = secrets.token_urlsafe(24)
        agora = time.monotonic()
        with self._trava:
            self._itens[token] = Envio(nome, conteudo, agora)
            self._limpar(agora)
        return token

    def obter(self, token: str) -> Envio | None:
        with self._trava:
            self._limpar(time.monotonic())
            return self._itens.get(token)

    def descartar(self, token: str) -> None:
        with self._trava:
            self._itens.pop(token, None)
