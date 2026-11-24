"""Tela Investimentos (corretora Clear/B3): envio do extrato e da posição, resumo, carteira e lançamentos."""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse

from app.dominio import cofre_senhas
from app.dominio import investimentos as dom
from app.dominio import notas_negociacao as dom_notas
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


LIMITE_LANCAMENTOS = 500
ERRO_CARREGAR = "Não foi possível carregar os investimentos."


def _render(request: Request, nome: str, ctx: dict[str, Any], status: int = 200) -> HTMLResponse:
    base = {"ativo": "investimentos", "erro": None, "categorias": dom.CATEGORIAS}
    return request.app.state.templates.TemplateResponse(
        request, f"investimentos/{nome}", {**base, **ctx}, status_code=status)


def _pagina(request: Request, con: Any, *, erro: str | None = None,
            resultado: dom.ResultadoImportacao | None = None,
            posicao: dom.ResultadoPosicao | None = None, aviso: str | None = None,
            status: int = 200) -> HTMLResponse:
    r = dom.resumo(con)
    mes = dom.mes_padrao(r, request.query_params.get("mes"))
    atual = next((m for m in r.meses if m.mes == mes), None)
    diario = dom.resultado_diario(repo.todos(con))
    return _render(request, "index.html", {
        "secao": "resumo", "base_url": "/investimentos", "erro": erro, "resultado": resultado,
        "posicao": posicao, "aviso": aviso, "resumo": r,
        "notas": dom_notas.notas_json(con), "senha_notas": bool(cofre_senhas.candidatas(
            con, dom_notas.EMISSOR_COFRE, dom_notas.FINAL_COFRE)), "mes": mes, "atual": atual,
        "diario": diario, "dias_json": json.dumps(dom.dias_json(diario)),
        "carteira": dom.carteira_atual(con),
        "total_irrf": sum(m.irrf for m in r.meses), "total_taxas": sum(m.taxas for m in r.meses),
        "total_custos": sum(m.custos for m in r.meses),
        "grafico": json.dumps([dom.mes_json(m) for m in r.meses]),
    }, status)


def _erro_carregar(request: Request, nome: str, secao: str) -> HTMLResponse:
    return _render(request, nome, {
        "secao": secao, "base_url": "/investimentos", "erro": ERRO_CARREGAR,
        "resumo": dom.ResumoInvestimentos(), "mes": None, "atual": None, "categoria": None,
        "lancamentos": [], "limitado": False, "total_filtro": None, "grafico": "[]",
        "resultado": None, "posicao": None, "aviso": None, "notas": [], "senha_notas": False,
        "carteira": None, "diario": dom.resultado_diario([]),
        "dias_json": "[]", "total_irrf": 0, "total_taxas": 0, "total_custos": 0}, 500)


@router.get("/investimentos", response_class=HTMLResponse)
def investimentos(request: Request, con: Any = Depends(get_con)) -> HTMLResponse:
    try:
        return _pagina(request, con)
    except Exception as exc:  # noqa: BLE001 - nunca expor stack trace
        log.error("event=investimentos_falhou erro=%s", type(exc).__name__)
        return _erro_carregar(request, "index.html", "resumo")


@router.get("/investimentos/lancamentos", response_class=HTMLResponse)
def lancamentos(request: Request, con: Any = Depends(get_con)) -> HTMLResponse:
    try:
        r = dom.resumo(con)
        mes = dom.mes_padrao(r, request.query_params.get("mes"))
        categoria = request.query_params.get("categoria")
        if categoria not in dom.CATEGORIAS:
            categoria = None
        linhas = repo.listar(con, categoria=categoria, mes=mes, limite=LIMITE_LANCAMENTOS) if mes else []
        return _render(request, "lancamentos.html", {
            "secao": "lancamentos", "base_url": "/investimentos/lancamentos", "resumo": r,
            "mes": mes, "categoria": categoria, "lancamentos": linhas,
            "limitado": len(linhas) >= LIMITE_LANCAMENTOS,
            "total_filtro": sum(int(l["valor_centavos"]) for l in linhas) if linhas else None})
    except Exception as exc:  # noqa: BLE001
        log.error("event=investimentos_lancamentos_falhou erro=%s", type(exc).__name__)
        return _erro_carregar(request, "lancamentos.html", "lancamentos")


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


@router.post("/investimentos/senha-notas", response_class=HTMLResponse)
def senha_notas(request: Request, senha: str = Form(""), con: Any = Depends(get_con)) -> HTMLResponse:
    """Guarda (cifrada) a senha dos PDFs de notas de negociação; nunca é exibida nem registrada."""
    senha = senha.strip()
    if not senha or len(senha) > 64:
        return _pagina(request, con, erro="Informe a senha das notas (até 64 caracteres).", status=400)
    try:
        guardou = cofre_senhas.guardar(con, dom_notas.EMISSOR_COFRE, dom_notas.FINAL_COFRE, senha)
        con.commit()
    except Exception as exc:  # noqa: BLE001
        con.rollback()
        log.error("event=senha_notas_falhou erro=%s", type(exc).__name__)
        return _pagina(request, con, erro="Não foi possível guardar a senha.", status=500)
    if not guardou:
        return _pagina(request, con, erro="Não foi possível guardar a senha agora.", status=500)
    log.info("event=senha_notas_guardada")
    return _pagina(request, con, aviso="Senha das notas guardada. O agente já pode importar as notas do Gmail.")
