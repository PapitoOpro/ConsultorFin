from app.importacao.fatura.deteccao import detectar
from app.importacao.fatura.itau import ParserItau
from app.importacao.fatura.modelo import FaturaLida
from app.importacao.fatura.porto import ParserPorto
from app.importacao.fatura.texto import PaginaTexto
from tests.faturas_util import paginas_de_fixture


def _ler() -> FaturaLida:
    p = paginas_de_fixture("porto")
    return detectar(p).ler(p)


def test_detectar_porto_e_nao_itau() -> None:
    p = paginas_de_fixture("porto")
    assert isinstance(detectar(p), ParserPorto)
    assert not ParserItau().reconhece(p)
    assert ParserPorto().emissor == "porto"


def test_cabecalho() -> None:
    f = _ler()
    assert (f.emissor, f.bandeira, f.final_cartao) == ("porto", "Mastercard", "000")
    assert f.data_vencimento == "2027-01-20"
    assert f.data_fechamento == "2027-01-05"
    assert f.total_impresso_centavos == 26500
    assert f.ajustes_resumo_centavos == 0


def test_confere_e_pagamento_fora_dos_itens() -> None:
    f = _ler()
    assert f.conferencia().confere
    assert f.calculado_centavos == 26500
    assert not any("PAGAMENTO" in i.descricao.upper() for i in f.itens)
    assert not any(i.valor_centavos > 0 for i in f.itens)


def test_cartao_adicional() -> None:
    f = _ler()
    adic = [i for i in f.itens if i.final_cartao == "111"]
    assert len(adic) == 1 and adic[0].valor_centavos == -4000
    assert (adic[0].parcela_atual, adic[0].parcela_total) == (1, 12)
    assert all(i.final_cartao == "000" for i in f.itens if i not in adic)


def test_parcela_e_datas_de_dezembro() -> None:
    f = _ler()
    parc = next(i for i in f.itens if i.descricao.startswith("LOJA PARCELADA"))
    assert (parc.parcela_atual, parc.parcela_total) == (7, 10)
    assert "07/10" not in parc.descricao
    assert parc.data == "2026-11-10"
    seguro = next(i for i in f.itens if i.descricao.startswith("SEGURO"))
    assert seguro.data == "2026-12-30"
    assert (seguro.parcela_atual, seguro.parcela_total) == (7, None)
    assert any("parcela" in a.lower() for a in f.avisos)


def test_encargos() -> None:
    f = _ler()
    enc = {i.descricao: i for i in f.itens if i.tipo == "encargo"}
    assert enc["MULTA POR ATRASO"].valor_centavos == -1000
    assert enc["JUROS DE MORA"].valor_centavos == -100
    assert enc["IOF ADICIONAL SOBRE SALDO FINANCIA"].valor_centavos == -200
    assert enc["Encargos da fatura"].valor_centavos == -1200
    assert enc["Encargos da fatura"].data == "2027-01-20"


def test_credito_negativo_nao_pagamento() -> None:
    txt = ("Cartão 5000 00** **** *000\nEsta fatura vence em\n20/01/2027\nO valor total é\nR$ 0,00\n"
           "Lançamentos: compras e saques\nT (final *000)\n05/01 ESTORNO LOJA 5,00\n"
           "06/01 LOJA 5,00\n07/01 ESTORNO X -5,00\n")
    f = ParserPorto().ler([PaginaTexto(1, txt, "", "")])
    cred = [i for i in f.itens if i.tipo == "credito"]
    assert len(cred) == 1 and cred[0].valor_centavos == 500


def test_porto_nao_reconhece_outros_emissores() -> None:
    for nome in ("itau_visa", "itau_mastercard", "itau_passai", "mercadopago"):
        assert not ParserPorto().reconhece(paginas_de_fixture(nome))
