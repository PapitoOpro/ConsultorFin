from app.importacao.bradesco_csv import ParserBradescoCsv
from app.importacao.deteccao import detectar

CAB = "Data;Histórico;Docto.;Crédito (R$);Débito (R$);Saldo (R$)"


def _csv(*linhas: str) -> bytes:
    corpo = ["Extrato de: Ag: 1234 | Conta: 98765-4;;;;;", CAB, *linhas,
             ";;;;;", "Lançamentos futuros;;;;;", CAB, ";;;;Não há lançamentos futuros;;;;"]
    return ("﻿" + "\r\n".join(corpo) + "\r\n").encode("utf-8")


DADOS = _csv(
    "16/10/2026;TRANSF SALDO C/SAL P/CC;1111111;100,00; ;150,00",
    "19/10/2026;PIX QR CODE DINAMICO;2222222; ;30,50;119,50",
    "19/10/2026;PIX ENVIADO;3333333; ;1.019,50;-900,00",
)


def test_detecta_e_le():
    p = detectar(DADOS, "x.csv")
    assert isinstance(p, ParserBradescoCsv)
    r = p.ler(DADOS)
    assert r.erros == []
    assert [l.valor_centavos for l in r.linhas] == [10000, -3050, -101950]
    assert r.conta_numero == "987654"
    assert r.saldos_dia == {"2026-10-16": 15000, "2026-10-19": -90000}
    assert r.saldo_anterior_estimado == ("2026-10-15", 5000)
    assert ParserBradescoCsv.final_numero(DADOS) == "98765-4"


def test_ordem_decrescente_e_ignora_tabela_futura():
    linhas = DADOS.decode("utf-8").splitlines()
    inv = _csv(*reversed(linhas[2:5]))
    r = ParserBradescoCsv().ler(inv)
    assert r.erros == []
    assert [l.valor_centavos for l in r.linhas] == [10000, -3050, -101950]
    assert r.saldo_anterior_estimado == ("2026-10-15", 5000)
