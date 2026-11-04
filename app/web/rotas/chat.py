"""Chat do consultor (SSE). Só é registrado com CONSULTORFIN_CHAT=1; a lógica vive em app/chat/."""
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, StrictBool, StrictStr

from app import config
from app.chat.turno import ENTRADA_CANCELADO, ENTRADA_CONFIRMADO, rodar_turno

router = APIRouter()

UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
MSG_OCUPADA = "Aguarde a resposta anterior."
MSG_TOKEN = "Confirmação não encontrada ou já usada."
CABECALHOS_SSE: dict[str, str] = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


class Mensagem(BaseModel):
    texto: StrictStr = Field(min_length=1, max_length=config.LIMITE_MENSAGEM_CHAT)
    sessao: StrictStr | None = Field(default=None, pattern=UUID)


class Confirmacao(BaseModel):
    sessao: StrictStr = Field(pattern=UUID)
    token: StrictStr = Field(min_length=1, max_length=64)
    aceitar: StrictBool


def _stream(eventos: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(eventos, media_type="text/event-stream", headers=CABECALHOS_SSE)


@router.post("/consultor/mensagem")
async def mensagem(request: Request, corpo: Mensagem) -> Response:
    estado = request.app.state.chat
    if not estado.reservar(corpo.sessao):
        return JSONResponse({"erro": MSG_OCUPADA}, status_code=409)
    return _stream(rodar_turno(estado, request.app.state.claude_fn, sessao=corpo.sessao, entrada=corpo.texto))


@router.post("/consultor/confirmar")
async def confirmar(request: Request, corpo: Confirmacao) -> Response:
    estado = request.app.state.chat
    if not estado.reservar(corpo.sessao):
        return JSONResponse({"erro": MSG_OCUPADA}, status_code=409)
    comando = estado.consumir_pendencia(corpo.sessao, corpo.token)
    if comando is None:
        estado.liberar(corpo.sessao)
        return JSONResponse({"erro": MSG_TOKEN}, status_code=404)
    entrada = ENTRADA_CONFIRMADO if corpo.aceitar else ENTRADA_CANCELADO
    return _stream(rodar_turno(estado, request.app.state.claude_fn, sessao=corpo.sessao, entrada=entrada,
                               confirmado=comando if corpo.aceitar else None))
