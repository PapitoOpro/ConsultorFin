"""Cheque especial: corrigir os dias de uso do mês (a configuração fica nas contas)."""
import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from app.dominio import cheque_especial
from app.web.deps import get_con

log = logging.getLogger(__name__)
router = APIRouter()


def _erro(msg: str) -> Response:
    return RedirectResponse(f"/?erro_cheque={quote(msg)}", status_code=303)


@router.post("/cheque-especial/dias")
def informar_dias(request: Request, dias: str = Form(""), con: Any = Depends(get_con)) -> Response:
    try:
        cheque_especial.informar_dias(con, request.app.state.hoje_fn(), int(dias))
    except ValueError as exc:
        texto = str(exc)
        return _erro(texto if texto.endswith(".") else "Informe um número de dias.")
    return RedirectResponse("/", status_code=303)
