"""Holerites: envio de PDF, prévia, confirmação, lista por ano e itens com categoria.

Entre a prévia e a confirmação guarda-se só o HoleriteLido (nunca os bytes do PDF). Os itens são
informativos: nada aqui altera totais de caixa. O HTML nunca traz CPF, CNPJ, nome nem cargo.
"""
import hashlib
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.dominio.holerites import (
    ROTULO, ErroHolerite, HoleriteDiferenteExistente, HoleriteJaImportado, PreviaHolerite,
    alterar_categoria_item, anos_com_holerite, detalhe, importar_holerite, listar_por_ano,
    preparar_previa,
)
from app.dominio.lancamentos import ErroLancamento, interpretar_id, opcoes_categorias
from app.importacao.holerite import ler_holerite
from app.importacao.holerite.modelo import HoleriteIlegivel, HoleriteLido
from app.repo import contas
from app.web.deps import get_con
from app.web.rotas.faturas import MSG_EXPIRADO, _ids_categorias, _ler_pdf, _sem_cache, _texto
from app.web.rotas.importar import ErroImportacao, _pagina_inicial
from app.web.rotas.lancamentos import MSG_INESPERADO
from app.web.uploads import HoleritePendente

log = logging.getLogger(__name__)
router = APIRouter()

MSG_ILEGIVEL = "Não foi possível ler o holerite. Verifique se é o PDF do holerite."
MSG_FALHA = "Não foi possível importar o holerite. Nada foi gravado."
MSG_CONTA = "Selecione a conta corrente que recebe o crédito do holerite."
# Erros de regra com mensagem própria; qualquer outro vira MSG_FALHA (loga só o tipo).
ERROS_REGRA = (ErroHolerite, HoleriteJaImportado, HoleriteDiferenteExistente, ErroLancamento)


def _inicial(request: Request, con: Any, erro: ErroImportacao) -> Response:
    return _sem_cache(_pagina_inicial(request, con, holerite={"erro": erro.mensagem},
                                      status=erro.status))


def _conta_valida(con: Any, texto: str) -> int:
    texto = texto.strip()
    conta = (contas.obter(con, int(texto))
             if texto.isascii() and texto.isdigit() and len(texto) <= 18 else None)
    if conta is None or conta["tipo"] == "cartao":
        raise ErroImportacao(MSG_CONTA)
    return int(conta["id"])


