import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from html import unescape
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.db import conectar
from app.dominio.categorias import criar_categoria, criar_subcategoria, despesas_do_mes
from app.dominio.lancamentos import criar_lancamento_manual
from app.dominio.painel import formatar_variacao, variacao_percentual
from app.dominio.parcelas import criar_parcelamento
from app.dominio.recorrentes import criar_recorrente
from app.importacao.deteccao import detectar
from app.importacao.servico import importar
from app.main import create_app
from app.repo import contas

FIXTURE = Path(__file__).parent / "fixtures" / "extrato_itau_exemplo.csv"
HOJE = date(2026, 10, 23)
MSG_MES = "Mês inválido. Use o formato AAAA-MM."
MSG_HOR = "Horizonte inválido. Use 3, 6 ou 12 meses."


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "t.db"


@pytest.fixture
def cliente(db: Path) -> Iterator[TestClient]:
    app = create_app(db)
    app.state.hoje_fn = lambda: HOJE
    with TestClient(app) as c:
        yield c


@pytest.fixture
def conta(cliente: TestClient, db: Path) -> int:
    con = conectar(db)
    try:
        cid = contas.criar_conta(con, "Itaú", "corrente", saldo_inicial_centavos=214808,
                                 data_saldo_inicial="2026-07-24")
        dados = FIXTURE.read_bytes()
        parser = detectar(dados, "extrato.csv")
        assert parser is not None
        importar(con, cid, "extrato.csv", dados, parser, HOJE)
        return cid
    finally:
        con.close()


