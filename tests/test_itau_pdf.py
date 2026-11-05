from app.importacao.itau_pdf import ParserItauPdf

TEXTO = """RAFAEL agência: 0865 conta: 011126-4
extrato conta / lançamentos
período de visualização: 06/10/2026 até 05/11/2026 emitido em: 05/11/2026 10:46:32
data lançamentos valor (R$) saldo (R$)
05/11/2026 SALDO DO DIA -26,90
05/11/2026 PAG BOLETO PORTOSEG SA -580,28
04/11/2026 PIX TRANSF RR Supo04/11 1.500,00
04/11/2026 SALDO DO DIA 595,88
05/10/2026 SALDO DO DIA 913,90
Aviso! texto qualquer
"""


def test_le_lancamentos_saldos_e_periodo() -> None:
    r = ParserItauPdf.ler_texto(TEXTO)
    assert [(l.data, l.descricao, l.valor_centavos) for l in r.linhas] == [
        ("2026-11-05", "PAG BOLETO PORTOSEG SA", -58028),
        ("2026-11-04", "PIX TRANSF RR Supo04/11", 150000)]
    assert r.saldos_dia == {"2026-11-05": -2690, "2026-11-04": 59588, "2026-10-05": 91390}
    assert r.periodo == ("2026-10-06", "2026-11-05")
    assert r.conta_numero == "0111264" and not r.erros


def test_texto_desconhecido_gera_erro() -> None:
    r = ParserItauPdf.ler_texto("qualquer coisa")
    assert not r.linhas and r.erros
