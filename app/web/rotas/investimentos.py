"""Tela Investimentos (extrato da corretora Clear): envio, resumo mensal e lançamentos."""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse

from app.dominio import investimentos as dom
from app.importacao.clear import ClearIlegivel, ler_extrato_clear
from app.repo import investimentos as repo
from app.web.deps import get_con

log = logging.getLogger(__name__)
router = APIRouter()

LIMITE_BYTES = 5 * 1024 * 1024
MSG_FALHA = "Não foi possível importar o extrato. Nada foi gravado."


def _pagina(request: Request, con: Any, *, erro: str | None = None,
            resultado: dom.ResultadoImportacao | None = None, status: int = 200) -> HTMLResponse:
    r = dom.resumo(con)
    mes = dom.mes_padrao(r, request.query_params.get("mes"))
    categoria = request.query_params.get("categoria")
    if categoria not in dom.CATEGORIAS:
        categoria = None
    atual = next((m for m in r.meses if m.mes == mes), None)
    ctx = {
        "ativo": "investimentos", "erro": erro, "resultado": resultado, "resumo": r,
        "mes": mes, "atual": atual, "categoria": categoria, "categorias": dom.CATEGORIAS,
        "lancamentos": repo.listar(con, categoria=categoria, mes=mes) if mes else [],
        "importacoes": repo.listar_importacoes(con, 5),
        "grafico": json.dumps([dom.mes_json(m) for m in r.meses]),
    }
    return request.app.state.templates.TemplateResponse(
        request, "investimentos/index.html", ctx, status_code=status)


@router.get("/investimentos", response_class=HTMLResponse)
def investimentos(request: Request, con: Any = Depends(get_con)) -> HTMLResponse:
    try:
        return _pagina(request, con)
    except Exception as exc:  # noqa: BLE001 - nunca expor stack trace
        log.error("event=investimentos_falhou erro=%s", type(exc).__name__)
        return request.app.state.templates.TemplateResponse(
            request, "investimentos/index.html",
            {"ativo": "investimentos", "erro": "Não foi possível carregar os investimentos.",
             "resumo": dom.ResumoInvestimentos(), "mes": None, "atual": None, "categoria": None,
             "categorias": dom.CATEGORIAS, "lancamentos": [], "importacoes": [], "grafico": "[]",
             "resultado": None}, status_code=500)


@router.post("/investimentos/importar", response_class=HTMLResponse)
def importar(request: Request, arquivo: UploadFile = File(...),
             con: Any = Depends(get_con)) -> HTMLResponse:
    nome = Path((arquivo.filename or "").replace("\\", "/")).name[:200]
    if Path(nome).suffix.lower() != ".xlsx":
        return _pagina(request, con, erro="Formato não aceito. Envie o extrato da Clear em .xlsx.",
                       status=400)
    conteudo = arquivo.file.read(LIMITE_BYTES + 1)
    if len(conteudo) > LIMITE_BYTES:
        return _pagina(request, con, erro="O arquivo excede o limite de 5 MB.", status=413)
    try:
        extrato = ler_extrato_clear(conteudo)
    except ClearIlegivel as exc:
        return _pagina(request, con, erro=str(exc), status=400)
    except Exception as exc:  # noqa: BLE001
        log.warning("event=investimentos_leitura_falhou erro=%s", type(exc).__name__)
        return _pagina(request, con, erro=MSG_FALHA, status=400)
    try:
        resultado = dom.importar_extrato(con, extrato, nome, datetime.now())
    except Exception as exc:  # noqa: BLE001
        con.rollback()
        log.error("event=investimentos_importacao_falhou erro=%s", type(exc).__name__)
        return _pagina(request, con, erro=MSG_FALHA, status=500)
    con.commit()
    return _pagina(request, con, resultado=resultado)
