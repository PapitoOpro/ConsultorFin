from pathlib import Path

from app.importacao.deteccao import detectar
from app.importacao.itau_csv import ParserItauCsv

FIXTURE = Path(__file__).parent / "fixtures" / "extrato_itau_exemplo.csv"
CABECALHO = "data;lançamento;ag./origem;valor (R$);saldos (R$)"


def _ler():
    return ParserItauCsv().ler(FIXTURE.read_bytes())


def _csv(*linhas: str) -> bytes:
    return ("\r\n".join(["banco;;;;", CABECALHO, *linhas]) + "\r\n").encode("latin-1")


def test_fixture_sem_erros_e_linhas_saldo_fora():
    r = _ler()
    assert r.erros == []
    assert len(r.linhas) == 18
    assert all(not l.descricao.upper().startswith("SALDO") for l in r.linhas)


def test_saldos_e_soma():
    r = _ler()
    assert r.saldo_anterior == ("2026-07-24", 214808)
    assert r.saldos_dia["2026-10-22"] == 140914
    assert sum(l.valor_centavos for l in r.linhas) == -73894
    assert r.saldo_anterior[1] + sum(l.valor_centavos for l in r.linhas) == 140914


def test_datas_iso_e_descricao_original():
    r = _ler()
    assert all(len(l.data) == 10 and l.data[4] == "-" for l in r.linhas)
    assert any(l.descricao == "PIX TRANSF RR Supo22/10" and l.data == "2026-10-22" for l in r.linhas)


def test_dois_lancamentos_identicos_no_mesmo_dia():
    r = _ler()
    iguais = [l for l in r.linhas if l.descricao == "PIX QRS PADARIA CENTRAL" and l.data == "2026-10-05"]
    assert len(iguais) == 2


def test_valor_invalido_gera_erro_sem_excecao():
    r = ParserItauCsv().ler(_csv("10/10/2026;COMPRA;;x;", "09/10/2026;OK;;-1,00;"))
    assert [e.mensagem for e in r.erros] == ["Linha 3: valor inválido 'x'"]
    assert r.erros[0].linha == 3
    assert len(r.linhas) == 1


def test_data_invalida_gera_erro():
    r = ParserItauCsv().ler(_csv("32/10/2026;COMPRA;;-1,00;"))
    assert len(r.erros) == 1 and "data inválida" in r.erros[0].mensagem
    assert r.linhas == []


def test_utf8_gera_erro():
    conteudo = (CABECALHO + "\r\n10/10/2026;COMPRA;;-1,00;\r\n").encode("utf-8")
    r = ParserItauCsv().ler(conteudo)
    assert r.linhas == [] and len(r.erros) == 1
    assert "Latin-1" in r.erros[0].mensagem


def test_bom_aceito():
    conteudo = b"\xef\xbb\xbf" + _csv("10/10/2026;COMPRA;;-1,00;")
    r = ParserItauCsv().ler(conteudo)
    assert r.erros == [] and len(r.linhas) == 1


def test_arquivo_vazio():
    r = ParserItauCsv().ler(b"")
    assert r.linhas == [] and r.erros[0].mensagem == "Arquivo vazio."


def test_detectar():
    assert isinstance(detectar(FIXTURE.read_bytes(), "extrato.csv"), ParserItauCsv)
    assert detectar(b"a;b;c\r\n", "x.csv") is None
    assert detectar(b"", "x.csv") is None


def test_mensagem_de_erro_trunca_o_valor_ecoado() -> None:
    from app.importacao.base import eco
    assert eco("x" * 100) == "x" * 29 + "…"
    assert eco("abc") == "abc"


def _fixture_com(*extras_apos_cabecalho: str, extras_dados: tuple[str, ...] = ()) -> bytes:
    txt = FIXTURE.read_bytes().decode("latin-1")
    txt = txt.replace("lançamentos;;;;\r\n", "")
    cab = CABECALHO + "\r\n"
    ins = "".join(e + "\r\n" for e in extras_apos_cabecalho)
    txt = txt.replace(cab, cab + ins, 1)
    if extras_dados:
        txt = txt.replace("SALDO ANTERIOR", "SALDO ANTERIOR", 1)
        linhas = txt.split("\r\n")
        i = next(k for k, l in enumerate(linhas) if l.startswith("22/10/2026;PIX"))
        linhas[i + 1:i + 1] = list(extras_dados)
        txt = "\r\n".join(linhas)
    return txt.encode("latin-1")


def test_linha_rotulo_lancamentos_e_ignorada_em_silencio():
    base = _ler()
    r = ParserItauCsv().ler(_fixture_com("lançamentos;;;;"))
    assert r.erros == []
    assert r.linhas == base.linhas
    assert r.saldos_dia == base.saldos_dia and r.saldo_anterior == base.saldo_anterior


def test_rotulo_maiusculo_e_linha_vazia_entre_dados_sem_erros():
    base = _ler()
    r = ParserItauCsv().ler(_fixture_com("Lançamentos;;;;", extras_dados=(";;;;",)))
    assert r.erros == []
    assert r.linhas == base.linhas


def test_primeira_celula_nao_data_com_outras_celulas_continua_erro():
    r = ParserItauCsv().ler(_csv("texto livre;1,00;;;"))
    assert [e.mensagem for e in r.erros] == ["Linha 3: data inválida 'texto livre'"]
    assert r.erros[0].linha == 3


def test_rotulo_nao_data_com_celula_preenchida_continua_erro():
    r = ParserItauCsv().ler(_csv("lançamentos;;;;1,00"))
    assert len(r.erros) == 1 and "data inválida" in r.erros[0].mensagem


def test_data_valida_com_valor_invalido_continua_erro():
    r = ParserItauCsv().ler(_csv("lançamentos;;;;", "10/10/2026;COMPRA;;abc;"))
    assert [e.mensagem for e in r.erros] == ["Linha 4: valor inválido 'abc'"]
    assert r.linhas == []
