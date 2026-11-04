import random

from app.dominio.versiculos import VERSICULOS, sortear


def test_versiculos_sao_variados_e_completos() -> None:
    assert len({v.referencia for v in VERSICULOS}) == len(VERSICULOS)
    assert len({v.tema for v in VERSICULOS}) >= 8
    assert all(v.texto and v.referencia and v.tema for v in VERSICULOS)


def test_sorteio_deterministico_com_semente() -> None:
    assert sortear(random.Random(1)) == sortear(random.Random(1))
    assert len({sortear(random.Random(i)).referencia for i in range(40)}) > 5
