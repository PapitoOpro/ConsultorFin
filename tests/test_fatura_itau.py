import pytest

from app.importacao.fatura import ler_fatura
from app.importacao.fatura.deteccao import ParserFatura, detectar
from app.importacao.fatura.itau import ParserItau
from app.importacao.fatura.modelo import EmissorNaoSuportado, FaturaLida
from app.importacao.fatura.texto import PaginaTexto
from tests.faturas_util import paginas_de_fixture
from tests.test_fatura_texto import _pdf_minimo


def _ler(nome: str) -> FaturaLida:
    return detectar(paginas_de_fixture(nome)).ler(paginas_de_fixture(nome))


def _tipos(f: FaturaLida) -> list[str]:
    return [i.tipo for i in f.itens]


def test_detectar_devolve_parser_itau() -> None:
    for nome in ("itau_visa", "itau_mastercard", "itau_passai"):
        parser = detectar(paginas_de_fixture(nome))
        assert isinstance(parser, ParserItau)
        assert parser.emissor == "itau"
        assert parser.reconhece(paginas_de_fixture(nome))


def test_detectar_nao_confunde_porto_com_itau() -> None:
    texto = ("PORTOSEG cartaoportoseguro.com.br\nFatura do cartão\n"
             "Pague no Banco Itaú 341-7 ou em qualquer banco\n")
    paginas = [PaginaTexto(1, texto, "", "")]
    assert not ParserItau().reconhece(paginas)
    assert detectar(paginas).emissor == "porto"


def test_detectar_emissor_desconhecido() -> None:
    with pytest.raises(EmissorNaoSuportado):
        detectar([PaginaTexto(1, "texto qualquer", "", "")])


def test_ler_fatura_pdf_de_outro_emissor() -> None:
    with pytest.raises(EmissorNaoSuportado):
        ler_fatura(_pdf_minimo("Fatura qualquer 1,00"))


def test_parser_atende_protocolo() -> None:
    p: ParserFatura = ParserItau()
    assert p.emissor == "itau"


# ---------- Visa Platinum ----------

def test_visa_cabecalho() -> None:
    f = _ler("itau_visa")
    assert (f.emissor, f.bandeira, f.produto, f.final_cartao) == ("itau", "Visa", "Platinum", "0001")
    assert f.data_vencimento == "2027-02-10"
    assert f.data_fechamento is None
    assert f.total_impresso_centavos == 13958
    assert f.ajustes_resumo_centavos == 0


def test_visa_itens_e_conferencia() -> None:
    f = _ler("itau_visa")
    assert len(f.itens) == 7
    assert _tipos(f).count("compra") == 5
    assert _tipos(f).count("credito") == 1
    assert _tipos(f).count("servico") == 1
    assert f.conferencia().confere
    assert f.calculado_centavos == 13958


def test_visa_parcela_e_ano_inferido() -> None:
    f = _ler("itau_visa")
    parc = next(i for i in f.itens if i.descricao.startswith("LOJA PARCELADA"))
    assert (parc.parcela_atual, parc.parcela_total) == (3, 10)
    assert parc.descricao == "LOJA PARCELADA"
    assert parc.data == "2026-12-20"
    assert parc.valor_centavos == -3000
    assert parc.final_cartao == "0001"


def test_visa_categoria_banco() -> None:
    f = _ler("itau_visa")
    por_desc = {i.descricao: i for i in f.itens}
    assert por_desc["LOJA TESTE UM"].categoria_banco == "outros"
    assert por_desc["POSTO TESTE"].categoria_banco == "transporte"
    assert por_desc["LOJA PARCELADA"].categoria_banco == "vestuário"
    assert por_desc["LOJA ESTORNO"].categoria_banco == "acessorios"  # colada à cidade
    assert all(i.categoria_banco for i in f.itens if i.tipo == "compra")


def test_visa_credito_e_servico() -> None:
    f = _ler("itau_visa")
    est = next(i for i in f.itens if i.descricao == "LOJA ESTORNO")
    assert est.tipo == "credito" and est.valor_centavos == 500
    srv = next(i for i in f.itens if i.tipo == "servico")
    assert srv.descricao == "PgConta TESTE"
    assert (srv.parcela_atual, srv.parcela_total) == (7, 12)
    assert srv.valor_centavos == -2000
    assert "Principal" not in srv.descricao


