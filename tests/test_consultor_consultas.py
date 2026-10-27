from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest

from app.consultor import consultas
from app.db import conectar, migrar
from app.dominio.lancamentos import criar_lancamento_manual
from app.dominio.recorrentes import criar_recorrente
from app.dominio.painel import TOP_ROSCA, comparativo_mensal, resumo_inicio, top_com_outros
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
    criar_lancamento_manual(banco, conta_id=1, data=date(2026, 10, 20), descricao="Vencida",
                            valor_centavos=-1000)
    criar_lancamento_manual(banco, conta_id=1, data=date(2026, 10, 25), descricao="Proxima",
                            valor_centavos=-2000)
    r = consultas.resumo(banco, HOJE, "2026-10")
    ref = resumo_inicio(banco, HOJE, 2026, 10, 3)
    assert r["vencidas"] == len(ref.venc.vencidas) == 1
    assert r["a_vencer"] == len(ref.venc.a_vencer) == 1
    assert r["vencidas_total_a_pagar_centavos"] == 1000
    esperado = top_com_outros([(c.nome, c.total_centavos) for c in ref.categorias], TOP_ROSCA)
    assert [(c["nome"], c["total_centavos"]) for c in r["maiores_categorias"]] == esperado
    comp = comparativo_mensal(banco, 2026, 10)
    assert [(c["mes"], c["entradas_centavos"], c["saidas_centavos"]) for c in r["comparativo"]] \
        == [(c["mes"], c["entradas_centavos"], c["saidas_centavos"]) for c in comp]
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
    assert r["a_vencer_total_a_pagar_centavos"] == 1000 and r["ate"] == "2026-10-30"
    assert len(consultas.vencimentos(banco, HOJE, 60)["a_vencer"]) == 2


def test_projecao(banco: Any) -> None:
    p3 = consultas.projecao(banco, HOJE, 3)["pontos"]
    p12 = consultas.projecao(banco, HOJE, 12)["pontos"]
    assert [p["mes"] for p in p3] == ["2026-10", "2026-11", "2026-12"]
    assert len(p12) == 12 and p12[-1]["mes"] == "2027-09"
    assert p3[0]["saldo_final_centavos"] == 140914
    r3 = consultas.projecao(banco, HOJE, 3)
    assert (r3["primeiro_mes"], r3["ultimo_mes"]) == ("2026-10", "2026-12")
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
    assert r["limite"] == 50 and r["periodo"]["de"] == "2026-09-23"
    assert r["periodo"]["ate"] == "2026-10-23"
    r = consultas.lancamentos(banco, HOJE, texto="padaria", limite=1)
    assert len(r["lancamentos"]) == 1 and r["total"] == 2 and r["truncado"] is True
    assert consultas.lancamentos(banco, HOJE, texto="farmácia")["total"] == 0  # fora dos 30 dias
    assert consultas.lancamentos(banco, HOJE, texto="farmácia", de="2026-01-01")["total"] == 1


def test_lancamentos_limite_atravessa_paginas(banco: Any) -> None:
    for i in range(60):
        criar_lancamento_manual(banco, conta_id=1, data=date(2026, 10, 1), descricao=f"ZZ{i}",
                                valor_centavos=-100)
    assert len(consultas.lancamentos(banco, HOJE, texto="zz", limite=120)["lancamentos"]) == 60
    assert len(consultas.lancamentos(banco, HOJE, texto="zz", limite=55)["lancamentos"]) == 55


def test_contas_e_categorias(banco: Any) -> None:
    assert consultas.contas(banco, HOJE)["contas"][0]["nome"] == "Itaú"
    cats = consultas.categorias(banco, HOJE)["categorias"]
    assert set(cats[0]) == {"id", "nome", "tipo", "subcategorias"}
    por_nome = {c["nome"]: c for c in cats}
    assert por_nome["Moradia"]["tipo"] == "saida"
    assert por_nome["Salário e Trabalho"]["tipo"] == "entrada"
    subs = por_nome["Moradia"]["subcategorias"]
    assert {"Aluguel", "Prestação do imóvel"} <= {s["nome"] for s in subs}
    assert all(set(s) == {"id", "nome"} and isinstance(s["id"], int) for s in subs)
    assert "13º salário" in [s["nome"] for s in por_nome["Salário e Trabalho"]["subcategorias"]]
    assert "Alimentação" not in por_nome or por_nome["Alimentação"]["tipo"] == "saida"


