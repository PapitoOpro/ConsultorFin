"""Busca de faturas no Gmail e manutenção da fila (PDFs em data/fila/<id>.pdf).

Nomes de arquivo vêm só do id inteiro da fila, nunca de texto de e-mail.
Assunto/trecho/nomes de anexo nunca vão para logs.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from dataclasses import dataclass, field
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
    def buscar(self, de: date, ate: date, limite: int = ...,
               remetentes_termos: tuple[str, ...] = ...) -> list[Mensagem]: ...
    def baixar_anexo(self, mensagem_id: str, anexo_id: str) -> bytes: ...


@dataclass(frozen=True)
class ResumoBusca:
    encontradas: int
    novas: int
    ja_na_fila: int
    ja_importadas: int
    ignoradas: int
    recuperadas: int = 0
    faturas: int = 0
    por_emissor: dict[str, int] = field(default_factory=dict)
    limite_atingido: bool = False


def _caminho(dir_fila: Path, item_id: int) -> Path:
    return dir_fila / f"{int(item_id)}.pdf"


def _gravar_pdf(dir_fila: Path, item_id: int, dados: bytes) -> None:
    alvo = _caminho(dir_fila, item_id)
    alvo.write_bytes(dados)
    try:
        os.chmod(alvo, 0o600)
    except OSError:  # best-effort (Windows)
        pass


def _preparar_dir(dir_fila: Path) -> None:
    if dir_fila.exists():
        return
    dir_fila.mkdir(parents=True)
    try:
        os.chmod(dir_fila, 0o700)
    except OSError:
        pass


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
    """Pendentes com mais de DIAS_EXPIRACAO_FILA dias são apagadas (linha e PDF).

    Assim uma nova busca as reenfileira; as 'puladas' pelo usuário nunca são tocadas."""
    limite = (agora - timedelta(days=config.DIAS_EXPIRACAO_FILA)).isoformat(timespec="seconds")
    itens = repo.expirados(con, limite)
    for item in itens:
        repo.excluir(con, int(item["id"]))
        descartar_pdf(dir_fila, int(item["id"]))
    con.commit()
    if itens:
        log.info("event=fila_expirou total=%d", len(itens))
    return len(itens)


def buscar_para_fila(con: sqlite3.Connection, cliente: ClienteBusca, de: date, ate: date,
                     agora: datetime, dir_fila: Path) -> ResumoBusca:
    expirar(con, agora, dir_fila)
    remetentes = carregar_remetentes()
    termos = tuple(dict.fromkeys(t for r in remetentes for t in r.contem))
    mensagens = cliente.buscar(de, ate, config.LIMITE_MENSAGENS_GMAIL, remetentes_termos=termos)
    truncada = bool(getattr(cliente, "ultima_busca_truncada", False))
    novas = na_fila = importadas = ignoradas = recuperadas = faturas = 0
    por_emissor: dict[str, int] = {}
    _preparar_dir(dir_fila)
    for msg in mensagens:
        cand = classificar(msg.de, msg.assunto, msg.trecho, remetentes)
        if cand is None:
            continue
        faturas += 1
        por_emissor[cand.emissor] = por_emissor.get(cand.emissor, 0) + 1
        for anexo in msg.anexos:
            if not anexo.nome.lower().endswith(".pdf") or anexo.tamanho > config.LIMITE_PDF_BYTES:
                ignoradas += 1
                continue
            existente = repo.obter_por_origem(con, msg.id, anexo.id)
            if existente is not None:
                if (existente["estado"] == "pendente"
                        and not _caminho(dir_fila, int(existente["id"])).exists()):
                    dados = cliente.baixar_anexo(msg.id, anexo.id)
                    if dados.startswith(_MAGICA) and len(dados) <= config.LIMITE_PDF_BYTES:
                        _gravar_pdf(dir_fila, int(existente["id"]), dados)
                        recuperadas += 1
                        continue
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
                _gravar_pdf(dir_fila, item_id, dados)
                con.commit()
            except BaseException:
                con.rollback()
                try:
                    descartar_pdf(dir_fila, item_id)
                except OSError as exc:
                    log.warning("event=fila_limpeza_falhou erro=%s", type(exc).__name__)
                raise
            if ja_importada(con, repo.obter(con, item_id)):  # type: ignore[arg-type]
                importadas += 1
            else:
                novas += 1
    resumo = ResumoBusca(len(mensagens), novas, na_fila, importadas, ignoradas, recuperadas,
                         faturas, por_emissor, truncada)
    log.info("event=fila_busca encontradas=%d faturas=%d novas=%d ja_na_fila=%d ja_importadas=%d"
             " ignoradas=%d recuperadas=%d limite_atingido=%s",
             resumo.encontradas, faturas, novas, na_fila, importadas, ignoradas, recuperadas,
             truncada)
    return resumo
