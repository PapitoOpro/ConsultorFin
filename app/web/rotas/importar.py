"""Tela Importar: envio, prévia, confirmação, conciliação, casamento e histórico."""
import logging
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.dominio.casamento import confirmar_casamento
from app.dominio.dinheiro import para_centavos
from app.dominio.saldo import conciliar
from app.importacao.base import Parser, ResultadoLeitura
from app.importacao.deteccao import detectar
from app.importacao.excel import ArquivoIlegivel, ParserExcel, listar_colunas
from app.importacao.servico import (
    ArquivoJaImportado, conta_detectada, desfazer_importacao, importar,
    propostas_com_detalhes, simular_importacao,
)
from app.repo import contas, importacoes
from app.web.deps import get_con
from app.web.uploads import Envio

log = logging.getLogger(__name__)
router = APIRouter()

LIMITE_BYTES = 5 * 1024 * 1024
EXTENSOES = {".csv", ".xls", ".xlsx"}
TIPOS_CONTA = {"corrente": "Conta corrente", "poupanca": "Poupança", "cartao": "Cartão"}
LINHAS_PREVIA = 10
MSG_ILEGIVEL = "Não foi possível ler o arquivo. Verifique se é um CSV ou uma planilha Excel válida."


class ErroImportacao(Exception):
    """Erro de validação mostrado ao usuário (mensagem em português)."""

    def __init__(self, mensagem: str, status: int = 400) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status


def _render(request: Request, nome: str, ctx: dict[str, Any], status: int = 200) -> HTMLResponse:
    ctx = {"ativo": "importar", **ctx}
    return request.app.state.templates.TemplateResponse(request, f"importar/{nome}", ctx, status_code=status)


def _pagina_inicial(request: Request, con: Any, erro: str | None = None, aviso: str | None = None,
                    status: int = 200) -> HTMLResponse:
    return _render(request, "index.html", {
        "contas": contas.listar(con), "historico": importacoes.listar_com_conta(con),
        "tipos": TIPOS_CONTA, "erro": erro, "aviso": aviso}, status)


def _ler_upload(arquivo: UploadFile) -> tuple[str, bytes]:
    nome = Path((arquivo.filename or "").replace("\\", "/")).name[:200]
    if Path(nome).suffix.lower() not in EXTENSOES:
        raise ErroImportacao("Formato não aceito. Envie um arquivo .csv, .xls ou .xlsx.")
    conteudo = arquivo.file.read(LIMITE_BYTES + 1)
    if len(conteudo) > LIMITE_BYTES:
        raise ErroImportacao("O arquivo excede o limite de 5 MB.", 413)
    if not conteudo.strip():
        raise ErroImportacao("O arquivo está vazio.")
    return nome, conteudo


def _mapa(data: str, descricao: str, valor: str) -> dict[str, str] | None:
    if data and descricao and valor:
        return {"data": data, "descricao": descricao, "valor": valor}
    return None


def _parser(nome: str, conteudo: bytes, mapa: dict[str, str] | None) -> Parser | None:
    """Parser pelo mapeamento manual ou pela detecção automática (revalidado a cada passo)."""
    if mapa is not None:
        return ParserExcel(mapa)
    return detectar(conteudo, nome)


def _ler(parser: Parser, conteudo: bytes) -> ResultadoLeitura:
    try:
        return parser.ler(conteudo)
    except Exception as exc:  # noqa: BLE001 - nunca expor stack trace ao usuário
        log.warning("event=leitura_falhou erro=%s", type(exc).__name__)
        raise ErroImportacao(MSG_ILEGIVEL) from exc


def _envio(request: Request, token: str) -> Envio:
    envio = request.app.state.uploads.obter(token) if token else None
    if envio is None:
        raise ErroImportacao("Envio expirado ou não encontrado. Envie o arquivo novamente.")
    return envio


def _conta_id(con: Any, texto: str) -> int | None:
    texto = texto.strip()
    if not (texto.isascii() and texto.isdigit() and len(texto) <= 18):
        return None
    conta = contas.obter(con, int(texto))
    return None if conta is None else int(conta["id"])


def _previa(request: Request, con: Any, token: str, envio: Envio, conta_txt: str,
            mapa: dict[str, str] | None) -> HTMLResponse:
    parser = _parser(envio.nome, envio.conteudo, mapa)
    lista_contas = contas.listar(con)
    base: dict[str, Any] = {"token": token, "nome": envio.nome, "mapa": mapa,
                            "tipos": TIPOS_CONTA, "contas": lista_contas}
    if parser is None:
        try:
            colunas, amostra = listar_colunas(envio.conteudo)
        except ArquivoIlegivel as exc:
            raise ErroImportacao(str(exc)) from exc
        return _render(request, "mapeamento.html", {**base, "colunas": colunas, "amostra": amostra,
                                                    "conta_id": conta_txt})
    leitura = _ler(parser, envio.conteudo)
    conta_id = _conta_id(con, conta_txt)
    detectada = conta_detectada(con, envio.conteudo, parser)
    if conta_id is None and detectada is not None:
        conta_id = detectada
    elif conta_id is None and len(lista_contas) == 1:
        conta_id = int(lista_contas[0]["id"])
    ctx = {**base, "conta_id": conta_id, "detectada": detectada is not None and detectada == conta_id,
           "leitura": leitura, "amostra": leitura.linhas[:LINHAS_PREVIA], "resumo": None,
           "ja_importado": None, "sugestao": leitura.saldo_anterior}
    if conta_id is not None:
        try:
            ctx["resumo"] = simular_importacao(con, conta_id, envio.nome, envio.conteudo, parser)
        except ArquivoJaImportado as exc:
            ctx["ja_importado"] = str(exc)
    return _render(request, "previa.html", ctx)


