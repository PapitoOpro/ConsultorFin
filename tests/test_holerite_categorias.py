"""Categorias usadas por itens de holerite: uso, exclusão e substituição (dados inventados)."""
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import conectar
from app.dominio import holerites as dom
from app.dominio.categorias import ErroCategoria, criar_categoria, remover_categoria
from app.importacao.holerite.modelo import HoleriteLido, ItemHolerite
from app.main import create_app
from app.repo import categorias as repo_categorias
from app.repo import contas as repo_contas

HOJE = date(2026, 10, 30)


def _com_item(con: sqlite3.Connection) -> tuple[int, int]:
    """Holerite com o item de desconto na categoria A; devolve (A, B), ambas de saída."""
    a = criar_categoria(con, "Pensao Inventada", "saida")
    b = criar_categoria(con, "Outra Inventada", "saida")
    conta = repo_contas.criar_conta(con, "Corrente", "corrente")
    h = HoleriteLido("mensal", "2026-09", "ACME", 500000, 120000, 380000,
                     [ItemHolerite("1", "SALARIO FICTICIO", None, "provento", 500000),
                      ItemHolerite("2", "PENSAO INVENTADA", None, "desconto", 120000)])
    dom.importar_holerite(con, h, "h1", None, conta, HOJE, [None, a], None)
    return a, b


def _cats(con: sqlite3.Connection) -> list[Any]:
    return [r[0] for r in con.execute("SELECT categoria_id FROM holerite_itens ORDER BY ordem")]


def test_uso_conta_itens_de_holerite(con: sqlite3.Connection) -> None:
    a, _ = _com_item(con)
    assert repo_categorias.uso(con, a)["itens_holerite"] == 1


def test_remover_categoria_de_item_de_holerite_sem_escolha_recusado(
        con: sqlite3.Connection) -> None:
    a, _ = _com_item(con)
    with pytest.raises(ErroCategoria, match="1 item\\(ns\\) de holerite"):
        remover_categoria(con, a)
    assert _cats(con) == [None, a]


def test_remover_categoria_de_item_de_holerite_substituir_por(con: sqlite3.Connection) -> None:
    a, b = _com_item(con)
    assert remover_categoria(con, a, substituir_por=b)["itens_holerite"] == 1
    assert _cats(con) == [None, b]


def test_remover_categoria_de_item_de_holerite_deixar_sem(con: sqlite3.Connection) -> None:
    a, _ = _com_item(con)
    assert remover_categoria(con, a, deixar_sem=True)["itens_holerite"] == 1
    assert _cats(con) == [None, None]


def test_web_excluir_categoria_de_item_de_holerite_mostra_contagem(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    with TestClient(create_app(db)) as cliente:
        c = conectar(db)
        try:
            a, _ = _com_item(c)
            c.commit()
        finally:
            c.close()
        assert "1 item de holerite" in cliente.get("/categorias?aba=saida").text
        r = cliente.post(f"/categorias/{a}/excluir", data={"aba": "saida"})
        assert r.status_code == 200 and "1 item de holerite" in r.text
        r = cliente.post(f"/categorias/{a}/excluir", data={"aba": "saida", "acao": "sem"})
        assert r.status_code == 200 and "Categoria excluída." in r.text
