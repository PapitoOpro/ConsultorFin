"""Autoriza o ConsultorFin a LER o Gmail (somente leitura) e grava gmail-token.json.

Roda no PC do usuário (abre o navegador). Uso:
    python scripts/gmail_autorizar.py [client_secret.json] [gmail-token.json]
Só biblioteca padrão. Nunca imprime segredos.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

ESCOPO = "https://www.googleapis.com/auth/gmail.readonly"
URL_AUTORIZAR = "https://accounts.google.com/o/oauth2/v2/auth"
URL_TOKEN = "https://oauth2.googleapis.com/token"
TEMPO_ESPERA_S = 300


def montar_token(client: dict[str, Any], resposta: dict[str, Any]) -> dict[str, str]:
    """Monta o JSON do token com só client_id, client_secret e refresh_token."""
    refresh = resposta.get("refresh_token")
    if not refresh:
        raise ValueError(
            "O Google não devolveu o refresh_token. Revogue o acesso anterior do app "
            "em myaccount.google.com/permissions e rode o script de novo."
        )
    try:
        return {
            "client_id": str(client["client_id"]),
            "client_secret": str(client["client_secret"]),
            "refresh_token": str(refresh),
        }
    except KeyError as exc:
        raise ValueError(f"client_secret.json sem o campo {exc.args[0]}.") from exc


def ler_client(caminho: Path) -> dict[str, Any]:
    """Lê o client_secret.json do tipo 'app para computador' (chave 'installed')."""
    try:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Não consegui ler {caminho.name}: arquivo ausente ou inválido.") from exc
    client = dados.get("installed") if isinstance(dados, dict) else None
    if not isinstance(client, dict):
        raise ValueError(
            "O arquivo não é de um 'app para computador' (falta a chave 'installed'). "
            "Crie a credencial como 'App para computador'."
        )
    return client


def gerar_pkce() -> tuple[str, str]:
    """Devolve (code_verifier, code_challenge S256)."""
    verificador = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verificador.encode("ascii")).digest()
    desafio = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verificador, desafio


def _esperar_codigo(servidor: HTTPServer, estado: str) -> str:
    resultado: dict[str, str] = {}

    class Tratador(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            params = parse_qs(urlparse(self.path).query)
            if "code" in params or "error" in params:
                if params.get("state", [""])[0] != estado:
                    resultado["erro"] = "state inválido"
                elif "error" in params:
                    resultado["erro"] = "acesso negado no navegador"
                else:
                    resultado["code"] = params["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<p>Pronto. Pode fechar esta aba.</p>".encode("utf-8"))

        def log_message(self, *_args: object) -> None:
            return

    servidor.RequestHandlerClass = Tratador
    servidor.timeout = 5
    limite = time.monotonic() + TEMPO_ESPERA_S
    while not resultado:
        if time.monotonic() > limite:
            raise ValueError("Tempo esgotado esperando a autorização no navegador.")
        servidor.handle_request()
    if "erro" in resultado:
        raise ValueError(f"Autorização não concluída: {resultado['erro']}.")
    return resultado["code"]


def _trocar_codigo(
    client: dict[str, Any], codigo: str, verificador: str, redirect_uri: str
) -> dict[str, Any]:
    corpo = urlencode(
        {
            "code": codigo,
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": verificador,
        }
    ).encode("ascii")
    req = Request(URL_TOKEN, data=corpo, method="POST")
    try:
        with urlopen(req, timeout=30) as r:  # noqa: S310 - URL fixa https
            return json.loads(r.read().decode("utf-8"))
    except (HTTPError, URLError, json.JSONDecodeError) as exc:
        raise ValueError("Falha ao trocar o código pelo token no Google.") from exc


def gravar_token(token: dict[str, str], destino: Path) -> None:
    """Grava o token com permissão 0600 quando o sistema permite."""
    fd = os.open(destino, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(token, f)
    try:
        os.chmod(destino, 0o600)
    except OSError:
        pass


def autorizar(caminho_client: Path, destino: Path) -> None:
    client = ler_client(caminho_client)
    verificador, desafio = gerar_pkce()
    estado = secrets.token_urlsafe(24)
    servidor = HTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    redirect_uri = f"http://127.0.0.1:{servidor.server_port}"
    url = URL_AUTORIZAR + "?" + urlencode(
        {
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": ESCOPO,
            "access_type": "offline",
            "prompt": "consent",
            "state": estado,
            "code_challenge": desafio,
            "code_challenge_method": "S256",
        }
    )
    print("Abrindo o navegador para você autorizar o acesso de leitura ao Gmail...")
    print("Se não abrir sozinho, copie o endereço abaixo no navegador:")
    print(url)
    threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    try:
        codigo = _esperar_codigo(servidor, estado)
    finally:
        servidor.server_close()
    token = montar_token(client, _trocar_codigo(client, codigo, verificador, redirect_uri))
    gravar_token(token, destino)
    print(f"Pronto: {destino} gravado. Não compartilhe nem versione este arquivo.")


def main(argv: list[str]) -> int:
    caminho_client = Path(argv[1] if len(argv) > 1 else "client_secret.json")
    destino = Path(argv[2] if len(argv) > 2 else "gmail-token.json")
    try:
        autorizar(caminho_client, destino)
    except ValueError as exc:
        print(f"Erro: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
