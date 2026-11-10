"""Ações de categorias (a tela fica em Cadastros): adicionar, renomear, mover, mudar tipo e excluir (com substituição)."""
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.dominio import categorias as dom
from app.dominio.categorias import ErroCategoria
from app.dominio.lancamentos import ErroLancamento, NaoEncontrado, interpretar_id, opcoes_categorias
from app.repo import categorias as repo
from app.web.deps import get_con
from app.web.rotas.cadastros import pagina_cadastros

log = logging.getLogger(__name__)
router = APIRouter()

MSG_INESPERADO = "Não foi possível concluir a operação. Nada foi alterado."
ABAS = ("entrada", "saida")


def _aba(valor: str | None) -> str:
    return valor if valor in ABAS else "saida"


def _pagina(request: Request, con: Any, aba: str, *, aviso: str | None = None,
            erro: str | None = None, status: int = 200,
            excluindo: dict[str, Any] | None = None) -> HTMLResponse:
    return pagina_cadastros(request, con, "categorias", aviso=aviso, erro=erro, status=status,
                            aba=aba, excluindo=excluindo)


def _seguro(request: Request, con: Any, aba: str, **kw: Any) -> HTMLResponse:
    try:
        return _pagina(request, con, aba, **kw)
    except Exception as exc:  # noqa: BLE001 - nunca expor stack trace
        log.error("event=categorias_render_falhou erro=%s", type(exc).__name__)
        return HTMLResponse(MSG_INESPERADO, status_code=500)


def _exigir(con: Any, categoria_id: str) -> tuple[int, Any]:
    cid = interpretar_id(categoria_id, "Categoria")
    row = repo.obter(con, cid)
    if row is None:
        raise NaoEncontrado("Categoria não encontrada.")
    return cid, row


def _executar(request: Request, con: Any, aba: str, acao: Callable[[], str]) -> HTMLResponse:
    """Roda a ação e re-renderiza a tela com aviso ou erro em português."""
    aba = _aba(aba)
    try:
        aviso = acao()
    except (ErroCategoria, ErroLancamento) as exc:
        status = 404 if isinstance(exc, NaoEncontrado) else 400
        con.rollback()
        return _seguro(request, con, aba, erro=str(exc), status=status)
    except Exception as exc:  # noqa: BLE001
        log.error("event=categorias_falhou erro=%s", type(exc).__name__)
        con.rollback()
        return _seguro(request, con, aba, erro=MSG_INESPERADO, status=500)
    return _seguro(request, con, aba, aviso=aviso)


@router.get("/categorias")
def tela(request: Request) -> RedirectResponse:
    aba = _aba(request.query_params.get("aba"))
    return RedirectResponse(f"/cadastros?secao=categorias&aba={aba}", status_code=303)


@router.post("/categorias/criar", response_class=HTMLResponse)
def criar(request: Request, con: Any = Depends(get_con), nome: str = Form(""),
          tipo: str = Form(""), aba: str = Form("")) -> HTMLResponse:
    def acao() -> str:
        dom.criar_categoria(con, nome, tipo)
        return "Categoria criada."
    return _executar(request, con, tipo if tipo in ABAS else aba, acao)


@router.post("/categorias/{categoria_id}/subcategoria", response_class=HTMLResponse)
def subcategoria(categoria_id: str, request: Request, con: Any = Depends(get_con),
                 nome: str = Form(""), aba: str = Form("")) -> HTMLResponse:
    def acao() -> str:
        cid, _ = _exigir(con, categoria_id)
        dom.criar_subcategoria(con, cid, nome)
        return "Subcategoria criada."
    return _executar(request, con, aba, acao)


@router.post("/categorias/{categoria_id}/renomear", response_class=HTMLResponse)
def renomear(categoria_id: str, request: Request, con: Any = Depends(get_con),
             nome: str = Form(""), aba: str = Form("")) -> HTMLResponse:
    def acao() -> str:
        cid, _ = _exigir(con, categoria_id)
        dom.renomear_categoria(con, cid, nome)
        return "Nome atualizado."
    return _executar(request, con, aba, acao)


@router.post("/categorias/{categoria_id}/tipo", response_class=HTMLResponse)
def mudar_tipo(categoria_id: str, request: Request, con: Any = Depends(get_con),
         tipo: str = Form(""), aba: str = Form("")) -> HTMLResponse:
    def acao() -> str:
        cid, _ = _exigir(con, categoria_id)
        dom.alterar_tipo(con, cid, tipo)
        return "Tipo atualizado (as subcategorias acompanham)."
    return _executar(request, con, tipo if tipo in ABAS else aba, acao)


@router.post("/categorias/{categoria_id}/mover", response_class=HTMLResponse)
def mover(categoria_id: str, request: Request, con: Any = Depends(get_con),
          destino_id: str = Form(""), aba: str = Form("")) -> HTMLResponse:
    def acao() -> str:
        cid, _ = _exigir(con, categoria_id)
        destino = interpretar_id(destino_id, "Categoria de destino")
        if repo.obter(con, destino) is None:
            raise NaoEncontrado("Categoria de destino não encontrada.")
        dom.mover_subcategoria(con, cid, destino)
        return "Subcategoria movida."
    return _executar(request, con, aba, acao)


@router.post("/categorias/{categoria_id}/excluir", response_class=HTMLResponse)
def excluir(categoria_id: str, request: Request, con: Any = Depends(get_con),
            acao: str = Form(""), substituir_por: str = Form(""),
            aba: str = Form("")) -> HTMLResponse:
    aba = _aba(aba)
    try:
        cid, cat = _exigir(con, categoria_id)
        aba = str(cat["tipo"])
        uso = repo.uso(con, cid)
        if repo.tem_filhas(con, cid):
            raise ErroCategoria("Remova ou mova as subcategorias antes de excluir a categoria.")
        if acao not in ("substituir", "sem") and sum(uso.values()):
            # passo de confirmação: nada é apagado sem escolha explícita
            nome = next(r[1] for r in opcoes_categorias(con, aba) if r[0] == cid)
            destinos = [o for o in opcoes_categorias(con, aba) if o[0] != cid]
            return _seguro(request, con, aba, excluindo={
                "id": cid, "nome": nome, "uso": uso, "destinos": destinos})
    except (ErroCategoria, ErroLancamento) as exc:
        status = 404 if isinstance(exc, NaoEncontrado) else 400
        return _seguro(request, con, aba, erro=str(exc), status=status)

    def executar() -> str:
        if acao == "substituir":
            destino = interpretar_id(substituir_por, "Categoria substituta")
            dom.remover_categoria(con, cid, substituir_por=destino)
        else:
            dom.remover_categoria(con, cid, deixar_sem=acao == "sem")
        return "Categoria excluída."
    return _executar(request, con, aba, executar)