def _sql(db: Path, sql: str, *args: Any) -> list[Any]:
    con = conectar(db)
    try:
        return [tuple(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def _manual(db: Path, conta_id: int, data: date, desc: str, valor: int, compensado: bool,
            categoria_id: int | None = None) -> int:
    con = conectar(db)
    try:
        return criar_lancamento_manual(con, conta_id=conta_id, data=data, descricao=desc,
                                       valor_centavos=valor, categoria_id=categoria_id,
                                       compensado=compensado)
    finally:
        con.close()


def _cat(db: Path, nome: str) -> int:
    return int(_sql(db, "SELECT id FROM categorias WHERE nome = ?", nome)[0][0])


def _bloco(html: str, titulo: str) -> str:
    m = re.search(rf'<section class="[^"]*" aria-label="{titulo}">(.*?)</section>', html, re.S)
    assert m, titulo
    return m.group(1)


def test_banco_vazio_mostra_estado_vazio(cliente: TestClient) -> None:
    r = cliente.get("/")
    assert r.status_code == 200
    assert "Importar primeiro extrato" in r.text
    assert 'href="/importar"' in r.text


def test_conta_sem_lancamentos_tambem_mostra_estado_vazio(cliente: TestClient, db: Path) -> None:
    con = conectar(db)
    contas.criar_conta(con, "Itaú", "corrente")
    con.close()
    assert "Importar primeiro extrato" in cliente.get("/").text


def test_saldo_itau_em_verde_e_total(cliente: TestClient, conta: int) -> None:
    html = cliente.get("/?mes=2026-10").text
    assert "Importar primeiro extrato" not in html
    contas_ = _bloco(html, "Saldos")
    assert re.search(r'class="valor entrada">R\$ 1\.409,14<', contas_)
    assert contas_.count("R$ 1.409,14") == 1
    kpis = _bloco(html, "Resumo do mês")
    assert re.search(r'Saldo total</div>\s*<div class="valor numero">R\$ 1\.409,14<', kpis)
    assert "kpi-destaque" in kpis


def test_cartao_negativo_com_classe_saida_e_fora_do_total(
        cliente: TestClient, conta: int, db: Path) -> None:
    con = conectar(db)
    contas.criar_conta(con, "Nubank", "cartao", saldo_inicial_centavos=-231000,
                       data_saldo_inicial="2026-07-24")
    con.close()
    html = cliente.get("/?mes=2026-10").text
    tiles = _bloco(html, "Saldos")
    assert re.search(r'class="valor saida">- R\$ 2\.310,00<', tiles)
    assert "cartão" in tiles
    assert re.search(r'Saldo total</div>\s*<div class="valor numero">R\$ 1\.409,14<',
                     _bloco(html, "Resumo do mês"))  # cartão fora do total


def test_cartoes_entradas_saidas_resultado_de_outubro(cliente: TestClient, conta: int) -> None:
    resumo = _bloco(cliente.get("/?mes=2026-10").text, "Resumo do mês")
    assert re.search(r'Entradas do mês</div>\s*<div class="valor numero entrada">R\$ 400,00<', resumo)
    assert re.search(r'Saídas do mês</div>\s*<div class="valor numero saida">R\$ 3\.233,29<', resumo)
    assert re.search(r'Resultado do mês</div>\s*<div class="valor numero saida">- R\$ 2\.833,29<', resumo)
    assert "déficit" in resumo


def test_resultado_positivo_em_verde_com_superavit(cliente: TestClient, conta: int) -> None:
    resumo = _bloco(cliente.get("/?mes=2026-09").text, "Resumo do mês")
    assert re.search(r'Resultado do mês</div>\s*<div class="valor numero entrada">R\$ 2\.906,20<', resumo)
    assert "superávit" in resumo


def test_variacao_percentual_vs_mes_anterior(cliente: TestClient, conta: int) -> None:
    resumo = _bloco(cliente.get("/?mes=2026-10").text, "Resumo do mês")
    ent = resumo[resumo.index("Entradas do mês"):resumo.index("Saídas do mês")]
    sai = resumo[resumo.index("Saídas do mês"):resumo.index("Resultado do mês")]
    assert "-87,5%" in ent and "desce" in ent and "vs set/26" in ent  # menos entrada: ruim
    assert "+1.000,5%" in sai and "desce" in sai and "subiu" in sai  # mais gasto: ruim
    assert "icone" in ent and "icone" in sai  # seta, não só cor
    assert "%" not in resumo[resumo.index("Resultado do mês"):]
    assert "%" not in resumo[:resumo.index("Entradas do mês")]


def test_variacao_boa_usa_classe_sobe(cliente: TestClient, conta: int, db: Path) -> None:
    con = conectar(db)
    try:
        esperado = formatar_variacao(variacao_percentual(
            despesas_do_mes(con, 2026, 9), despesas_do_mes(con, 2026, 8)) or 0.0)
    finally:
        con.close()
    assert esperado.startswith("-")
    resumo = _bloco(cliente.get("/?mes=2026-09").text, "Resumo do mês")
    sai = resumo[resumo.index("Saídas do mês"):resumo.index("Resultado do mês")]
    assert esperado in sai and "caiu" in sai and "sobe" in sai  # gastou menos que ago/26: bom


def test_variacao_so_quando_calculavel(cliente: TestClient, conta: int) -> None:
    resumo = _bloco(cliente.get("/?mes=2026-07").text, "Resumo do mês")  # jun/26 sem nada
    assert "%" not in resumo
    assert "sem mês anterior para comparar" in resumo


def test_mes_default_e_o_mes_do_relogio(cliente: TestClient, conta: int) -> None:
    html = cliente.get("/").text
    assert 'value="2026-10"' in html
    assert "R$ 3.233,29" in html


def test_vencida_e_a_vencer_no_bloco_de_vencimentos(cliente: TestClient, conta: int, db: Path) -> None:
    _manual(db, conta, date(2026, 10, 20), "Internet", -11990, False)
    _manual(db, conta, date(2026, 10, 30), "Luz", -21000, False)
    _manual(db, conta, date(2026, 10, 31), "Longe", -5000, False)
    venc = _bloco(cliente.get("/?mes=2026-10").text, "Próximos vencimentos")
    assert "Internet" in venc and "20/10/2026" in venc and "R$ 119,90" in venc
    assert "vencida" in venc and "Luz" in venc and "30/10/2026" in venc and "R$ 210,00" in venc
    assert "em 7 dias" in venc
    assert "Longe" in venc and "em 8 dias" in venc  # lista = próximos pendentes, sem janela fixa
    assert "1 vencida" in venc and "1 a vencer" in venc  # resumo da janela de 7 dias
    assert venc.index("Internet") < venc.index("Luz") < venc.index("Longe")
    assert 'href="/contas-a-pagar"' in venc and "Ver todas" in venc


def test_vence_hoje_e_limite_de_seis(cliente: TestClient, conta: int, db: Path) -> None:
    _manual(db, conta, date(2026, 10, 23), "Aluguel", -100000, False)
    for i in range(7):
        _manual(db, conta, date(2026, 11, 1 + i), f"Conta{i}", -1000, False)
    venc = _bloco(cliente.get("/").text, "Próximos vencimentos")
    assert "vence hoje" in venc and "Aluguel" in venc
    assert venc.count("Conta") == 5  # 1 + 5 = limite de 6


def test_vencimentos_vazio(cliente: TestClient, conta: int) -> None:
    venc = _bloco(cliente.get("/").text, "Próximos vencimentos")
    assert "Nenhuma conta vencida ou a vencer" in venc
    assert 'class="vazio' in venc


def test_blocos_novos_do_dashboard_com_dados_reais(cliente: TestClient, conta: int) -> None:
    html = cliente.get("/?mes=2026-10").text
    for titulo in ("Saldo total", "Entradas do mês", "Saídas do mês", "Resultado do mês",
                   "Fluxo de caixa", "Gastos por categoria", "Lançamentos recentes",
                   "Próximos vencimentos"):
        assert titulo in html, titulo
    assert "Visão geral das suas finanças — outubro de 2026" in html
    rec = _bloco(html, "Lançamentos recentes")
    assert "PIX TRANSF RR Supo22/10" in rec and "22/10/2026" in rec and "- R$ 343,54" in rec
    assert "UBER *TRIP" in rec and "Sem categoria" in rec and "Itaú" in rec
    assert "PIX RECEBIDO Joao Teste" in rec and "+ R$ 400,00" in rec
    assert len(re.findall(r"<li[ >]", rec)) == 5
    assert re.search(r'href="/lancamentos"[^>]*>\s*Ver todos', rec)
    fluxo = _bloco(html, "Fluxo de caixa")
    assert 'id="graf-fluxo"' in fluxo and "sr-only" in fluxo
    assert "R$ 3.233,29" in fluxo and "R$ 400,00" in fluxo and "set/26" in fluxo
    assert 'id="graf-rosca"' in _bloco(html, "Gastos por categoria")


def test_sem_avatar_sino_busca_saudacao(cliente: TestClient, conta: int) -> None:
    html = cliente.get("/?mes=2026-10").text.lower()
    for proibido in ("avatar", "notifica", "bom dia", "boa tarde", "olá,", "metas", 'type="search"'):
        assert proibido not in html, proibido


def test_filtros_mes_e_horizonte_alteram_numeros(cliente: TestClient, conta: int) -> None:
    out = cliente.get("/?mes=2026-10&horizonte=3").text
    sep = cliente.get("/?mes=2026-09&horizonte=12").text
    assert "outubro de 2026" in out and "setembro de 2026" in sep
    assert "R$ 3.233,29" in _bloco(out, "Resumo do mês")
    assert "R$ 3.233,29" not in _bloco(sep, "Resumo do mês")
    assert "R$ 293,80" in _bloco(sep, "Resumo do mês")
    assert 'data-horizonte="3"' in out and 'data-horizonte="12"' in sep
    m = re.search(r'<div class="filtro-periodo" role="group" aria-label="Horizonte">(.*?)</div>', out, re.S)
    assert m
    hor = m.group(1)
    assert re.search(r'aria-current="true"[^>]*>\s*3 meses', hor)
    assert "horizonte=12" in hor and "mes=2026-10" in hor


def test_dados_json_traz_fluxo_e_campos_antigos(cliente: TestClient, conta: int) -> None:
    d = cliente.get("/inicio/dados.json?mes=2026-10&horizonte=3").json()
    assert [p["mes"] for p in d["fluxo"]] == [
        "2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"]
    assert d["fluxo"][-1]["saidas_centavos"] == 323329 and d["fluxo"][-1]["entradas_centavos"] == 40000
    assert d["fluxo"][-1]["saidas_fmt"] == "R$ 3.233,29"
    for campo in ("mes", "horizonte", "rosca", "maiores", "parcelas", "saldo_projetado", "comparativo"):
        assert campo in d


def test_xss_em_todos_os_blocos(cliente: TestClient, conta: int, db: Path) -> None:
    con = conectar(db)
    cid = criar_categoria(con, "<img src=x onerror=alert(1)>")
    con.close()
    _manual(db, conta, date(2026, 10, 23), "<b>recente</b>", -1000, True, cid)
    _manual(db, conta, date(2026, 10, 24), "<i>venc</i>", -1000, False, cid)
    html = cliente.get("/?mes=2026-10").text
    assert "<img src=x" not in html and "<b>recente" not in html and "<i>venc" not in html
    rec = _bloco(html, "Lançamentos recentes")
    assert "&lt;img src=x onerror=alert(1)&gt;" in rec and "&lt;b&gt;recente&lt;/b&gt;" in rec
    assert "&lt;i&gt;venc&lt;/i&gt;" in _bloco(html, "Próximos vencimentos")
    assert "&lt;img src=x" in _bloco(html, "Gastos por categoria")
    assert "&lt;img src=x" in _bloco(html, "Detalhamento por categoria")


@pytest.mark.parametrize("h,n", [(3, 3), (6, 6), (12, 12)])
def test_dados_json_pontos_por_horizonte(cliente: TestClient, conta: int, h: int, n: int) -> None:
    r = cliente.get(f"/inicio/dados.json?mes=2026-10&horizonte={h}")
    assert r.status_code == 200
    d = r.json()
    assert len(d["parcelas"]) == n and len(d["saldo_projetado"]) == n
    assert d["horizonte"] == h and d["mes"] == "2026-10"


def test_dados_json_labels_atravessam_virada_de_ano(cliente: TestClient, conta: int) -> None:
    d = cliente.get("/inicio/dados.json?horizonte=6").json()
    assert [p["mes"] for p in d["saldo_projetado"]] == [
        "2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03"]
    assert d["saldo_projetado"][3]["rotulo"] == "jan/27"
    assert isinstance(d["saldo_projetado"][0]["valor_centavos"], int)
    assert d["saldo_projetado"][0]["valor_fmt"] == "R$ 1.409,14"


@pytest.mark.parametrize("q", ["mes=2026-13", "mes=abc", "mes=2026-1", "mes=", "mes=２０２６-10"])
def test_mes_invalido_400(cliente: TestClient, q: str) -> None:
    for url in ("/?" + q, "/inicio/dados.json?" + q):
        r = cliente.get(url)
        assert r.status_code == 400, url
        assert MSG_MES in r.text


@pytest.mark.parametrize("h", ["5", "abc", "", "０６"])
def test_horizonte_invalido_400(cliente: TestClient, h: str) -> None:
    for url in ("/?horizonte=" + h, "/inicio/dados.json?horizonte=" + h):
        r = cliente.get(url)
        assert r.status_code == 400, url
        assert MSG_HOR in r.text


def test_rosca_soma_igual_despesas_do_mes(cliente: TestClient, conta: int, db: Path) -> None:
    mercado, lazer = _cat(db, "Alimentação"), _cat(db, "Lazer e Entretenimento")
    con = conectar(db)
    sub = criar_subcategoria(con, mercado, "Feira")
    con.close()
    _manual(db, conta, date(2026, 10, 9), "Feira X", -10000, True, sub)
    _manual(db, conta, date(2026, 10, 10), "Super Y", -20000, True, mercado)
    _manual(db, conta, date(2026, 10, 11), "Cinema", -5000, True, lazer)
    d = cliente.get("/inicio/dados.json?mes=2026-10").json()
    con = conectar(db)
    esperado = despesas_do_mes(con, 2026, 10)
    con.close()
    assert sum(f["valor_centavos"] for f in d["rosca"]) == esperado
    por_nome = {f["nome"]: f["valor_centavos"] for f in d["rosca"]}
    assert por_nome["Alimentação"] == 30000  # subcategoria soma na categoria pai
    assert por_nome["Lazer e Entretenimento"] == 5000
    assert por_nome["Sem categoria"] == esperado - 35000


def test_rosca_top_n_com_outros(cliente: TestClient, conta: int, db: Path) -> None:
    nomes = ["Moradia", "Alimentação", "Transporte e Veículo", "Saúde", "Lazer e Entretenimento", "Assinaturas e Serviços Digitais", "Educação"]
    for i, n in enumerate(nomes):
        _manual(db, conta, date(2026, 10, 2), f"x{i}", -(100000 + i * 1000), True, _cat(db, n))
    d = cliente.get("/inicio/dados.json?mes=2026-10").json()
    assert len(d["rosca"]) == 6 and d["rosca"][-1]["nome"] == "Outros"
    con = conectar(db)
    assert sum(f["valor_centavos"] for f in d["rosca"]) == despesas_do_mes(con, 2026, 10)
    con.close()


def test_comparativo_mes_atual_x_anterior(cliente: TestClient, conta: int) -> None:
    c = cliente.get("/inicio/dados.json?mes=2026-10").json()["comparativo"]
    assert [p["mes"] for p in c] == ["2026-09", "2026-10"]
    assert (c[0]["entradas_centavos"], c[0]["saidas_centavos"]) == (320000, 29380)
    assert (c[1]["entradas_centavos"], c[1]["saidas_centavos"]) == (40000, 323329)


def test_chama_gerar_recorrentes(cliente: TestClient, conta: int, db: Path) -> None:
    con = conectar(db)
    criar_recorrente(con, nome="Aluguel", tipo="saida", dia=5, valor_estimado_centavos=150000,
                     conta_id=conta, categoria_id=None)
    con.close()
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos WHERE recorrente_id IS NOT NULL") == [(0,)]
    d = cliente.get("/inicio/dados.json?mes=2026-10&horizonte=3").json()
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos WHERE recorrente_id IS NOT NULL") == [(3,)]
    assert d["saldo_projetado"][1]["valor_centavos"] == 140914 - 300000  # out (vencida, entra no 1º ponto) + nov
    cliente.get("/?horizonte=12")
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos WHERE recorrente_id IS NOT NULL") == [(12,)]


def test_parcelas_por_mes_no_json(cliente: TestClient, conta: int, db: Path) -> None:
    con = conectar(db)
    criar_parcelamento(con, conta_id=conta, descricao="TV", valor_total_centavos=30000,
                       n_parcelas=3, primeiro_vencimento=date(2026, 11, 10), categoria_id=None)
    con.close()
    p = cliente.get("/inicio/dados.json?horizonte=6").json()["parcelas"]
    assert [x["valor_centavos"] for x in p] == [0, 10000, 10000, 10000, 0, 0]


def test_xss_escapado(cliente: TestClient, conta: int, db: Path) -> None:
    con = conectar(db)
    cid = criar_categoria(con, "<script>alert(1)</script>")
    con.close()
    _manual(db, conta, date(2026, 10, 9), "<img src=x onerror=alert(2)>", -1000, True, cid)
    _manual(db, conta, date(2026, 10, 21), "<b>venc</b>", -1000, False)
    html = cliente.get("/?mes=2026-10").text
    assert "<script>alert(1)" not in html
    assert "<img src=x" not in html
    assert "<b>venc</b>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;img src=x onerror=alert(2)&gt;" in html


def test_sem_urls_externas(cliente: TestClient, conta: int) -> None:
    html = cliente.get("/?mes=2026-10").text
    assert not re.search(r'(?:src|href|action)="(?:https?:)?//', html)
    assert 'src="/static/js/echarts.min.js"' in html and 'src="/static/js/graficos.js"' in html
    js = (Path(__file__).parent.parent / "app/web/static/js/graficos.js").read_text(encoding="utf-8")
    assert "http://" not in js and "https://" not in js
    assert "innerHTML" not in js


def test_fallback_acessivel_com_numeros(cliente: TestClient, conta: int) -> None:
    html = cliente.get("/?mes=2026-10").text
    assert 'class="sr-only"' in html
    comp = _bloco(html, "Entradas e saídas")
    assert "R$ 3.233,29" in comp and "R$ 400,00" in comp and "set/26" in comp


def test_tabela_expansivel_categoria_subcategoria_lancamentos(
        cliente: TestClient, conta: int, db: Path) -> None:
    mercado = _cat(db, "Alimentação")
    con = conectar(db)
    sub = criar_subcategoria(con, mercado, "Feira")
    con.close()
    _manual(db, conta, date(2026, 10, 9), "Feira do Zé", -10000, True, sub)
    tab = _bloco(cliente.get("/?mes=2026-10").text, "Detalhamento por categoria")
    assert "<details" in tab and "Alimentação" in tab and "Feira" in tab
    assert "Feira do Zé" in tab and "09/10/2026" in tab and "R$ 100,00" in tab
    assert "Sem categoria" in tab
    assert re.search(r'Total</td>\s*<td class="valor saida">R\$ 3\.333,29<', tab)
    assert "filtro aproximado por data e categoria; pode diferir do painel" in tab
    hrefs = [unescape(h) for h in re.findall(r'href="(/lancamentos\?[^"]+)"', tab)]
    qs = next(parse_qs(urlparse(h).query) for h in hrefs if f"categoria_id={sub}&" in h)
    assert qs["de"] == ["2026-10-01"] and qs["ate"] == ["2026-10-31"]
    assert qs["tipo"] == ["saida"] and qs["status"] == ["compensados"]
    sem = next(parse_qs(urlparse(h).query) for h in hrefs if "categoria_id=sem" in h)
    assert sem["tipo"] == ["saida"]


def test_tabela_limita_lancamentos_por_subcategoria(
        cliente: TestClient, conta: int, db: Path) -> None:
    lazer = _cat(db, "Lazer e Entretenimento")
    for i in range(55):
        _manual(db, conta, date(2026, 10, 3), f"Item{i:02d}", -100, True, lazer)
    tab = _bloco(cliente.get("/?mes=2026-10").text, "Detalhamento por categoria")
    assert tab.count("Item") == 50
    assert "Mostrando 50 de 55 lançamentos" in tab
    assert f"categoria_id={lazer}" not in tab  # '(sem subcategoria)' não linka com o id do pai


def _saidas_e_tabela(cliente: TestClient, mes: str = "2026-10") -> tuple[str, str, dict[str, Any]]:
    html_ = cliente.get(f"/?mes={mes}").text
    resumo = _bloco(html_, "Resumo do mês")
    tab = _bloco(html_, "Detalhamento por categoria")
    return resumo, tab, cliente.get(f"/inicio/dados.json?mes={mes}").json()


def test_cartao_nao_altera_saidas_rosca_nem_tabela(cliente: TestClient, conta: int, db: Path) -> None:
    con = conectar(db)
    cartao = contas.criar_conta(con, "Nubank", "cartao", saldo_inicial_centavos=0,
                                data_saldo_inicial="2026-01-01")
    con.close()
    _manual(db, conta, date(2026, 10, 12), "Gasto cartão", -99900, True)
    con = conectar(db)
    con.execute("UPDATE lancamentos SET conta_id = ? WHERE descricao = 'Gasto cartão'", (cartao,))
    con.commit()
    con.close()
    resumo, tab, d = _saidas_e_tabela(cliente)
    assert "R$ 3.233,29" in resumo and "Gasto cartão" not in tab
    assert re.search(r'Total</td>\s*<td class="valor saida">R\$ 3\.233,29<', tab)
    assert sum(f["valor_centavos"] for f in d["rosca"]) == 323329
    assert d["comparativo"][1]["saidas_centavos"] == 323329


def test_transferencia_excluida(cliente: TestClient, conta: int, db: Path) -> None:
    lid = _manual(db, conta, date(2026, 10, 12), "Mov interno", -50000, True)
    con = conectar(db)
    con.execute("UPDATE lancamentos SET tipo = 'transferencia' WHERE id = ?", (lid,))
    con.commit()
    con.close()
    resumo, tab, d = _saidas_e_tabela(cliente)
    assert "R$ 3.233,29" in resumo and "R$ 400,00" in resumo and "Mov interno" not in tab
    assert sum(f["valor_centavos"] for f in d["rosca"]) == 323329


def test_pagamento_fatura_sem_categoria_conta_como_saida(cliente: TestClient, conta: int) -> None:
    resumo, tab, d = _saidas_e_tabela(cliente)
    assert "R$ 3.233,29" in resumo  # inclui os R$ 2.500,00 da fatura
    sem = tab[tab.index("Sem categoria"):]
    assert "PAGAMENTO FATURA CARTAO" in sem and "R$ 2.500,00" in sem
    assert {f["nome"]: f["valor_centavos"] for f in d["rosca"]}["Sem categoria"] == 323329


def test_limites_do_mes_31_e_01(cliente: TestClient, conta: int, db: Path) -> None:
    _manual(db, conta, date(2026, 10, 31), "Fim de outubro", -1000, True)
    _manual(db, conta, date(2026, 11, 1), "Início de novembro", -2000, True)
    out, tab_out, _ = _saidas_e_tabela(cliente, "2026-10")
    assert "R$ 3.243,29" in out and "Fim de outubro" in tab_out and "Início de novembro" not in tab_out
    nov, tab_nov, d = _saidas_e_tabela(cliente, "2026-11")
    assert "R$ 20,00" in nov and "Início de novembro" in tab_nov and "Fim de outubro" not in tab_nov
    assert d["comparativo"][0]["saidas_centavos"] == 324329  # outubro, mês anterior


def test_janeiro_compara_com_dezembro_do_ano_anterior(cliente: TestClient, conta: int, db: Path) -> None:
    _manual(db, conta, date(2025, 12, 31), "Dez saída", -5000, True)
    _manual(db, conta, date(2025, 12, 15), "Dez entrada", 7000, True)
    _manual(db, conta, date(2026, 1, 1), "Jan saída", -300, True)
    c = cliente.get("/inicio/dados.json?mes=2026-01").json()["comparativo"]
    assert [p["mes"] for p in c] == ["2025-12", "2026-01"]
    assert [p["rotulo"] for p in c] == ["dez/25", "jan/26"]
    assert (c[0]["entradas_centavos"], c[0]["saidas_centavos"]) == (7000, 5000)
    assert (c[1]["entradas_centavos"], c[1]["saidas_centavos"]) == (0, 300)
    assert c[0]["saidas_fmt"] == "R$ 50,00"


def test_rotulos_dos_graficos(cliente: TestClient, conta: int) -> None:
    html_ = cliente.get("/?mes=2026-10").text
    assert "Saldo projetado · a partir de out/26" in html_
    assert "Colunas de saldo projetado por mês, a partir de out/26" in html_
    assert "Entradas e saídas: out/26 comparado a set/26" in html_
    assert "<thead>" in _bloco(html_, "Parcelas")


def test_rosca_vazia_mostra_texto(cliente: TestClient, conta: int) -> None:
    html_ = cliente.get("/?mes=2026-05").text
    bloco = _bloco(html_, "Gastos por categoria")
    assert "Sem gastos neste mês" in bloco and 'id="graf-rosca"' not in bloco
