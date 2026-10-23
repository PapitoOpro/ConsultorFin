from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest

from app.consultor import consultas
from app.db import conectar, migrar
from app.dominio.lancamentos import criar_lancamento_manual
from app.dominio.painel import resumo_inicio
from tests._consultor_base import HOJE, importar_extrato, nova_conta


@pytest.fixture
def banco() -> Iterator[Any]:
    con = conectar(":memory:")
    migrar(con)
    importar_extrato(con, nova_conta(con))
    yield con
    con.close()


def test_saldos(banco: Any) -> None:
    r = consultas.saldos(banco, HOJE)
    c = r["contas"][0]
    assert c["saldo_centavos"] == 140914
    assert c["saldo"] == "R$ 1.409,14"
    assert r["saldo_total_centavos"] == 140914


def test_resumo_igual_ao_inicio(banco: Any) -> None:
    r = consultas.resumo(banco, HOJE, "2026-10")
    ref = resumo_inicio(banco, HOJE, 2026, 10, 3)
    assert r["entradas_centavos"] == ref.entradas_centavos
    assert r["saidas_centavos"] == ref.saidas_centavos
    assert r["sobra_centavos"] == ref.sobra_centavos
    assert r["saldo_total_centavos"] == ref.saldo_total_centavos
    assert r["mes"] == "2026-10"
    assert r["saidas_centavos"] > 0


def test_vencimentos_respeita_dias(banco: Any) -> None:
    for dia, desc in (("2026-10-20", "Vencida"), ("2026-10-25", "Proxima"), ("2026-12-01", "Longe")):
        criar_lancamento_manual(banco, conta_id=1, data=date.fromisoformat(dia), descricao=desc,
                                valor_centavos=-1000)
    r = consultas.vencimentos(banco, HOJE, 7)
    assert [x["descricao"] for x in r["vencidas"]] == ["Vencida"]
    assert [x["descricao"] for x in r["a_vencer"]] == ["Proxima"]
    assert r["a_vencer"][0]["valor_centavos"] == -1000
    assert r["a_vencer"][0]["data_br"] == "25/10/2026"
    assert len(consultas.vencimentos(banco, HOJE, 60)["a_vencer"]) == 2


def test_projecao(banco: Any) -> None:
    p3 = consultas.projecao(banco, HOJE, 3)["pontos"]
    p12 = consultas.projecao(banco, HOJE, 12)["pontos"]
    assert [p["mes"] for p in p3] == ["2026-10", "2026-11", "2026-12"]
    assert len(p12) == 12 and p12[-1]["mes"] == "2027-09"
    assert p3[0]["saldo_final_centavos"] == 140914
    with pytest.raises(ValueError):
        consultas.projecao(banco, HOJE, 5)


def test_gastos_soma_igual_saidas(banco: Any) -> None:
    r = consultas.gastos(banco, HOJE, "2026-10")
    assert sum(c["total_centavos"] for c in r["categorias"]) == r["saidas_centavos"] > 0
    assert r["categorias"][0]["nome"] == "Sem categoria"


def test_lancamentos_texto_sem_acento_e_limite(banco: Any) -> None:
    r = consultas.lancamentos(banco, HOJE, texto="PADARIA")
    assert r["total"] == 2 and len(r["lancamentos"]) == 2
    assert r["lancamentos"][0]["valor_centavos"] == -2490
    r = consultas.lancamentos(banco, HOJE, texto="padaria", limite=1)
    assert len(r["lancamentos"]) == 1 and r["total"] == 2 and r["truncado"] is True
    assert consultas.lancamentos(banco, HOJE, texto="farmácia")["total"] == 1


def test_lancamentos_limite_atravessa_paginas(banco: Any) -> None:
    for i in range(60):
        criar_lancamento_manual(banco, conta_id=1, data=date(2026, 10, 1), descricao=f"ZZ{i}",
                                valor_centavos=-100)
    assert len(consultas.lancamentos(banco, HOJE, texto="zz", limite=120)["lancamentos"]) == 60
    assert len(consultas.lancamentos(banco, HOJE, texto="zz", limite=55)["lancamentos"]) == 55


def test_contas_e_categorias(banco: Any) -> None:
    assert consultas.contas(banco, HOJE)["contas"][0]["nome"] == "Itaú"
    cats = consultas.categorias(banco, HOJE)["categorias"]
    assert {"id", "nome", "subcategorias"} <= set(cats[0])
    assert "Mercado" in [c["nome"] for c in cats]
