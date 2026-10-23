"""Tela Lançamentos: extrato filtrável, edição inline de categoria e ações por linha."""
import logging
from collections.abc import Callable
from datetime import date
from typing import Any
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, Response

from app.dominio import lancamentos as dom
from app.dominio.casamento import MSG_LINHA_NO_EXTRATO, linha_do_extrato_para
from app.dominio.dinheiro import para_centavos
from app.dominio.lancamentos import ErroLancamento, Filtros, NaoEncontrado
from app.repo import contas
from app.web.deps import get_con

log = logging.getLogger(__name__)
router = APIRouter()

MSG_INESPERADO = "Não foi possível concluir a operação. Nada foi alterado."
TIPOS_ROTULO = {"entrada": "Entrada", "saida": "Saída", "transferencia": "Transferência",
                "pagamento_fatura": "Pagamento de fatura"}


def _status(exc: Exception) -> int:
    return 404 if isinstance(exc, NaoEncontrado) else 400


def _filtros_de(voltar: str) -> Filtros:
    """Filtros a partir da query string devolvida pelo formulário; inválida -> padrão."""
    params = dict(parse_qsl(voltar[:1000], keep_blank_values=True))
    try:
        return Filtros.de_parametros(params, date.today())
    except ErroLancamento:
        return Filtros.de_parametros({}, date.today())


def _pagina(request: Request, con: Any, filtros: Filtros, *, aviso: str | None = None,
            erro: str | None = None, status: int = 200) -> HTMLResponse:
    pagina = dom.listar(con, filtros)
    base = filtros.como_parametros(pagina.pagina)
    ctx = {
        "ativo": "lancamentos", "pagina": pagina, "filtros": filtros, "contas": contas.listar(con),
        "categorias": dom.opcoes_categorias(con), "tipos": TIPOS_ROTULO, "aviso": aviso,
        "erro": erro, "voltar": urlencode(base), "hoje": date.today().isoformat(),
        "anterior": urlencode({**base, "pagina": str(pagina.pagina - 1)}) if pagina.pagina > 1 else None,
        "proxima": urlencode({**base, "pagina": str(pagina.pagina + 1)})
        if pagina.pagina < pagina.paginas else None,
    }
    return request.app.state.templates.TemplateResponse(
        request, "lancamentos/index.html", ctx, status_code=status)


def _executar(request: Request, con: Any, voltar: str, acao: Callable[[], str]) -> HTMLResponse:
    """Roda a ação e re-renderiza o extrato com aviso ou erro em português."""
    filtros = _filtros_de(voltar)
    try:
        aviso = acao()
    except ErroLancamento as exc:
        return _pagina(request, con, filtros, erro=str(exc), status=_status(exc))
    except Exception as exc:  # noqa: BLE001 - nunca expor stack trace
        log.error("event=lancamentos_falhou erro=%s", type(exc).__name__)
        return _pagina(request, con, filtros, erro=MSG_INESPERADO, status=500)
    return _pagina(request, con, filtros, aviso=aviso)


@router.get("/lancamentos", response_class=HTMLResponse)
def extrato(request: Request, con: Any = Depends(get_con)) -> HTMLResponse:
    try:
        filtros = Filtros.de_parametros(dict(request.query_params), date.today())
    except ErroLancamento as exc:
        return _pagina(request, con, Filtros.de_parametros({}, date.today()), erro=str(exc), status=400)
    try:
        return _pagina(request, con, filtros)
    except Exception as exc:  # noqa: BLE001
        log.error("event=extrato_falhou erro=%s", type(exc).__name__)
        return HTMLResponse(MSG_INESPERADO, status_code=500)


@router.post("/lancamentos/novo", response_class=HTMLResponse)
def novo(request: Request, con: Any = Depends(get_con), data: str = Form(""), descricao: str = Form(""),
         valor: str = Form(""), sinal: str = Form(""), conta_id: str = Form(""),
         categoria_id: str = Form(""), observacao: str = Form(""), compensado: str = Form(""),
         voltar: str = Form("")) -> HTMLResponse:
    def acao() -> str:
        dom.criar_manual_de_formulario(
            con, data=data, descricao=descricao, valor=valor, sinal=sinal, conta_id=conta_id,
            categoria_id=categoria_id, observacao=observacao, compensado=bool(compensado))
        return "Lançamento criado."
    return _executar(request, con, voltar, acao)


