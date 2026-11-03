from app.dominio import insights


def _p(rotulo: str, centavos: int) -> dict[str, object]:
    from app.dominio.dinheiro import formatar_brl
    return {"rotulo": rotulo, "valor_centavos": centavos, "valor_fmt": formatar_brl(centavos), "negativo": centavos < 0}


def test_saldo_negativo_aponta_primeiro_mes_e_pior_ponto() -> None:
    r = insights.saldo_projetado([_p("nov/26", 10000), _p("dez/26", -5000), _p("jan/27", -20000)])
    assert r["tom"] == "ruim" and "a partir de dez/26" in r["texto"] and "jan/27" in r["texto"]
    assert r["destaque"] == "- R$ 200,00"


def test_saldo_positivo_e_bom() -> None:
    r = insights.saldo_projetado([_p("nov/26", 10000), _p("dez/26", 8000)])
    assert r["tom"] == "bom" and "R$ 80,00" in r["texto"]


def test_parcelas_queda_percentual() -> None:
    r = insights.parcelas_futuras([_p("nov/26", 10000), _p("dez/26", 5000)])
    assert "cai 50%" in r["texto"] and r["destaque"] == "R$ 150,00" and r["tom"] == "bom"


def test_parcelas_zeradas() -> None:
    assert insights.parcelas_futuras([_p("nov/26", 0)])["destaque"] == "R$ 0,00"


def test_maiores_categorias_percentual_do_total() -> None:
    r = insights.maiores_categorias([("Mercado", 6000), ("Lazer", 2000)], 10000)
    assert r["destaque"] == "60% em Mercado" and "80%" in r["texto"]


def test_cartoes_maior_que_saldo_e_ruim() -> None:
    r = insights.cartoes_em_aberto([{"nome": "Itaú", "valor_centavos": 50000}], 10000)
    assert r["tom"] == "ruim" and "Itaú" in r["texto"]


def test_gastos_comparados_ao_mes_anterior() -> None:
    r = insights.gastos_do_mes(15000, 10000, "set/26", "Mercado")
    assert r["texto"].startswith("50% acima de set/26") and r["tom"] == "ruim"
