"""Um turno do chat: roda o `claude`, traduz as linhas em eventos SSE e sempre libera a sessão."""
import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any

import anyio

from app.chat.argv import montar_argv
from app.chat.estado import EstadoChat
from app.chat.eventos import MSG_ERRO, traduzir_linha

log = logging.getLogger(__name__)

ClaudeFn = Callable[[list[str], str], AsyncIterator[str]]
ENTRADA_CONFIRMADO = "Confirmado. Execute agora a ação que foi bloqueada e conte o resultado."
ENTRADA_CANCELADO = "Cancelado. Não faça essa alteração."


def sse(evento: dict[str, Any]) -> str:
    return f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"


async def rodar_turno(estado: EstadoChat, claude_fn: ClaudeFn, *, sessao: str | None, entrada: str,
                      extras_permitidos: Sequence[str] = ()) -> AsyncIterator[str]:
    """A sessão já vem reservada por quem chama; aqui ela é liberada em qualquer desfecho."""
    terminou = False
    try:
        linhas = claude_fn(montar_argv(sessao=sessao, extras_permitidos=extras_permitidos), entrada)
        try:
            async for linha in linhas:
                for evento in traduzir_linha(linha, estado):
                    terminou = terminou or evento["tipo"] == "fim"
                    yield sse(evento)
        finally:
            fechar = getattr(linhas, "aclose", None)
            if fechar is not None:
                with anyio.CancelScope(shield=True):  # cliente desconectou: ainda assim mata o processo
                    await fechar()
        if not terminou:
            log.warning("event=chat_falhou erro=SemResultado")
            yield sse({"tipo": "erro", "texto": MSG_ERRO})
    except Exception as exc:  # noqa: BLE001 - o usuário só vê a mensagem genérica
        log.warning("event=chat_falhou erro=%s", type(exc).__name__)
        if not terminou:
            yield sse({"tipo": "erro", "texto": MSG_ERRO})
    finally:
        estado.liberar(sessao)
