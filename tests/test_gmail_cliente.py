import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import pytest

from app.gmail.cliente import Anexo, ClienteGmail, ErroGmail, Mensagem

SEGREDO = "refresh-sentinela-xyz"
ATOKEN = "access-sentinela-abc"


def _token(tmp_path: Path) -> Path:
    p = tmp_path / "gmail-token.json"
    p.write_text(
        json.dumps({"client_id": "cid", "client_secret": "csec", "refresh_token": SEGREDO}),
        encoding="utf-8",
    )
    return p


class Falso:
    def __init__(self, n_msgs: int = 3, token_erro: bytes | None = None) -> None:
        self.reqs: list[urllib.request.Request] = []
        self.n = n_msgs
        self.token_erro = token_erro
        self.next_token = False

    def __call__(self, req: urllib.request.Request) -> bytes:
        self.reqs.append(req)
        url = req.full_url
        if url.startswith("https://oauth2.googleapis.com/token"):
            if self.token_erro is not None:
                return self.token_erro
            return json.dumps({"access_token": ATOKEN, "expires_in": 3600}).encode()
        assert req.get_header("Authorization") == f"Bearer {ATOKEN}"
        if "/attachments/" in url:
            dados = base64.urlsafe_b64encode(b"%PDF-fake\xff\xfe").decode().rstrip("=")
            return json.dumps({"data": dados}).encode()
        if "/messages?" in url:
            corpo = {"messages": [{"id": f"m{i}"} for i in range(self.n)]}
            if self.next_token:
                corpo["nextPageToken"] = "tok"
            return json.dumps(corpo).encode()
        mid = url.split("/messages/")[1].split("?")[0]
        return json.dumps(
            {
                "id": mid,
                "snippet": "trecho ficticio",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Banco <a@banco.exemplo.com>"},
                        {"name": "Subject", "value": "Fatura"},
                    ],
                    "parts": [
                        {"filename": "", "body": {"size": 5}},
                        {"filename": "f.pdf", "body": {"attachmentId": "att1", "size": 123}},
                    ],
                },
            }
        ).encode()


def test_buscar_monta_query_e_limita_a_50(tmp_path: Path) -> None:
    f = Falso(n_msgs=80)
    msgs = ClienteGmail(_token(tmp_path), f).buscar(date(2026, 10, 1), date(2026, 11, 1), limite=500)
    lista = [r for r in f.reqs if "/messages?" in r.full_url][0]
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(lista.full_url).query)
    # 2026-10-01 e 2026-11-01 00:00 em America/Sao_Paulo (UTC-3)
    assert qs["q"] == ["has:attachment filename:pdf after:1790823600 before:1793502000"]
    assert qs["maxResults"] == ["50"]
    assert len(msgs) == 50
    assert msgs[0] == Mensagem("m0", "Banco <a@banco.exemplo.com>", "Fatura", "trecho ficticio", (Anexo("att1", "f.pdf", 123),))


def test_query_com_remetentes_e_sanitizacao(tmp_path: Path) -> None:
    f = Falso()
    ClienteGmail(_token(tmp_path), f).buscar(
        date(2026, 10, 1), date(2026, 11, 1), remetentes_termos=("itau", "nu bank)", 'x"y'))
    lista = [r for r in f.reqs if "/messages?" in r.full_url][0]
    q = urllib.parse.parse_qs(urllib.parse.urlparse(lista.full_url).query)["q"][0]
    assert q.endswith(" from:(itau OR nubank OR xy)")


def test_truncamento_marca_atributo(tmp_path: Path) -> None:
    f = Falso(n_msgs=3)
    c = ClienteGmail(_token(tmp_path), f)
    c.buscar(date(2026, 10, 1), date(2026, 11, 1))
    assert c.ultima_busca_truncada is False
    f.next_token = True
    c.buscar(date(2026, 10, 1), date(2026, 11, 1))
    assert c.ultima_busca_truncada is True


def test_epoch_sem_tzdata_usa_utc_menos_3(monkeypatch) -> None:
    from zoneinfo import ZoneInfoNotFoundError

    from app.gmail import cliente as mod

    def falha(nome):
        raise ZoneInfoNotFoundError(nome)
    monkeypatch.setattr(mod, "ZoneInfo", falha)
    assert mod._epoch_meia_noite(date(2026, 10, 1)) == 1790823600


def test_baixar_anexo_decodifica_base64url(tmp_path: Path) -> None:
    assert ClienteGmail(_token(tmp_path), Falso()).baixar_anexo("m1", "att1") == b"%PDF-fake\xff\xfe"


def test_token_ausente_mensagem_clara(tmp_path: Path) -> None:
    with pytest.raises(ErroGmail) as e:
        ClienteGmail(tmp_path / "nao.json", Falso()).buscar(date(2026, 1, 1), date(2026, 2, 1))
    assert str(e.value) == "O Gmail ainda não foi configurado."


def test_invalid_grant_mensagem_clara_sem_token(tmp_path: Path) -> None:
    f = Falso(token_erro=json.dumps({"error": "invalid_grant"}).encode())
    with pytest.raises(ErroGmail) as e:
        ClienteGmail(_token(tmp_path), f).buscar(date(2026, 1, 1), date(2026, 2, 1))
    assert str(e.value) == "A autorização do Gmail expirou. Refaça a configuração."
    assert SEGREDO not in str(e.value)


def test_http_401_vira_expirou(tmp_path: Path) -> None:
    def t(req: urllib.request.Request) -> bytes:
        raise urllib.error.HTTPError(req.full_url, 401, "x", {}, None)  # type: ignore[arg-type]

    with pytest.raises(ErroGmail) as e:
        ClienteGmail(_token(tmp_path), t).buscar(date(2026, 1, 1), date(2026, 2, 1))
    assert "expirou" in str(e.value) and SEGREDO not in str(e.value)


def test_erro_de_rede_generico(tmp_path: Path) -> None:
    def t(req: urllib.request.Request) -> bytes:
        raise OSError(f"falha {SEGREDO}")

    with pytest.raises(ErroGmail) as e:
        ClienteGmail(_token(tmp_path), t).baixar_anexo("m", "a")
    assert SEGREDO not in str(e.value)


def test_so_escopo_leitura(tmp_path: Path) -> None:
    f = Falso()
    c = ClienteGmail(_token(tmp_path), f)
    c.buscar(date(2026, 1, 1), date(2026, 2, 1))
    c.baixar_anexo("m0", "att1")
    for r in f.reqs:
        if r.full_url.startswith("https://oauth2.googleapis.com/token"):
            assert r.get_method() == "POST"
        else:
            assert r.get_method() == "GET"
            assert r.full_url.startswith("https://gmail.googleapis.com/gmail/v1/users/me/messages")
    assert sum(r.full_url.startswith("https://oauth2") for r in f.reqs) == 1  # cache