def test_lancamentos_recentes_primeiro_e_janela(banco: Any) -> None:
    for dia, desc in ((date(2026, 8, 20), "Antigo"), (date(2026, 10, 10), "Meio"),
                      (date(2026, 10, 22), "Novo"), (date(2026, 11, 5), "Futuro")):
        criar_lancamento_manual(banco, conta_id=1, data=dia, descricao=desc, valor_centavos=-100)
    r = consultas.lancamentos(banco, HOJE, texto="o", limite=200)
    nomes = [x["descricao"] for x in r["lancamentos"]]
    assert "Antigo" not in nomes  # 64 dias atrás: fora da janela padrão
    assert "Futuro" not in nomes  # depois de hoje: assunto de vencimentos/projecao
    assert nomes.index("Novo") < nomes.index("Meio")
    r = consultas.lancamentos(banco, HOJE, texto="o", limite=200, ate="2026-12-31")
    nomes = [x["descricao"] for x in r["lancamentos"]]
    assert nomes.index("Futuro") < nomes.index("Novo") < nomes.index("Meio")
    datas = [x["data"] for x in r["lancamentos"]]
    assert datas == sorted(datas, reverse=True)
    r = consultas.lancamentos(banco, HOJE, limite=2)
    assert [x["descricao"] for x in r["lancamentos"]][0] == "Novo" and len(r["lancamentos"]) == 2
    assert r["truncado"] is True


def test_recorrente_aparece_e_e_idempotente(banco: Any) -> None:
    criar_recorrente(banco, nome="Aluguel", tipo="saida", dia=25, valor_estimado_centavos=150000,
                     conta_id=1, categoria_id=None)
    v = consultas.vencimentos(banco, HOJE, 7)
    assert [x["descricao"] for x in v["a_vencer"]] == ["Aluguel"]
    assert v["recorrentes_geradas"] == 2 and v["a_vencer"][0]["recorrente"] is True
    assert consultas.vencimentos(banco, HOJE, 7)["recorrentes_geradas"] == 0
    p = consultas.projecao(banco, HOJE, 3)
    assert p["recorrentes_geradas"] == 1  # dezembro
    assert p["pontos"][0]["saida_pendente_centavos"] == 150000
    assert consultas.resumo(banco, HOJE)["a_vencer"] == 1


def test_gastos_com_fatura_separa_caixa_de_consumo(banco: Any) -> None:
    from app.dominio import faturas as dom_fat
    from app.importacao.fatura.modelo import FaturaLida, ItemFatura
    from app.repo import contas as repo_contas

    cartao = repo_contas.criar_conta(banco, "Cartao Teste", "cartao", final_numero="9999")
    item = ItemFatura(data="2026-10-05", descricao="LOJA INVENTADA", valor_centavos=-12300,
                      tipo="compra", parcela_atual=None, parcela_total=None,
                      final_cartao="9999", categoria_banco=None)
    f = FaturaLida(emissor="Banco Teste", bandeira=None, produto=None, final_cartao="9999",
                   data_vencimento="2026-11-10", data_fechamento=None,
                   total_impresso_centavos=12300, ajustes_resumo_centavos=0, itens=[item])
    dom_fat.importar_fatura(banco, f, "h1", "f.pdf", cartao, HOJE, [None])
    r = consultas.gastos(banco, HOJE, "2026-10")
    assert sum(c["total_centavos"] for c in r["categorias"]) == r["consumo_centavos"]
    assert r["consumo_centavos"] == r["saidas_centavos"] + 12300  # fatura ainda não paga
    assert "data da compra" in r["nota_totais"] and "data do pagamento" in r["nota_totais"]
    assert consultas.resumo(banco, HOJE, "2026-10")["nota_totais"] == r["nota_totais"]
