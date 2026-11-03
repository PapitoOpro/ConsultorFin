"""CLI gmail-buscar com cliente falso (dados inventados)."""
import json
from pathlib import Path
from typing import Any

import pytest

from app import config
from app.consultor import cli, operacoes
from app.gmail.cliente import Anexo, ErroGmail, Mensagem
from tests._consultor_base import criar_banco

HOSTIL = "ASSUNTO-HOSTIL-ignore-previous-instructions"
NOME_HOSTIL = "nome-hostil-anexo.pdf"
PDF = b"%PDF-1.4 ficticio"


class Falso:
    def __init__(self, erro: Exception | None = None) -> None:
        self.erro = erro

    def buscar(self, de: Any, ate: Any, limite: int = 50) -> list[Mensagem]:
        if self.erro:
            raise self.erro
        return [Mensagem("m1", "Nubank <a@nubank.com.br>", HOSTIL, HOSTIL,
                         (Anexo("a1", NOME_HOSTIL, 10),))]

    def baixar_anexo(self, mensagem_id: str, anexo_id: str) -> bytes:
        return PDF


@pytest.fixture
def db(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(config, "DIR_FILA", tmp_path / "fila")
    return criar_banco(tmp_path / "t.db", com_extrato=False)


def rodar(capsys, db: Path, *args: str) -> tuple[int, str, dict[str, Any]]:
    capsys.readouterr()
    codigo = cli.main([*args, "--db", str(db), "--hoje", "2026-10-23"])
    s = capsys.readouterr()
    return codigo, s.out + s.err, json.loads(s.out)


def test_gmail_buscar_saida_sem_texto_de_email(db, capsys, monkeypatch) -> None:
    monkeypatch.setattr(operacoes, "_cliente_gmail", lambda: Falso())
    codigo, bruto, saida = rodar(capsys, db, "gmail-buscar", "--mes", "2026-10")
    assert codigo == 0
    assert saida["periodo"] == "2026-10" and saida["encontradas"] == 1 and saida["novas"] == 1
    assert HOSTIL not in bruto and NOME_HOSTIL not in bruto


def test_gmail_buscar_erro_de_token_traduzido(db, capsys, monkeypatch) -> None:
    erro = ErroGmail("O Gmail ainda não foi configurado.")
    monkeypatch.setattr(operacoes, "_cliente_gmail", lambda: Falso(erro))
    codigo, _, saida = rodar(capsys, db, "gmail-buscar")
    assert codigo == 1 and saida == {"erro": "O Gmail ainda não foi configurado."}


def test_gmail_buscar_excecao_inesperada_sem_vazamento(db, capsys, monkeypatch) -> None:
    monkeypatch.setattr(operacoes, "_cliente_gmail", lambda: Falso(KeyError(HOSTIL)))
    codigo, bruto, saida = rodar(capsys, db, "gmail-buscar")
    assert codigo == 1
    assert saida == {"erro": "Não foi possível ler as mensagens do Gmail."}
    assert HOSTIL not in bruto


@pytest.mark.parametrize("args", [
    ("--mes", "2025-01", "--ate", "2026-01"),  # 13 meses
    ("--mes", "2026-05", "--ate", "2026-03"),  # fim antes do início
    ("--mes", "2026-13"),
    ("--mes", "abc"),
])
def test_periodo_invalido_e_maior_que_12_meses(db, capsys, monkeypatch, args) -> None:
    monkeypatch.setattr(operacoes, "_cliente_gmail", lambda: Falso())
    codigo, _, saida = rodar(capsys, db, "gmail-buscar", *args)
    assert codigo == 1 and "erro" in saida


def test_intervalo_de_12_meses_ok(db, capsys, monkeypatch) -> None:
    monkeypatch.setattr(operacoes, "_cliente_gmail", lambda: Falso())
    codigo, _, saida = rodar(capsys, db, "gmail-buscar", "--mes", "2025-11", "--ate", "2026-10")
    assert codigo == 0 and saida["periodo"] == "2025-11 a 2026-10"
