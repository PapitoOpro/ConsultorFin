"""Tela Contas a pagar: vencimentos, parcelamentos, contas fixas/receitas recorrentes e contas."""
import logging
from collections.abc import Callable
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from app.dominio import contas_pagar as dom
from app.dominio import contas_pagar_acoes as acoes
from app.dominio.lancamentos import ErroLancamento, NaoEncontrado, opcoes_categorias
from app.repo import contas
from app.web.deps import get_con

log = logging.getLogger(__name__)
router = APIRouter()

MSG_INESPERADO = "Não foi possível concluir a operação. Nada foi alterado."


def _hoje(request: Request) -> date:
    hoje: date = request.app.state.hoje_fn()
    return hoje


def _pagina(request: Request, con: Any, horizonte: int, *, aviso: str | None = None,
            erro: str | None = None, status: int = 200) -> HTMLResponse:
    hoje = _hoje(request)
    ctx = {
        "ativo": "contas", "painel": dom.abrir_painel(con, hoje, horizonte),
        "horizonte": horizonte, "horizontes": (3, 6, 12), "hoje": hoje.isoformat(),
        "correspondencias": dom.correspondencias(con),
        "parcelamentos": dom.resumo_parcelamentos(con), "recorrentes": dom.listar_recorrentes(con),
        "contas": contas.listar(con), "categorias": opcoes_categorias(con),
        "categorias_saida": opcoes_categorias(con, "saida"),
        "categorias_entrada": opcoes_categorias(con, "entrada"),
        "tipos_conta": acoes.TIPOS_CONTA, "aviso": aviso, "erro": erro,
    }
    return request.app.state.templates.TemplateResponse(
        request, "contas_pagar/index.html", ctx, status_code=status)


def _executar(request: Request, con: Any, horizonte_txt: str,
              acao: Callable[[date], str]) -> HTMLResponse:
    """Roda a ação e re-renderiza a tela com aviso ou erro em português."""
    try:
        horizonte = dom.interpretar_horizonte(horizonte_txt or None)
    except ErroLancamento as exc:
        return _pagina(request, con, dom.HORIZONTE_PADRAO, erro=str(exc), status=400)
    try:
        aviso = acao(_hoje(request))
    except ErroLancamento as exc:
        status = 404 if isinstance(exc, NaoEncontrado) else 400
        return _seguro(request, con, horizonte, erro=str(exc), status=status)
    except Exception as exc:  # noqa: BLE001 - nunca expor stack trace
        log.error("event=contas_pagar_falhou erro=%s", type(exc).__name__)
        return _seguro(request, con, horizonte, erro=MSG_INESPERADO, status=500)
    return _seguro(request, con, horizonte, aviso=aviso)


def _seguro(request: Request, con: Any, horizonte: int, *, aviso: str | None = None,
            erro: str | None = None, status: int = 200) -> HTMLResponse:
    """Renderiza a tela; falha inesperada na renderização vira mensagem amigável."""
    try:
        return _pagina(request, con, horizonte, aviso=aviso, erro=erro, status=status)
    except Exception as exc:  # noqa: BLE001
        log.error("event=contas_pagar_render_falhou erro=%s", type(exc).__name__)
        return HTMLResponse(MSG_INESPERADO, status_code=500)


@router.get("/contas-a-pagar", response_class=HTMLResponse)
def tela(request: Request, con: Any = Depends(get_con)) -> HTMLResponse:
    try:
        horizonte = dom.interpretar_horizonte(request.query_params.get("horizonte"))
    except ErroLancamento as exc:
        return _seguro(request, con, dom.HORIZONTE_PADRAO, erro=str(exc), status=400)
    return _seguro(request, con, horizonte)


@router.post("/contas-a-pagar/parcelamento", response_class=HTMLResponse)
def novo_parcelamento(
    request: Request, con: Any = Depends(get_con), descricao: str = Form(""),
    valor_total: str = Form(""), n_parcelas: str = Form(""), primeiro_vencimento: str = Form(""),
    conta_id: str = Form(""), categoria_id: str = Form(""), horizonte: str = Form(""),
) -> HTMLResponse:
    def acao(_: date) -> str:
        n = acoes.criar_parcelamento_de_formulario(
            con, descricao=descricao, valor_total=valor_total, n_parcelas=n_parcelas,
            primeiro_vencimento=primeiro_vencimento, conta_id=conta_id, categoria_id=categoria_id)
        return "Parcelamento criado: 1 parcela." if n == 1 else f"Parcelamento criado: {n} parcelas."
    return _executar(request, con, horizonte, acao)


