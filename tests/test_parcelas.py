from datetime import date

import pytest

from app.dominio.lancamentos import criar_lancamento_manual
from app.dominio.parcelas import (
    criar_parcelamento, dividir_em_parcelas, editar_grupo_parcelas,
    excluir_grupo_parcelas, marcar_pago,
)
from app.repo import contas


@pytest.fixture
def conta(con) -> int:
    return contas.criar_conta(con, "Corrente", "corrente")


def _grupo(con, g):
    return con.execute(
        "SELECT * FROM lancamentos WHERE grupo_parcela_uuid = ? ORDER BY parcela_atual", (g,)
    ).fetchall()


def _parcelar(con, conta, total=10000, n=3, venc=date(2026, 1, 31)):
    ids = criar_parcelamento(
        con, descricao="Geladeira", valor_total_centavos=total, n_parcelas=n,
        primeiro_vencimento=venc, conta_id=conta, categoria_id=None)
    g = con.execute("SELECT grupo_parcela_uuid g FROM lancamentos WHERE id=?", (ids[0],)).fetchone()["g"]
    return ids, g


def test_dividir():
    assert dividir_em_parcelas(10000, 3) == [3333, 3333, 3334]
    assert dividir_em_parcelas(5000, 1) == [5000]
    for total in (1, 7, 9999, 10001):
        for n in (1, 2, 3, 7, 12):
            if total >= n:  # total < n é recusado (parcela de 0 centavo)
                assert sum(dividir_em_parcelas(total, n)) == total
    assert dividir_em_parcelas(3, 3) == [1, 1, 1]


@pytest.mark.parametrize("total,n", [(10000, 0), (10000, -1), (0, 3), (-100, 3), (2, 3), (1, 2)])
def test_dividir_invalido(total, n):
    with pytest.raises(ValueError):
        dividir_em_parcelas(total, n)


def test_manual(con, conta):
    i = criar_lancamento_manual(con, conta_id=conta, data=date(2026, 5, 1),
                                descricao="Café", valor_centavos=-1500)
    j = criar_lancamento_manual(con, conta_id=conta, data=date(2026, 5, 2),
                                descricao="Freela", valor_centavos=9000, compensado=True)
    a = con.execute("SELECT * FROM lancamentos WHERE id=?", (i,)).fetchone()
    b = con.execute("SELECT * FROM lancamentos WHERE id=?", (j,)).fetchone()
    assert (a["origem"], a["tipo"], a["compensado"], a["data_compensacao"]) == ("manual", "saida", 0, None)
    assert a["chave_deduplicacao"] == f"manual:{a['uuid']}"
    assert (b["tipo"], b["compensado"], b["data_compensacao"]) == ("entrada", 1, "2026-05-02")


def test_manual_invalido(con, conta):
    with pytest.raises(ValueError):
        criar_lancamento_manual(con, conta_id=conta, data=date(2026, 5, 1),
                                descricao="  ", valor_centavos=-1)
    with pytest.raises(ValueError):
        criar_lancamento_manual(con, conta_id=conta, data=date(2026, 5, 1),
                                descricao="x", valor_centavos=0)


def test_parcelamento_10(con, conta):
    ids, g = _parcelar(con, conta, total=10000, n=10, venc=date(2026, 1, 31))
    rows = _grupo(con, g)
    assert len(ids) == 10
    assert [(r["parcela_atual"], r["parcela_total"]) for r in rows] == [(k, 10) for k in range(1, 11)]
    assert sum(r["valor_centavos"] for r in rows) == -10000
    assert all(r["compensado"] == 0 and r["tipo"] == "saida" and r["descricao"] == "Geladeira" for r in rows)
    assert [r["data"] for r in rows][:3] == ["2026-01-31", "2026-02-28", "2026-03-31"]


