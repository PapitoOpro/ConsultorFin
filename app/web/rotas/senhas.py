"""Senhas de PDF guardadas: o dono pode esquecer a de um cartão (a senha nunca é exibida)."""
import logging
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse, Response

from app.dominio import cofre_senhas
from app.web.deps import get_con

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/senhas/{item_id}/esquecer")
def esquecer(item_id: int, con: Any = Depends(get_con)) -> Response:
    if cofre_senhas.esquecer(con, item_id):
        log.info("event=senha_pdf_esquecida")
    return RedirectResponse("/importar", status_code=303)
