"""Importar fatura de cartão em PDF: envio (+ senha opcional), prévia e confirmação.

A senha do PDF só serve para abrir o arquivo nesta requisição: nunca vai para armazém,
log nem HTML. Entre a prévia e a confirmação guarda-se só a FaturaLida (sem bytes do PDF).
"""
import calendar
import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.dominio.faturas import (
    FaturaDiferenteExistente, FaturaJaImportada, PreviaFatura, RegistroNaoEncontrado,
    alterar_categoria_item, categorizar_itens_da_fatura, detalhe, importar_fatura, nome_cartao,
    preparar_previa,
)
from app.dominio.faturas_pagamento import confirmar_pagamento, desfazer_pagamento, propor_pagamentos
from app.dominio.lancamentos import (
    ErroLancamento, NaoEncontrado, interpretar_id, opcoes_categorias,
)
from app.importacao.fatura import ler_fatura
from app.importacao.fatura.modelo import FaturaIlegivel, FaturaLida, PdfProtegido, SenhaIncorreta
from app.repo import contas
from app.web.deps import get_con
from app.web.rotas.importar import LIMITE_BYTES, ErroImportacao, _pagina_inicial
from app.web.rotas.lancamentos import MSG_INESPERADO, executar
from app.web.uploads import FaturaPendente

log = logging.getLogger(__name__)
router = APIRouter()

MSG_EXPIRADO = "Envio expirado ou não encontrado. Envie o arquivo novamente."
MSG_ILEGIVEL = "Não foi possível ler a fatura. Verifique se é o PDF da fatura do cartão."
MSG_FALHA = "Não foi possível importar a fatura. Nada foi gravado."
ROTULO_TIPO = {"compra": "Compra", "servico": "Serviço", "internacional": "Internacional",
               "encargo": "Encargo", "credito": "Crédito"}


class _PedirSenha(ErroImportacao):
    """PDF protegido ou senha errada: o formulário volta pedindo a senha (arquivo guardado)."""

    def __init__(self, mensagem: str, token: str, nome: str) -> None:
        super().__init__(mensagem)
        self.token = token
        self.nome = nome


def _sem_cache(resp: Response) -> Response:
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _inicial(request: Request, con: Any, erro: ErroImportacao) -> Response:
    ctx: dict[str, Any] = {"erro": erro.mensagem}
    if isinstance(erro, _PedirSenha):
        ctx.update(token=erro.token, nome=erro.nome, pedir_senha=True)
    return _sem_cache(_pagina_inicial(request, con, fatura=ctx, status=erro.status))


def _ler_pdf(arquivo: UploadFile) -> tuple[str, bytes]:
    nome = Path((arquivo.filename or "").replace("\\", "/")).name[:200]
    if Path(nome).suffix.lower() != ".pdf":
        raise ErroImportacao("Formato não aceito. Envie a fatura em .pdf.")
    conteudo = arquivo.file.read(LIMITE_BYTES + 1)
    if len(conteudo) > LIMITE_BYTES:
        raise ErroImportacao("O arquivo excede o limite de 5 MB.", 413)
    if not conteudo.strip():
        raise ErroImportacao("O arquivo está vazio.")
    return nome, conteudo


def _abrir(request: Request, nome: str, conteudo: bytes, senha: str, token: str) -> FaturaLida:
    """Lê a fatura; protegida/senha errada guarda os bytes (sem a senha) para pedir a senha."""
    try:
        return ler_fatura(conteudo, senha or None)
    except (PdfProtegido, SenhaIncorreta) as exc:
        if not token:
            token = request.app.state.uploads.guardar(nome, conteudo)
        raise _PedirSenha(str(exc), token, nome) from None
    except Exception as exc:  # noqa: BLE001 - nunca expor conteúdo nem stack trace
        if token:  # senha certa, mas a fatura não serve: o arquivo guardado sai da memória
            request.app.state.uploads.descartar(token)
        if isinstance(exc, FaturaIlegivel):
            raise ErroImportacao(str(exc)) from None
        log.warning("event=fatura_leitura_falhou erro=%s", type(exc).__name__)
        raise ErroImportacao(MSG_ILEGIVEL) from None