def test_visa_ignora_futuras_e_pagamento() -> None:
    f = _ler("itau_visa")
    assert not any(i.parcela_atual in (4, 8) for i in f.itens)
    assert not any("PAGAMENTO" in i.descricao.upper() for i in f.itens)
    assert not any(i.valor_centavos == -10000 for i in f.itens)


def test_compras_identicas_mantidas() -> None:
    f = _ler("itau_visa")
    iguais = [i for i in f.itens if i.descricao == "PADARIA TESTE"]
    assert len(iguais) == 2
    assert iguais[0] == iguais[1]


# ---------- Mastercard Gold ----------

def test_mastercard_cabecalho_e_conferencia() -> None:
    f = _ler("itau_mastercard")
    assert (f.bandeira, f.produto, f.final_cartao) == ("Mastercard", "Gold", "0002")
    assert f.data_vencimento == "2027-04-20"
    assert f.total_impresso_centavos == 22823
    assert len(f.itens) == 7
    assert f.conferencia().confere


def test_mastercard_iof_e_internacional() -> None:
    f = _ler("itau_mastercard")
    intl = [i for i in f.itens if i.tipo == "internacional"]
    assert len(intl) == 1
    assert intl[0].valor_centavos == -10000
    assert intl[0].descricao.startswith("LOJA INTL TESTE")
    assert "EUR" in intl[0].descricao
    assert intl[0].data == "2027-03-30"
    iof = [i for i in f.itens if i.tipo == "encargo"]
    assert len(iof) == 1
    assert iof[0].descricao == "Repasse de IOF"
    assert iof[0].valor_centavos == -350


def test_mastercard_servico_sem_futuras() -> None:
    f = _ler("itau_mastercard")
    srv = next(i for i in f.itens if i.tipo == "servico")
    assert srv.valor_centavos == -1700
    assert srv.descricao.startswith("Mensalidade - Plano do")
    parcelas = [i for i in f.itens if i.parcela_atual]
    assert [(p.parcela_atual, p.parcela_total) for p in parcelas] == [(2, 4)]


# ---------- Visa Passaí ----------

def test_passai_cabecalho() -> None:
    f = _ler("itau_passai")
    assert (f.bandeira, f.produto, f.final_cartao) == ("Visa", "Passaí", "0003")
    assert f.data_vencimento == "2027-03-01"
    assert f.total_impresso_centavos == 25140


def test_passai_encargo_sintetico_e_confere() -> None:
    f = _ler("itau_passai")
    enc = [i for i in f.itens if i.tipo == "encargo"]
    assert len(enc) == 1
    assert enc[0].descricao == "Encargos da fatura"
    assert enc[0].valor_centavos == -850
    assert enc[0].data == f.data_vencimento
    assert f.conferencia().confere


def test_passai_pagamentos_so_em_ajustes() -> None:
    f = _ler("itau_passai")
    assert f.ajustes_resumo_centavos == 2000  # saldo financiado (já inclui o que sobrou do anterior)
    assert not any(i.valor_centavos > 0 for i in f.itens)
    assert len(f.itens) == 7  # 5 compras + 1 serviço + 1 encargo
    assert _tipos(f).count("compra") == 5


def test_passai_duas_colunas_e_futuras() -> None:
    f = _ler("itau_passai")
    descs = {i.descricao for i in f.itens}
    assert {"ATACADO TESTE", "PADARIA TESTE", "POSTO TESTE"} <= descs
    atacado = [i for i in f.itens if i.descricao == "ATACADO TESTE"]
    assert len(atacado) == 1 and (atacado[0].parcela_atual, atacado[0].parcela_total) == (2, 2)
    assert next(i for i in f.itens if i.descricao == "ASSAI TESTE").categoria_banco == "outros"


def test_sinal_final_nao_usado_em_valor_positivo() -> None:
    # valor com sinal depois ("9,99-") também é tratado como negativo (crédito)
    texto = "\n".join([
        "Banco Itaú S.A. Itaú Cartões Vencimento: 10/02/2027",
        "Cartão 4000.XXXX.XXXX.0001",
        "Total desta fatura -4,00",
    ])
    esq = "Lançamentos: compras e saques\n05/01 LOJA X 4,00-\noutros CIDADE\nTotal dos lançamentos atuais -4,00"
    f = ParserItau().ler([PaginaTexto(1, texto, esq, "")])
    assert f.itens[0].tipo == "credito" and f.itens[0].valor_centavos == 400
    assert f.conferencia().confere


