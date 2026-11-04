"""Tradução do stream-json do `claude -p` em eventos do chat (nunca comando, JSON cru ou erro técnico)."""
import json
import logging
import re
import unicodedata
from collections.abc import Callable
from typing import Any

from app.chat.argv import sessao_valida
from app.chat.estado import EstadoChat
from app.chat.guarda import analisar, subcomando_escrita
from app.dominio.dinheiro import formatar_brl, para_centavos

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
LIMITE_TEXTO_CARTAO = 60
_SUBCOMANDO = re.compile(r"-m app\.consultor ([a-z-]+)")


def _limpo(valor: str | bool | None, limite: int = LIMITE_TEXTO_CARTAO) -> str:
    """Texto vindo do modelo: sem caracteres de controle/formatação, espaços colapsados, cortado."""
    texto = "".join(" " if c.isspace() else c for c in str(valor or "")
                    if c.isspace() or not unicodedata.category(c).startswith("C"))
    texto = " ".join(texto.split())
    return texto if len(texto) <= limite else texto[:limite - 1] + "…"


def _dinheiro(valor: str | bool | None) -> str:
    try:
        return formatar_brl(para_centavos(str(valor)))
    except (ValueError, ArithmeticError):
        return f"valor {_limpo(valor, 20)}"


def _gmail(o: dict[str, str | bool]) -> str:
    if o.get("--ate"):
        return f"Buscar faturas no Gmail ({_limpo(o.get('--mes') or 'mês atual', 20)} até {_limpo(o['--ate'], 20)})"
    mes = o.get("--mes")
    return f"Buscar faturas no Gmail (mês {_limpo(mes, 20)})" if mes else "Buscar faturas no Gmail (mês atual)"


def _importar(o: dict[str, str | bool]) -> str:
    arquivo = _limpo(re.split(r"[\\/]", str(o.get("--arquivo", "")))[-1])
    acao = "Importar o arquivo" if o.get("--confirmar") else "Simular a importação do arquivo"
    return f"{acao} {arquivo} na conta #{_limpo(o.get('--conta'), 20)}"


def _pagar(o: dict[str, str | bool]) -> str:
    valor = _dinheiro(o["--valor"]) if o.get("--valor") else "valor previsto"
    data = f" em {_limpo(o['--data'], 20)}" if o.get("--data") else ""
    return f"Registrar pagamento do lançamento #{_limpo(o.get('--lancamento'), 20)} ({valor}){data}"


_DESCREVER: dict[str, Callable[[dict[str, str | bool]], str]] = {
    "categorizar": lambda o: f"Categorizar lançamento #{_limpo(o.get('--lancamento'), 20)} → "
                             f"categoria {_limpo(o.get('--categoria'), 20)}",
    "pagar": _pagar,
    "criar-conta": lambda o: f"Criar conta «{_limpo(o.get('--nome'))}» ({_limpo(o.get('--tipo'), 20)}, "
                             f"saldo inicial {_dinheiro(o.get('--saldo-inicial'))} em "
                             f"{_limpo(o.get('--data-saldo'), 20)})",
    "importar": _importar,
    "regra": lambda o: f"Criar regra: descrições com «{_limpo(o.get('--padrao'))}» → "
                       f"categoria {_limpo(o.get('--categoria'), 20)}",
    "casar": lambda o: f"Casar pendente #{_limpo(o.get('--pendente'), 20)} com a linha #{_limpo(o.get('--linha'), 20)}",
    "gmail-buscar": _gmail,
}


def descrever(comando: str) -> str | None:
    """Descrição do cartão montada dos argumentos já conferidos pelo porteiro (nunca o comando)."""
    a = analisar(comando)
    if a is None or a.subcomando not in _DESCREVER:
        return None
    return _DESCREVER[a.subcomando](a.opcoes)
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
        saida.append({"tipo": "confirmar", "token": token, "descricao": descrever(comando) or DESCRICOES[escrita]})
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
