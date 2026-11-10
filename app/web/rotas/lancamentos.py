"""Tela Lançamentos: extrato filtrável, edição inline de categoria e ações por linha."""
import logging
from collections.abc import Callable, Mapping
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.dominio import contas_pagar_acoes as acoes
from app.dominio import lancamentos as dom
from app.dominio.casamento import MSG_LINHA_NO_EXTRATO, linha_do_extrato_para
from app.dominio.contas_pagar import resumo_vencidas
from app.dominio.dinheiro import para_centavos, somar_meses
from app.dominio.faturas import ids_previstas_orfas
from app.dominio.faturas_pagamento import (
    FaturaCasavel, PropostaPagamento, fatura_para_pagamento, propor_pagamentos,
)
from app.dominio.lancamentos import ErroLancamento, Filtros, NaoEncontrado
from app.dominio.recorrentes import gerar_recorrentes
from app.repo import contas
from app.web.deps import get_con

log = logging.getLogger(__name__)
router = APIRouter()

MSG_INESPERADO = "Não foi possível concluir a operação. Nada foi alterado."
TIPOS_ROTULO = {"entrada": "Entrada", "saida": "Saída", "transferencia": "Transferência",
                "pagamento_fatura": "Pagamento de fatura", "ajuste": "Ajuste de saldo", "fatura": "Fatura"}
ABAS = {"todos": "Todos", "a-pagar": "A pagar", "realizados": "Realizados"}
MESES_A_PAGAR = 6  # horizonte fixo da aba A pagar
MSG_ABA = "Aba inválida. Use Todos, A pagar ou Realizados."


def _status(exc: Exception) -> int:
    return 404 if isinstance(exc, NaoEncontrado) else 400


def _hoje(request: Request) -> date:
    hoje: date = request.app.state.hoje_fn()
    return hoje


def _aba_de(params: Mapping[str, str]) -> str:
    aba = params.get("aba", "").strip() or "todos"
    if aba not in ABAS:
        raise ErroLancamento(MSG_ABA)
    return aba


def _filtros_da_aba(params: Mapping[str, str], aba: str, hoje: date) -> Filtros:
    """Padrões da aba (A pagar: pendentes vencidos e dos próximos 6 meses; Realizados:
    compensados) só para as chaves que o usuário não informou."""
    padroes: dict[str, str] = {}
    if aba == "a-pagar":
        fim = somar_meses(hoje.replace(day=1), MESES_A_PAGAR) - timedelta(days=1)
        padroes = {"status": "pendentes", "de": "", "ate": fim.isoformat()}
    elif aba == "realizados":
        padroes = {"status": "compensados"}
    return Filtros.de_parametros({**padroes, **params}, hoje)


def _filtros_de(voltar: str, hoje: date) -> tuple[str, Filtros]:
    """Aba e filtros a partir da query string devolvida pelo formulário; inválida -> padrão."""
    params = dict(parse_qsl(voltar[:1000], keep_blank_values=True))
    try:
        aba = _aba_de(params)
        return aba, _filtros_da_aba(params, aba, hoje)
    except ErroLancamento:
        return "todos", Filtros.de_parametros({}, hoje)


def _propostas(con: Any, pagina: dom.PaginaLancamentos) -> dict[int, list[PropostaPagamento]]:
    """Propostas de casamento das faturas a pagar visíveis na página."""
    return {l.fatura_id: propor_pagamentos(con, l.fatura_id) for l in pagina.linhas
            if l.fatura_id is not None and not l.fatura_paga}


def _casaveis(con: Any, pagina: dom.PaginaLancamentos) -> dict[int, FaturaCasavel]:
    """Pagamentos de fatura não casados que já têm uma fatura importada para casar."""
    saida: dict[int, FaturaCasavel] = {}
    for l in pagina.linhas:
        if l.tipo == "pagamento_fatura" and l.fatura_casada_id is None:
            fat = fatura_para_pagamento(con, l.id)
            if fat is not None:
                saida[l.id] = fat
    return saida