def test_itau_nao_reconhece_porto_nem_mercadopago() -> None:
    for nome in ("porto", "mercadopago"):
        assert not ParserItau().reconhece(paginas_de_fixture(nome))


def _sintetica(esq: str, total: str = "10,00", direita: str = "") -> FaturaLida:
    texto = "\n".join([
        "Banco Itaú S.A. Itaú Cartões Vencimento: 10/02/2027",
        "Cartão 4000.XXXX.XXXX.0001",
        f"Total desta fatura {total}",
    ])
    return ParserItau().ler([PaginaTexto(1, texto, esq, direita)])


def test_fim_so_no_inicio_da_linha() -> None:
    esq = "\n".join([
        "Lançamentos: compras e saques",
        "05/01 LOJA COMPRAS PARCELADAS 6,00",
        "outros CIDADE",
        "06/01 LOJA DOIS 4,00",
        "outros CIDADE",
        "L Total dos lançamentos atuais 10,00",
        "Compras parceladas - próximas faturas",
        "07/01 LOJA FUTURA 02/03 9,00",
    ])
    f = _sintetica(esq)
    assert [i.descricao for i in f.itens] == ["LOJA COMPRAS PARCELADAS", "LOJA DOIS"]
    assert f.conferencia().confere


def test_sem_lancamentos_gera_aviso() -> None:
    f = _sintetica("Texto sem seção de lançamentos")
    assert f.itens == []
    assert f.avisos == ["Nenhum lançamento lido na fatura: confira o arquivo."]


def test_iof_zerado_nao_vira_item() -> None:
    esq = "\n".join([
        "Lançamentos internacionais",
        "05/01 LOJA INTL 10,00",
        "Repasse de IOF em R$ 0,00",
        "Total lançamentos inter. em R$ 10,00",
        "L Total dos lançamentos atuais 10,00",
    ])
    f = _sintetica(esq)
    assert _tipos(f) == ["internacional"]
    assert f.conferencia().confere


def test_cartao_adicional_tem_final_proprio() -> None:
    esq = "\n".join([
        "Lançamentos: compras e saques",
        "TITULAR TESTE",
        "05/01 LOJA TITULAR 6,00",
        "outros CIDADE",
        "Lançamentos no cartão (final 0001) 6,00",
        "ADICIONAL TESTE (final 0009)",
        "06/01 LOJA ADICIONAL 4,00",
        "outros CIDADE",
        "Lançamentos no cartão (final 0009) 4,00",
        "L Total dos lançamentos atuais 10,00",
    ])
    f = _sintetica(esq)
    assert [(i.descricao, i.final_cartao) for i in f.itens] == [
        ("LOJA TITULAR", "0001"), ("LOJA ADICIONAL", "0009")]
    assert f.final_cartao == "0001"
    assert f.conferencia().confere


def test_categoria_colada_no_inicio_da_descricao() -> None:
    esq = "\n".join([
        "Lançamentos: compras e saques",
        "05/01 bebidasLOJA FICTICIA 6,00",
        "06/01 petshopMERCADO TESTE 4,00",
        "lazer CIDADE TESTE",
        "L Total dos lançamentos atuais 10,00",
    ])
    f = _sintetica(esq)
    assert [(i.descricao, i.categoria_banco) for i in f.itens] == [
        ("LOJA FICTICIA", "bebidas"), ("MERCADO TESTE", "lazer")]
    assert f.conferencia().confere


def test_descricao_com_minuscula_legitima_nao_e_cortada() -> None:
    esq = "Lançamentos: compras e saques\n05/01 iFood TESTE 10,00\noutros CIDADE\n"
    assert [i.descricao for i in _sintetica(esq).itens] == ["iFood TESTE"]


def test_categoria_com_varias_palavras() -> None:
    esq = "\n".join([
        "Lançamentos: compras e saques",
        "05/01 LOJA FICTICIA 6,00",
        "casa e construção CIDADE TESTE",
        "06/01 LOJA DOIS 4,00",
        "outros Cidade Teste",
        "L Total dos lançamentos atuais 10,00",
    ])
    f = _sintetica(esq)
    assert [i.categoria_banco for i in f.itens] == ["casa e construção", "outros"]
