import sqlite3
from datetime import date
from typing import Any

import pytest

from app.dominio import recorrentes as rec
from app.repo import contas


@pytest.fixture
def conta(con: sqlite3.Connection) -> int:
    return contas.criar_conta(con, "Itaú", "corrente")


def _lancs(con: sqlite3.Connection, rid: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM lancamentos WHERE recorrente_id = ? ORDER BY data", (rid,)).fetchall()


def _conta_fixa(con: sqlite3.Connection, conta: int, dia: int = 10, valor: int = 20000) -> int:
    return rec.criar_recorrente(
        con, nome="Luz", tipo="saida", dia=dia, valor_estimado_centavos=valor,
        conta_id=conta, categoria_id=None)


def test_gerar_janela_e_idempotencia(con: sqlite3.Connection, conta: int) -> None:
    rid = _conta_fixa(con, conta)
    hoje = date(2026, 10, 23)
    assert rec.gerar_recorrentes(con, hoje, 3) == 3  # janela: [mês de hoje, hoje + meses - 1]
    assert rec.gerar_recorrentes(con, hoje, 3) == 0
    ls = _lancs(con, rid)
    assert [r["data"] for r in ls] == ["2026-10-10", "2026-11-10", "2026-12-10"]
    assert all(r["estimado"] == 1 and r["compensado"] == 0 and r["origem"] == "manual"
               and r["valor_centavos"] == -20000 and r["tipo"] == "saida"
               and r["descricao"] == "Luz" for r in ls)
    # reabrir o app num mês seguinte só acrescenta o que falta
    assert rec.gerar_recorrentes(con, date(2026, 11, 2), 3) == 1
    assert len(_lancs(con, rid)) == 4


def test_dia_31_fevereiro(con: sqlite3.Connection, conta: int) -> None:
    rid = _conta_fixa(con, conta, dia=31)
    rec.gerar_recorrentes(con, date(2027, 1, 15), 2)
    rec.gerar_recorrentes(con, date(2028, 2, 1), 1)
    assert [r["data"] for r in _lancs(con, rid)] == ["2027-01-31", "2027-02-28", "2028-02-29"]


def test_receita_positiva(con: sqlite3.Connection, conta: int) -> None:
    rid = rec.criar_recorrente(con, nome="Salário", tipo="entrada", dia=5,
                               valor_estimado_centavos=500000, conta_id=conta, categoria_id=None)
    rec.gerar_recorrentes(con, date(2026, 10, 1), 1)
    (l,) = _lancs(con, rid)
    assert l["valor_centavos"] == 500000 and l["tipo"] == "entrada"


def test_confirmar_atualiza_futuros_estimados(con: sqlite3.Connection, conta: int) -> None:
    rid = _conta_fixa(con, conta)
    rec.gerar_recorrentes(con, date(2026, 10, 1), 4)
    out, nov, dez, jan = _lancs(con, rid)
    con.execute("UPDATE lancamentos SET estimado = 0, valor_centavos = -25000 WHERE id = ?",
                (dez["id"],))  # dez já confirmado antes: não pode ser sobrescrito
    con.commit()
    rec.confirmar_recorrente(con, nov["id"], 23000, date(2026, 11, 9))
    por_id = {r["id"]: r for r in _lancs(con, rid)}
    assert por_id[nov["id"]]["valor_centavos"] == -23000
    assert por_id[nov["id"]]["compensado"] == 1 and por_id[nov["id"]]["estimado"] == 0
    assert por_id[nov["id"]]["data_compensacao"] == "2026-11-09"
    assert por_id[out["id"]]["valor_centavos"] == -20000  # mês anterior intocado
    assert por_id[dez["id"]]["valor_centavos"] == -25000
    assert por_id[jan["id"]]["valor_centavos"] == -23000 and por_id[jan["id"]]["estimado"] == 1


def test_valor_da_geracao_usa_ultimo_real(con: sqlite3.Connection, conta: int) -> None:
    rid = _conta_fixa(con, conta)
    rec.gerar_recorrentes(con, date(2026, 10, 1), 1)
    (out,) = _lancs(con, rid)
    rec.confirmar_recorrente(con, out["id"], 23000, date(2026, 10, 9))
    rec.gerar_recorrentes(con, date(2026, 10, 1), 3)
    assert [r["valor_centavos"] for r in _lancs(con, rid)] == [-23000, -23000, -23000]


def test_confirmar_validacoes(con: sqlite3.Connection, conta: int) -> None:
    rid = _conta_fixa(con, conta)
    rec.gerar_recorrentes(con, date(2026, 10, 1), 1)
    (l,) = _lancs(con, rid)
    with pytest.raises(ValueError, match="maior que zero"):
        rec.confirmar_recorrente(con, l["id"], 0, date(2026, 10, 9))
    with pytest.raises(ValueError, match="não encontrado"):
        rec.confirmar_recorrente(con, 9999, 100, date(2026, 10, 9))
    avulso = con.execute(
        "INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
        " chave_deduplicacao, origem) VALUES ('u', ?, '2026-10-01', 'x', -1, 'saida', 'k', 'manual')",
        (conta,)).lastrowid
    con.commit()
    with pytest.raises(ValueError, match="recorrente"):
        rec.confirmar_recorrente(con, avulso, 100, date(2026, 10, 9))


def test_pular_mes(con: sqlite3.Connection, conta: int) -> None:
    rid = _conta_fixa(con, conta)
    rec.gerar_recorrentes(con, date(2026, 10, 1), 3)
    nov = _lancs(con, rid)[1]
    rec.pular_mes(con, nov["id"])
    assert [r["data"] for r in _lancs(con, rid)] == ["2026-10-10", "2026-12-10"]
    assert rec.gerar_recorrentes(con, date(2026, 10, 1), 3) == 0
    assert [r["data"] for r in _lancs(con, rid)] == ["2026-10-10", "2026-12-10"]
    with pytest.raises(ValueError, match="não encontrado"):
        rec.pular_mes(con, nov["id"])


def test_pular_mes_rejeita_pago(con: sqlite3.Connection, conta: int) -> None:
    rid = _conta_fixa(con, conta)
    rec.gerar_recorrentes(con, date(2026, 10, 1), 1)
    (l,) = _lancs(con, rid)
    rec.confirmar_recorrente(con, l["id"], 21000, date(2026, 10, 9))
    with pytest.raises(ValueError, match="pago"):
        rec.pular_mes(con, l["id"])


def test_desativar_so_pendentes_futuros(con: sqlite3.Connection, conta: int) -> None:
    rid = _conta_fixa(con, conta)
    rec.gerar_recorrentes(con, date(2026, 9, 1), 4)  # set, out, nov, dez
    _set, out, _nov, _dez = _lancs(con, rid)
    rec.confirmar_recorrente(con, out["id"], 21000, date(2026, 10, 9))
    rec.desativar_recorrente(con, rid, date(2026, 10, 23))
    # set vencida (pendente) e out paga permanecem; nov e dez futuras somem
    assert [r["data"] for r in _lancs(con, rid)] == ["2026-09-10", "2026-10-10"]
    assert rec.gerar_recorrentes(con, date(2026, 10, 1), 3) == 0
    with pytest.raises(ValueError, match="não encontrada"):
        rec.desativar_recorrente(con, 9999, date(2026, 10, 23))


def test_editar_data_nao_duplica_nem_pula(con: sqlite3.Connection, conta: int) -> None:
    rid = _conta_fixa(con, conta)
    rec.gerar_recorrentes(con, date(2026, 10, 1), 2)  # out, nov
    out, _nov = _lancs(con, rid)
    con.execute("UPDATE lancamentos SET data = '2026-11-20' WHERE id = ?", (out["id"],))
    con.commit()
    assert rec.gerar_recorrentes(con, date(2026, 10, 1), 3) == 1  # só dez
    assert sorted(r["competencia"] for r in _lancs(con, rid)) == [
        "2026-10", "2026-11", "2026-12"]
    assert rec.gerar_recorrentes(con, date(2026, 10, 1), 3) == 0


def test_pular_mes_apos_mover_usa_competencia_original(
    con: sqlite3.Connection, conta: int
) -> None:
    rid = _conta_fixa(con, conta)
    rec.gerar_recorrentes(con, date(2026, 10, 1), 1)
    (out,) = _lancs(con, rid)
    con.execute("UPDATE lancamentos SET data = '2026-11-20' WHERE id = ?", (out["id"],))
    con.commit()
    rec.pular_mes(con, out["id"])
    assert con.execute("SELECT mes FROM recorrentes_pulados").fetchone()["mes"] == "2026-10"
    assert rec.gerar_recorrentes(con, date(2026, 10, 1), 1) == 0


def test_criar_conta_ou_categoria_inexistente(con: sqlite3.Connection, conta: int) -> None:
    base: dict[str, Any] = dict(nome="Luz", tipo="saida", dia=10, valor_estimado_centavos=100)
    with pytest.raises(ValueError, match="Conta não encontrada"):
        rec.criar_recorrente(con, **base, conta_id=9999, categoria_id=None)
    with pytest.raises(ValueError, match="Categoria não encontrada"):
        rec.criar_recorrente(con, **base, conta_id=conta, categoria_id=9999)


@pytest.mark.parametrize("kw,msg", [
    ({"dia": 0}, "dia"), ({"dia": 32}, "dia"), ({"tipo": "transferencia"}, "tipo"),
    ({"valor_estimado_centavos": 0}, "valor"), ({"nome": "  "}, "nome"),
])
def test_criar_validacoes(con: sqlite3.Connection, conta: int, kw: dict[str, Any], msg: str) -> None:
    args: dict[str, Any] = dict(nome="Luz", tipo="saida", dia=10, valor_estimado_centavos=100,
                                conta_id=conta, categoria_id=None) | kw
    with pytest.raises(ValueError, match=msg):
        rec.criar_recorrente(con, **args)


def test_gerar_meses_invalido(con: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="meses"):
        rec.gerar_recorrentes(con, date(2026, 10, 1), 0)
