"""Um turno do chat: roda o `claude`, traduz as linhas em eventos SSE e sempre libera a sessão."""
import json
import logging
import shutil
import tempfile
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import anyio

from app.chat.argv import montar_argv
from app.chat.estado import EstadoChat
from app.chat.eventos import MSG_ERRO, traduzir_linha
from app.chat.guarda import VAR_CONFIRMADO, VAR_MARCA

log = logging.getLogger(__name__)

ClaudeFn = Callable[[list[str], str, dict[str, str]], AsyncIterator[str]]
ENTRADA_CONFIRMADO = "Confirmado. Execute agora a ação que foi bloqueada e conte o resultado."
ENTRADA_CANCELADO = "Cancelado. Não faça essa alteração."


def sse(evento: dict[str, Any]) -> str:
    return f"data: {json.dumps(evento, ensure_ascii=False)}\n\n"


async def rodar_turno(estado: EstadoChat, claude_fn: ClaudeFn, *, sessao: str | None, entrada: str,
                      confirmado: str | None = None) -> AsyncIterator[str]:
    """A sessão já vem reservada por quem chama; aqui ela é liberada em qualquer desfecho.

    `confirmado`: escrita aceita no cartão. O porteiro a aprova uma única vez neste turno (marca
    criada com O_EXCL numa pasta só deste turno, apagada no fim).
    """
    terminou = False
    pasta: Path | None = None
    try:
        ambiente: dict[str, str] = {}
        if confirmado is not None:
            pasta = Path(tempfile.mkdtemp(prefix="consultorfin-confirma-"))
            ambiente = {VAR_CONFIRMADO: confirmado, VAR_MARCA: str(pasta / "usado")}
        linhas = claude_fn(montar_argv(sessao=sessao), entrada, ambiente)
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
        if pasta is not None:
            shutil.rmtree(pasta, ignore_errors=True)
        estado.liberar(sessao)
