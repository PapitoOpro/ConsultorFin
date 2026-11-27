from datetime import date, datetime
from pathlib import Path

import pytest

from app.db import conectar, migrar
from app.dominio import cofre_senhas, notas_negociacao as dom
from app.gmail.cliente import Anexo, Mensagem
from app.importacao.nota_negociacao import NotaIlegivel, SenhaDaNota, interpretar_texto, ler_notas

TEXTO = """NOTA DE NEGOCIAÇÃO
Nr. nota Folha Data pregão
111.222 1 19/11/2026
Corretora C.N.P.J.
CLIENTE FICTICIO 000.000.000-00
Negociações
C/V Mercadoria Vencimento Quantidade Preço/Ajuste Tipo Negócio Vlr de Operação/Ajuste D/C Taxa Operacional
C WIN Z26 16/12/2026 2 150.000,00 DAY TRADE 300,00 D 0,00
V WIN Z26 16/12/2026 2 150.050,00 DAY TRADE 350,00 C 0,00
Venda disponível Compra disponível Venda Opções Compra Opções Valor dos negócios
0,00 0,00 0,00 0,00 50,00 | C
IRRF IRRF Day Trade (proj.) Taxa operacional Taxa registro BM&F Taxas BM&F (emol+f.gar)
0,00| 0,50 0,00 1,00 0,70 | D
+Outros Custos Impostos Ajuste de posição Ajuste day trade Total de custos operacionais
0,00 0,00 0,00 | 50,00 | C 1,70 | D
Outros IRRF operacional Total Conta Investimento Total Conta Normal Total liquido (#) Total líquido da nota
0,00 0,00 0,00| 47,80 | C 48,30 | C 47,80 | C
"""


def test_interpreta_nota_bmf():
    n = interpretar_texto(TEXTO)
    assert (n.numero, n.data) == ("111.222", "2026-11-19")
    assert len(n.negocios) == 2 and n.negocios[0].valor_centavos == -30000
    assert n.negocios[1].valor_centavos == 35000 and n.negocios[0].mercadoria == "WIN Z26"
    assert (n.ajuste_day_trade_centavos, n.custos_centavos, n.irrf_day_trade_centavos) == (5000, -170, -50)
    assert n.liquido_centavos == 4780 and n.avisos == []
    assert "FICTICIO" not in repr(n)


def test_total_que_nao_confere_gera_aviso():
    n = interpretar_texto(TEXTO.replace("47,80 | C\n", "47,81 | C\n"))
    assert n.avisos


@pytest.mark.parametrize("texto", ["", "OUTRO DOCUMENTO\n1 2 3", "NOTA DE NEGOCIAÇÃO\nsem dados"])
def test_layout_nao_suportado(texto):
    with pytest.raises(NotaIlegivel):
        interpretar_texto(texto)


def test_pdf_com_senha_errada_ou_ausente():
    falso = b"%PDF-1.4\n/Encrypt\nlixo"
    with pytest.raises(SenhaDaNota):
        ler_notas(falso, [])
    with pytest.raises(SenhaDaNota):
        ler_notas(falso, ["errada"])
    with pytest.raises(NotaIlegivel):
        ler_notas(b"nao e pdf", ["x"])


class _Cliente:
    def __init__(self, msgs, anexos):
        self._m, self._a, self.ultima_busca_truncada = msgs, anexos, False

    def buscar(self, de, ate, limite=50, remetentes_termos=()):
        return self._m

    def baixar_anexo(self, mid, aid):
        return self._a[aid]


def _msg(i, de="Clear <noreply@clear.com.br>", assunto="Clear Corretora | Nota de Negociação"):
    return Mensagem(f"m{i}", de, assunto, "", (Anexo(f"a{i}", "n.pdf", 100),))


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.caminho_chave_senhas", lambda: tmp_path / "k.key")
    c = conectar(tmp_path / "t.db")
    migrar(c)
    yield c
    c.close()