def previa_de_upload(request: Request, con: Any, arquivo: UploadFile | None, token: str,
                     senha: str) -> Response:
    """Envio novo (ou reenvio da senha de um envio guardado) -> prévia da fatura."""
    try:
        if arquivo is not None and arquivo.filename:
            nome, conteudo = _ler_pdf(arquivo)
            token = ""
        elif token:
            envio = request.app.state.uploads.obter(token)
            if envio is None:
                raise ErroImportacao(MSG_EXPIRADO)
            nome, conteudo = envio.nome, envio.conteudo
        else:
            raise ErroImportacao("Selecione a fatura em PDF para importar.")
        fatura = _abrir(request, nome, conteudo, senha, token)
        if token:
            request.app.state.uploads.descartar(token)
        arquivo_hash = hashlib.sha256(conteudo).hexdigest()
        previa = preparar_previa(con, fatura, arquivo_hash)
        novo = request.app.state.faturas.guardar(nome, arquivo_hash, fatura)
        return _render_previa(request, con, novo, FaturaPendente(nome, arquivo_hash, fatura, 0.0),
                              previa)
    except ErroImportacao as e:
        return _inicial(request, con, e)


@dataclass(frozen=True)
class _Conferencia:
    itens: int
    creditos: int
    encargos: int
    ajustes: int
    calculado: int
    impresso: int
    diferenca: int
    confere: bool


def _conferencia(f: FaturaLida) -> _Conferencia:
    c = f.conferencia()
    despesas = [i for i in f.itens if i.valor_centavos < 0]
    return _Conferencia(
        itens=-sum(i.valor_centavos for i in despesas if i.tipo != "encargo"),
        creditos=sum(i.valor_centavos for i in f.itens if i.valor_centavos > 0),
        encargos=-sum(i.valor_centavos for i in despesas if i.tipo == "encargo"),
        ajustes=c.ajustes_resumo_centavos, calculado=c.calculado_centavos + c.ajustes_resumo_centavos,
        impresso=c.impresso_centavos, diferenca=c.diferenca_centavos, confere=c.confere)


def _render_previa(request: Request, con: Any, token: str, pendente: FaturaPendente,
                   previa: PreviaFatura, erro: str | None = None,
                   categorias: list[int | None] | None = None, status: int = 200) -> Response:
    f = pendente.fatura
    conta = None if previa.conta_id is None else contas.obter(con, previa.conta_id)
    escolhidas = categorias if categorias is not None else previa.categorias_sugeridas
    opcoes = opcoes_categorias(con)
    rotulos = {cid: rotulo for cid, rotulo, _ in opcoes}
    ctx = {
        "ativo": "importar", "token": token, "nome": pendente.nome, "f": f, "previa": previa,
        "conta": conta, "nome_sugerido": nome_cartao(f), "conferencia": _conferencia(f),
        "linhas": list(zip(f.itens, escolhidas)), "categorias": opcoes, "rotulos": rotulos,
        "tipos": ROTULO_TIPO, "erro": erro,
    }
    resp = request.app.state.templates.TemplateResponse(request, "faturas/previa.html", ctx,
                                                        status_code=status)
    return _sem_cache(resp)


@router.post("/faturas/previa", response_class=HTMLResponse)
def previa(request: Request, con: Any = Depends(get_con), arquivo: UploadFile | None = File(None),
           token: str = Form(""), senha: str = Form("")) -> Response:
    return previa_de_upload(request, con, arquivo, token, senha)


def _ids_categorias(textos: list[str], quantidade: int) -> list[int | None]:
    if len(textos) != quantidade:
        raise ErroImportacao("A lista de categorias não corresponde aos itens da fatura.")
    ids: list[int | None] = []
    for t in textos:
        t = t.strip()
        if not t:
            ids.append(None)
        elif t.isascii() and t.isdigit() and len(t) <= 18:
            ids.append(int(t))
        else:
            raise ErroImportacao("Categoria inválida.")
    return ids


