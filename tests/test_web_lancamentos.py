import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import conectar
from app.dominio.categorias import despesas_do_mes
from app.dominio.lancamentos import criar_lancamento_manual
from app.importacao.deteccao import detectar
from app.importacao.servico import importar
from app.main import create_app
from app.repo import contas

FIXTURE = Path(__file__).parent / "fixtures" / "extrato_itau_exemplo.csv"
TUDO = {"de": "2000-01-01", "ate": "2100-01-01"}
VOLTAR = "de=2000-01-01&ate=2100-01-01"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "t.db"


@pytest.fixture
def cliente(db: Path) -> Iterator[TestClient]:
    with TestClient(create_app(db)) as c:
        yield c


@pytest.fixture
def conta(cliente: TestClient, db: Path) -> int:
    """Conta Itaú com o extrato de exemplo importado (18 lançamentos)."""
    con = conectar(db)
    try:
        cid = contas.criar_conta(con, "Itaú", "corrente", saldo_inicial_centavos=214808,
                                 data_saldo_inicial="2026-07-24")
        dados = FIXTURE.read_bytes()
        parser = detectar(dados, "extrato.csv")
        assert parser is not None
        importar(con, cid, "extrato.csv", dados, parser, date(2026, 10, 23))
        return cid
    finally:
        con.close()


