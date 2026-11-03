"""Senhas de PDF de fatura guardadas por cartão, cifradas com AES-256-GCM.

Chave de 32 bytes em arquivo (config.caminho_chave_senhas), fora do banco. O par
(emissor, final) é dado associado (AAD): a cifra de um cartão não abre em outro.
Nunca registra nem devolve senha em log, erro ou listagem.
"""
import logging
import os
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

from Crypto.Cipher import AES

from app import config
from app.repo import senhas_pdf as repo

log = logging.getLogger(__name__)

TAM_CHAVE = 32
TAM_NONCE = 12
TAM_TAG = 16


def _aad(emissor: str, final: str) -> bytes:
    return f"{emissor}\x00{final}".encode()


def _ler_chave(caminho: Path) -> bytes | None:
    try:
        chave = caminho.read_bytes()
    except OSError:
        return None
    return chave if len(chave) == TAM_CHAVE else None


def _obter_ou_criar_chave(caminho: Path) -> bytes:
    existente = _ler_chave(caminho)
    if existente is not None:
        return existente
    if caminho.exists():
        raise ValueError("Arquivo de chave das senhas inválido.")
    pasta = caminho.parent
    if not pasta.exists():
        pasta.mkdir(parents=True, mode=0o700)
    chave = secrets.token_bytes(TAM_CHAVE)
    try:
        fd = os.open(caminho, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        lida = _ler_chave(caminho)
        if lida is None:
            raise ValueError("Arquivo de chave das senhas inválido.") from None
        return lida
    with os.fdopen(fd, "wb") as f:
        f.write(chave)
    try:
        os.chmod(caminho, 0o600)
    except OSError:
        pass
    return chave


def guardar(con: sqlite3.Connection, emissor: str, final: str, senha: str) -> None:
    """Cifra e grava (substitui a do mesmo cartão). Sem commit."""
    if not emissor or not final or not senha:
        raise ValueError("Emissor, final do cartão e senha são obrigatórios.")
    chave = _obter_ou_criar_chave(config.caminho_chave_senhas())
    nonce = secrets.token_bytes(TAM_NONCE)
    cifra = AES.new(chave, AES.MODE_GCM, nonce=nonce)
    cifra.update(_aad(emissor, final))
    texto, tag = cifra.encrypt_and_digest(senha.encode())
    repo.salvar(con, emissor, final, nonce, texto + tag, datetime.now().isoformat(timespec="seconds"))


def _abrir(chave: bytes, emissor: str, final: str, nonce: bytes, cifra: bytes) -> str | None:
    try:
        c = AES.new(chave, AES.MODE_GCM, nonce=bytes(nonce))
        c.update(_aad(emissor, final))
        return c.decrypt_and_verify(bytes(cifra[:-TAM_TAG]), bytes(cifra[-TAM_TAG:])).decode()
    except (ValueError, KeyError):
        return None


def candidatas(con: sqlite3.Connection, emissor: str | None = None,
               final: str | None = None) -> list[str]:
    """Senhas guardadas: cartão exato, depois mesmo emissor, depois as demais (sem repetir)."""
    linhas = repo.linhas(con)
    if not linhas:
        return []
    chave = _ler_chave(config.caminho_chave_senhas())
    if chave is None:
        log.warning("event=senhas_chave_ausente linhas=%d", len(linhas))
        return []

    def rank(l: sqlite3.Row) -> int:
        if emissor is not None and l["emissor"] == emissor:
            return 0 if final is not None and l["cartao_final"] == final else 1
        return 2

    senhas: list[str] = []
    ilegiveis = 0
    for l in sorted(linhas, key=rank):
        s = _abrir(chave, l["emissor"], l["cartao_final"], l["nonce"], l["cifra"])
        if s is None:
            ilegiveis += 1
        elif s not in senhas:
            senhas.append(s)
    if ilegiveis:
        log.warning("event=senhas_ilegiveis quantidade=%d", ilegiveis)
    return senhas


def listar(con: sqlite3.Connection) -> list[dict[str, object]]:
    return [{"id": int(r["id"]), "emissor": r["emissor"], "cartao_final": r["cartao_final"]}
            for r in repo.listar(con)]


def esquecer(con: sqlite3.Connection, item_id: int) -> bool:
    """Apaga a senha guardada. Sem commit."""
    return repo.apagar(con, item_id)