def test_buscar_importa_e_nao_duplica(con, monkeypatch):
    monkeypatch.setattr(dom, "ler_notas", lambda conteudo, senhas: ([interpretar_texto(TEXTO)], 0))
    cli = _Cliente([_msg(1), _msg(2, de="x@outro.com"), _msg(3, assunto="Promoção")],
                   {"a1": b"%PDF"})
    agora = datetime(2026, 11, 24)
    r = dom.buscar_e_importar(con, cli, date(2026, 11, 1), date(2026, 12, 1), agora)
    assert (r.encontradas, r.novas, r.ja_importadas) == (1, 1, 0)
    r2 = dom.buscar_e_importar(con, cli, date(2026, 11, 1), date(2026, 12, 1), agora)
    assert (r2.novas, r2.ja_importadas) == (0, 1)
    lista = dom.notas_json(con)
    assert len(lista) == 1 and lista[0]["liquido_centavos"] == 4780 and lista[0]["negocios"] == 2


def test_sem_senha_conta_e_nao_grava(con):
    cli = _Cliente([_msg(1)], {"a1": b"%PDF-1.4\n/Encrypt\nx"})
    r = dom.buscar_e_importar(con, cli, date(2026, 11, 1), date(2026, 12, 1), datetime(2026, 11, 24))
    assert (r.sem_senha, r.novas) == (1, 0) and dom.notas_json(con) == []


def test_usa_senha_do_cofre(con, monkeypatch):
    assert cofre_senhas.guardar(con, dom.EMISSOR_COFRE, dom.FINAL_COFRE, "segredo")
    con.commit()
    vistas = []
    monkeypatch.setattr(dom, "ler_notas", lambda c, s: vistas.append(s) or ([interpretar_texto(TEXTO)], 0))
    dom.buscar_e_importar(con, _Cliente([_msg(1)], {"a1": b"%PDF"}), date(2026, 11, 1),
                          date(2026, 12, 1), datetime(2026, 11, 24))
    assert vistas == [["segredo"]]


def test_nota_alimenta_totais_so_se_extrato_nao_cobre_o_dia(con):
    from app.dominio import investimentos as inv
    from app.importacao.clear import ExtratoClear, LinhaClear
    nota = interpretar_texto(TEXTO)  # pregão 2026-11-19, líquido 47,80
    dom.importar_nota(con, nota, datetime(2026, 11, 24))
    base = inv.resumo(con)
    assert base.meses and base.meses[-1].day_trade_liquido == 4780
    linhas, provis = inv.linhas_com_notas(con)
    assert provis == ["2026-11-19"] and all(l["saldo_centavos"] == 0 for l in linhas)
    assert inv.resultado_diario(linhas).total_centavos == 4780

    ext = ExtratoClear("1", [
        LinhaClear("2026-11-19", "2026-11-20", "AJUSTE DAY-TRADE PR. 19/11/2026 NC. 1", 5000, 5000),
        LinhaClear("2026-11-19", "2026-11-20", "IRRF S/DAY-TRADE PR. 19/11/2026 NC. 1", -50, 4950),
        LinhaClear("2026-11-19", "2026-11-20", "LIQUIDO DAS OPERAÇÕES BM&F PR. 19/11/2026", -170, 4780),
    ], None)
    inv.importar_extrato(con, ext, "e.xlsx", datetime(2026, 11, 24))
    con.commit()
    depois = inv.resumo(con)
    assert inv.linhas_com_notas(con)[1] == []
    assert depois.meses[-1].day_trade_liquido == 4780  # não soma duas vezes
    assert depois.saldo_caixa == 4780 and depois.ultima_data == "2026-11-19"


TEXTO_BOV = """NOTA DE NEGOCIAÇÃO
Nr. nota Folha Data pregão
222333444 1 23/11/2026
CLEAR CTVM S/A
Negociações
Negócios realizados
QNegociação C/VTipo mercado Prazo Especificação do título Obs. (*) Quantidade Preço / Ajuste Valor Operação / Ajuste D/C
1-BOVESPA C VISTA FII FICTICIO XXXX11 CI @ 4 6,96 27,84 D
Resumo dos Negócios Resumo Financeiro
Valor líquido das operações 27,84 D
I.R.R.F. s/ operações, base R$0,00 0,00
Total Custos / Despesas 0,10
Líquido para 25/11/2026 27,94 D
"""


