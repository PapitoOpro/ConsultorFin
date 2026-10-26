import random
import re
import time
from pathlib import Path

import pytest

from app.importacao.base import FormatoNaoSuportado, ResultadoLeitura
from app.importacao.deteccao import detectar
from app.importacao.ofx import ParserOfx, tem_cabecalho_ofx

FIXTURE = Path(__file__).parent / "fixtures" / "extrato_itau_exemplo.ofx"
VALORES = [-51230, 3, -11875, 31000, 9550, -6420, -25000, 240000, -43010, -1290, -840, 12000]
DATAS = ["2026-10-13"] * 3 + ["2026-10-15"] * 2 + ["2026-10-16"] + ["2026-10-20"] * 3 + ["2026-10-21", "2026-10-22", "2026-10-22"]


def _bytes() -> bytes:
    """Fixture normalizada para LF (independe do autocrlf do git)."""
    return FIXTURE.read_bytes().replace(b"\r\n", b"\n")


def _texto() -> str:
    return _bytes().decode("latin-1")


def _ler(conteudo: bytes | None = None) -> ResultadoLeitura:
    return ParserOfx().ler(_bytes() if conteudo is None else conteudo)


def _trn(memo: str = "PIX TESTE", valor: str = "-1.00", data: str = "20261013100000[-03:EST]", n: int = 1) -> str:
    return (f"<STMTTRN>\n<TRNTYPE>DEBIT\n<DTPOSTED>{data}\n<TRNAMT>{valor}\n"
            f"<FITID>2026101300{n}\n<MEMO>{memo}\n</STMTTRN>\n")


def _ofx(trns: str, ledger: str = "<LEDGERBAL>\n<BALAMT>10.00\n<DTASOF>20261026100000[-03:EST]\n</LEDGERBAL>\n") -> bytes:
    return ("OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nENCODING:USASCII\nCHARSET:1252\n\n<OFX>\n<BANKMSGSRSV1>\n<STMTRS>\n"
            "<BANKACCTFROM>\n<BANKID>0341\n<ACCTID>0001000000000\n</BANKACCTFROM>\n<BANKTRANLIST>\n"
            "<DTSTART>20261013100000[-03:EST]\n<DTEND>20261022100000[-03:EST]\n"
            f"{trns}</BANKTRANLIST>\n{ledger}</STMTRS>\n</BANKMSGSRSV1>\n</OFX>\n").encode("cp1252")


def test_fixture_linhas_datas_valores() -> None:
    r = _ler()
    assert r.erros == []
    assert len(r.linhas) == 12
    assert [l.data for l in r.linhas] == DATAS
    assert [l.valor_centavos for l in r.linhas] == VALORES
    assert r.linhas[0].descricao == "PIX QRS MERCADO PAG13 10"
    assert r.formato == "ofx"
    assert sum(l.valor_centavos for l in r.linhas) == 152888
    assert r.saldo_anterior_estimado[1] == 47112
    assert b"CHECKNUM>9000" in _bytes() and b"DA OPERADORA SP 00000001" in _bytes()


def test_periodo_conta_e_saldo_informado() -> None:
    r = _ler()
    assert r.periodo == ("2026-10-13", "2026-10-22")
    assert r.conta_numero == "0001000000000"
    assert r.saldo_informado == ("2026-10-26", 200000)
    assert r.saldos_dia == {}
    assert r.saldo_anterior is None


def test_saldo_anterior_estimado() -> None:
    r = _ler()
    assert r.saldo_anterior_estimado == ("2026-10-12", 200000 - sum(VALORES))


def test_final_numero_so_digitos() -> None:
    assert ParserOfx().final_numero(_bytes()) == "0001000000000"
    assert ParserOfx().final_numero(_ofx("").replace(b"0001000000000", b"00-01 5")) == "00015"
    assert ParserOfx().final_numero(b"lixo") is None


def test_crlf() -> None:
    r = _ler(_texto().replace("\n", "\r\n").encode("latin-1"))
    assert r.erros == [] and [l.valor_centavos for l in r.linhas] == VALORES


