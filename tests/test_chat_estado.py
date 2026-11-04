import asyncio
import sys
import time
from collections.abc import AsyncIterator

import pytest

from app import config
from app.chat import processo
from app.chat.estado import EstadoChat

A = "5b1f6a8e-2c3d-4e5f-8a9b-0c1d2e3f4a5b"
B = "0f1e2d3c-4b5a-4968-8776-655443322110"


def test_uma_requisicao_por_sessao() -> None:
    e = EstadoChat()
    assert e.reservar(A)
    assert not e.reservar(A)
    assert e.reservar(B)
    e.liberar(A)
    assert e.reservar(A)


def test_sessoes_novas_tem_limite_global() -> None:
    e = EstadoChat(limite_simultaneas=2)
    assert e.reservar(None) and e.reservar(None)
    assert not e.reservar(None)
    assert not e.reservar(A)
    e.liberar(None)
    assert e.reservar(A)


def test_reserva_esquecida_expira() -> None:
    agora = [100.0]
    e = EstadoChat(relogio=lambda: agora[0], validade_s=10)
    assert e.reservar(A)
    agora[0] += 11
    assert e.reservar(A)


def test_pendencia_uso_unico_e_presa_a_sessao() -> None:
    e = EstadoChat()
    t = e.guardar_pendencia(A, "cmd")
    assert len(t) >= 10
    assert e.consumir_pendencia(B, t) is None
    assert e.consumir_pendencia(A, t) == "cmd"
    assert e.consumir_pendencia(A, t) is None
    assert e.consumir_pendencia(A, "inexistente") is None


def test_no_maximo_20_pendencias_por_sessao() -> None:
    e = EstadoChat()
    tokens = [e.guardar_pendencia(A, f"cmd{i}") for i in range(21)]
    assert e.consumir_pendencia(A, tokens[0]) is None  # a mais antiga sai
    assert e.consumir_pendencia(A, tokens[20]) == "cmd20"
    assert len(set(tokens)) == 21


def test_turno_cancelado_fecha_o_processo_e_libera_a_sessao() -> None:
    import anyio

    from app.chat.turno import rodar_turno

    fechou: list[bool] = []
    e = EstadoChat()
    assert e.reservar(A)

    async def claude_preso(argv: list[str], entrada: str) -> AsyncIterator[str]:
        try:
            yield '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"oi"}}}'
            await anyio.sleep(60)
            yield "{}"
        finally:
            await anyio.sleep(0)  # limpeza que também aguarda (como esperar o processo morrer)
            fechou.append(True)

    async def cenario() -> None:
        turno = rodar_turno(e, claude_preso, sessao=A, entrada="x")
        with anyio.move_on_after(0.5):  # cliente parou de ler no meio da resposta
            async for _ in turno:
                await anyio.sleep(60)
        with anyio.CancelScope() as escopo:  # o servidor fecha o stream já cancelado
            escopo.cancel()
            await turno.aclose()

    anyio.run(cenario)
    assert fechou == [True]
    assert e.reservar(A)


async def _coletar(argv: list[str], entrada: str) -> list[str]:
    return [linha async for linha in processo.executar_claude(argv, entrada)]


def test_executar_claude_passa_entrada_por_stdin() -> None:
    eco = ("import sys\nfor l in sys.stdin.buffer:\n"
           "    sys.stdout.buffer.write(l.decode('utf-8').strip().upper().encode('utf-8') + b'\\n')\n"
           "    sys.stdout.flush()")
    assert asyncio.run(_coletar([sys.executable, "-c", eco], "olá\nmundo")) == ["OLÁ", "MUNDO"]


def test_executar_claude_mata_no_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TIMEOUT_CHAT_S", 1)
    preso = "import time\nprint('a', flush=True)\ntime.sleep(60)"
    inicio = time.monotonic()
    with pytest.raises(TimeoutError):
        asyncio.run(_coletar([sys.executable, "-c", preso], ""))
    assert time.monotonic() - inicio < 15


def test_executar_claude_mata_ao_cancelar() -> None:
    preso = "import time\nprint('a', flush=True)\ntime.sleep(60)"
    procs: list[asyncio.subprocess.Process] = []
    original = asyncio.create_subprocess_exec

    async def espiao(*a: object, **kw: object) -> asyncio.subprocess.Process:
        p = await original(*a, **kw)  # type: ignore[arg-type]
        procs.append(p)
        return p

    async def cenario() -> None:
        gen = processo.executar_claude([sys.executable, "-c", preso], "")
        assert await gen.__anext__() == "a"
        await gen.aclose()  # cliente desconectou

    mp = pytest.MonkeyPatch()
    mp.setattr(processo.asyncio, "create_subprocess_exec", espiao)
    try:
        asyncio.run(cenario())
    finally:
        mp.undo()
    assert procs and procs[0].returncode is not None
