"""Cliente Gmail somente leitura (escopo gmail.readonly), só stdlib.

Apenas GET em /messages e POST no endpoint de token. Token, texto de e-mail e
nomes de anexos nunca vão para logs nem para mensagens de erro.
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

log = logging.getLogger(__name__)

ESCOPO = "https://www.googleapis.com/auth/gmail.readonly"
_API = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_TIMEOUT = 20
_MAX_MENSAGENS = 50
_UA = "ConsultorFin/1.0 (gmail-readonly)"

_MSG_NAO_CONFIG = "O Gmail ainda não foi configurado."
_MSG_EXPIROU = "A autorização do Gmail expirou. Refaça a configuração."
_MSG_GENERICA = "Não foi possível acessar o Gmail agora. Tente novamente mais tarde."

Transporte = Callable[[urllib.request.Request], bytes]


class ErroGmail(Exception):
    """Erro com mensagem em português, segura para o usuário."""


@dataclass(frozen=True)
class Anexo:
    id: str
    nome: str
    tamanho: int


@dataclass(frozen=True)
class Mensagem:
    id: str
    de: str
    assunto: str
    trecho: str
    anexos: tuple[Anexo, ...]


def _epoch_meia_noite(dia: date) -> int:
    """Segundos Unix de 00:00 de `dia` em America/Sao_Paulo (sem tzdata: UTC-3 fixo)."""
    try:
        fuso: Any = ZoneInfo("America/Sao_Paulo")
    except ZoneInfoNotFoundError:
        fuso = timezone(timedelta(hours=-3))
    return int(datetime(dia.year, dia.month, dia.day, tzinfo=fuso).timestamp())


def _termo_seguro(termo: str) -> str:
    return "".join(c for c in termo.lower() if c.isalnum() or c in "._-")


def _transporte_padrao(req: urllib.request.Request) -> bytes:
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 - URLs fixas https
        return resp.read()


def _anexos(parte: dict[str, Any]) -> list[Anexo]:
    achados: list[Anexo] = []
    corpo = parte.get("body") or {}
    nome = parte.get("filename") or ""
    if nome and corpo.get("attachmentId"):
        achados.append(Anexo(str(corpo["attachmentId"]), str(nome), int(corpo.get("size") or 0)))
    for filha in parte.get("parts") or []:
        achados.extend(_anexos(filha))
    return achados


class ClienteGmail:
    def __init__(self, token_path: Path, transporte: Transporte | None = None) -> None:
        self._token_path = token_path
        self._transporte: Transporte = transporte or _transporte_padrao
        self._access: str | None = None
        self.ultima_busca_truncada = False

    # -- autenticação --------------------------------------------------
    def _credenciais(self) -> dict[str, str]:
        try:
            dados = json.loads(self._token_path.read_text(encoding="utf-8"))
            return {k: str(dados[k]) for k in ("client_id", "client_secret", "refresh_token")}
        except FileNotFoundError:
            raise ErroGmail(_MSG_NAO_CONFIG) from None
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("event=gmail_token_invalido")
            raise ErroGmail(_MSG_NAO_CONFIG) from None

    def _access_token(self) -> str:
        if self._access:
            return self._access
        cred = self._credenciais()
        corpo = urllib.parse.urlencode(
            {
                "client_id": cred["client_id"],
                "client_secret": cred["client_secret"],
                "refresh_token": cred["refresh_token"],
                "grant_type": "refresh_token",
            }
        ).encode()
        req = urllib.request.Request(
            _TOKEN_URL,
            data=corpo,
            method="POST",
            headers={"User-Agent": _UA, "Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            resp = json.loads(self._transporte(req))
        except urllib.error.HTTPError as e:
            log.warning("event=gmail_token_http status=%s", e.code)
            raise ErroGmail(_MSG_EXPIROU if e.code in (400, 401) else _MSG_GENERICA) from None
        except (OSError, ValueError):
            log.warning("event=gmail_token_falha")
            raise ErroGmail(_MSG_GENERICA) from None
        if not isinstance(resp, dict):
            raise ErroGmail(_MSG_GENERICA)
        if resp.get("error") == "invalid_grant":
            log.warning("event=gmail_invalid_grant")
            raise ErroGmail(_MSG_EXPIROU)
        token = resp.get("access_token")
        if not isinstance(token, str) or not token:
            log.warning("event=gmail_token_sem_access")
            raise ErroGmail(_MSG_GENERICA)
        self._access = token
        return token

    # -- HTTP ----------------------------------------------------------
    def _get(self, url: str) -> dict[str, Any]:
        req = urllib.request.Request(
            url,
            method="GET",
            headers={"Authorization": f"Bearer {self._access_token()}", "User-Agent": _UA},
        )
        try:
            dados = json.loads(self._transporte(req))
        except urllib.error.HTTPError as e:
            log.warning("event=gmail_http status=%s", e.code)
            if e.code == 401:
                self._access = None
                raise ErroGmail(_MSG_EXPIROU) from None
            raise ErroGmail(_MSG_GENERICA) from None
        except (OSError, ValueError):
            log.warning("event=gmail_falha_rede")
            raise ErroGmail(_MSG_GENERICA) from None
        if not isinstance(dados, dict):
            raise ErroGmail(_MSG_GENERICA)
        return dados

    # -- API -----------------------------------------------------------
    def buscar(self, de: date, ate: date, limite: int = _MAX_MENSAGENS,
               remetentes_termos: tuple[str, ...] = ()) -> list[Mensagem]:
        limite = max(1, min(int(limite), _MAX_MENSAGENS))
        q = (f"has:attachment filename:pdf after:{_epoch_meia_noite(de)}"
             f" before:{_epoch_meia_noite(ate)}")
        termos = [t for t in (_termo_seguro(x) for x in remetentes_termos) if t]
        if termos:
            q += f" from:({' OR '.join(termos)})"
        qs = urllib.parse.urlencode({"q": q, "maxResults": limite})
        pagina = self._get(f"{_API}?{qs}")
        todos = [str(m["id"]) for m in (pagina.get("messages") or [])]
        ids = todos[:limite]
        self.ultima_busca_truncada = bool(pagina.get("nextPageToken")) or len(todos) > limite
        resultado: list[Mensagem] = []
        for mid in ids:
            m = self._get(f"{_API}/{urllib.parse.quote(mid, safe='')}?format=full")
            payload = m.get("payload") or {}
            cab = {h.get("name", "").lower(): h.get("value", "") for h in payload.get("headers") or []}
            resultado.append(
                Mensagem(
                    id=mid,
                    de=cab.get("from", ""),
                    assunto=cab.get("subject", ""),
                    trecho=str(m.get("snippet") or ""),
                    anexos=tuple(_anexos(payload)),
                )
            )
        log.info("event=gmail_busca encontradas=%d truncada=%s", len(resultado),
                 self.ultima_busca_truncada)
        return resultado

    def baixar_anexo(self, mensagem_id: str, anexo_id: str) -> bytes:
        url = (
            f"{_API}/{urllib.parse.quote(mensagem_id, safe='')}"
            f"/attachments/{urllib.parse.quote(anexo_id, safe='')}"
        )
        data = self._get(url).get("data")
        if not isinstance(data, str):
            raise ErroGmail(_MSG_GENERICA)
        try:
            bruto = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
        except ValueError:
            raise ErroGmail(_MSG_GENERICA) from None
        log.info("event=gmail_anexo_baixado bytes=%d", len(bruto))
        return bruto