def _pagina(request: Request, con: Any, aba: str, filtros: Filtros, *,
            aviso: str | None = None, erro: str | None = None, status: int = 200,
            aberta: int | None = None) -> HTMLResponse:
    hoje = _hoje(request)
    if aba == "a-pagar":
        gerar_recorrentes(con, hoje, MESES_A_PAGAR)
    pagina = dom.listar(con, filtros)
    base = {"aba": aba, **filtros.como_parametros(pagina.pagina)}
    n_vencidas, total_vencidas = resumo_vencidas(con, hoje)
    ctx = {
        "aba": aba, "abas": ABAS, "n_vencidas": n_vencidas,
        "total_vencidas_centavos": total_vencidas,
        "propostas": _propostas(con, pagina), "casaveis": _casaveis(con, pagina),
        "orfas": ids_previstas_orfas(con),
        "aberta": aberta,
        "ativo": "lancamentos", "pagina": pagina, "filtros": filtros, "contas": contas.listar(con),
        "categorias": dom.opcoes_categorias(con),
        "categorias_saida": dom.opcoes_categorias(con, "saida"),
        "categorias_entrada": dom.opcoes_categorias(con, "entrada"), "tipos": TIPOS_ROTULO, "aviso": aviso,
        "erro": erro, "voltar": urlencode(base), "hoje": hoje.isoformat(),
        "anterior": urlencode({**base, "pagina": str(pagina.pagina - 1)}) if pagina.pagina > 1 else None,
        "proxima": urlencode({**base, "pagina": str(pagina.pagina + 1)})
        if pagina.pagina < pagina.paginas else None,
    }
    return request.app.state.templates.TemplateResponse(
        request, "lancamentos/index.html", ctx, status_code=status)


def executar(request: Request, con: Any, voltar: str, acao: Callable[[], str],
             aberta: int | None = None) -> HTMLResponse:
    """Roda a ação e re-renderiza o extrato com aviso ou erro em português.
    aberta: fatura que volta expandida."""
    aba, filtros = _filtros_de(voltar, _hoje(request))
    try:
        aviso = acao()
    except ErroLancamento as exc:
        return _pagina(request, con, aba, filtros, erro=str(exc), status=_status(exc),
                       aberta=aberta)
    except Exception as exc:  # noqa: BLE001 - nunca expor stack trace
        log.error("event=lancamentos_falhou erro=%s", type(exc).__name__)
        return _pagina(request, con, aba, filtros, erro=MSG_INESPERADO, status=500)
    return _pagina(request, con, aba, filtros, aviso=aviso, aberta=aberta)


@router.get("/contas-a-pagar")
def contas_a_pagar_antiga() -> RedirectResponse:
    """A tela Contas a pagar virou a aba A pagar de Lançamentos."""
    return RedirectResponse("/lancamentos?aba=a-pagar", status_code=303)


@router.get("/lancamentos", response_class=HTMLResponse)
def extrato(request: Request, con: Any = Depends(get_con)) -> HTMLResponse:
    params = dict(request.query_params)
    try:
        aba = _aba_de(params)
        filtros = _filtros_da_aba(params, aba, _hoje(request))
    except ErroLancamento as exc:
        return _pagina(request, con, "todos", Filtros.de_parametros({}, _hoje(request)),
                       erro=str(exc), status=400)
    try:
        return _pagina(request, con, aba, filtros)
    except Exception as exc:  # noqa: BLE001
        log.error("event=extrato_falhou erro=%s", type(exc).__name__)
        return HTMLResponse(MSG_INESPERADO, status_code=500)


