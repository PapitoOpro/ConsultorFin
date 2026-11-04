"""Tradução do stream-json do `claude -p` em eventos do chat (nunca comando, JSON cru ou erro técnico)."""
import json
import logging
import re
from typing import Any

from app.chat.argv import sessao_valida
from app.chat.estado import EstadoChat
from app.chat.guarda import subcomando_escrita

log = logging.getLogger(__name__)

MSG_ERRO = "Não consegui responder agora. Tente de novo."
STATUS_PADRAO = "Consultando seus dados…"
STATUS_ESCRITA = "Preparando a alteração…"
SEPARADOR = "\n\n"  # o cliente descarta espaços no início da resposta
STATUS: dict[str, str] = {
    "saldos": "Consultando saldos…",
    "resumo": "Montando o resumo do mês…",
    "vencimentos": "Verificando vencimentos…",
    "projecao": "Calculando a projeção…",
    "gastos": "Somando os gastos…",
    "lancamentos": "Buscando lançamentos…",
    "faturas": "Consultando faturas…",
    "holerites": "Consultando holerites…",
    "contas": "Consultando contas…",
    "categorias": "Consultando categorias…",
    "relatorio": "Montando o relatório…",
}
DESCRICOES: dict[str, str] = {
    "importar": "Importar extrato",
    "casar": "Casar lançamento",
    "categorizar": "Categorizar lançamento",
    "regra": "Criar regra de categoria",
    "pagar": "Registrar pagamento",
    "criar-conta": "Criar conta",
    "gmail-buscar": "Buscar faturas no Gmail",
}
_SUBCOMANDO = re.compile(r"-m app\.consultor ([a-z-]+)")
_RID = re.compile(r"[0-9a-f]{32}")


def _status(bloco: dict[str, Any]) -> dict[str, Any]:
    entrada = bloco.get("input")
    comando = entrada.get("command") if isinstance(entrada, dict) else None
    m = _SUBCOMANDO.search(comando) if isinstance(comando, str) else None
    sub = m.group(1) if m else ""
    texto = STATUS_ESCRITA if sub in DESCRICOES else STATUS.get(sub, STATUS_PADRAO)
    return {"tipo": "status", "texto": texto}


def _texto_do_resultado(conteudo: Any) -> str:
    if isinstance(conteudo, str):
        return conteudo
    if isinstance(conteudo, list):
        return "".join(b.get("text", "") for b in conteudo if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _relatorio(bloco: dict[str, Any]) -> list[dict[str, Any]]:
    if bloco.get("type") != "tool_result" or bloco.get("is_error"):
        return []
    try:
        dados = json.loads(_texto_do_resultado(bloco.get("content")))
    except ValueError:
        return []
    if not isinstance(dados, dict):
        return []
    rid, titulo = dados.get("relatorio_id"), dados.get("titulo")
    if not isinstance(rid, str) or not _RID.fullmatch(rid):
        return []
    return [{"tipo": "relatorio", "id": rid, "titulo": titulo if isinstance(titulo, str) else "Relatório",
             "url": f"/relatorio/{rid}"}]


def _cartoes(dados: dict[str, Any], sessao: str | None, estado: EstadoChat) -> list[dict[str, Any]]:
    saida: list[dict[str, Any]] = []
    negados = dados.get("permission_denials")
    for negado in negados if isinstance(negados, list) else []:
        entrada = negado.get("tool_input") if isinstance(negado, dict) else None
        comando = entrada.get("command") if isinstance(entrada, dict) else None
        escrita = subcomando_escrita(comando) if isinstance(comando, str) else None
        if escrita is None or sessao is None:
            log.warning("event=chat_negado escrita=%s", escrita or "nao")
            continue
        token = estado.guardar_pendencia(sessao, str(comando))
        saida.append({"tipo": "confirmar", "token": token, "descricao": DESCRICOES[escrita]})
    return saida


def _resultado(dados: dict[str, Any], estado: EstadoChat) -> list[dict[str, Any]]:
    sid = dados.get("session_id")
    sessao = sid if isinstance(sid, str) and sessao_valida(sid) else None
    saida = _cartoes(dados, sessao, estado)
    if dados.get("is_error") is not False or sessao is None:
        log.warning("event=chat_resultado_erro subtipo=%s", str(dados.get("subtype", ""))[:40])
        saida.append({"tipo": "erro", "texto": MSG_ERRO})
    saida.append({"tipo": "fim", "sessao": sessao})
    return saida


def traduzir_linha(linha: str, estado: EstadoChat) -> list[dict[str, Any]]:
    try:
        dados = json.loads(linha)
    except ValueError:
        return []
    if not isinstance(dados, dict):
        return []
    tipo = dados.get("type")
    if tipo == "stream_event":
        evento = dados.get("event")
        evento = evento if isinstance(evento, dict) else {}
        delta, bloco = evento.get("delta"), evento.get("content_block")
        if evento.get("type") == "content_block_delta" and isinstance(delta, dict) \
                and delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
            return [{"tipo": "texto", "texto": delta["text"]}]
        if evento.get("type") == "content_block_start" and isinstance(bloco, dict) and bloco.get("type") == "text":
            return [{"tipo": "texto", "texto": SEPARADOR}]  # textos antes/depois de ferramentas não grudam
        return []
    mensagem = dados.get("message")
    conteudo = mensagem.get("content") if isinstance(mensagem, dict) else None
    blocos = [b for b in conteudo if isinstance(b, dict)] if isinstance(conteudo, list) else []
    if tipo == "assistant":
        return [_status(b) for b in blocos if b.get("type") == "tool_use"]
    if tipo == "user":
        return [ev for b in blocos for ev in _relatorio(b)]
    if tipo == "result":
        return _resultado(dados, estado)
    return []
