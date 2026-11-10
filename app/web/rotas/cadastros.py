"""Tela Cadastros: contas e cartões, categorias e contas fixas/receitas recorrentes."""
import logging
from collections.abc import Callable
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from app.dominio import categorias as dom_categorias
from app.dominio import cheque_especial
from app.dominio import contas_pagar as dom
from app.dominio import contas_pagar_acoes as acoes
from app.dominio.lancamentos import ErroLancamento, NaoEncontrado, opcoes_categorias
from app.repo import contas
from app.web.deps import get_con

log = logging.getLogger(__name__)
router = APIRouter()

MSG_INESPERADO = "Não foi possível concluir a operação. Nada foi alterado."
SECOES = ("contas", "categorias", "recorrentes")
ABAS = ("entrada", "saida")


def _secao(valor: str | None) -> str:
    return valor if valor in SECOES else "contas"


def pagina_cadastros(request: Request, con: Any, secao: str, *, aviso: str | None = None,
                     erro: str | None = None, status: int = 200, aba: str = "saida",
                     excluindo: dict[str, Any] | None = None) -> HTMLResponse:
    """Renderiza a tela de Cadastros na seção pedida."""
    hoje: date = request.app.state.hoje_fn()
    ctx: dict[str, Any] = {
        "ativo": "cadastros", "secao": _secao(secao), "aviso": aviso, "erro": erro,
        "hoje": hoje.isoformat(), "aba": aba if aba in ABAS else "saida", "excluindo": excluindo,
        "contas": contas.listar(con), "tipos_conta": acoes.TIPOS_CONTA,
        "cheque_cfg": cheque_especial.carregar_config(con), "recorrentes": dom.listar_recorrentes(con),
        "categorias_saida": opcoes_categorias(con, "saida"),
        "categorias_entrada": opcoes_categorias(con, "entrada"),
        "arvore": dom_categorias.arvore_para_tela(con),
        "todas": {t: opcoes_categorias(con, t) for t in ABAS},
    }
    return request.app.state.templates.TemplateResponse(
        request, "cadastros/index.html", ctx, status_code=status)


def _seguro(request: Request, con: Any, secao: str, **kw: Any) -> HTMLResponse:
    """Renderiza a tela; falha inesperada na renderização vira mensagem amigável."""
    try:
        return pagina_cadastros(request, con, secao, **kw)
    except Exception as exc:  # noqa: BLE001 - nunca expor stack trace
        log.error("event=cadastros_render_falhou erro=%s", type(exc).__name__)
        return HTMLResponse(MSG_INESPERADO, status_code=500)


def _executar(request: Request, con: Any, secao: str,
              acao: Callable[[date], str]) -> HTMLResponse:
    """Roda a ação e re-renderiza a tela com aviso ou erro em português."""
    hoje: date = request.app.state.hoje_fn()
    try:
        aviso = acao(hoje)
    except ErroLancamento as exc:
        status = 404 if isinstance(exc, NaoEncontrado) else 400
        con.rollback()
        return _seguro(request, con, secao, erro=str(exc), status=status)
    except Exception as exc:  # noqa: BLE001 - nunca expor stack trace
        log.error("event=cadastros_falhou erro=%s", type(exc).__name__)
        con.rollback()
        return _seguro(request, con, secao, erro=MSG_INESPERADO, status=500)
    return _seguro(request, con, secao, aviso=aviso)


@router.get("/cadastros", response_class=HTMLResponse)
def tela(request: Request, con: Any = Depends(get_con)) -> HTMLResponse:
    params = request.query_params
    return _seguro(request, con, _secao(params.get("secao")), aba=params.get("aba") or "saida")


@router.post("/cadastros/conta", response_class=HTMLResponse)
def nova_conta(
    request: Request, con: Any = Depends(get_con), nome: str = Form(""), tipo: str = Form(""),
    instituicao: str = Form(""), final_numero: str = Form(""), saldo_inicial: str = Form(""),
    data_saldo_inicial: str = Form(""), limite_cheque_especial: str = Form(""),
) -> HTMLResponse:
    def acao(_: date) -> str:
        return acoes.criar_conta_de_formulario(
            con, nome=nome, tipo=tipo, instituicao=instituicao, final_numero=final_numero,
            saldo_inicial=saldo_inicial, data_saldo_inicial=data_saldo_inicial,
            limite_cheque_especial=limite_cheque_especial)
    return _executar(request, con, "contas", acao)


@router.post("/cadastros/conta/{conta_id}/cheque-especial", response_class=HTMLResponse)
def configurar_cheque_especial(
    conta_id: str, request: Request, con: Any = Depends(get_con), limite: str = Form(""),
    dias_isentos: str = Form(""), alerta_a_partir_de: str = Form(""),
) -> HTMLResponse:
    def acao(_: date) -> str:
        return acoes.configurar_cheque_especial_de_formulario(
            con, conta_id=conta_id, limite=limite, dias_isentos=dias_isentos,
            alerta_a_partir_de=alerta_a_partir_de)
    return _executar(request, con, "contas", acao)


@router.post("/cadastros/recorrente", response_class=HTMLResponse)
def nova_recorrente(
    request: Request, con: Any = Depends(get_con), nome: str = Form(""), tipo: str = Form(""),
    dia: str = Form(""), valor_estimado: str = Form(""), conta_id: str = Form(""),
    categoria_id: str = Form(""), categoria_entrada: str = Form(""),
    categoria_saida: str = Form(""),
) -> HTMLResponse:
    def acao(_: date) -> str:
        escolhida = categoria_entrada if tipo == "entrada" else categoria_saida
        return acoes.criar_recorrente_de_formulario(
            con, nome=nome, tipo=tipo, dia=dia, valor_estimado=valor_estimado,
            conta_id=conta_id, categoria_id=escolhida or categoria_id)
    return _executar(request, con, "recorrentes", acao)


@router.post("/cadastros/recorrente/{recorrente_id}/desativar", response_class=HTMLResponse)
def desativar(recorrente_id: str, request: Request, con: Any = Depends(get_con)) -> HTMLResponse:
    return _executar(request, con, "recorrentes",
                     lambda hoje: acoes.desativar(con, recorrente_id, hoje))