def _destino(conta_id: int, vencimento: str) -> str:
    d = date.fromisoformat(vencimento)
    ultimo = calendar.monthrange(d.year, d.month)[1]
    return f"/lancamentos?conta_id={conta_id}&de={d:%Y-%m}-01&ate={d:%Y-%m}-{ultimo:02d}"


@router.post("/faturas/confirmar", response_class=HTMLResponse)
def confirmar(request: Request, con: Any = Depends(get_con), token: str = Form(""),
              categoria: list[str] = Form(default=[]), criar_conta: str = Form(""),
              substituir: str = Form("")) -> Response:
    pendente = request.app.state.faturas.obter(token) if token else None
    if pendente is None:
        return _inicial(request, con, ErroImportacao(MSG_EXPIRADO))
    previa = preparar_previa(con, pendente.fatura, pendente.arquivo_hash)
    escolhidas: list[int | None] | None = None
    try:
        escolhidas = _ids_categorias(categoria, len(pendente.fatura.itens))
        if previa.situacao == "identica":
            raise ErroImportacao(str(FaturaJaImportada()))
        if previa.conta_id is None and criar_conta != "1":
            raise ErroImportacao("Marque “Criar conta-cartão” para continuar.")
        if previa.situacao == "substitui" and substituir != "1":
            raise ErroImportacao("Marque a opção de substituir a fatura existente para continuar.")
        try:
            resumo = importar_fatura(con, pendente.fatura, pendente.arquivo_hash, pendente.nome,
                                     previa.conta_id, request.app.state.hoje_fn(), escolhidas,
                                     substituir=substituir == "1")
        except (FaturaJaImportada, FaturaDiferenteExistente, ValueError) as exc:
            raise ErroImportacao(str(exc)) from None
        except Exception as exc:  # noqa: BLE001 - a transação já fez rollback
            log.error("event=fatura_importacao_falhou erro=%s", type(exc).__name__)
            raise ErroImportacao(MSG_FALHA, 500) from None
    except ErroImportacao as e:
        return _render_previa(request, con, token, pendente, previa, erro=e.mensagem,
                              categorias=escolhidas, status=e.status)
    request.app.state.faturas.descartar(token)
    log.info("event=fatura_importada itens=%d", resumo.itens)
    return RedirectResponse(_destino(resumo.conta_id, pendente.fatura.data_vencimento),
                            status_code=303)


# --- Fatura em Lançamentos: itens, categoria por item e casamento do pagamento -------------

def _erro_web(exc: ValueError) -> ErroLancamento:
    """Erro de regra do domínio de faturas -> erro da tela (inexistente = 404)."""
    if isinstance(exc, ErroLancamento):
        return exc
    return NaoEncontrado(str(exc)) if isinstance(exc, RegistroNaoEncontrado) else ErroLancamento(
        str(exc))


def _texto(mensagem: str, status: int) -> Response:
    return Response(mensagem, status_code=status, media_type="text/plain; charset=utf-8")


def _fragmento(request: Request, nome: str, ctx: dict[str, Any], evento: str,
               montar: Callable[[], dict[str, Any]]) -> Response:
    """Parcial HTMX; erro -> texto curto em português (a página mostra na área de mensagens)."""
    try:
        ctx.update(montar())
    except ValueError as exc:
        erro = _erro_web(exc)
        return _texto(str(erro), 404 if isinstance(erro, NaoEncontrado) else 400)
    except Exception as exc:  # noqa: BLE001 - nunca expor stack trace
        log.error("event=%s erro=%s", evento, type(exc).__name__)
        return _texto(MSG_INESPERADO, 500)
    return request.app.state.templates.TemplateResponse(request, nome, ctx)


