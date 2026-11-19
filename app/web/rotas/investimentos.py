"""Tela Investimentos (corretora Clear/B3): envio do extrato e da posição, resumo, carteira e lançamentos."""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse

from app.dominio import investimentos as dom
from app.importacao.b3_posicao import PosicaoIlegivel, ler_posicao_b3, parece_posicao_b3
from app.importacao.clear import ClearIlegivel, ler_extrato_clear, parece_extrato_clear
from app.repo import investimentos as repo
from app.web.deps import get_con

log = logging.getLogger(__name__)
router = APIRouter()

LIMITE_BYTES = 5 * 1024 * 1024
MSG_FALHA = "Não foi possível importar o arquivo. Nada foi gravado."
MSG_FORMATO = "Formato não aceito. Envie o extrato da Clear ou a posição da B3, em .xlsx."
MSG_DESCONHECIDO = ("Não reconheci o arquivo. Envie o extrato da conta Clear ou a posição de "
                    "custódia da B3 (posicao-….xlsx).")


def _pagina(request: Request, con: Any, *, erro: str | None = None,
            resultado: dom.ResultadoImportacao | None = None,
            posicao: dom.ResultadoPosicao | None = None, status: int = 200) -> HTMLResponse:
    r = dom.resumo(con)
    mes = dom.mes_padrao(r, request.query_params.get("mes"))
    categoria = request.query_params.get("categoria")
    if categoria not in dom.CATEGORIAS:
        categoria = None
    atual = next((m for m in r.meses if m.mes == mes), None)
    diario = dom.resultado_diario(repo.todos(con))
    ctx = {
        "diario": diario, "dias_json": json.dumps(dom.dias_json(diario)),
        "ativo": "investimentos", "erro": erro, "resultado": resultado, "posicao": posicao,
        "resumo": r, "mes": mes, "atual": atual, "categoria": categoria,
        "categorias": dom.CATEGORIAS, "carteira": dom.carteira_atual(con),
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
             "resultado": None, "posicao": None, "carteira": None,
             "diario": dom.resultado_diario([]), "dias_json": "[]"}, status_code=500)


@router.post("/investimentos/importar", response_class=HTMLResponse)
def importar(request: Request, arquivo: UploadFile = File(...),
             con: Any = Depends(get_con)) -> HTMLResponse:
    nome = Path((arquivo.filename or "").replace("\\", "/")).name[:200]
    if Path(nome).suffix.lower() != ".xlsx":
        return _pagina(request, con, erro=MSG_FORMATO, status=400)
    conteudo = arquivo.file.read(LIMITE_BYTES + 1)
    if len(conteudo) > LIMITE_BYTES:
        return _pagina(request, con, erro="O arquivo excede o limite de 5 MB.", status=413)
    agora = datetime.now()
    try:
        if parece_extrato_clear(conteudo):
            resultado = dom.importar_extrato(con, ler_extrato_clear(conteudo), nome, agora)
            con.commit()
            return _pagina(request, con, resultado=resultado)
        if parece_posicao_b3(conteudo):
            posicao = dom.importar_posicao(con, ler_posicao_b3(conteudo, nome), agora)
            con.commit()
            return _pagina(request, con, posicao=posicao)
    except (ClearIlegivel, PosicaoIlegivel) as exc:
        con.rollback()
        return _pagina(request, con, erro=str(exc), status=400)
    except Exception as exc:  # noqa: BLE001
        con.rollback()
        log.error("event=investimentos_importacao_falhou erro=%s", type(exc).__name__)
        return _pagina(request, con, erro=MSG_FALHA, status=500)
    return _pagina(request, con, erro=MSG_DESCONHECIDO, status=400)