def _ler(conteudo: bytes) -> HoleriteLido:
    try:
        return ler_holerite(conteudo)
    except HoleriteIlegivel as exc:
        raise ErroImportacao(str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - nunca expor conteúdo nem stack trace
        log.warning("event=holerite_leitura_falhou erro=%s", type(exc).__name__)
        raise ErroImportacao(MSG_ILEGIVEL) from None


def _previa_segura(request: Request, con: Any, pendente: HoleritePendente) -> PreviaHolerite:
    try:
        return preparar_previa(con, pendente.holerite, pendente.arquivo_hash, pendente.conta_id,
                               request.app.state.hoje_fn())
    except ERROS_REGRA as exc:
        raise ErroImportacao(str(exc)) from None
    except Exception as exc:  # noqa: BLE001 - nunca expor stack trace nem dados do holerite
        log.error("event=holerite_previa_falhou erro=%s", type(exc).__name__)
        raise ErroImportacao(MSG_FALHA, 500) from None


def _credito_padrao(previa: PreviaHolerite) -> str:
    """Vínculo atual (substituição) > único crédito do extrato > entrada pendente sugerida
    (única perto da data prevista) > vazio (prever entrada)."""
    if previa.vinculo_atual is not None:
        return str(previa.vinculo_atual)
    extrato = [c for c in previa.creditos_candidatos if not c.pendente]
    if len(extrato) == 1:
        return str(extrato[0].lancamento_id)
    if not extrato and previa.pendente_sugerido is not None:
        return str(previa.pendente_sugerido)
    return ""


def _competencia_br(competencia: str) -> str:
    return f"{competencia[5:7]}/{competencia[:4]}"


def _render_previa(request: Request, con: Any, token: str, pendente: HoleritePendente,
                   previa: PreviaHolerite, erro: str | None = None,
                   categorias: list[int | None] | None = None, credito: str | None = None,
                   status: int = 200) -> Response:
    h = pendente.holerite
    escolhidas = categorias if categorias is not None else previa.categorias_sugeridas
    opcoes = opcoes_categorias(con)
    ctx = {
        "ativo": "holerites", "token": token, "h": h, "previa": previa,
        "rotulo": ROTULO.get(h.tipo, h.tipo), "competencia": _competencia_br(h.competencia),
        "conta": contas.obter(con, pendente.conta_id), "conferencia": h.conferencia(),
        "linhas": list(zip(h.itens, escolhidas)), "categorias": opcoes,
        "credito": _credito_padrao(previa) if credito is None else credito, "erro": erro,
    }
    resp = request.app.state.templates.TemplateResponse(request, "holerites/previa.html", ctx,
                                                        status_code=status)
    return _sem_cache(resp)


@router.post("/holerites/previa", response_class=HTMLResponse)
def previa(request: Request, con: Any = Depends(get_con), arquivo: UploadFile | None = File(None),
           conta_id: str = Form("")) -> Response:
    try:
        if arquivo is None or not arquivo.filename:
            raise ErroImportacao("Selecione o holerite em PDF para importar.")
        _, conteudo = _ler_pdf(arquivo, "o holerite")  # o nome do arquivo é descartado
        conta = _conta_valida(con, conta_id)
        holerite = _ler(conteudo)
        arquivo_hash = hashlib.sha256(conteudo).hexdigest()
        pendente = HoleritePendente(arquivo_hash, holerite, conta, 0.0)
        prev = _previa_segura(request, con, pendente)
        token = request.app.state.holerites.guardar(arquivo_hash, holerite, conta)
        return _render_previa(request, con, token, pendente, prev)
    except ErroImportacao as e:
        return _inicial(request, con, e)


def _id_credito(texto: str) -> int | None:
    texto = texto.strip()
    if not texto:
        return None
    if texto.isascii() and texto.isdigit() and len(texto) <= 18:
        return int(texto)
    raise ErroImportacao("Crédito inválido.")


@router.post("/holerites/confirmar", response_class=HTMLResponse)
def confirmar(request: Request, con: Any = Depends(get_con), token: str = Form(""),
              categoria: list[str] = Form(default=[]), credito: str = Form(""),
              substituir: str = Form("")) -> Response:
    pendente = request.app.state.holerites.obter(token) if token else None
    if pendente is None:
        return _inicial(request, con, ErroImportacao(MSG_EXPIRADO))
    try:
        prev = _previa_segura(request, con, pendente)
    except ErroImportacao as e:
        return _inicial(request, con, e)
    escolhidas: list[int | None] | None = None
    try:
        escolhidas = _ids_categorias(categoria, len(pendente.holerite.itens), "do holerite")
        if prev.situacao == "identico":
            raise ErroImportacao(str(HoleriteJaImportado()))
        if prev.situacao == "substitui" and substituir != "1":
            raise ErroImportacao("Marque a opção de substituir o holerite existente para continuar.")
        lancamento_id = _id_credito(credito)
        try:
            # "prever" explícito (vazio) não herda o vínculo antigo da substituição
            resumo = importar_holerite(
                con, pendente.holerite, pendente.arquivo_hash, None, pendente.conta_id,
                request.app.state.hoje_fn(), escolhidas, lancamento_id,
                substituir=substituir == "1", manter_vinculo=lancamento_id is not None)
        except ERROS_REGRA as exc:
            raise ErroImportacao(str(exc)) from None
        except Exception as exc:  # noqa: BLE001 - a transação já fez rollback
            log.error("event=holerite_importacao_falhou erro=%s", type(exc).__name__)
            raise ErroImportacao(MSG_FALHA, 500) from None
    except ErroImportacao as e:
        return _render_previa(request, con, token, pendente, prev, erro=e.mensagem,
                              categorias=escolhidas, credito=credito, status=e.status)
    request.app.state.holerites.descartar(token)
    log.info("event=holerite_importado itens=%d vinculado=%s", resumo.itens, resumo.vinculado)
    return RedirectResponse(f"/holerites?ano={pendente.holerite.competencia[:4]}", status_code=303)


# --- Lista por ano, itens e categoria por item ----------------------------------------------

def _ano(texto: str) -> int | None:
    texto = texto.strip()
    return int(texto) if texto.isascii() and texto.isdigit() and len(texto) == 4 else None


@router.get("/holerites", response_class=HTMLResponse)
def lista(request: Request, con: Any = Depends(get_con), ano: str = "") -> Response:
    anos = anos_com_holerite(con)
    escolhido = _ano(ano) or (anos[0] if anos else request.app.state.hoje_fn().year)
    ctx = {"ativo": "holerites", "ano": escolhido,
           "anos": anos if escolhido in anos else sorted({*anos, escolhido}, reverse=True),
           "linhas": listar_por_ano(con, escolhido)}
    return _sem_cache(request.app.state.templates.TemplateResponse(
        request, "holerites/index.html", ctx))


def _fragmento(request: Request, nome: str, montar: Callable[[], dict[str, Any]],
               evento: str) -> Response:
    """Parcial HTMX; erro -> texto curto em português (a página mostra na área de mensagens)."""
    try:
        ctx = montar()
    except ERROS_REGRA as exc:
        nao_achou = isinstance(exc, ErroHolerite) and str(exc).startswith(("Holerite ", "Item "))
        return _sem_cache(_texto(str(exc), 404 if nao_achou else 400))
    except Exception as exc:  # noqa: BLE001 - nunca expor stack trace
        log.error("event=%s erro=%s", evento, type(exc).__name__)
        return _sem_cache(_texto(MSG_INESPERADO, 500))
    return _sem_cache(request.app.state.templates.TemplateResponse(request, nome, ctx))


@router.get("/holerites/{holerite_id}/itens", response_class=HTMLResponse)
def itens(holerite_id: str, request: Request, con: Any = Depends(get_con)) -> Response:
    def montar() -> dict[str, Any]:
        hol, linhas = detalhe(con, interpretar_id(holerite_id, "Holerite"))
        return {"holerite": hol, "itens": linhas, "categorias": opcoes_categorias(con)}
    return _fragmento(request, "holerites/_itens.html", montar, "holerite_itens_falhou")


@router.post("/holerites/itens/{item_id}/categoria", response_class=HTMLResponse)
def categoria_item(item_id: str, request: Request, con: Any = Depends(get_con),
                   categoria_id: str = Form("")) -> Response:
    """Parcial HTMX: só a célula de categoria do item (os totais não mudam)."""
    def montar() -> dict[str, Any]:
        iid = interpretar_id(item_id)
        cat = interpretar_id(categoria_id, "Categoria") if categoria_id.strip() else None
        alterar_categoria_item(con, iid, cat)
        return {"item": {"id": iid, "categoria_id": cat}, "categorias": opcoes_categorias(con)}
    return _fragmento(request, "holerites/_item_categoria.html", montar,
                      "holerite_item_categoria_falhou")
