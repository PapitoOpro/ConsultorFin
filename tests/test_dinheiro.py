from datetime import date

import pytest

from app.dominio.dinheiro import (
    data_br_para_iso,
    formatar_brl,
    iso_para_br,
    para_centavos,
    somar_meses,
)


def test_para_centavos_com_milhar_e_virgula() -> None:
    assert para_centavos("1.234,56") == 123456


def test_para_centavos_negativo() -> None:
    assert para_centavos("-603,89") == -60389


def test_para_centavos_com_simbolo_moeda() -> None:
    assert para_centavos("R$ 10,00") == 1000


def test_para_centavos_sem_centavos_aceita_inteiro() -> None:
    assert para_centavos("10") == 1000


@pytest.mark.parametrize("invalido", ["abc", "", "1,234.56x", "12,345"])
def test_para_centavos_invalido_levanta_valueerror(invalido: str) -> None:
    with pytest.raises(ValueError, match="valor"):
        para_centavos(invalido)


def test_formatar_brl() -> None:
    assert formatar_brl(140914) == "R$ 1.409,14"


def test_formatar_brl_negativo() -> None:
    assert formatar_brl(-123456) == "- R$ 1.234,56"


def test_data_br_para_iso() -> None:
    assert data_br_para_iso("22/10/2026") == "2026-10-22"


def test_data_br_para_iso_invalida_levanta_valueerror() -> None:
    with pytest.raises(ValueError, match="data"):
        data_br_para_iso("31/02/2026")


def test_iso_para_br() -> None:
    assert iso_para_br("2026-10-22") == "22/10/2026"


def test_somar_meses_clampa_fevereiro() -> None:
    assert somar_meses(date(2026, 1, 31), 1) == date(2026, 2, 28)


def test_somar_meses_clampa_fevereiro_bissexto() -> None:
    assert somar_meses(date(2028, 1, 31), 1) == date(2028, 2, 29)


def test_somar_meses_vira_ano() -> None:
    assert somar_meses(date(2026, 11, 15), 3) == date(2027, 2, 15)


def test_somar_meses_negativo() -> None:
    assert somar_meses(date(2026, 3, 31), -1) == date(2026, 2, 28)


def test_somar_meses_com_dia_fixo() -> None:
    assert somar_meses(date(2026, 1, 5), 1, dia=31) == date(2026, 2, 28)
    assert somar_meses(date(2026, 1, 5), 2, dia=31) == date(2026, 3, 31)