def test_parcelamento_invalido(con, conta):
    for kw in ({"n_parcelas": 0}, {"valor_total_centavos": 0}, {"valor_total_centavos": -5},
               {"descricao": " "}):
        args = dict(descricao="x", valor_total_centavos=100, n_parcelas=2,
                    primeiro_vencimento=date(2026, 1, 1), conta_id=conta, categoria_id=None)
        args.update(kw)
        with pytest.raises(ValueError):
            criar_parcelamento(con, **args)
    assert con.execute("SELECT COUNT(*) c FROM lancamentos").fetchone()["c"] == 0


def test_marcar_pago(con, conta):
    ids, g = _parcelar(con, conta)
    con.execute("UPDATE lancamentos SET estimado=1 WHERE id=?", (ids[0],)); con.commit()
    marcar_pago(con, ids[0], 3500, date(2026, 2, 1))
    r = con.execute("SELECT * FROM lancamentos WHERE id=?", (ids[0],)).fetchone()
    assert (r["compensado"], r["data_compensacao"], r["estimado"], r["valor_centavos"]) == (1, "2026-02-01", 0, -3500)
    marcar_pago(con, ids[1], None, date(2026, 3, 1))
    r = con.execute("SELECT * FROM lancamentos WHERE id=?", (ids[1],)).fetchone()
    assert (r["compensado"], r["valor_centavos"]) == (1, -3333)


def test_marcar_pago_mantem_sinal_entrada(con, conta):
    i = criar_lancamento_manual(con, conta_id=conta, data=date(2026, 5, 1),
                                descricao="Receber", valor_centavos=1000)
    marcar_pago(con, i, 800, date(2026, 5, 3))
    assert con.execute("SELECT valor_centavos v FROM lancamentos WHERE id=?", (i,)).fetchone()["v"] == 800


def test_marcar_pago_invalido(con, conta):
    ids, _ = _parcelar(con, conta)
    with pytest.raises(ValueError):
        marcar_pago(con, ids[0], -5, date(2026, 2, 1))
    with pytest.raises(ValueError):
        marcar_pago(con, 99999, None, date(2026, 2, 1))


def test_excluir_so_pendentes(con, conta):
    ids, g = _parcelar(con, conta)
    marcar_pago(con, ids[0], None, date(2026, 2, 1))
    excluir_grupo_parcelas(con, g)
    rows = _grupo(con, g)
    assert [r["id"] for r in rows] == [ids[0]]


def test_editar_total(con, conta):
    ids, g = _parcelar(con, conta, total=9000, n=3)
    marcar_pago(con, ids[0], None, date(2026, 2, 1))
    editar_grupo_parcelas(con, g, valor_total_centavos=10001)  # total das 2 pendentes
    rows = _grupo(con, g)
    assert rows[0]["id"] == ids[0] and rows[0]["valor_centavos"] == -3000
    assert [r["valor_centavos"] for r in rows[1:]] == [-5000, -5001]
    assert [r["data"] for r in rows[1:]] == ["2026-02-28", "2026-03-31"]
    assert [(r["parcela_atual"], r["parcela_total"]) for r in rows] == [(1, 3), (2, 3), (3, 3)]


def test_editar_n(con, conta):
    ids, g = _parcelar(con, conta, total=9000, n=3)
    marcar_pago(con, ids[0], None, date(2026, 2, 1))
    editar_grupo_parcelas(con, g, n_parcelas=3)  # 3 pendentes, soma pendente mantida (6000)
    rows = _grupo(con, g)
    assert [r["valor_centavos"] for r in rows] == [-3000, -2000, -2000, -2000]
    assert [(r["parcela_atual"], r["parcela_total"]) for r in rows] == [(1, 4), (2, 4), (3, 4), (4, 4)]
    assert rows[0]["id"] == ids[0] and rows[0]["compensado"] == 1


def test_editar_invalido(con, conta):
    ids, g = _parcelar(con, conta)
    with pytest.raises(ValueError):
        editar_grupo_parcelas(con, g, n_parcelas=0)
    with pytest.raises(ValueError):
        editar_grupo_parcelas(con, g, valor_total_centavos=0)
    for i in ids:
        marcar_pago(con, i, None, date(2026, 2, 1))
    with pytest.raises(ValueError):
        editar_grupo_parcelas(con, g, n_parcelas=2)
