"""Proteção contra requisições de outras origens (CSRF) e DNS rebinding.

Aplica-se a todas as rotas: Host restrito a localhost e, em métodos que alteram estado,
Origin/Sec-Fetch-Site precisam indicar a mesma origem. Sem esses cabeçalhos (curl,
TestClient) a requisição é aceita.
"""
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from app import config

HOSTS_FIXOS = frozenset({"localhost", "127.0.0.1", "[::1]", "testserver"})
METODOS_MUTAVEIS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
MSG_ORIGEM = "Requisição bloqueada por segurança (origem diferente)."


def _hostname(host: str) -> str:
    host = host.strip().lower()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.split(":")[0]


def _bloqueio(status: int, mensagem: str) -> Response:
    return HTMLResponse(
        f'<!DOCTYPE html><html lang="pt-BR"><meta charset="utf-8">'
        f"<title>Bloqueado</title><p>{mensagem}</p></html>",
        status_code=status,
    )


class ProtecaoOrigemHost(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        host = request.headers.get("host", "")
        if _hostname(host) not in HOSTS_FIXOS | set(config.HOSTS_PERMITIDOS):
            return _bloqueio(400, "Requisição bloqueada por segurança (host não permitido).")
        if request.method in METODOS_MUTAVEIS:
            origem = request.headers.get("origin")
            if origem is not None:
                if urlsplit(origem).netloc.lower() != host.strip().lower():
                    return _bloqueio(403, MSG_ORIGEM)
            else:
                site = request.headers.get("sec-fetch-site")
                if site is not None and site.lower() not in {"same-origin", "none"}:
                    return _bloqueio(403, MSG_ORIGEM)
        return await call_next(request)