@router.get("/importar", response_class=HTMLResponse)
def pagina(request: Request, con: Any = Depends(get_con)) -> HTMLResponse:
    return _pagina_inicial(request, con)


@router.post("/importar/previa", response_class=HTMLResponse)
def previa(request: Request, con: Any = Depends(get_con),
           arquivo: UploadFile | None = File(None), token: str = Form(""),
           conta_id: str = Form(""), mapa_data: str = Form(""), mapa_descricao: str = Form(""),
           mapa_valor: str = Form("")) -> HTMLResponse:
    try:
        if arquivo is not None and arquivo.filename:
            nome, conteudo = _ler_upload(arquivo)
            token = request.app.state.uploads.guardar(nome, conteudo)
            envio = _envio(request, token)
        elif token:
            envio = _envio(request, token)
        else:
            raise ErroImportacao("Selecione um arquivo para importar.")
        return _previa(request, con, token, envio, conta_id, _mapa(mapa_data, mapa_descricao, mapa_valor))
    except ErroImportacao as e:
        return _pagina_inicial(request, con, erro=e.mensagem, status=e.status)


@router.post("/importar/conta", response_class=HTMLResponse)
def criar_conta_rota(request: Request, con: Any = Depends(get_con), nome: str = Form(""),
                     tipo: str = Form("corrente"), saldo_inicial: str = Form("0,00"),
                     data_saldo_inicial: str = Form(""), final_numero: str = Form(""),
                     token: str = Form("")) -> Response:
    try:
        nome = nome.strip()[:80]
        if not nome:
            raise ErroImportacao("Informe o nome da conta.")
        if tipo not in TIPOS_CONTA:
            raise ErroImportacao("Tipo de conta inválido.")
        try:
            saldo = para_centavos(saldo_inicial)
        except ValueError as exc:
            raise ErroImportacao("Saldo inicial inválido.") from exc
        try:
            data_iso = date.fromisoformat(data_saldo_inicial).isoformat()
        except ValueError as exc:
            raise ErroImportacao("Data do saldo inicial inválida.") from exc
        envio = _envio(request, token) if token else None
        novo_id = contas.criar_conta(con, nome, tipo, final_numero=final_numero.strip()[:20] or None,
                                     saldo_inicial_centavos=saldo, data_saldo_inicial=data_iso)
        if token and envio is not None:
            return _previa(request, con, token, envio, str(novo_id), None)
        return RedirectResponse("/importar", status_code=303)
    except ErroImportacao as e:
        return _pagina_inicial(request, con, erro=e.mensagem, status=e.status)


@router.post("/importar/confirmar", response_class=HTMLResponse)
def confirmar(request: Request, con: Any = Depends(get_con), token: str = Form(""),
              conta_id: str = Form(""), mapa_data: str = Form(""), mapa_descricao: str = Form(""),
              mapa_valor: str = Form("")) -> HTMLResponse:
    try:
        envio = _envio(request, token)
        conta = _conta_id(con, conta_id)
        if conta is None:
            raise ErroImportacao("Conta não encontrada.")
        parser = _parser(envio.nome, envio.conteudo, _mapa(mapa_data, mapa_descricao, mapa_valor))
        if parser is None:
            raise ErroImportacao("Formato do arquivo não reconhecido. Envie o arquivo novamente.")
        leitura = _ler(parser, envio.conteudo)
        if not leitura.linhas:
            raise ErroImportacao("Nenhuma linha válida encontrada no arquivo; nada foi importado.")
        try:
            resumo = importar(con, conta, envio.nome, envio.conteudo, parser, date.today())
        except ArquivoJaImportado as exc:
            raise ErroImportacao(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 - transacao() já fez rollback
            log.error("event=importacao_falhou erro=%s", type(exc).__name__)
            raise ErroImportacao("Não foi possível concluir a importação. Nada foi gravado.", 500) from exc
        request.app.state.uploads.descartar(token)
        return _render(request, "resultado.html", {
            "nome": envio.nome, "resumo": resumo, "tem_saldos": bool(leitura.saldos_dia),
            "conciliacao": conciliar(con, conta, leitura.saldos_dia),
            "propostas": propostas_com_detalhes(con, conta, resumo.importacao_id)})
    except ErroImportacao as e:
        return _pagina_inicial(request, con, erro=e.mensagem, status=e.status)


@router.post("/importar/casamento/confirmar", response_class=HTMLResponse)
def casamento(request: Request, con: Any = Depends(get_con), pendente_id: int = Form(...),
              lancamento_id: int = Form(...)) -> HTMLResponse:
    try:
        confirmar_casamento(con, pendente_id, lancamento_id)
    except ValueError as exc:
        return _render(request, "_casamento_resultado.html", {"erro": str(exc)})
    return _render(request, "_casamento_resultado.html", {"erro": None})


@router.post("/importar/{importacao_id}/desfazer")
def desfazer(importacao_id: int, request: Request, con: Any = Depends(get_con)) -> Response:
    try:
        desfazer_importacao(con, importacao_id)
    except ValueError:
        return _pagina_inicial(request, con, erro="Importação não encontrada.", status=404)
    return _pagina_inicial(request, con, aviso="Importação desfeita.")
