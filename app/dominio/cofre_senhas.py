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
from app.db import sem_acento
from app.repo import senhas_pdf as repo

log = logging.getLogger(__name__)

TAM_CHAVE = 32
TAM_NONCE = 12
TAM_TAG = 16


def _aad(emissor: str, final: str) -> bytes:
    return f"{emissor}\x00{final}".encode()


def _chave_emissor(emissor: str | None) -> str:
    """Chave de comparação: 'Itaú', 'itau' e 'Mercado Pago'/'mercadopago' coincidem."""
    return sem_acento(emissor).replace(" ", "")


_NOMES = {"itau": "Itaú", "porto": "Porto", "mercadopago": "Mercado Pago"}


def nome_exibicao(emissor: str) -> str:
    return _NOMES.get(_chave_emissor(emissor), emissor)


def _ler_chave(caminho: Path) -> tuple[bytes | None, str]:
    """(chave, motivo): motivo 'ok', 'ausente' ou 'invalida'."""
    try:
        chave = caminho.read_bytes()
    except FileNotFoundError:
        return None, "ausente"
    except OSError:
        return None, "invalida"
    return (chave, "ok") if len(chave) == TAM_CHAVE else (None, "invalida")


def _criar_chave(caminho: Path) -> bytes:
    """Grava a chave num temporário 0600 e a publica sem sobrescrever (corrida segura)."""
    caminho.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    chave = secrets.token_bytes(TAM_CHAVE)
    tmp = caminho.with_name(f".{caminho.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(chave)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        try:
            os.link(tmp, caminho)  # falha se já existe: nunca troca uma chave em uso
        except FileExistsError:
            lida, _ = _ler_chave(caminho)
            if lida is None:
                raise ValueError("Arquivo de chave das senhas inválido.") from None
            return lida
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return chave


def _evento_chave(motivo: str, linhas: int) -> None:
    log.warning("event=senhas_chave_%s linhas=%d", motivo, linhas)


def guardar(con: sqlite3.Connection, emissor: str, final: str, senha: str) -> bool:
    """Cifra e grava (substitui a do mesmo cartão). Sem commit.

    False (sem gravar nem criar chave) se a chave sumiu/é inválida e já há senhas guardadas:
    uma chave nova tornaria as antigas ilegíveis para sempre.
    """
    if not emissor or not final or not senha:
        raise ValueError("Emissor, final do cartão e senha são obrigatórios.")
    caminho = config.caminho_chave_senhas()
    chave, motivo = _ler_chave(caminho)
    if chave is None:
        existentes = len(repo.linhas(con))
        if existentes:
            _evento_chave(motivo, existentes)
            return False
        if motivo == "invalida":
            raise ValueError("Arquivo de chave das senhas inválido.")
        chave = _criar_chave(caminho)
    nonce = secrets.token_bytes(TAM_NONCE)
    cifra = AES.new(chave, AES.MODE_GCM, nonce=nonce)
    cifra.update(_aad(emissor, final))
    texto, tag = cifra.encrypt_and_digest(senha.encode())
    repo.salvar(con, emissor, final, nonce, texto + tag,
                datetime.now().isoformat(timespec="seconds"))
    return True


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
    chave, motivo = _ler_chave(config.caminho_chave_senhas())
    if chave is None:
        _evento_chave(motivo, len(linhas))
        return []
    alvo = _chave_emissor(emissor) if emissor else None

    def rank(l: sqlite3.Row) -> int:
        if alvo and _chave_emissor(l["emissor"]) == alvo:
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
    return [{"id": int(r["id"]), "emissor": nome_exibicao(r["emissor"]),
             "cartao_final": r["cartao_final"]}
            for r in repo.listar(con)]


def esquecer(con: sqlite3.Connection, item_id: int) -> bool:
    """Apaga a senha guardada. Sem commit."""
    return repo.apagar(con, item_id)
