from datetime import date

from app.dominio.categorias import (
    aplicar_regras, categorizar, classificar_tipo, criar_categoria, criar_regra,
    criar_subcategoria, despesas_do_mes, entradas_do_mes, listar_categorias,
    listar_subcategorias, remover_categoria, renomear_categoria,
)
from app.importacao.itau_csv import ParserItauCsv
from app.importacao.servico import importar
from app.repo import configuracoes
from app.repo.contas import criar_conta

PADROES = ["PAG FATURA", "FATURA CARTAO", "PAGAMENTO CARTAO"]


def _lanc(con, conta, data, desc, valor, tipo, compensado=1, chave=None):
    con.execute(
        "INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
        " chave_deduplicacao, origem, data_compensacao, compensado)"
        " VALUES (?,?,?,?,?,?,?,'manual',?,?)",
        (chave or desc + data, conta, data, desc, valor, tipo, chave or desc + data,
         data, compensado))


def test_classificar_tipo() -> None:
    assert classificar_tipo("PIX TRANSF RAFAEL S", -1000, "Rafael S", PADROES) == "transferencia"
    assert classificar_tipo("PIX TRANSF MARIA", -1000, "Rafael S", PADROES) == "saida"
    assert classificar_tipo("PIX TRANSF RAFAEL S", -1000, None, PADROES) == "saida"
    assert classificar_tipo("PAG FATURA ITAU", -5000, None, PADROES) == "pagamento_fatura"
    assert classificar_tipo("SALARIO", 5000, "X", PADROES) == "entrada"


def test_transferencia_fora_e_fatura_dentro_das_despesas(con) -> None:
    c = criar_conta(con, "Itaú", "corrente")
    _lanc(con, c, "2026-10-05", "PIX TRANSF RAFAEL", -10000,
          classificar_tipo("PIX TRANSF RAFAEL", -10000, "Rafael", PADROES))
    _lanc(con, c, "2026-10-06", "PAG FATURA", -30000,
          classificar_tipo("PAG FATURA", -30000, "Rafael", PADROES))
    _lanc(con, c, "2026-10-07", "MERCADO", -2000, "saida")
    _lanc(con, c, "2026-10-08", "SALARIO", 90000, "entrada")
    _lanc(con, c, "2026-10-09", "PENDENTE", -999, "saida", compensado=0)
    _lanc(con, c, "2026-11-01", "OUTRO MES", -111, "saida")
    assert despesas_do_mes(con, 2026, 10) == 32000
    assert entradas_do_mes(con, 2026, 10) == 90000


def test_conta_cartao_fora_das_despesas(con) -> None:
    cc = criar_conta(con, "Cartão", "cartao")
    _lanc(con, cc, "2026-10-05", "LOJA", -7000, "saida")
    assert despesas_do_mes(con, 2026, 10) == 0


def test_regra_de_maior_prioridade_vence(con) -> None:
    mercado = criar_categoria(con, "Mercado X")
    lazer = criar_categoria(con, "Lazer X")
    criar_regra(con, "uber", mercado, 1)
    criar_regra(con, "UBER EATS", lazer, 5)
    assert categorizar(con, "Compra Uber Eats SP") == lazer
    assert categorizar(con, "nada") is None


def test_empate_menor_id(con) -> None:
    a = criar_categoria(con, "A")
    b = criar_categoria(con, "B")
    criar_regra(con, "loja", a)
    criar_regra(con, "loja", b)
    assert categorizar(con, "LOJA") == a


def test_aplicar_regras_so_sem_categoria(con) -> None:
    c = criar_conta(con, "Itaú", "corrente")
    cat = criar_categoria(con, "Cat")
    outra = criar_categoria(con, "Outra")
    criar_regra(con, "mercado", cat)
    _lanc(con, c, "2026-10-05", "MERCADO A", -100, "saida")
    _lanc(con, c, "2026-10-06", "MERCADO B", -100, "saida")
    _lanc(con, c, "2026-10-07", "PADARIA", -100, "saida")
    con.execute("UPDATE lancamentos SET categoria_id = ? WHERE descricao = 'MERCADO B'", (outra,))
    assert aplicar_regras(con) == 1
    assert aplicar_regras(con) == 0
    cats = {r["descricao"]: r["categoria_id"]
            for r in con.execute("SELECT descricao, categoria_id FROM lancamentos")}
    assert cats == {"MERCADO A": cat, "MERCADO B": outra, "PADARIA": None}


def test_crud_categorias_e_subcategorias(con) -> None:
    pai = criar_categoria(con, "Pai")
    sub = criar_subcategoria(con, pai, "Filha")
    assert [s["id"] for s in listar_subcategorias(con, pai)] == [sub]
    assert "Pai" in [c["nome"] for c in listar_categorias(con)]
    assert "Filha" not in [c["nome"] for c in listar_categorias(con)]
    renomear_categoria(con, sub, "Filha2")
    assert listar_subcategorias(con, pai)[0]["nome"] == "Filha2"
    remover_categoria(con, sub)
    assert listar_subcategorias(con, pai) == []


def test_importar_classifica_e_categoriza(con) -> None:
    c = criar_conta(con, "Itaú", "corrente")
    cat = criar_categoria(con, "Cartão X")
    criar_regra(con, "fatura", cat)
    configuracoes.definir(con, "nome_titular", "Rafael")
    csv = ("data;lançamento;ag./origem;valor (R$);saldos (R$)\n"
           "05/10/2026;PIX TRANSF RAFAEL;;-100,00\n"
           "06/10/2026;PAG FATURA ITAU;;-300,00\n"
           "07/10/2026;SALARIO;;900,00\n").encode("latin-1")
    importar(con, c, "x.csv", csv, ParserItauCsv(), date(2026, 10, 23))
    rows = {r["descricao"]: (r["tipo"], r["categoria_id"])
            for r in con.execute("SELECT descricao, tipo, categoria_id FROM lancamentos")}
    assert rows["PIX TRANSF RAFAEL"] == ("transferencia", None)
    assert rows["PAG FATURA ITAU"] == ("pagamento_fatura", cat)
    assert rows["SALARIO"] == ("entrada", None)