def test_tags_fechadas() -> None:
    t = re.sub(r"<([A-Z]+)>([^<\n]+)\n", r"<\1>\2</\1>\n", _texto())
    assert "</CODE>" in t
    r = _ler(t.encode("latin-1"))
    assert r.erros == [] and [l.valor_centavos for l in r.linhas] == VALORES
    assert r.linhas[0].descricao == "PIX QRS MERCADO PAG13 10"


def test_sem_quebras_entre_tags() -> None:
    cab, corpo = _texto().split("<OFX>", 1)
    r = _ler((cab + "<OFX>" + corpo.replace("\n", "")).encode("latin-1"))
    assert r.erros == [] and [l.valor_centavos for l in r.linhas] == VALORES
    assert r.periodo == ("2026-10-13", "2026-10-22")


def test_dtasof_igual_dtend_cai_em_saldos_dia() -> None:
    r = _ler(_texto().replace("<DTASOF>20261026", "<DTASOF>20261022").encode("latin-1"))
    assert r.saldos_dia == {"2026-10-22": 200000}
    assert r.saldo_informado is None
    assert r.saldo_anterior_estimado == ("2026-10-12", 200000 - sum(VALORES))


def test_charset_1252_acentos_e_entidade() -> None:
    r = _ler(_ofx(_trn(memo="PIX TRANSF AÇÃO &amp; CIA")))
    assert r.erros == []
    assert r.linhas[0].descricao == "PIX TRANSF AÇÃO & CIA"


def test_utf8_declarado() -> None:
    b = _ofx(_trn(memo="AÇÃO")).replace(b"CHARSET:1252", b"CHARSET:UTF-8")
    b = b.decode("cp1252").encode("utf-8")
    assert _ler(b).linhas[0].descricao == "AÇÃO"


def test_bytes_invalidos_nao_levantam() -> None:
    b = _ofx(_trn(memo="QQ")).replace(b"QQ", b"\x81\x8d\xff")
    r = _ler(b)
    assert r.erros == []
    assert r.linhas[0].descricao == "ÿ" or r.linhas[0].descricao  # cp1252 com substituição
    assert "�" in r.linhas[0].descricao


def test_valor_com_virgula() -> None:
    r = _ler(_ofx(_trn(valor="-1234,56") + _trn(valor="0,02", n=2)))
    assert [l.valor_centavos for l in r.linhas] == [-123456, 2]


def test_sem_ledgerbal() -> None:
    r = _ler(_ofx(_trn(), ledger=""))
    assert r.erros == []
    assert r.saldo_informado is None and r.saldos_dia == {} and r.saldo_anterior_estimado is None
    assert len(r.linhas) == 1


def test_trnamt_ausente() -> None:
    t = _trn(memo="A").replace("<TRNAMT>-1.00\n", "") + _trn(memo="B", n=2)
    r = _ler(_ofx(t))
    assert [l.descricao for l in r.linhas] == ["B"]
    assert len(r.erros) == 1
    assert "Item 1" in r.erros[0].mensagem and r.erros[0].linha == 1


def test_trnamt_invalido_com_eco() -> None:
    longo = "ABC" * 20
    r = _ler(_ofx(_trn(memo="A") + _trn(memo="B", valor=longo, n=2)))
    assert len(r.linhas) == 1
    assert "Item 2" in r.erros[0].mensagem
    assert "ABCABCABCABC" in r.erros[0].mensagem and longo not in r.erros[0].mensagem


def test_trnamt_gigante_vira_erro() -> None:
    r = _ler(_ofx(_trn(valor="99999999999999.00") + _trn(n=2)))
    assert len(r.linhas) == 1
    assert "Item 1" in r.erros[0].mensagem and "muito alto" in r.erros[0].mensagem


def test_trnamt_exponencial_ou_enorme_nao_trava() -> None:
    r = _ler(_ofx(_trn(valor="1E999999999") + _trn(valor="9" * 5000, n=2)))
    assert r.linhas == [] and len(r.erros) == 2


