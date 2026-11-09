"""Cheque especial: configurar o limite e corrigir os dias de uso do mês."""
import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response

from app.dominio import cheque_especial
from app.dominio.dinheiro import para_centavos
from app.repo import contas
from app.web.deps import get_con

log = logging.getLogger(__name__)
router = APIRouter()


def _erro(msg: str) -> Response:
    return RedirectResponse(f"/?erro_cheque={quote(msg)}", status_code=303)


@router.post("/cheque-especial")
def configurar(conta_id: str = Form(""), limite: str = Form(""), dias_isentos: str = Form("10"),
               alerta_a_partir_de: str = Form("8"), con: Any = Depends(get_con)) -> Response:
    try:
        cid, isentos, alerta = int(conta_id), int(dias_isentos), int(alerta_a_partir_de)
        if contas.obter(con, cid) is None:
            raise ValueError("Conta não encontrada.")
        cfg = cheque_especial.validar_config(cid, para_centavos(limite), isentos, alerta)
    except ValueError as exc:
        texto = str(exc)
        return _erro(texto if texto.endswith(".") else "Confira os valores informados.")
    cheque_especial.salvar_config(con, cfg)
    log.info("event=cheque_especial_configurado")
    return RedirectResponse("/", status_code=303)


@router.post("/cheque-especial/dias")
def informar_dias(request: Request, dias: str = Form(""), con: Any = Depends(get_con)) -> Response:
    try:
        cheque_especial.informar_dias(con, request.app.state.hoje_fn(), int(dias))
    except ValueError as exc:
        texto = str(exc)
        return _erro(texto if texto.endswith(".") else "Informe um número de dias.")
    return RedirectResponse("/", status_code=303)
