"""Testes da função pura do script de autorização do Gmail (sem rede nem navegador)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_CAMINHO = Path(__file__).resolve().parent.parent / "scripts" / "gmail_autorizar.py"


def _carregar() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gmail_autorizar", _CAMINHO)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_montar_token_so_campos_necessarios() -> None:
    m = _carregar()
    client = {"client_id": "id-x", "client_secret": "seg-x", "redirect_uris": ["http://localhost"]}
    resposta = {"access_token": "at", "refresh_token": "rt", "expires_in": 3599, "scope": "s"}
    token = m.montar_token(client, resposta)
    assert token == {"client_id": "id-x", "client_secret": "seg-x", "refresh_token": "rt"}
    assert "access_token" not in token


def test_montar_token_sem_refresh_token_falha() -> None:
    m = _carregar()
    client = {"client_id": "id-x", "client_secret": "seg-x"}
    with pytest.raises(ValueError, match="refresh_token"):
        m.montar_token(client, {"access_token": "at"})
