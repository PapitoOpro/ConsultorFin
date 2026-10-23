from datetime import date

import pytest

from app.dominio.projecao import projetar, vencimentos
from app.repo.contas import criar_conta
from app.repo.lancamentos import inserir_manual

HOJE = date(2026, 10, 15)


def _lanc(con, conta, data, valor, *, grupo=None, comp=False, estimado=0, rec=None):
    i = inserir_manual(
        con, conta_id=conta, data=data, descricao="x", valor_centavos=valor,
        tipo="saida" if valor < 0 else "entrada", categoria_id=None,
        compensado=comp, observacao=None, grupo_parcela_uuid=grupo,
    )
    if estimado:
        con.execute("UPDATE lancamentos SET estimado = 1 WHERE id = ?", (i,))
    return i


def _conta(con, saldo=100000):
    return criar_conta(con, "C", "corrente", saldo_inicial_centavos=saldo,
                       data_saldo_inicial="2026-01-01")


def test_vencimentos_janela(con) -> None:
    c = _conta(con)
    _lanc(con, c, "2026-10-14", -100)   # vencida
    _lanc(con, c, "2026-10-15", -200)   # hoje: a vencer
    _lanc(con, c, "2026-10-22", -300)   # +7: a vencer
    _lanc(con, c, "2026-10-23", -400)   # +8: fora
    _lanc(con, c, "2026-10-16", 500)    # entrada: fora
    _lanc(con, c, "2026-10-16", -600, comp=True)  # compensada: fora
    v = vencimentos(con, HOJE)
    assert [r["valor_centavos"] for r in v.vencidas] == [-100]
    assert [r["valor_centavos"] for r in v.a_vencer] == [-200, -300]


def test_projecao_parcelas(con) -> None:
    c = _conta(con)
    for d in ("2026-10-20", "2026-11-20", "2026-12-20"):
        _lanc(con, c, d, -30000, grupo="g")
    p = projetar(con, HOJE, 3)
    assert [x.saldo_final_centavos for x in p] == [70000, 40000, 10000]
    assert [x.mes for x in p] == ["2026-10", "2026-11", "2026-12"]
    assert p[0].parcelas_centavos == 30000 and p[0].saida_pendente_centavos == 30000


def test_projecao_com_receita(con) -> None:
    c = _conta(con)
    for d in ("2026-10-20", "2026-11-20", "2026-12-20"):
        _lanc(con, c, d, -30000, grupo="g")
    _lanc(con, c, "2026-11-05", 50000)
    p = projetar(con, HOJE, 3)
    assert [x.saldo_final_centavos for x in p] == [70000, 90000, 60000]
    assert p[1].entrada_pendente_centavos == 50000


def test_estimado_entra_e_vencida_conta_uma_vez(con) -> None:
    c = _conta(con)
    _lanc(con, c, "2026-10-20", -10000, estimado=1)
    _lanc(con, c, "2026-09-10", -5000)  # vencida, vai no primeiro ponto
    p = projetar(con, HOJE, 3)
    assert p[0].saida_pendente_centavos == 15000
    assert [x.saldo_final_centavos for x in p] == [85000] * 3


def test_compensados_horizonte_e_cartao(con) -> None:
    c = _conta(con)
    cartao = criar_conta(con, "Cartão", "cartao", saldo_inicial_centavos=999999,
                         data_saldo_inicial="2026-01-01")
    _lanc(con, c, "2026-10-01", -7000, comp=True)  # já no saldo atual
    _lanc(con, c, "2027-05-10", -8000)  # além de 3 meses
    _lanc(con, cartao, "2026-11-10", -2000)  # pendente no cartão subtrai
    p = projetar(con, HOJE, 3)
    assert [x.saldo_final_centavos for x in p] == [93000, 91000, 91000]


@pytest.mark.parametrize("n", [3, 6, 12])
def test_numero_de_pontos(con, n) -> None:
    _conta(con)
    assert len(projetar(con, HOJE, n)) == n


def test_horizonte_invalido(con) -> None:
    with pytest.raises(ValueError, match="Horizonte inválido"):
        projetar(con, HOJE, 5)
