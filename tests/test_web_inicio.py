import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import conectar
from app.dominio.categorias import criar_categoria, criar_subcategoria, despesas_do_mes
from app.dominio.lancamentos import criar_lancamento_manual
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
    tiles = _bloco(html, "Saldos")
    assert re.search(r'class="valor numero entrada">R\$ 1\.409,14<', tiles)
    assert "Saldo total" in tiles
    assert tiles.count("R$ 1.409,14") == 2


def test_cartao_negativo_com_classe_saida_e_fora_do_total(
        cliente: TestClient, conta: int, db: Path) -> None:
    con = conectar(db)
    contas.criar_conta(con, "Nubank", "cartao", saldo_inicial_centavos=-231000,
                       data_saldo_inicial="2026-07-24")
    con.close()
    tiles = _bloco(cliente.get("/?mes=2026-10").text, "Saldos")
    assert re.search(r'class="valor numero saida">- R\$ 2\.310,00<', tiles)
    assert tiles.count("R$ 1.409,14") == 2  # conta + total (cartão fora do total)


def test_cartoes_entradas_saidas_sobra_de_outubro(cliente: TestClient, conta: int) -> None:
    resumo = _bloco(cliente.get("/?mes=2026-10").text, "Resumo do mês")
    assert re.search(r'Entradas do mês</div>\s*<div class="valor numero entrada">R\$ 400,00<', resumo)
    assert re.search(r'Saídas do mês</div>\s*<div class="valor numero saida">R\$ 3\.233,29<', resumo)
    assert re.search(r'Sobra do mês</div>\s*<div class="valor numero saida">- R\$ 2\.833,29<', resumo)


def test_mes_default_e_o_mes_do_relogio(cliente: TestClient, conta: int) -> None:
    html = cliente.get("/").text
    assert 'value="2026-10"' in html
    assert "R$ 3.233,29" in html


def test_vencida_e_a_vencer_no_bloco_ambar(cliente: TestClient, conta: int, db: Path) -> None:
    _manual(db, conta, date(2026, 10, 20), "Internet", -11990, False)
    _manual(db, conta, date(2026, 10, 30), "Luz", -21000, False)
    _manual(db, conta, date(2026, 10, 31), "Longe", -5000, False)
    venc = _bloco(cliente.get("/?mes=2026-10").text, "Vencimentos")
    assert "Internet" in venc and "20/10/2026" in venc and "R$ 119,90" in venc
    assert "Luz" in venc and "30/10/2026" in venc and "R$ 210,00" in venc
    assert "Longe" not in venc
    assert "1 vencida" in venc and "1 a vencer" in venc


def test_vencimentos_vazio(cliente: TestClient, conta: int) -> None:
    venc = _bloco(cliente.get("/").text, "Vencimentos")
    assert "Nenhuma conta vencida ou a vencer" in venc


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
    mercado, lazer = _cat(db, "Mercado"), _cat(db, "Lazer")
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
    assert por_nome["Mercado"] == 30000  # subcategoria soma na categoria pai
    assert por_nome["Lazer"] == 5000
    assert por_nome["Sem categoria"] == esperado - 35000


def test_rosca_top_n_com_outros(cliente: TestClient, conta: int, db: Path) -> None:
    nomes = ["Moradia", "Mercado", "Transporte", "Saúde", "Lazer", "Assinaturas", "Educação"]
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
    mercado = _cat(db, "Mercado")
    con = conectar(db)
    sub = criar_subcategoria(con, mercado, "Feira")
    con.close()
    _manual(db, conta, date(2026, 10, 9), "Feira do Zé", -10000, True, sub)
    tab = _bloco(cliente.get("/?mes=2026-10").text, "Detalhamento por categoria")
    assert "<details" in tab and "Mercado" in tab and "Feira" in tab
    assert "Feira do Zé" in tab and "09/10/2026" in tab and "R$ 100,00" in tab
    assert "Sem categoria" in tab
    assert re.search(r'Total</td>\s*<td class="valor saida">R\$ 3\.333,29<', tab)
    assert f"categoria_id={sub}" in tab and "de=2026-10-01" in tab and "ate=2026-10-31" in tab


def test_tabela_limita_lancamentos_por_subcategoria(
        cliente: TestClient, conta: int, db: Path) -> None:
    lazer = _cat(db, "Lazer")
    for i in range(55):
        _manual(db, conta, date(2026, 10, 3), f"Item{i:02d}", -100, True, lazer)
    tab = _bloco(cliente.get("/?mes=2026-10").text, "Detalhamento por categoria")
    assert tab.count("Item") == 50
    assert "Ver todos os 55 lançamentos" in tab
