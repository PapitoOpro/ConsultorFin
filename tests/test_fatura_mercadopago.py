import pytest

from app.importacao.fatura.deteccao import detectar
from app.importacao.fatura.itau import ParserItau
from app.importacao.fatura.mercadopago import ParserMercadoPago
from app.importacao.fatura.modelo import FaturaIlegivel, FaturaLida
from app.importacao.fatura.porto import ParserPorto
from app.importacao.fatura.texto import PaginaTexto
from tests.faturas_util import paginas_de_fixture

_FIXTURES = {
    "itau": ("itau_visa", "itau_mastercard", "itau_passai"),
    "porto": ("porto",),
    "mercadopago": ("mercadopago",),
}
_PARSERS = {"itau": ParserItau(), "porto": ParserPorto(), "mercadopago": ParserMercadoPago()}


def _ler() -> FaturaLida:
    p = paginas_de_fixture("mercadopago")
    return detectar(p).ler(p)


def test_detectar_mercadopago() -> None:
    p = paginas_de_fixture("mercadopago")
    assert isinstance(detectar(p), ParserMercadoPago)
    assert ParserMercadoPago().emissor == "mercadopago"


@pytest.mark.parametrize("parser", list(_PARSERS))
def test_reconhece_somente_o_proprio_emissor(parser: str) -> None:
    for emissor, nomes in _FIXTURES.items():
        for nome in nomes:
            assert _PARSERS[parser].reconhece(paginas_de_fixture(nome)) == (parser == emissor)


def test_cabecalho() -> None:
    f = _ler()
    assert (f.emissor, f.bandeira, f.final_cartao) == ("mercadopago", "Visa", "0000")
    assert f.data_vencimento == "2027-02-15"
    assert f.data_fechamento == "2027-02-08"
    assert f.total_impresso_centavos == 142696
    assert f.ajustes_resumo_centavos == 0


def test_confere_e_pagamento_fora_dos_itens() -> None:
    f = _ler()
    assert f.conferencia().confere
    assert f.calculado_centavos == 142696
    assert not any("PAGAMENTO" in i.descricao.upper() for i in f.itens)


def test_credito_positivo_e_encargos_negativos() -> None:
    f = _ler()
    cred = [i for i in f.itens if i.tipo == "credito"]
    assert [i.valor_centavos for i in cred] == [3000]
    enc = {i.descricao: i.valor_centavos for i in f.itens if i.tipo == "encargo"}
    assert enc == {"Juros do rotativo": -1200, "Juros de mora": -300,
                   "Multa por atraso": -1000, "IOF do rotativo": -200}
    assert all(i.valor_centavos < 0 for i in f.itens if i.tipo in ("compra", "encargo"))


def test_parcela_e_ano_inferido() -> None:
    f = _ler()
    p = next(i for i in f.itens if i.descricao == "LOJA ALFA")
    assert (p.parcela_atual, p.parcela_total) == (3, 10)
    assert p.data == "2026-12-10" and p.valor_centavos == -8550
    assert p.final_cartao == "0000"


def test_compras_identicas_em_datas_distintas_mantidas() -> None:
    betas = [i for i in _ler().itens if i.descricao == "LOJA BETA"]
    assert sorted(i.data for i in betas) == ["2027-01-12", "2027-01-20"]


def test_valor_com_milhar() -> None:
    f = _ler()
    assert any(i.valor_centavos == -123456 for i in f.itens)


def test_pagamento_parcial_vai_para_ajustes() -> None:
    txt = ("Mercado Pago\nTotal a pagar Vence em Limite total\nR$ 200,00 15/02/2027 R$ 1,00\n"
           "Total da fatura de dezembro R$ 300,00\nMovimentações na fatura\n"
           "20/01 Pagamento da fatura de dezembro/2026 R$ 200,00\n"
           "Cartão Visa [************0000]\n10/02 LOJA R$ 100,00\nParcele a fatura\n")
    f = ParserMercadoPago().ler([PaginaTexto(1, txt, "", "")])
    assert f.ajustes_resumo_centavos == 10000
    assert f.calculado_centavos == 10000
    assert f.conferencia().confere


def test_sem_total_e_ilegivel() -> None:
    with pytest.raises(FaturaIlegivel):
        ParserMercadoPago().ler([PaginaTexto(1, "Mercado Pago\nMovimentações na fatura", "", "")])


def _mp(movs: str, total: str = "10,00", extra: str = "Total da fatura de dezembro R$ 0,00\n") -> FaturaLida:
    txt = (f"Mercado Pago\nTotal a pagar Vence em Limite total\nR$ {total} 15/02/2027 R$ 1,00\n"
           f"{extra}Movimentações na fatura\n{movs}Parcele a fatura\n")
    return ParserMercadoPago().ler([PaginaTexto(1, txt, "", "")])


def test_descricao_desconhecida_vira_compra_com_um_aviso() -> None:
    f = _mp("10/01 COBRANCA X R$ 4,00\n11/01 COBRANCA Y R$ 6,00\n",
            extra="Cartão Visa [************0000]\nTotal da fatura de dezembro R$ 0,00\n")
    assert [i.tipo for i in f.itens] == ["compra", "compra"]
    assert f.avisos == ["Movimentação não reconhecida tratada como compra."]
    assert f.conferencia().confere


def test_variantes_de_pagamento() -> None:
    f = _mp("10/01 PGTO FATURA R$ 5,00\n11/01 Pagto da fatura R$ 5,00\n"
            "Cartão Visa [************0000]\n10/02 LOJA R$ 10,00\n",
            extra="Total da fatura de dezembro R$ 10,00\n")
    assert [i.descricao for i in f.itens] == ["LOJA"]
    assert f.ajustes_resumo_centavos == 0
    assert f.conferencia().confere


def test_palavras_coladas_e_rs_colado() -> None:
    txt = ("Mercado Pago\nTotalapagar Vence em Limite total\nR$10,00 15/02/2027 R$1,00\n"
           "Fechamentodafatura 08/02/2027\nTotal da fatura de dezembro R$0,00\n"
           "Movimentações na fatura\nCartão Visa [************0000]\n10/02 LOJA R$10,00\n")
    f = ParserMercadoPago().ler([PaginaTexto(1, txt, "", "")])
    assert f.total_impresso_centavos == 1000
    assert f.data_fechamento == "2027-02-08"
    assert f.conferencia().confere


def test_sem_fechamento_e_sem_total_anterior_avisa() -> None:
    f = _mp("Cartão Visa [************0000]\n10/02 LOJA R$ 10,00\n", extra="")
    assert f.data_fechamento is None
    assert f.ajustes_resumo_centavos == 0
    assert f.avisos == ["Total da fatura anterior não encontrado: conferência sem ajustes."]


def test_duplicata_mesmo_dia_e_valor_mantida() -> None:
    f = _mp("Cartão Visa [************0000]\n10/02 LOJA R$ 5,00\n10/02 LOJA R$ 5,00\n")
    assert len(f.itens) == 2 and f.itens[0] == f.itens[1]
    assert f.conferencia().confere