def test_dtposted_invalido() -> None:
    r = _ler(_ofx(_trn(data="2026XX13") + _trn(data="20261345", n=2) + _trn(n=3)))
    assert len(r.linhas) == 1
    assert [e.linha for e in r.erros] == [1, 2]
    assert "2026XX13" in r.erros[0].mensagem


def test_memo_vazio_usa_name_ou_tipo() -> None:
    a = _trn(memo="").replace("<MEMO>\n", "<NAME>NOME FIC\n")
    b = _trn(memo="", n=2)
    r = _ler(_ofx(a + b))
    assert [l.descricao for l in r.linhas] == ["NOME FIC", "DEBIT"]


def test_tags_repetidas_nao_quebram() -> None:
    r = _ler(_ofx(_trn().replace("<TRNAMT>-1.00\n", "<TRNAMT>-1.00\n<TRNAMT>-9.00\n")))
    assert len(r.linhas) == 1 and r.erros == []
    assert r.linhas[0].valor_centavos == -100  # a primeira ocorrência vence


def test_arquivo_vazio() -> None:
    r = _ler(b"")
    assert r.linhas == [] and r.erros and "vazio" in r.erros[0].mensagem.lower()


def test_so_cabecalho() -> None:
    r = _ler(b"OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nCHARSET:1252\n\n")
    assert r.linhas == [] and r.erros


def test_truncado_no_meio_de_stmttrn() -> None:
    b = _bytes()
    segundo = b.index(b"<STMTTRN>", b.index(b"<STMTTRN>") + 1)
    r = _ler(b[: b.index(b"<TRNAMT>", segundo)])
    assert len(r.linhas) == 1
    assert r.erros and "truncado" in r.erros[0].mensagem.lower()


def test_binario_aleatorio() -> None:
    rnd = random.Random(7)
    b = bytes(rnd.randrange(256) for _ in range(5000))
    r = _ler(b)
    assert r.linhas == [] and r.erros
    assert detectar(b, "x.ofx") is None


def test_tem_cabecalho_ofx() -> None:
    assert tem_cabecalho_ofx(_bytes())
    assert tem_cabecalho_ofx(b"\xef\xbb\xbf" + _bytes())
    assert tem_cabecalho_ofx(b"  \n<OFX>\n<X>1")
    assert not tem_cabecalho_ofx(b"data;lancamento")
    assert not tem_cabecalho_ofx(b"")


def test_detectar_ofx_por_conteudo() -> None:
    assert isinstance(detectar(_bytes(), "extrato.ofx"), ParserOfx)
    assert isinstance(detectar(_bytes(), "extrato.txt"), ParserOfx)
    assert isinstance(detectar(b"\xef\xbb\xbf" + _bytes(), "e.ofx"), ParserOfx)
    assert detectar(b"qualquer coisa", "e.ofx") is None


def test_ofc_nao_suportado() -> None:
    ofc = b"<OFC>\n<ACCTSTMT>\n<ACCTFROM>\n<BANKID>0341\n</ACCTFROM>\n</ACCTSTMT>\n</OFC>\n"
    for nome in ("e.ofc", "e.ofx"):
        with pytest.raises(FormatoNaoSuportado) as exc:
            detectar(ofc, nome)
        assert str(exc.value) == "O formato OFC ainda não é suportado. No Itaú, baixe o extrato em OFX."
    with pytest.raises(FormatoNaoSuportado):
        detectar(b"conteudo qualquer", "e.OFC")
    assert issubclass(FormatoNaoSuportado, ValueError)


def test_arquivo_grande_rapido() -> None:
    b = _ofx("".join(_trn(memo=f"PIX {i}", n=i % 900) for i in range(20000)))
    t0 = time.perf_counter()
    r = _ler(b)
    assert time.perf_counter() - t0 < 3
    assert len(r.linhas) == 20000 and r.erros == []


