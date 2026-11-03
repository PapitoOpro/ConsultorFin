"""Busca de faturas no Gmail e manutenção da fila (PDFs em data/fila/<id>.pdf).

Nomes de arquivo vêm só do id inteiro da fila, nunca de texto de e-mail.
Assunto/trecho/nomes de anexo nunca vão para logs.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from app import config
from app.gmail.classificador import carregar_remetentes, classificar
from app.gmail.cliente import Mensagem
from app.repo import fila_faturas as repo

log = logging.getLogger(__name__)

_MAGICA = b"%PDF"


class ClienteBusca(Protocol):
    def buscar(self, de: date, ate: date, limite: int = ...) -> list[Mensagem]: ...
    def baixar_anexo(self, mensagem_id: str, anexo_id: str) -> bytes: ...


@dataclass(frozen=True)
class ResumoBusca:
    encontradas: int
    novas: int
    ja_na_fila: int
    ja_importadas: int
    ignoradas: int


def _caminho(dir_fila: Path, item_id: int) -> Path:
    return dir_fila / f"{int(item_id)}.pdf"


def descartar_pdf(dir_fila: Path, item_id: int) -> None:
    """Apaga o PDF do item; idempotente."""
    _caminho(dir_fila, item_id).unlink(missing_ok=True)


def ja_importada(con: sqlite3.Connection, item: sqlite3.Row) -> bool:
    """Hash do PDF já em faturas, ou (final, vencimento) já existentes."""
    if repo.hash_ja_importado(con, item["pdf_hash"]):
        return True
    final, venc = item["cartao_final"], item["vencimento"]
    return final is not None and venc is not None and repo.fatura_existente(con, final, venc)


def expirar(con: sqlite3.Connection, agora: datetime, dir_fila: Path) -> int:
    """Pendentes com mais de DIAS_EXPIRACAO_FILA dias viram 'pulada' e perdem o PDF."""
    limite = (agora - timedelta(days=config.DIAS_EXPIRACAO_FILA)).isoformat(timespec="seconds")
    itens = repo.expirados(con, limite)
    for item in itens:
        repo.definir_estado(con, int(item["id"]), "pulada")
        descartar_pdf(dir_fila, int(item["id"]))
    con.commit()
    if itens:
        log.info("event=fila_expirou total=%d", len(itens))
    return len(itens)


def buscar_para_fila(con: sqlite3.Connection, cliente: ClienteBusca, de: date, ate: date,
                     agora: datetime, dir_fila: Path) -> ResumoBusca:
    expirar(con, agora, dir_fila)
    remetentes = carregar_remetentes()
    mensagens = cliente.buscar(de, ate, config.LIMITE_MENSAGENS_GMAIL)
    novas = na_fila = importadas = ignoradas = 0
    dir_fila.mkdir(parents=True, exist_ok=True)
    for msg in mensagens:
        cand = classificar(msg.de, msg.assunto, msg.trecho, remetentes)
        if cand is None:
            continue
        for anexo in msg.anexos:
            if not anexo.nome.lower().endswith(".pdf") or anexo.tamanho > config.LIMITE_PDF_BYTES:
                ignoradas += 1
                continue
            if repo.obter_por_origem(con, msg.id, anexo.id) is not None:
                na_fila += 1
                continue
            dados = cliente.baixar_anexo(msg.id, anexo.id)
            if not dados.startswith(_MAGICA) or len(dados) > config.LIMITE_PDF_BYTES:
                ignoradas += 1
                continue
            item_id = repo.inserir(
                con, gmail_msg_id=msg.id, anexo_id=anexo.id, emissor=cand.emissor,
                cartao_final=cand.cartao_final, vencimento=cand.vencimento,
                pdf_hash=hashlib.sha256(dados).hexdigest(),
                agora=agora.isoformat(timespec="seconds"))
            if item_id is None:
                na_fila += 1
                continue
            try:
                _caminho(dir_fila, item_id).write_bytes(dados)
                con.commit()
            except BaseException:
                con.rollback()
                descartar_pdf(dir_fila, item_id)
                raise
            if ja_importada(con, repo.obter(con, item_id)):  # type: ignore[arg-type]
                importadas += 1
            else:
                novas += 1
    resumo = ResumoBusca(len(mensagens), novas, na_fila, importadas, ignoradas)
    log.info("event=fila_busca encontradas=%d novas=%d ja_na_fila=%d ja_importadas=%d ignoradas=%d",
             resumo.encontradas, novas, na_fila, importadas, ignoradas)
    return resumo