def _sql(db: Path, sql: str, *args: Any) -> list[Any]:
    con = conectar(db)
    try:
        return [tuple(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def _id(db: Path, descricao: str, n: int = 0) -> int:
    return int(_sql(db, "SELECT id FROM lancamentos WHERE descricao = ? ORDER BY id", descricao)[n][0])


def _cat(db: Path, nome: str) -> int:
    return int(_sql(db, "SELECT id FROM categorias WHERE nome = ?", nome)[0][0])


def _linhas(html: str) -> int:
    return len(re.findall(r'<tr class="linha', html))


def _total(html: str, nome: str) -> str:
    m = re.search(rf'id="total-{nome}"[^>]*>([^<]*)<', html)
    assert m, nome
    return m.group(1).strip()


def _get(c: TestClient, **q: str) -> Any:
    return c.get("/lancamentos", params={**TUDO, **q})


def test_extrato_lista_18_linhas_e_totais(cliente: TestClient, conta: int) -> None:
    r = _get(cliente)
    assert r.status_code == 200
    assert _linhas(r.text) == 18
    assert _total(r.text, "entradas") == "R$ 4.100,00"
    assert _total(r.text, "saidas") == "R$ 4.838,94"
    assert _total(r.text, "liquido") == "- R$ 738,94"
    assert _total(r.text, "pendentes") == "R$ 0,00"
    assert "18 lançamentos" in r.text
    assert "PIX QRS PADARIA CENTRAL" in r.text
    assert 'class="origem importado">importado<' in r.text


def test_filtro_texto_reduz_linhas_e_recalcula_totais(cliente: TestClient, conta: int) -> None:
    r = _get(cliente, texto="PIX RECEBIDO")
    assert _linhas(r.text) == 2
    assert _total(r.text, "entradas") == "R$ 900,00"
    assert _total(r.text, "saidas") == "R$ 0,00"
    assert _total(r.text, "liquido") == "R$ 900,00"


def test_busca_sem_acento_e_sem_caixa(cliente: TestClient, db: Path, conta: int) -> None:
    assert _linhas(_get(cliente, texto="padaria").text) == 2
    cliente.post("/lancamentos/novo", data={
        "data": "2026-10-10", "descricao": "Café da manhã", "valor": "10,00", "sinal": "saida",
        "conta_id": str(conta), "compensado": "1"})
    assert _linhas(_get(cliente, texto="CAFE DA MANHA").text) == 1
    assert _linhas(_get(cliente, texto="café").text) == 2  # "CAFE DA ESQUINA" e "Café da manhã"


def test_texto_com_percentual_e_literal(cliente: TestClient, conta: int) -> None:
    cliente.post("/lancamentos/novo", data={
        "data": "2026-10-10", "descricao": "Desconto 10% loja", "valor": "5,00", "sinal": "saida",
        "conta_id": str(conta), "compensado": "1"})
    assert _linhas(_get(cliente, texto="%").text) == 1
    assert _linhas(_get(cliente, texto="_").text) == 0
    assert _linhas(_get(cliente, texto="10%").text) == 1


def test_filtros_conta_tipo_status_categoria_periodo(cliente: TestClient, db: Path, conta: int) -> None:
    assert _linhas(_get(cliente, tipo="pagamento_fatura").text) == 1
    assert _linhas(_get(cliente, tipo="entrada").text) == 3
    assert _linhas(_get(cliente, status="pendentes").text) == 0
    assert _linhas(_get(cliente, status="compensados").text) == 18
    assert _linhas(_get(cliente, conta_id=str(conta)).text) == 18
    assert _linhas(cliente.get("/lancamentos", params={"de": "2026-10-01", "ate": "2026-10-31"}).text) == 7
    assert _linhas(_get(cliente, categoria_id="sem").text) == 18
    mercado = _cat(db, "Mercado")
    cliente.post("/lancamentos/lote", data={"ids": [str(_id(db, "SUPERMERCADO EXEMPLO", 0))],
                                            "categoria_id": str(mercado)})
    assert _linhas(_get(cliente, categoria_id=str(mercado)).text) == 1
    assert _linhas(_get(cliente, categoria_id="sem").text) == 17


def test_periodo_padrao_e_mes_atual(cliente: TestClient, conta: int) -> None:
    hoje = date.today()
    r = cliente.get("/lancamentos")
    assert r.status_code == 200
    assert f'value="{hoje.strftime("%Y-%m")}-01"' in r.text


def test_filtros_invalidos_retornam_400(cliente: TestClient, conta: int) -> None:
    for q in ({"de": "xx"}, {"tipo": "foo"}, {"status": "foo"}, {"conta_id": "abc"},
              {"categoria_id": "-1"}, {"pagina": "0"}, {"pagina": "x"}):
        r = cliente.get("/lancamentos", params=q)
        assert r.status_code == 400, q
        assert "Traceback" not in r.text


def test_sql_injection_em_texto_e_inerte(cliente: TestClient, conta: int) -> None:
    r = _get(cliente, texto="'; DROP TABLE lancamentos; --")
    assert r.status_code == 200 and _linhas(r.text) == 0
    assert _linhas(_get(cliente).text) == 18


def test_categorizar_em_lote(cliente: TestClient, db: Path, conta: int) -> None:
    ids = [str(_id(db, "SUPERMERCADO EXEMPLO", 0)), str(_id(db, "SUPERMERCADO EXEMPLO", 1))]
    mercado = _cat(db, "Mercado")
    r = cliente.post("/lancamentos/lote", data={"ids": ids, "categoria_id": str(mercado),
                                                "voltar": VOLTAR})
    assert r.status_code == 200
    assert "2 lançamentos categorizados" in r.text
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos WHERE categoria_id = ?", mercado) == [(2,)]


def test_lote_sem_selecao_ou_categoria_invalida(cliente: TestClient, db: Path, conta: int) -> None:
    r = cliente.post("/lancamentos/lote", data={"categoria_id": "1"})
    assert r.status_code == 400 and "Selecione ao menos um lançamento" in r.text
    r = cliente.post("/lancamentos/lote", data={"ids": ["1"], "categoria_id": "9999"})
    assert r.status_code == 400 and "Categoria não encontrada" in r.text
    r = cliente.post("/lancamentos/lote", data={"ids": ["1; DROP"], "categoria_id": "1"})
    assert r.status_code == 400


def test_categoria_inline_htmx(cliente: TestClient, db: Path, conta: int) -> None:
    lid = _id(db, "NETFLIX.COM")
    lazer = _cat(db, "Lazer")
    r = cliente.post(f"/lancamentos/{lid}/categoria", data={"categoria_id": str(lazer)},
                     headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert f'<option value="{lazer}" selected>' in r.text
    assert "<html" not in r.text
    assert _sql(db, "SELECT categoria_id FROM lancamentos WHERE id = ?", lid) == [(lazer,)]
    cliente.post(f"/lancamentos/{lid}/categoria", data={"categoria_id": ""})
    assert _sql(db, "SELECT categoria_id FROM lancamentos WHERE id = ?", lid) == [(None,)]
    assert cliente.post("/lancamentos/99999/categoria", data={"categoria_id": str(lazer)}).status_code == 404
    assert cliente.post("/lancamentos/abc/categoria", data={"categoria_id": "1"}).status_code == 400


def test_criar_regra_a_partir_de_linha(cliente: TestClient, db: Path, conta: int) -> None:
    lid = _id(db, "SUPERMERCADO EXEMPLO", 0)
    mercado = _cat(db, "Mercado")
    r = cliente.post(f"/lancamentos/{lid}/regra", data={"padrao": "SUPERMERCADO", "categoria_id": str(mercado)})
    assert r.status_code == 200
    assert "Regra criada: 2 lançamentos categorizados" in r.text
    assert _sql(db, "SELECT padrao, categoria_id FROM regras_categoria") == [("SUPERMERCADO", mercado)]
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos WHERE categoria_id = ?", mercado) == [(2,)]


def test_regra_formulario_preenchido_com_descricao(cliente: TestClient, conta: int) -> None:
    assert 'value="NETFLIX.COM"' in _get(cliente).text


def test_regra_invalida(cliente: TestClient, db: Path, conta: int) -> None:
    lid = _id(db, "NETFLIX.COM")
    r = cliente.post(f"/lancamentos/{lid}/regra", data={"padrao": "  ", "categoria_id": "1"})
    assert r.status_code == 400 and "padrão" in r.text
    r = cliente.post(f"/lancamentos/{lid}/regra", data={"padrao": "X", "categoria_id": "9999"})
    assert r.status_code == 400 and "Categoria não encontrada" in r.text
    assert _sql(db, "SELECT COUNT(*) FROM regras_categoria") == [(0,)]


def test_marcar_transferencia_remove_das_despesas_e_totais(cliente: TestClient, db: Path, conta: int) -> None:
    con = conectar(db)
    antes = despesas_do_mes(con, 2026, 10)
    con.close()
    lid = _id(db, "PIX TRANSF RR Supo22/10")
    r = cliente.post(f"/lancamentos/{lid}/tipo", data={"tipo": "transferencia", "voltar": VOLTAR})
    assert r.status_code == 200
    con = conectar(db)
    depois = despesas_do_mes(con, 2026, 10)
    con.close()
    assert antes - depois == 34354
    assert _total(r.text, "saidas") == "R$ 4.495,40"
    assert _total(r.text, "liquido") == "- R$ 395,40"


def test_marcar_pagamento_fatura_e_reverter(cliente: TestClient, db: Path, conta: int) -> None:
    lid = _id(db, "UBER *TRIP")
    cliente.post(f"/lancamentos/{lid}/tipo", data={"tipo": "pagamento_fatura"})
    assert _sql(db, "SELECT tipo FROM lancamentos WHERE id = ?", lid) == [("pagamento_fatura",)]
    cliente.post(f"/lancamentos/{lid}/tipo", data={"tipo": "auto"})
    assert _sql(db, "SELECT tipo FROM lancamentos WHERE id = ?", lid) == [("saida",)]
    assert cliente.post(f"/lancamentos/{lid}/tipo", data={"tipo": "xyz"}).status_code == 400
    assert cliente.post("/lancamentos/99999/tipo", data={"tipo": "auto"}).status_code == 404


def _novo(c: TestClient, conta: int, **extra: str) -> Any:
    dados = {"data": "2026-10-10", "descricao": "Lanche manual", "valor": "12,34", "sinal": "saida",
             "conta_id": str(conta), "compensado": "1", **extra}
    return c.post("/lancamentos/novo", data=dados)


def test_lancamento_manual_aparece_com_indicador(cliente: TestClient, db: Path, conta: int) -> None:
    r = _novo(cliente, conta, observacao="obs", voltar=VOLTAR)
    assert r.status_code == 200
    assert _linhas(r.text) == 19
    assert "Lançamento criado." in r.text
    assert 'class="origem manual">manual<' in r.text
    assert _sql(db, "SELECT valor_centavos, tipo, compensado, observacao FROM lancamentos"
                " WHERE descricao = 'Lanche manual'") == [(-1234, "saida", 1, "obs")]
    _novo(cliente, conta, descricao="Freela", valor="100,00", sinal="entrada")
    assert _sql(db, "SELECT valor_centavos, tipo FROM lancamentos WHERE descricao = 'Freela'") == [(10000, "entrada")]


def test_lancamento_manual_pendente(cliente: TestClient, db: Path, conta: int) -> None:
    r = cliente.post("/lancamentos/novo", data={
        "data": "2026-11-05", "descricao": "Boleto", "valor": "50,00", "sinal": "saida",
        "conta_id": str(conta), "voltar": VOLTAR})
    assert 'class="selo">pendente<' in r.text
    assert _total(r.text, "pendentes") == "- R$ 50,00"


def test_lancamento_manual_invalido(cliente: TestClient, conta: int) -> None:
    casos = [
        ({"valor": "abc"}, "Valor inválido"),
        ({"valor": "0,00"}, "O valor deve ser maior que zero"),
        ({"valor": "-5,00"}, "O valor deve ser maior que zero"),
        ({"data": "31/02/2026"}, "Data inválida"),
        ({"descricao": "   "}, "descrição"),
        ({"sinal": "x"}, "Escolha Entrada ou Saída"),
        ({"conta_id": "9999"}, "Conta não encontrada"),
        ({"categoria_id": "9999"}, "Categoria não encontrada"),
    ]
    for extra, msg in casos:
        r = _novo(cliente, conta, **extra)
        assert r.status_code == 400, extra
        assert msg in r.text, (extra, r.text[:300])
    assert _linhas(_get(cliente).text) == 18


def test_excluir_manual_e_recusar_importado(cliente: TestClient, db: Path, conta: int) -> None:
    _novo(cliente, conta)
    mid = _id(db, "Lanche manual")
    r = cliente.post(f"/lancamentos/{mid}/excluir")
    assert r.status_code == 200 and "Lançamento excluído." in r.text
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos WHERE id = ?", mid) == [(0,)]
    iid = _id(db, "NETFLIX.COM")
    r = cliente.post(f"/lancamentos/{iid}/excluir")
    assert r.status_code == 400
    assert "Lançamentos importados não podem ser excluídos aqui" in r.text
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos WHERE id = ?", iid) == [(1,)]
    assert cliente.post("/lancamentos/99999/excluir").status_code == 404


def _boleto(c: TestClient, conta: int) -> None:
    c.post("/lancamentos/novo", data={
        "data": "2026-11-05", "descricao": "Boleto", "valor": "50,00", "sinal": "saida",
        "conta_id": str(conta)})


def test_pagar_pendente_e_recusar_ja_compensado(cliente: TestClient, db: Path, conta: int) -> None:
    _boleto(cliente, conta)
    pid = _id(db, "Boleto")
    r = cliente.post(f"/lancamentos/{pid}/pagar", data={"valor": "48,90", "data": "2026-11-04"})
    assert r.status_code == 200 and "Pagamento registrado." in r.text
    assert _sql(db, "SELECT valor_centavos, compensado, data_compensacao FROM lancamentos WHERE id = ?",
                pid) == [(-4890, 1, "2026-11-04")]
    r = cliente.post(f"/lancamentos/{pid}/pagar", data={"valor": "1,00", "data": "2026-11-06"})
    assert r.status_code == 400 and "já está compensado" in r.text
    assert _sql(db, "SELECT valor_centavos FROM lancamentos WHERE id = ?", pid) == [(-4890,)]
    iid = _id(db, "NETFLIX.COM")
    assert cliente.post(f"/lancamentos/{iid}/pagar", data={"valor": "1,00", "data": "2026-11-06"}).status_code == 400


def test_pagar_validacoes(cliente: TestClient, db: Path, conta: int) -> None:
    _boleto(cliente, conta)
    pid = _id(db, "Boleto")
    assert cliente.post(f"/lancamentos/{pid}/pagar", data={"valor": "x", "data": "2026-11-04"}).status_code == 400
    assert cliente.post(f"/lancamentos/{pid}/pagar", data={"valor": "1,00", "data": "nada"}).status_code == 400
    assert cliente.post(f"/lancamentos/{pid}/pagar", data={"valor": "-1,00", "data": "2026-11-04"}).status_code == 400
    assert cliente.post("/lancamentos/99999/pagar", data={"valor": "1,00", "data": "2026-11-04"}).status_code == 404
    assert _sql(db, "SELECT compensado FROM lancamentos WHERE id = ?", pid) == [(0,)]


def test_paginacao_limites(cliente: TestClient, db: Path, conta: int) -> None:
    con = conectar(db)
    for i in range(33):  # 18 + 33 = 51 -> 2 páginas
        criar_lancamento_manual(con, conta_id=conta, data=date(2026, 10, 1), descricao=f"Extra {i:02d}",
                                valor_centavos=-100, compensado=True)
    con.close()
    p1 = _get(cliente)
    assert _linhas(p1.text) == 50 and "51 lançamentos" in p1.text and "Página 1 de 2" in p1.text
    p2 = _get(cliente, pagina="2")
    assert _linhas(p2.text) == 1 and "Página 2 de 2" in p2.text
    p3 = _get(cliente, pagina="3")
    assert p3.status_code == 200 and _linhas(p3.text) == 1 and "Página 2 de 2" in p3.text


def test_saldo_acumulado_ultima_linha_igual_saldo_atual(cliente: TestClient, conta: int) -> None:
    r = _get(cliente)
    saldos = re.findall(r'class="valor saldo">([^<]*)<', r.text)
    assert len(saldos) == 18
    assert saldos[-1].strip() == "R$ 1.409,14"
    assert saldos[0].strip() == "R$ 2.028,08"


def test_saldo_acumulado_nao_depende_do_filtro(cliente: TestClient, conta: int) -> None:
    r = _get(cliente, texto="PIX RECEBIDO")
    saldos = re.findall(r'class="valor saldo">([^<]*)<', r.text)
    assert saldos == ["R$ 2.273,68", "R$ 1.780,03"]


def test_pendente_sem_saldo_acumulado(cliente: TestClient, conta: int) -> None:
    _boleto(cliente, conta)
    r = _get(cliente, status="pendentes")
    assert _linhas(r.text) == 1
    assert 'class="valor saldo">—<' in r.text


def test_descricao_com_html_e_escapada(cliente: TestClient, conta: int) -> None:
    _novo(cliente, conta, descricao="<script>alert(1)</script>")
    r = _get(cliente)
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in r.text