def test_interpreta_nota_bovespa():
    n = interpretar_texto(TEXTO_BOV)
    assert (n.tipo, n.numero, n.data, n.data_liquidacao) == ("bovespa", "222333444", "2026-11-23", "2026-11-25")
    assert n.negocios[0].mercadoria == "XXXX11" and n.negocios[0].valor_centavos == -2784
    assert (n.operacoes_centavos, n.liquido_centavos, n.custos_centavos) == (-2784, -2794, -10)
    assert n.avisos == []


@pytest.mark.parametrize("irrf,liquido,esperado", [
    ("0,21", "27,94", (-10, 0)),       # até R$ 1,00 a retenção é dispensada: só informativo
    ("1,50", "29,44", (-10, -150)),    # acima disso é descontado do líquido
])
def test_irrf_de_acoes_so_e_descontado_acima_de_um_real(irrf, liquido, esperado):
    texto = TEXTO_BOV.replace("base R$0,00 0,00", f"base R$4.200,00 {irrf}").replace(
        "25/11/2026 27,94 D", f"25/11/2026 {liquido} D")
    n = interpretar_texto(texto)
    assert (n.custos_centavos, n.irrf_day_trade_centavos) == esperado


def test_migracao_corrige_irrf_dispensado_ja_gravado(con):
    from app.db import MIGRACOES
    nota = interpretar_texto(TEXTO_BOV)
    nota.custos_centavos, nota.irrf_day_trade_centavos = 11, -21  # como era gravado
    dom.importar_nota(con, nota, datetime(2026, 11, 24))
    MIGRACOES[18](con)
    n = dom.notas_json(con)[0]
    assert (n["custos_centavos"], n["irrf_centavos"]) == (-10, 0)


def test_pdf_com_duas_notas_uma_por_pagina(monkeypatch):
    from app.importacao import nota_negociacao as m

    class Pg:
        def __init__(self, t):
            self.t = t

        def extract_text(self):
            return self.t

    class Pdf:
        pages = [Pg(TEXTO_BOV), Pg(TEXTO), Pg("aviso legal sem nota")]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(m.pdfplumber, "open", lambda *a, **k: Pdf())
    notas, falhas = ler_notas(b"%PDF-1.4\n/Encrypt\nx", ["s"])
    assert sorted(n.tipo for n in notas) == ["bmf", "bovespa"] and falhas == 0


def test_nota_de_acoes_alimenta_ativos_ate_o_extrato_cobrir(con):
    from app.dominio import investimentos as inv
    from app.importacao.clear import ExtratoClear, LinhaClear
    dom.importar_nota(con, interpretar_texto(TEXTO_BOV), datetime(2026, 11, 24))
    r = inv.resumo(con)
    assert r.meses[-1].ativos == -2784 and r.meses[-1].aplicado_acum == 2784
    assert inv.linhas_com_notas(con)[1] == ["2026-11-23"]
    ext = ExtratoClear("1", [LinhaClear("2026-11-23", "2026-11-25",
                                        "OPERAÇÕES EM BOLSA PR 23/11/2026 NOTA Nº 222333444", -2794, -2794)], None)
    inv.importar_extrato(con, ext, "e.xlsx", datetime(2026, 11, 25))
    con.commit()
    assert inv.linhas_com_notas(con)[1] == []
    assert inv.resumo(con).meses[-1].ativos == -2794  # só o extrato: nada em dobro


def test_lista_de_notas_filtra_por_mes(con):
    dom.importar_nota(con, interpretar_texto(TEXTO), datetime(2026, 11, 24))
    dom.importar_nota(con, interpretar_texto(TEXTO.replace("111.222", "999.888").replace("19/11/2026", "20/10/2026")),
                      datetime(2026, 11, 24))
    assert [n["numero"] for n in dom.notas_json(con, "2026-11-01", "2026-11-31")] == ["111.222"]
    assert [n["numero"] for n in dom.notas_json(con, "2026-10-01", "2026-10-31")] == ["999.888"]