@router.get("/faturas/{fatura_id}/itens", response_class=HTMLResponse)
def itens(fatura_id: str, request: Request, con: Any = Depends(get_con),
          voltar: str = "") -> Response:
    def montar() -> dict[str, Any]:
        fat, linhas = detalhe(con, interpretar_id(fatura_id, "Fatura"))
        return {"fatura": fat, "itens": linhas, "categorias": opcoes_categorias(con)}
    return _fragmento(request, "faturas/_itens.html", {"voltar": voltar[:1000]},
                      "fatura_itens_falhou", montar)


@router.post("/faturas/itens/{item_id}/categoria", response_class=HTMLResponse)
def categoria_item(item_id: str, request: Request, con: Any = Depends(get_con),
                   categoria_id: str = Form("")) -> Response:
    """Parcial HTMX: só a célula de categoria do item (o total da fatura não muda)."""
    def montar() -> dict[str, Any]:
        iid = interpretar_id(item_id)
        cat = interpretar_id(categoria_id, "Categoria") if categoria_id.strip() else None
        alterar_categoria_item(con, iid, cat)
        return {"item": {"id": iid, "categoria_id": cat}, "categorias": opcoes_categorias(con)}
    return _fragmento(request, "faturas/_item_categoria.html", {}, "fatura_item_categoria_falhou",
                      montar)


@router.post("/faturas/{fatura_id}/itens/categoria-lote", response_class=HTMLResponse)
def categoria_lote(fatura_id: str, request: Request, con: Any = Depends(get_con),
                   ids: list[str] = Form(default=[]), categoria_id: str = Form(""),
                   voltar: str = Form("")) -> HTMLResponse:
    try:
        fid: int | None = interpretar_id(fatura_id, "Fatura")
    except ErroLancamento:
        fid = None

    def acao() -> str:
        alvo = interpretar_id(fatura_id, "Fatura")
        lista = [interpretar_id(i) for i in ids]
        cat = interpretar_id(categoria_id, "Categoria")
        if not lista:
            raise ErroLancamento("Selecione ao menos um item.")
        try:
            n = categorizar_itens_da_fatura(con, alvo, lista, cat)
        except ValueError as exc:
            raise _erro_web(exc) from None
        return f"{n} item categorizado." if n == 1 else f"{n} itens categorizados."
    return executar(request, con, voltar, acao, aberta=fid)


@router.get("/faturas/{fatura_id}/pagamento", response_class=HTMLResponse)
def propostas(fatura_id: str, request: Request, con: Any = Depends(get_con),
              voltar: str = "") -> Response:
    def montar() -> dict[str, Any]:
        fid = interpretar_id(fatura_id, "Fatura")
        return {"fatura_id": fid, "lista": propor_pagamentos(con, fid)}
    return _fragmento(request, "faturas/_casamento.html", {"voltar": voltar[:1000]},
                      "fatura_propostas_falhou", montar)


@router.post("/faturas/{fatura_id}/pagamento/confirmar", response_class=HTMLResponse)
def pagamento_confirmar(fatura_id: str, request: Request, con: Any = Depends(get_con),
                        lancamento_id: str = Form(""), voltar: str = Form("")) -> HTMLResponse:
    def acao() -> str:
        fid = interpretar_id(fatura_id, "Fatura")
        lid = interpretar_id(lancamento_id)
        try:
            confirmar_pagamento(con, fid, lid)
        except ValueError as exc:
            raise _erro_web(exc) from None
        log.info("event=fatura_pagamento_casado")
        return "Pagamento casado: fatura paga."
    return executar(request, con, voltar, acao)


@router.post("/faturas/{fatura_id}/pagamento/desfazer", response_class=HTMLResponse)
def pagamento_desfazer(fatura_id: str, request: Request, con: Any = Depends(get_con),
                       voltar: str = Form("")) -> HTMLResponse:
    def acao() -> str:
        try:
            desfazer_pagamento(con, interpretar_id(fatura_id, "Fatura"))
        except ValueError as exc:
            raise _erro_web(exc) from None
        log.info("event=fatura_pagamento_desfeito")
        return "Pagamento desfeito: a fatura voltou a pendente."
    return executar(request, con, voltar, acao)
