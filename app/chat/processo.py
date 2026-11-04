"""Processo do `claude -p`: mensagem por stdin, linhas do stdout, morte no timeout ou ao cancelar."""
import asyncio
import logging
import os
import signal
import sys
from collections.abc import AsyncIterator

from app import config

log = logging.getLogger(__name__)

LIMITE_LINHA_BYTES: int = 16 * 1024 * 1024  # resultado de ferramenta grande vem numa linha só
_POSIX = sys.platform != "win32"


def _matar(proc: asyncio.subprocess.Process) -> None:
    try:
        if _POSIX:
            os.killpg(proc.pid, signal.SIGKILL)  # leva junto bash/python filhos
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass


async def executar_claude(argv: list[str], entrada: str) -> AsyncIterator[str]:
    """Produz as linhas do stdout. TimeoutError após `config.TIMEOUT_CHAT_S` (processo morto)."""
    loop = asyncio.get_running_loop()
    prazo = loop.time() + config.TIMEOUT_CHAT_S
    proc = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, cwd=config.RAIZ, limit=LIMITE_LINHA_BYTES,
        start_new_session=_POSIX)
    try:
        assert proc.stdin is not None and proc.stdout is not None
        try:
            proc.stdin.write(entrada.encode("utf-8"))
            await asyncio.wait_for(proc.stdin.drain(), max(prazo - loop.time(), 0))
            proc.stdin.close()
        except (BrokenPipeError, ConnectionResetError):
            pass  # processo já saiu; o stdout diz o resto
        while True:
            bruta = await asyncio.wait_for(proc.stdout.readline(), max(prazo - loop.time(), 0))
            if not bruta:
                break
            yield bruta.decode("utf-8", errors="replace").rstrip("\r\n")
        codigo = await asyncio.wait_for(proc.wait(), max(prazo - loop.time(), 0))
        if codigo != 0:
            log.warning("event=chat_processo_saida codigo=%s", codigo)
    except TimeoutError:
        log.warning("event=chat_falhou erro=Timeout")
        raise
    finally:
        if proc.returncode is None:
            _matar(proc)
            await proc.wait()
