"""Estado em memória do chat: uma requisição por sessão e confirmações de escrita pendentes."""
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Callable

from app import config

LIMITE_PENDENCIAS: int = 20
LIMITE_SESSOES_COM_PENDENCIA: int = 200
LIMITE_SIMULTANEAS: int = 3


class EstadoChat:
    def __init__(self, *, relogio: Callable[[], float] = time.monotonic,
                 validade_s: float | None = None, limite_simultaneas: int = LIMITE_SIMULTANEAS) -> None:
        self._relogio = relogio
        # Rede de segurança: reserva nunca liberada (resposta que nem começou) expira sozinha.
        self._validade = float(config.TIMEOUT_CHAT_S + 60) if validade_s is None else validade_s
        self._limite = limite_simultaneas
        self._trava = threading.Lock()
        self._ocupadas: dict[str, float] = {}
        self._novas: list[float] = []
        self._pendencias: OrderedDict[str, OrderedDict[str, str]] = OrderedDict()

    def _expirar(self, agora: float) -> None:
        limite = agora - self._validade
        self._ocupadas = {s: t for s, t in self._ocupadas.items() if t > limite}
        self._novas = [t for t in self._novas if t > limite]

    def reservar(self, sessao: str | None) -> bool:
        """Marca a sessão como ocupada. False se já há resposta em andamento (ou limite global)."""
        with self._trava:
            agora = self._relogio()
            self._expirar(agora)
            if (sessao is not None and sessao in self._ocupadas) or \
                    len(self._ocupadas) + len(self._novas) >= self._limite:
                return False
            if sessao is None:
                self._novas.append(agora)
            else:
                self._ocupadas[sessao] = agora
            return True

    def liberar(self, sessao: str | None) -> None:
        with self._trava:
            if sessao is None:
                if self._novas:
                    self._novas.pop(0)
            else:
                self._ocupadas.pop(sessao, None)

    def guardar_pendencia(self, sessao: str, comando: str) -> str:
        """Guarda o comando negado e devolve o token (uso único) que o cliente manda de volta."""
        token = secrets.token_urlsafe(8)
        with self._trava:
            da_sessao = self._pendencias.setdefault(sessao, OrderedDict())
            self._pendencias.move_to_end(sessao)
            da_sessao[token] = comando
            while len(da_sessao) > LIMITE_PENDENCIAS:
                da_sessao.popitem(last=False)
            while len(self._pendencias) > LIMITE_SESSOES_COM_PENDENCIA:
                self._pendencias.popitem(last=False)
        return token

    def consumir_pendencia(self, sessao: str, token: str) -> str | None:
        with self._trava:
            da_sessao = self._pendencias.get(sessao)
            if da_sessao is None:
                return None
            return da_sessao.pop(token, None)