@router.post("/lancamentos/novo", response_class=HTMLResponse)
def novo(request: Request, con: Any = Depends(get_con), data: str = Form(""), descricao: str = Form(""),
         valor: str = Form(""), sinal: str = Form(""), conta_id: str = Form(""),
         categoria_id: str = Form(""), categoria_entrada: str = Form(""),
         categoria_saida: str = Form(""), observacao: str = Form(""), modo: str = Form("avista"),
         n_parcelas: str = Form(""), ja_pago: str = Form(""),
         voltar: str = Form("")) -> HTMLResponse:
    def acao() -> str:
        escolhida = categoria_entrada if sinal == "entrada" else categoria_saida
        return dom.criar_avulso_de_formulario(
            con, modo=modo, data=data, descricao=descricao, valor=valor, sinal=sinal,
            conta_id=conta_id, categoria_id=escolhida or categoria_id, observacao=observacao,
            ja_pago=bool(ja_pago), n_parcelas=n_parcelas, hoje=_hoje(request))
    return executar(request, con, voltar, acao)


@router.post("/lancamentos/lote", response_class=HTMLResponse)
def lote(request: Request, con: Any = Depends(get_con), ids: list[str] = Form(default=[]),
         categoria_id: str = Form(""), voltar: str = Form("")) -> HTMLResponse:
    def acao() -> str:
        n = dom.categorizar_em_lote(con, [dom.interpretar_id(i) for i in ids],
                                    dom.interpretar_id(categoria_id, "Categoria"))
        return f"{n} lançamento categorizado." if n == 1 else f"{n} lançamentos categorizados."
    return executar(request, con, voltar, acao)


@router.post("/lancamentos/grupo/{grupo}/editar", response_class=HTMLResponse)
def editar_grupo(grupo: str, request: Request, con: Any = Depends(get_con),
                 valor_total: str = Form(""), n_parcelas: str = Form(""),
                 voltar: str = Form("")) -> HTMLResponse:
    return executar(request, con, voltar,
                    lambda: acoes.editar_grupo(con, grupo, valor_total, n_parcelas))


@router.post("/lancamentos/grupo/{grupo}/excluir", response_class=HTMLResponse)
def excluir_grupo(grupo: str, request: Request, con: Any = Depends(get_con),
                  voltar: str = Form("")) -> HTMLResponse:
    return executar(request, con, voltar, lambda: acoes.excluir_grupo(con, grupo))


@router.post("/lancamentos/{lancamento_id}/pular", response_class=HTMLResponse)
def pular(lancamento_id: str, request: Request, con: Any = Depends(get_con),
          voltar: str = Form("")) -> HTMLResponse:
    return executar(request, con, voltar,
                    lambda: acoes.pular(con, lancamento_id, _hoje(request)))


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
    return executar(request, con, voltar, acao)


@router.post("/lancamentos/{lancamento_id}/tipo", response_class=HTMLResponse)
def mudar_tipo(lancamento_id: str, request: Request, con: Any = Depends(get_con),
         tipo: str = Form(""), voltar: str = Form("")) -> HTMLResponse:
    def acao() -> str:
        dom.alterar_tipo(con, dom.interpretar_id(lancamento_id), tipo)
        return "Tipo atualizado."
    return executar(request, con, voltar, acao)


@router.post("/lancamentos/{lancamento_id}/excluir", response_class=HTMLResponse)
def excluir(lancamento_id: str, request: Request, con: Any = Depends(get_con),
            voltar: str = Form("")) -> HTMLResponse:
    def acao() -> str:
        dom.excluir_manual(con, dom.interpretar_id(lancamento_id), request.app.state.hoje_fn())
        return "Lançamento excluído."
    return executar(request, con, voltar, acao)


@router.post("/lancamentos/{lancamento_id}/descartar", response_class=HTMLResponse)
def descartar(lancamento_id: str, request: Request, con: Any = Depends(get_con),
              voltar: str = Form("")) -> HTMLResponse:
    def acao() -> str:
        dom.descartar_prevista(con, dom.interpretar_id(lancamento_id))
        return "Parcela prevista descartada."
    return executar(request, con, voltar, acao)


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
        dom.pagar_pendente(con, lid, real, dia, hoje=_hoje(request))
        if linha_do_extrato_para(con, lid):
            return f"Pagamento registrado. {MSG_LINHA_NO_EXTRATO}"
        return "Pagamento registrado."
    return executar(request, con, voltar, acao)
