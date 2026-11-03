"""Fila de faturas vindas do Gmail, mostrada na tela Importar.

A tabela guarda só emissor/final/vencimento/hash (nunca texto de e-mail). O PDF fica em
DIR_FILA/<id>.pdf (id inteiro) e a senha nunca é guardada: "Importar" entrega o PDF ao fluxo
normal de fatura (token + formulário de senha).
"""
import hashlib
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from app import config
from app.dominio import fila_faturas as dominio
from app.repo import fila_faturas as repo
from app.web.deps import get_con
from app.web.rotas.importar import _pagina_inicial

log = logging.getLogger(__name__)
router = APIRouter()

MSG_EXPIRADO = "O arquivo desta fatura expirou. Busque de novo."
MSG_NAO_ENCONTRADO = "Fatura da fila não encontrada."


def _sem_cache(resp: Response) -> Response:
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _sincronizar(con: Any) -> None:
    """Itens cujo PDF já foi importado pelo fluxo normal saem da fila; expirados também."""
    dominio.expirar(con, datetime.now(), config.DIR_FILA)
    for item in repo.listar_pendentes(con):
        if repo.hash_ja_importado(con, item["pdf_hash"]):
            repo.definir_estado(con, int(item["id"]), "importada")
            dominio.descartar_pdf(config.DIR_FILA, int(item["id"]))
    con.commit()


def _fragmento(request: Request, con: Any, versao_cliente: str = "") -> Response:
    _sincronizar(con)
    itens = [{"id": int(i["id"]), "emissor": i["emissor"], "cartao_final": i["cartao_final"],
              "vencimento": i["vencimento"], "ja_importada": dominio.ja_importada(con, i)}
             for i in repo.listar_pendentes(con)]
    versao = hashlib.sha256(repr(itens).encode()).hexdigest()[:16] if itens else ""
    if versao and versao == versao_cliente:  # nada mudou: sem swap, o foco do teclado fica
        return _sem_cache(Response(status_code=204))
    resp = request.app.state.templates.TemplateResponse(
        request, "importar/_fila.html", {"itens": itens, "versao": versao})
    return _sem_cache(resp)


def _pendente(con: Any, item_id: int) -> Any | None:
    item = repo.obter(con, item_id)
    return item if item is not None and item["estado"] == "pendente" else None


def _nao_encontrado() -> Response:
    return _sem_cache(Response(MSG_NAO_ENCONTRADO, status_code=404,
                               media_type="text/plain; charset=utf-8"))


@router.get("/faturas/fila", response_class=HTMLResponse)
def listar(request: Request, con: Any = Depends(get_con), v: str = "") -> Response:
    return _fragmento(request, con, v[:32])


@router.post("/faturas/fila/{item_id}/pular", response_class=HTMLResponse)
def pular(item_id: int, request: Request, con: Any = Depends(get_con)) -> Response:
    if _pendente(con, item_id) is None:
        return _nao_encontrado()
    repo.definir_estado(con, item_id, "pulada")
    con.commit()
    dominio.descartar_pdf(config.DIR_FILA, item_id)
    log.info("event=fila_item_pulado")
    return _fragmento(request, con)


@router.post("/faturas/fila/{item_id}/importar", response_class=HTMLResponse)
def importar(item_id: int, request: Request, con: Any = Depends(get_con)) -> Response:
    item = _pendente(con, item_id)
    if item is None:
        return _nao_encontrado()
    try:
        conteudo = (config.DIR_FILA / f"{item_id}.pdf").read_bytes()
    except OSError:
        log.info("event=fila_pdf_ausente id=%d", item_id)
        return _sem_cache(_pagina_inicial(request, con, fatura={"erro": MSG_EXPIRADO},
                                          status=410))
    nome = f"Fatura {item['emissor'] or 'cartão'}.pdf"
    token = request.app.state.uploads.guardar(nome, conteudo)
    return _sem_cache(_pagina_inicial(
        request, con, fatura={"token": token, "nome": nome, "pedir_senha": True}))