@router.post("/lancamentos/lote", response_class=HTMLResponse)
def lote(request: Request, con: Any = Depends(get_con), ids: list[str] = Form(default=[]),
         categoria_id: str = Form(""), voltar: str = Form("")) -> HTMLResponse:
    def acao() -> str:
        n = dom.categorizar_em_lote(con, [dom.interpretar_id(i) for i in ids],
                                    dom.interpretar_id(categoria_id, "Categoria"))
        return f"{n} lançamento categorizado." if n == 1 else f"{n} lançamentos categorizados."
    return _executar(request, con, voltar, acao)


@router.post("/lancamentos/{lancamento_id}/categoria", response_class=HTMLResponse)
def categoria_inline(lancamento_id: str, request: Request, con: Any = Depends(get_con),
                     categoria_id: str = Form("")) -> Response:
    """Parcial HTMX: devolve só a célula de categoria atualizada."""
    try:
        lid = dom.interpretar_id(lancamento_id)
        cat = dom.interpretar_id(categoria_id, "Categoria") if categoria_id.strip() else None
        dom.alterar_categoria(con, lid, cat)
    except ErroLancamento as exc:
        return Response(str(exc), status_code=_status(exc), media_type="text/plain; charset=utf-8")
    except Exception as exc:  # noqa: BLE001
        log.error("event=categoria_falhou erro=%s", type(exc).__name__)
        return Response(MSG_INESPERADO, status_code=500, media_type="text/plain; charset=utf-8")
    return request.app.state.templates.TemplateResponse(request, "lancamentos/_categoria.html", {
        "l": {"id": lid, "categoria_id": cat}, "categorias": dom.opcoes_categorias(con)})


@router.post("/lancamentos/{lancamento_id}/regra", response_class=HTMLResponse)
def regra(lancamento_id: str, request: Request, con: Any = Depends(get_con),
          padrao: str = Form(""), categoria_id: str = Form(""), voltar: str = Form("")) -> HTMLResponse:
    def acao() -> str:
        dom.interpretar_id(lancamento_id)
        n = dom.criar_regra_e_aplicar(con, padrao, dom.interpretar_id(categoria_id, "Categoria"))
        return (f"Regra criada: {n} lançamento categorizado." if n == 1
                else f"Regra criada: {n} lançamentos categorizados.")
    return _executar(request, con, voltar, acao)


@router.post("/lancamentos/{lancamento_id}/tipo", response_class=HTMLResponse)
def mudar_tipo(lancamento_id: str, request: Request, con: Any = Depends(get_con),
         tipo: str = Form(""), voltar: str = Form("")) -> HTMLResponse:
    def acao() -> str:
        dom.alterar_tipo(con, dom.interpretar_id(lancamento_id), tipo)
        return "Tipo atualizado."
    return _executar(request, con, voltar, acao)


@router.post("/lancamentos/{lancamento_id}/excluir", response_class=HTMLResponse)
def excluir(lancamento_id: str, request: Request, con: Any = Depends(get_con),
            voltar: str = Form("")) -> HTMLResponse:
    def acao() -> str:
        dom.excluir_manual(con, dom.interpretar_id(lancamento_id))
        return "Lançamento excluído."
    return _executar(request, con, voltar, acao)


@router.post("/lancamentos/{lancamento_id}/pagar", response_class=HTMLResponse)
def pagar(lancamento_id: str, request: Request, con: Any = Depends(get_con),
          valor: str = Form(""), data: str = Form(""), voltar: str = Form("")) -> HTMLResponse:
    def acao() -> str:
        lid = dom.interpretar_id(lancamento_id)
        dia = date.fromisoformat(dom.interpretar_data(data))
        try:
            real = para_centavos(valor) if valor.strip() else None
        except ValueError as exc:
            raise ErroLancamento("Valor inválido. Use o formato 1.234,56.") from exc
        dom.pagar_pendente(con, lid, real, dia)
        if linha_do_extrato_para(con, lid):
            return f"Pagamento registrado. {MSG_LINHA_NO_EXTRATO}"
        return "Pagamento registrado."
    return _executar(request, con, voltar, acao)