def test_ano_implausivel_e_extremos_nao_estouram() -> None:
    for dt in ("00010101", "99991231", "18991231", "21010101"):
        r = _ler(_ofx(_trn(data=dt)))
        assert r.linhas == [] and "Item 1" in r.erros[0].mensagem, dt
    r = _ler(_ofx(_trn(), ledger="<LEDGERBAL>\n<BALAMT>1.00\n<DTASOF>00010101\n</LEDGERBAL>\n"))
    assert any("Data do saldo (DTASOF)" in e.mensagem for e in r.erros)
    assert not any("BALAMT" in e.mensagem for e in r.erros)
    r = _ler(_ofx(_trn(data="19000101")))
    assert r.saldo_anterior_estimado == ("1899-12-31", 1000 + 100)


def test_saldo_anterior_estimado_fronteiras() -> None:
    for dt, esperado in (("20260301", "2026-02-28"), ("20240301", "2024-02-29"),
                         ("20270101", "2026-12-31"), ("20260101", "2025-12-31")):
        assert _ler(_ofx(_trn(data=dt))).saldo_anterior_estimado[0] == esperado


def test_dtasof_mesmo_dia_horas_diferentes() -> None:
    b = _ofx(_trn()).replace(b"<DTASOF>20261026100000", b"<DTASOF>20261022235959")
    r = _ler(b)
    assert r.saldos_dia == {"2026-10-22": 1000} and r.saldo_informado is None


def test_nul_e_bom_atraves_de_ler() -> None:
    b = _ofx(_trn(memo="A\x00B"))
    r = _ler(b)
    assert len(r.linhas) == 1
    r2 = _ler(b"\xef\xbb\xbf" + _ofx(_trn()))
    assert r2.erros == [] and len(r2.linhas) == 1


def test_cartao_recusado() -> None:
    cartao = _ofx(_trn()).replace(b"<STMTRS>", b"<CCSTMTRS>").replace(b"</STMTRS>", b"</CCSTMTRS>")
    cartao = cartao.replace(b"<BANKACCTFROM>", b"<CCACCTFROM>").replace(b"</BANKACCTFROM>", b"</CCACCTFROM>")
    msg = "Extrato de cartão de crédito (fatura) ainda não é suportado. Isso fica para a v0.2."
    with pytest.raises(FormatoNaoSuportado) as exc:
        detectar(cartao, "fatura.ofx")
    assert str(exc.value) == msg
    r = _ler(cartao)
    assert r.linhas == [] and r.erros[0].mensagem == msg
    with pytest.raises(FormatoNaoSuportado):
        detectar(_ofx(_trn()).replace(b"<BANKMSGSRSV1>", b"<CREDITCARDMSGSRSV1>"), "f.ofx")


def test_erros_limitados() -> None:
    r = _ler(_ofx("<STMTTRN>\n" * 100_000))
    assert len(r.erros) == 51
    assert r.erros[-1].mensagem.startswith("... e mais ") and r.erros[-1].mensagem.endswith(" erros.")


def test_sinal_do_trnamt_e_confiado() -> None:
    r = _ler(_ofx(_trn(valor="5.00")))  # TRNTYPE DEBIT com valor positivo
    assert r.linhas[0].valor_centavos == 500


def test_ultima_transacao_sem_fechamento_nao_e_truncada() -> None:
    corpo = _trn(n=1) + _trn(memo="ULTIMA", n=2).replace("</STMTTRN>\n", "")
    r = _ler(_ofx(corpo))
    assert r.erros == [] and [l.descricao for l in r.linhas] == ["PIX TESTE", "ULTIMA"]


def test_fixture_lf_e_crlf_dao_o_mesmo_resultado() -> None:
    lf = FIXTURE.read_bytes().replace(b"\r\n", b"\n")
    crlf = lf.replace(b"\n", b"\r\n")
    assert _ler(lf) == _ler(crlf)
    assert len(_ler(crlf).linhas) == 12


def test_cr_e_espacos_antes_de_tag_de_fechamento() -> None:
    b = _ofx(_trn(valor="-1.00")).replace(b"-1.00\n", b"-1.00\r\t </TRNAMT>\n")
    r = _ler(b)
    assert r.erros == [] and r.linhas[0].valor_centavos == -100