@router.post("/contas-a-pagar/recorrente", response_class=HTMLResponse)
def nova_recorrente(
    request: Request, con: Any = Depends(get_con), nome: str = Form(""), tipo: str = Form(""),
    dia: str = Form(""), valor_estimado: str = Form(""), conta_id: str = Form(""),
    categoria_id: str = Form(""), categoria_entrada: str = Form(""),
    categoria_saida: str = Form(""), horizonte: str = Form(""),
) -> HTMLResponse:
    def acao(_: date) -> str:
        escolhida = categoria_entrada if tipo == "entrada" else categoria_saida
        return acoes.criar_recorrente_de_formulario(
            con, nome=nome, tipo=tipo, dia=dia, valor_estimado=valor_estimado,
            conta_id=conta_id, categoria_id=escolhida or categoria_id)
    return _executar(request, con, horizonte, acao)


@router.post("/contas-a-pagar/conta", response_class=HTMLResponse)
def nova_conta(
    request: Request, con: Any = Depends(get_con), nome: str = Form(""), tipo: str = Form(""),
    instituicao: str = Form(""), final_numero: str = Form(""), saldo_inicial: str = Form(""),
    data_saldo_inicial: str = Form(""), horizonte: str = Form(""),
) -> HTMLResponse:
    def acao(_: date) -> str:
        return acoes.criar_conta_de_formulario(
            con, nome=nome, tipo=tipo, instituicao=instituicao, final_numero=final_numero,
            saldo_inicial=saldo_inicial, data_saldo_inicial=data_saldo_inicial)
    return _executar(request, con, horizonte, acao)


@router.post("/contas-a-pagar/recorrente/{recorrente_id}/desativar", response_class=HTMLResponse)
def desativar(recorrente_id: str, request: Request, con: Any = Depends(get_con),
              horizonte: str = Form("")) -> HTMLResponse:
    return _executar(request, con, horizonte,
                     lambda hoje: acoes.desativar(con, recorrente_id, hoje))


@router.post("/contas-a-pagar/grupo/{grupo}/editar", response_class=HTMLResponse)
def editar_grupo(grupo: str, request: Request, con: Any = Depends(get_con),
                 valor_total: str = Form(""), n_parcelas: str = Form(""),
                 horizonte: str = Form("")) -> HTMLResponse:
    return _executar(request, con, horizonte,
                     lambda _: acoes.editar_grupo(con, grupo, valor_total, n_parcelas))


@router.post("/contas-a-pagar/grupo/{grupo}/excluir", response_class=HTMLResponse)
def excluir_grupo(grupo: str, request: Request, con: Any = Depends(get_con),
                  horizonte: str = Form("")) -> HTMLResponse:
    return _executar(request, con, horizonte, lambda _: acoes.excluir_grupo(con, grupo))


@router.post("/contas-a-pagar/casamento/confirmar", response_class=HTMLResponse)
def confirmar_correspondencia(request: Request, con: Any = Depends(get_con),
                              pendente_id: str = Form(""), lancamento_id: str = Form(""),
                              horizonte: str = Form("")) -> HTMLResponse:
    return _executar(request, con, horizonte,
                     lambda _: acoes.confirmar_correspondencia(con, pendente_id, lancamento_id))


@router.post("/contas-a-pagar/{lancamento_id}/pagar", response_class=HTMLResponse)
def pagar(lancamento_id: str, request: Request, con: Any = Depends(get_con),
          valor: str = Form(""), data: str = Form(""), horizonte: str = Form("")) -> HTMLResponse:
    return _executar(request, con, horizonte,
                     lambda hoje: acoes.pagar(con, lancamento_id, valor, data, hoje))


@router.post("/contas-a-pagar/{lancamento_id}/pular", response_class=HTMLResponse)
def pular(lancamento_id: str, request: Request, con: Any = Depends(get_con),
          horizonte: str = Form("")) -> HTMLResponse:
    return _executar(request, con, horizonte, lambda _: acoes.pular(con, lancamento_id))
