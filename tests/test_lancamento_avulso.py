from datetime import date
from typing import Any

import pytest

from app.dominio.lancamentos import ErroLancamento, criar_avulso_de_formulario
from app.repo import contas

HOJE = date(2026, 11, 10)


@pytest.fixture
def conta(con: Any) -> int:
    return contas.criar_conta(con, "Itaú", "corrente")


def _criar(con: Any, conta: int, **sobrescrever: Any) -> str:
    campos: dict[str, Any] = dict(
        modo="avista", data="2026-11-05", descricao="Mercado", valor="100,00", sinal="saida",
        conta_id=str(conta), categoria_id="", observacao="", ja_pago=False, n_parcelas="1",
        hoje=HOJE)
    campos.update(sobrescrever)
    return criar_avulso_de_formulario(con, **campos)


def _contar(con: Any, tabela: str) -> int:
    return int(con.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0])


def test_avista_passado_ja_pago_fica_compensado(con: Any, conta: int) -> None:
    assert _criar(con, conta, ja_pago=True) == "Lançamento criado."
    [linha] = con.execute("SELECT compensado, valor_centavos FROM lancamentos").fetchall()
    assert (linha["compensado"], linha["valor_centavos"]) == (1, -10000)


def test_avista_futuro_fica_pendente(con: Any, conta: int) -> None:
    _criar(con, conta, data="2026-12-05")
    [linha] = con.execute("SELECT compensado FROM lancamentos").fetchall()
    assert linha["compensado"] == 0


def test_ja_pago_com_data_futura_e_recusado(con: Any, conta: int) -> None:
    with pytest.raises(ErroLancamento, match="Só é possível marcar como pago"):
        _criar(con, conta, data="2026-12-05", ja_pago=True)
    assert _contar(con, "lancamentos") == 0


def test_parcelado_cria_n_parcelas_pendentes(con: Any, conta: int) -> None:
    msg = _criar(con, conta, modo="parcelado", valor="300,00", n_parcelas="3")
    assert msg == "Parcelamento criado: 3 parcelas."
    linhas = con.execute("SELECT valor_centavos, compensado, parcela_total FROM lancamentos"
                         ).fetchall()
    assert [(r["valor_centavos"], r["compensado"], r["parcela_total"]) for r in linhas] == \
        [(-10000, 0, 3)] * 3


def test_parcelado_uma_parcela_no_singular(con: Any, conta: int) -> None:
    assert _criar(con, conta, modo="parcelado", n_parcelas="1") == "Parcelamento criado: 1 parcela."


def test_parcelado_entrada_e_recusado(con: Any, conta: int) -> None:
    with pytest.raises(ErroLancamento, match="Parcelamento só para saídas."):
        _criar(con, conta, modo="parcelado", sinal="entrada", n_parcelas="3")
    assert _contar(con, "lancamentos") == 0


def test_parcelado_ja_pago_e_recusado(con: Any, conta: int) -> None:
    with pytest.raises(ErroLancamento):
        _criar(con, conta, modo="parcelado", n_parcelas="3", ja_pago=True)
    assert _contar(con, "lancamentos") == 0


def test_recorrente_usa_dia_da_data_e_valor_estimado(con: Any, conta: int) -> None:
    msg = _criar(con, conta, modo="recorrente", data="2026-11-17", descricao="Luz")
    assert msg == "Conta fixa cadastrada."
    [r] = con.execute("SELECT dia, valor_estimado_centavos, tipo FROM recorrentes").fetchall()
    assert (r["dia"], r["valor_estimado_centavos"], r["tipo"]) == (17, 10000, "saida")


def test_recorrente_ja_pago_e_recusado(con: Any, conta: int) -> None:
    with pytest.raises(ErroLancamento):
        _criar(con, conta, modo="recorrente", ja_pago=True)
    assert _contar(con, "recorrentes") == 0


def test_modo_invalido(con: Any, conta: int) -> None:
    with pytest.raises(ErroLancamento, match="Escolha À vista, Parcelado ou Repetir todo mês."):
        _criar(con, conta, modo="xyz")
