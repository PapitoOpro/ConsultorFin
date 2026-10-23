from datetime import date
from typing import Any

import pytest

from app.dominio.contas_pagar import (
    abrir_painel,
    interpretar_horizonte,
    listar_recorrentes,
    resumo_parcelamentos,
)
from app.dominio.lancamentos import ErroLancamento
from app.dominio.parcelas import criar_parcelamento, marcar_pago
from app.dominio.recorrentes import confirmar_recorrente, criar_recorrente
from app.repo import contas

HOJE = date(2026, 10, 23)


@pytest.fixture
def conta(con: Any) -> int:
    return contas.criar_conta(con, "Itaú", "corrente")


def test_interpretar_horizonte() -> None:
    assert interpretar_horizonte(None) == 6
    assert [interpretar_horizonte(x) for x in ("3", "6", "12")] == [3, 6, 12]
    for ruim in ("", "5", "abc", "٣", "3.0", " 3x"):
        with pytest.raises(ErroLancamento, match="Horizonte inválido"):
            interpretar_horizonte(ruim)


def test_resumo_parcelamentos(con: Any, conta: int) -> None:
    ids = criar_parcelamento(con, descricao="TV", valor_total_centavos=10000, n_parcelas=3,
                             primeiro_vencimento=date(2026, 11, 5), conta_id=conta,
                             categoria_id=None)
    marcar_pago(con, ids[0], None, HOJE)
    [g] = resumo_parcelamentos(con)
    assert (g.descricao, g.n, g.pagas, g.pendentes) == ("TV", 3, 1, 2)
    assert (g.total_centavos, g.restante_centavos, g.proxima_data) == (10000, 6667, "2026-12-05")


def test_resumo_vazio(con: Any) -> None:
    assert resumo_parcelamentos(con) == []


def test_listar_recorrentes_com_ultimo_real(con: Any, conta: int) -> None:
    rid = criar_recorrente(con, nome="Luz", tipo="saida", dia=15, valor_estimado_centavos=9000,
                           conta_id=conta, categoria_id=None)
    [r] = listar_recorrentes(con)
    assert (r.nome, r.dia, r.valor_estimado_centavos, r.ultimo_real_centavos, r.ativa) == (
        "Luz", 15, 9000, None, True)
    abrir_painel(con, HOJE, 3)
    pid = con.execute("SELECT id FROM lancamentos ORDER BY data LIMIT 1").fetchone()["id"]
    confirmar_recorrente(con, pid, 9500, HOJE)
    [r] = listar_recorrentes(con)
    assert r.ultimo_real_centavos == 9500 and r.conta_nome == "Itaú" and r.id == rid


def test_abrir_painel_particiona_e_gera(con: Any, conta: int) -> None:
    criar_recorrente(con, nome="Luz", tipo="saida", dia=25, valor_estimado_centavos=9000,
                     conta_id=conta, categoria_id=None)
    criar_recorrente(con, nome="Salário", tipo="entrada", dia=5, valor_estimado_centavos=500000,
                     conta_id=conta, categoria_id=None)
    p = abrir_painel(con, HOJE, 3)
    assert p.horizonte == 3
    assert [r["data"] for r in p.vencidas] == []
    assert [r["data"] for r in p.a_vencer] == ["2026-10-25"]
    assert [r["data"] for r in p.proximos] == ["2026-11-25", "2026-12-25"]
    assert [r["data"] for r in p.receitas] == ["2026-10-05", "2026-11-05", "2026-12-05"]
    assert p.total_a_vencer_centavos == -9000
    assert [r["data"] for r in abrir_painel(con, HOJE, 3).proximos] == ["2026-11-25", "2026-12-25"]
