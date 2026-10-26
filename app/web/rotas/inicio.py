"""Tela Início: saldos, resumo do mês, vencimentos, gráficos e detalhamento por categoria."""
import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.dominio import painel
from app.dominio.contas_pagar import interpretar_horizonte
from app.dominio.lancamentos import ErroLancamento
from app.dominio.projecao import HORIZONTES
from app.web.deps import get_con

log = logging.getLogger(__name__)
router = APIRouter()

MSG_INESPERADO = "Não foi possível carregar o painel. Nada foi alterado."


def _parametros(request: Request) -> tuple[date, int, int, int]:
    """(hoje, ano, mês, horizonte) a partir da query string; ErroLancamento se inválida."""
    hoje: date = request.app.state.hoje_fn()
    ano, mes = painel.interpretar_mes(request.query_params.get("mes"), hoje)
    horizonte = interpretar_horizonte(request.query_params.get("horizonte"))
    return hoje, ano, mes, horizonte


def _html(request: Request, ctx: dict[str, Any], status: int = 200) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request, "inicio/index.html", {"ativo": "inicio", "horizontes": HORIZONTES, **ctx},
        status_code=status)


def _pagina_erro(request: Request, msg: str, status: int) -> HTMLResponse:
    hoje: date = request.app.state.hoje_fn()
    return _html(request, {"erro": msg, "resumo": None, "dados": None, "horizonte": 6, "ind": None,
                           "mes_iso": f"{hoje.year:04d}-{hoje.month:02d}"}, status)


@router.get("/", response_class=HTMLResponse)
def inicio(request: Request, con: Any = Depends(get_con)) -> HTMLResponse:
    try:
        hoje, ano, mes, horizonte = _parametros(request)
    except ErroLancamento as exc:
        return _pagina_erro(request, str(exc), 400)
    try:
        resumo = painel.resumo_inicio(con, hoje, ano, mes, horizonte)
        dados = None if resumo.vazio else painel.dados_graficos(con, hoje, resumo, horizonte)
        ind = None if resumo.vazio else painel.indicadores_inicio(con, hoje, resumo)
        return _html(request, {"erro": None, "resumo": resumo, "dados": dados, "ind": ind,
                               "horizonte": horizonte, "mes_iso": resumo.mes_iso})
    except Exception as exc:  # noqa: BLE001 - nunca expor stack trace
        log.error("event=inicio_falhou erro=%s", type(exc).__name__)
        return _pagina_erro(request, MSG_INESPERADO, 500)


@router.get("/inicio/dados.json")
def dados_json(request: Request, con: Any = Depends(get_con)) -> Response:
    try:
        hoje, ano, mes, horizonte = _parametros(request)
    except ErroLancamento as exc:
        return JSONResponse({"erro": str(exc)}, status_code=400)
    try:
        resumo = painel.resumo_inicio(con, hoje, ano, mes, horizonte)
        return JSONResponse(painel.dados_graficos(con, hoje, resumo, horizonte))
    except Exception as exc:  # noqa: BLE001
        log.error("event=inicio_dados_falhou erro=%s", type(exc).__name__)
        return JSONResponse({"erro": MSG_INESPERADO}, status_code=500)
