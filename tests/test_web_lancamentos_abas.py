"""Lançamentos com abas Todos / A pagar / Realizados, formulário único e ações de pendentes."""
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import conectar
from app.dominio.faturas import importar_fatura
from app.dominio.lancamentos import criar_lancamento_manual
from app.dominio.parcelas import criar_parcelamento
from app.dominio.recorrentes import criar_recorrente
from app.importacao.fatura.modelo import FaturaLida, ItemFatura
from app.main import create_app
from app.repo import contas
from app.repo import lancamentos as repo

HOJE = date(2026, 10, 23)
A_PAGAR = "aba=a-pagar&de=&ate=2027-03-31&status=pendentes"


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
        return contas.criar_conta(con, "Corrente Teste", "corrente")
    finally:
        con.close()


def _sql(db: Path, sql: str, *args: Any) -> list[Any]:
    con = conectar(db)
    try:
        return [tuple(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def _manual(db: Path, conta: int, data: str, descricao: str, valor: int,
            compensado: bool = False) -> int:
    con = conectar(db)
    try:
        return criar_lancamento_manual(con, conta_id=conta, data=date.fromisoformat(data),
                                       descricao=descricao, valor_centavos=valor,
                                       compensado=compensado)
    finally:
        con.close()


def _aba(c: TestClient, aba: str, **q: str) -> Any:
    return c.get("/lancamentos", params={"aba": aba, **q})


def _aba_ativa(html: str) -> str:
    m = re.search(r'<a href="/lancamentos\?aba=([a-z-]+)"[^>]*aria-current="true"', html)
    assert m, "nenhuma aba ativa"
    return m.group(1)


def test_aba_a_pagar_lista_pendentes_vencidos_e_futuros(cliente: TestClient, db: Path,
                                                        conta: int) -> None:
    _manual(db, conta, "2026-09-15", "Boleto antigo", -5000)
    _manual(db, conta, "2026-12-10", "Boleto futuro", -7000)
    _manual(db, conta, "2027-06-10", "Boleto distante", -9000)
    _manual(db, conta, "2026-10-10", "Mercado pago", -3000, compensado=True)
    r = _aba(cliente, "a-pagar")
    assert r.status_code == 200
    assert "Boleto antigo" in r.text and "Boleto futuro" in r.text
    assert "Boleto distante" not in r.text and "Mercado pago" not in r.text
    assert _aba_ativa(r.text) == "a-pagar"


def test_aba_realizados_so_compensados(cliente: TestClient, db: Path, conta: int) -> None:
    _manual(db, conta, "2026-10-05", "Boleto pendente", -5000)
    _manual(db, conta, "2026-10-10", "Mercado pago", -3000, compensado=True)
    r = _aba(cliente, "realizados")
    assert r.status_code == 200
    assert "Mercado pago" in r.text and "Boleto pendente" not in r.text
    assert _aba_ativa(r.text) == "realizados"


def test_aba_padrao_e_todos(cliente: TestClient, db: Path, conta: int) -> None:
    _manual(db, conta, "2026-10-05", "Boleto pendente", -5000)
    _manual(db, conta, "2026-10-10", "Mercado pago", -3000, compensado=True)
    r = cliente.get("/lancamentos")
    assert "Mercado pago" in r.text and "Boleto pendente" in r.text
    assert _aba_ativa(r.text) == "todos"


def test_aba_invalida_400(cliente: TestClient, conta: int) -> None:
    r = _aba(cliente, "xyz")
    assert r.status_code == 400
    assert "Aba inválida" in r.text and "Traceback" not in r.text


def test_parametro_explicito_prevalece_sobre_a_aba(cliente: TestClient, db: Path,
                                                    conta: int) -> None:
    _manual(db, conta, "2026-09-15", "Boleto antigo", -5000)
    _manual(db, conta, "2026-11-10", "Boleto futuro", -7000)
    r = _aba(cliente, "a-pagar", de="2026-11-01")
    assert "Boleto futuro" in r.text and "Boleto antigo" not in r.text


def test_aba_a_pagar_gera_recorrentes(cliente: TestClient, db: Path, conta: int) -> None:
    con = conectar(db)
    try:
        rid = criar_recorrente(con, nome="Luz", tipo="saida", dia=15,
                               valor_estimado_centavos=9000, conta_id=conta, categoria_id=None)
    finally:
        con.close()
    r = _aba(cliente, "a-pagar")
    assert r.status_code == 200
    datas = [d for (d,) in _sql(db, "SELECT data FROM lancamentos WHERE recorrente_id = ?"
                                    " ORDER BY data", rid)]
    assert datas == ["2026-10-15", "2026-11-15", "2026-12-15", "2027-01-15", "2027-02-15",
                     "2027-03-15"]
    assert r.text.count("Luz") >= 6


def test_aba_a_pagar_sem_contas_mostra_estado_vazio(cliente: TestClient, conta: int) -> None:
    r = _aba(cliente, "a-pagar")
    assert r.status_code == 200
    assert "Nenhuma conta a pagar nos próximos 6 meses." in r.text
    assert 'id="faixa-vencidas"' not in r.text


def test_a_pagar_mostra_contador_e_destaque_de_vencidas(cliente: TestClient, db: Path,
                                                        conta: int) -> None:
    v1 = _manual(db, conta, "2026-10-01", "Boleto vencido", -5000)
    v2 = _manual(db, conta, "2026-09-20", "Outro vencido", -2500)
    futuro = _manual(db, conta, "2026-11-10", "Boleto futuro", -7000)
    r = _aba(cliente, "a-pagar")
    m = re.search(r'id="faixa-vencidas"[^>]*>(.*?)</', r.text, re.S)
    assert m and re.sub(r"\s+", " ", m.group(1)).strip() == "2 vencidas · R$ 75,00"
    assert re.search(r'id="selo-vencidas"[^>]*>2<', r.text)
    for lid in (v1, v2):
        assert re.search(rf'<tr class="linha[^"]*linha-vencida[^"]*" id="lancamento-{lid}"', r.text)
    assert not re.search(rf'<tr class="linha[^"]*linha-vencida[^"]*" id="lancamento-{futuro}"',
                         r.text)


def test_selo_de_vencidas_aparece_em_todas_as_abas(cliente: TestClient, db: Path,
                                                   conta: int) -> None:
    _manual(db, conta, "2026-10-01", "Boleto vencido", -5000)
    r = _aba(cliente, "todos")
    assert re.search(r'id="selo-vencidas"[^>]*>1<', r.text)
    assert 'id="faixa-vencidas"' not in r.text


def test_novo_parcelado_pelo_formulario_unico(cliente: TestClient, db: Path, conta: int) -> None:
    r = cliente.post("/lancamentos/novo", data={
        "modo": "parcelado", "n_parcelas": "3", "data": "2026-11-05", "descricao": "TV",
        "valor": "300,00", "sinal": "saida", "conta_id": str(conta), "voltar": A_PAGAR})
    assert r.status_code == 200
    assert "Parcelamento criado: 3 parcelas." in r.text
    assert _sql(db, "SELECT data, valor_centavos, parcela_atual, parcela_total, compensado"
                    " FROM lancamentos WHERE descricao = 'TV' ORDER BY data") == [
        ("2026-11-05", -10000, 1, 3, 0), ("2026-12-05", -10000, 2, 3, 0),
        ("2027-01-05", -10000, 3, 3, 0)]
    assert _aba_ativa(r.text) == "a-pagar"


def test_novo_recorrente_pelo_formulario_unico(cliente: TestClient, db: Path, conta: int) -> None:
    r = cliente.post("/lancamentos/novo", data={
        "modo": "recorrente", "data": "2026-10-28", "descricao": "Internet", "valor": "99,90",
        "sinal": "saida", "conta_id": str(conta), "voltar": A_PAGAR})
    assert r.status_code == 200
    assert "Conta fixa cadastrada." in r.text
    assert _sql(db, "SELECT nome, tipo, dia, valor_estimado_centavos FROM recorrentes") == [
        ("Internet", "saida", 28, 9990)]
    assert "Internet" in r.text  # a aba A pagar gera e lista as pendentes da recorrente


def test_novo_a_vista_ja_pago(cliente: TestClient, db: Path, conta: int) -> None:
    r = cliente.post("/lancamentos/novo", data={
        "modo": "avista", "data": "2026-10-20", "descricao": "Padaria", "valor": "12,00",
        "sinal": "saida", "conta_id": str(conta), "ja_pago": "1"})
    assert r.status_code == 200 and "Lançamento criado." in r.text
    assert _sql(db, "SELECT compensado FROM lancamentos WHERE descricao = 'Padaria'") == [(1,)]


def test_novo_ja_pago_com_data_futura_e_recusado(cliente: TestClient, db: Path,
                                                 conta: int) -> None:
    r = cliente.post("/lancamentos/novo", data={
        "modo": "avista", "data": "2026-11-20", "descricao": "Padaria", "valor": "12,00",
        "sinal": "saida", "conta_id": str(conta), "ja_pago": "1"})
    assert r.status_code == 400 and "data de hoje ou anterior" in r.text
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos") == [(0,)]


def test_formulario_unico_tem_modos(cliente: TestClient, conta: int) -> None:
    html = cliente.get("/lancamentos").text
    for valor, rotulo in (("avista", "À vista"), ("parcelado", "Parcelado em N"),
                          ("recorrente", "Repetir todo mês")):
        assert f'name="modo" value="{valor}"' in html and rotulo in html
    assert 'name="n_parcelas"' in html and 'name="ja_pago"' in html
    assert 'name="compensado"' not in html


def _recorrente_gerada(cliente: TestClient, db: Path, conta: int) -> int:
    con = conectar(db)
    try:
        criar_recorrente(con, nome="Luz", tipo="saida", dia=15, valor_estimado_centavos=9000,
                         conta_id=conta, categoria_id=None)
    finally:
        con.close()
    _aba(cliente, "a-pagar")
    return int(_sql(db, "SELECT id FROM lancamentos WHERE data = '2026-10-15'")[0][0])


def test_pular_recorrente_na_aba(cliente: TestClient, db: Path, conta: int) -> None:
    lid = _recorrente_gerada(cliente, db, conta)
    html = _aba(cliente, "a-pagar").text
    assert f'action="/lancamentos/{lid}/pular"' in html and "Pular este mês" in html
    assert "Confirmar valor e pagar" in html
    r = cliente.post(f"/lancamentos/{lid}/pular", data={"voltar": A_PAGAR})
    assert r.status_code == 200 and "Mês pulado." in r.text
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos WHERE id = ?", lid) == [(0,)]
    assert _aba_ativa(r.text) == "a-pagar"
    assert cliente.post("/lancamentos/99999/pular", data={"voltar": A_PAGAR}).status_code == 404


def test_pular_lancamento_comum_e_recusado(cliente: TestClient, db: Path, conta: int) -> None:
    lid = _manual(db, conta, "2026-11-10", "Boleto", -5000)
    r = cliente.post(f"/lancamentos/{lid}/pular", data={"voltar": A_PAGAR})
    assert r.status_code == 400
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos WHERE id = ?", lid) == [(1,)]


def _parcelamento(db: Path, conta: int, n: int = 10, inicio: str = "2026-08-05") -> str:
    con = conectar(db)
    try:
        criar_parcelamento(con, descricao="Geladeira", valor_total_centavos=n * 10000,
                           n_parcelas=n, primeiro_vencimento=date.fromisoformat(inicio),
                           conta_id=conta, categoria_id=None)
    finally:
        con.close()
    return str(_sql(db, "SELECT grupo_parcela_uuid FROM lancamentos LIMIT 1")[0][0])


def test_excluir_grupo_volta_para_a_mesma_aba(cliente: TestClient, db: Path, conta: int) -> None:
    grupo = _parcelamento(db, conta, n=3, inicio="2026-11-05")
    html = _aba(cliente, "a-pagar").text
    assert f'action="/lancamentos/grupo/{grupo}/excluir"' in html
    assert f'action="/lancamentos/grupo/{grupo}/editar"' in html
    r = cliente.post(f"/lancamentos/grupo/{grupo}/excluir", data={"voltar": A_PAGAR})
    assert r.status_code == 200 and "Parcelas pendentes excluídas." in r.text
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos") == [(0,)]
    assert _aba_ativa(r.text) == "a-pagar"


def test_editar_grupo_pela_tela(cliente: TestClient, db: Path, conta: int) -> None:
    grupo = _parcelamento(db, conta, n=3, inicio="2026-11-05")
    r = cliente.post(f"/lancamentos/grupo/{grupo}/editar",
                     data={"valor_total": "600,00", "n_parcelas": "", "voltar": A_PAGAR})
    assert r.status_code == 200 and "Parcelamento atualizado." in r.text
    assert _sql(db, "SELECT SUM(valor_centavos) FROM lancamentos") == [(-60000,)]
    r = cliente.post("/lancamentos/grupo/nao-e-uuid/excluir", data={"voltar": A_PAGAR})
    assert r.status_code in (400, 404) and "Traceback" not in r.text


def test_linha_parcelada_mostra_etiqueta_3_de_10(cliente: TestClient, db: Path,
                                                 conta: int) -> None:
    _parcelamento(db, conta)
    html = cliente.get("/lancamentos").text  # período padrão: outubro (3ª parcela)
    assert "Geladeira (3/10)" in html
    assert "(4/10)" not in html


def test_fatura_na_aba_a_pagar_sem_botao_pagar(cliente: TestClient, db: Path, conta: int) -> None:
    f = FaturaLida(
        emissor="Banco Teste", bandeira="Visa", produto="Gold", final_cartao="1234",
        data_vencimento="2026-11-10", data_fechamento="2026-11-01",
        total_impresso_centavos=10000, ajustes_resumo_centavos=0,
        itens=[ItemFatura("2026-10-05", "LOJA FICTICIA", -10000, "compra", None, None, "1234",
                          None)])
    con = conectar(db)
    try:
        cartao = contas.criar_conta(con, "Cartao Teste", "cartao", final_numero="1234")
        res = importar_fatura(con, f, "h1", "f.pdf", cartao, HOJE, [None])
    finally:
        con.close()
    html = _aba(cliente, "a-pagar").text
    assert f'id="lancamento-{res.lancamento_id}"' in html
    assert f'action="/lancamentos/{res.lancamento_id}/pagar"' not in html


def test_prevista_de_fatura_sem_acoes_de_grupo(cliente: TestClient, db: Path, conta: int) -> None:
    f = FaturaLida(
        emissor="Banco Teste", bandeira="Visa", produto="Gold", final_cartao="1234",
        data_vencimento="2026-11-10", data_fechamento="2026-11-01",
        total_impresso_centavos=10000, ajustes_resumo_centavos=0,
        itens=[ItemFatura("2026-10-05", "LOJA FICTICIA", -10000, "compra", None, None, "1234",
                          None)])
    con = conectar(db)
    try:
        cartao = contas.criar_conta(con, "Cartao Teste", "cartao", final_numero="1234")
        importar_fatura(con, f, "h1", "f.pdf", cartao, HOJE, [None])
        prevista = repo.inserir_prevista(
            con, conta_id=cartao, data="2026-10-15", descricao="SOFA", valor_centavos=-3000,
            tipo="saida", categoria_id=None, grupo_parcela_uuid="11111111-1111-1111-1111-111111111111",
            parcela_atual=2, parcela_total=3)
        con.commit()
    finally:
        con.close()
    html = _aba(cliente, "a-pagar").text
    assert f'id="lancamento-{prevista}"' in html
    assert "/lancamentos/grupo/11111111-1111-1111-1111-111111111111/" not in html


def test_confirmar_valor_real_de_recorrente_atualiza_futuros(cliente: TestClient, db: Path,
                                                             conta: int) -> None:
    _recorrente_gerada(cliente, db, conta)
    primeiro, = _sql(db, "SELECT id FROM lancamentos WHERE data = '2026-10-15'")[0]
    antes = _sql(db, "SELECT COUNT(*) FROM lancamentos WHERE estimado = 1")[0][0]
    r = cliente.post(f"/lancamentos/{primeiro}/pagar",
                     data={"valor": "1.600,00", "data": "2026-10-23", "voltar": A_PAGAR})
    assert r.status_code == 200
    assert "Pagamento registrado." in r.text
    assert _sql(db, "SELECT estimado, compensado FROM lancamentos WHERE id = ?", primeiro) == [(0, 1)]
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos WHERE estimado = 1")[0][0] == antes - 1
    assert _sql(db, "SELECT COUNT(DISTINCT valor_centavos) FROM lancamentos") == [(1,)]


def test_pagamento_duplo_e_recusado(cliente: TestClient, db: Path, conta: int) -> None:
    pid = _manual(db, conta, "2026-10-20", "CONTA X", -5000)
    ok = cliente.post(f"/lancamentos/{pid}/pagar", data={"valor": "55,00", "data": "2026-10-23"})
    assert ok.status_code == 200
    r = cliente.post(f"/lancamentos/{pid}/pagar", data={"valor": "99,00", "data": "2026-10-24"})
    assert r.status_code == 400 and "já está compensado" in r.text
    assert _sql(db, "SELECT valor_centavos FROM lancamentos") == [(-5500,)]


def _importar_linhas(db: Path, conta_id: int, linhas: list[tuple[str, str, int]]) -> None:
    from app.importacao.base import LinhaImportada, ResultadoLeitura
    from app.importacao.servico import importar

    class _P:
        def ler(self, conteudo: bytes) -> ResultadoLeitura:
            return ResultadoLeitura(linhas=[LinhaImportada(*ln) for ln in linhas])

    con = conectar(db)
    try:
        importar(con, conta_id, "x.csv", repr(linhas).encode(), _P(), HOJE)
    finally:
        con.close()


def _com_correspondencia(cliente: TestClient, db: Path, conta: int) -> tuple[str, str]:
    _importar_linhas(db, conta, [("2026-10-05", "PIX ALUGUEL APTO", -150000)])
    con = conectar(db)
    try:
        criar_recorrente(con, nome="Aluguel", tipo="saida", dia=10,
                         valor_estimado_centavos=150000, conta_id=conta, categoria_id=None)
    finally:
        con.close()
    html = _aba(cliente, "a-pagar").text
    m = re.search(r'name="pendente_id" value="(\d+)">\s*<input type="hidden" name="lancamento_id" '
                  r'value="(\d+)"', html)
    assert m
    return m.group(1), m.group(2)


def test_correspondencias_so_na_aba_a_pagar(cliente: TestClient, db: Path, conta: int) -> None:
    _com_correspondencia(cliente, db, conta)
    html = _aba(cliente, "a-pagar").text
    assert 'id="correspondencias"' in html and "Possíveis correspondências no extrato" in html
    assert "PIX ALUGUEL APTO" in html.split('id="correspondencias"')[1]
    for aba in ("todos", "realizados"):
        assert 'id="correspondencias"' not in _aba(cliente, aba).text


def test_sem_correspondencias_painel_oculto(cliente: TestClient, db: Path, conta: int) -> None:
    _manual(db, conta, "2026-11-10", "Boleto", -5000)
    assert 'id="correspondencias"' not in _aba(cliente, "a-pagar").text


def test_confirmar_correspondencia_limpa_pendente(cliente: TestClient, db: Path,
                                                  conta: int) -> None:
    pend, linha = _com_correspondencia(cliente, db, conta)
    r = cliente.post("/lancamentos/casamento/confirmar",
                     data={"pendente_id": pend, "lancamento_id": linha, "voltar": A_PAGAR})
    assert r.status_code == 200 and "Correspondência confirmada." in r.text
    assert _aba_ativa(r.text) == "a-pagar"
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos WHERE valor_centavos = -150000"
                " AND data <= '2026-10-31'") == [(1,)]
    assert 'id="correspondencias"' not in r.text


def test_recusar_correspondencia_some_do_painel_e_nao_altera_lancamentos(
        cliente: TestClient, db: Path, conta: int) -> None:
    pend, linha = _com_correspondencia(cliente, db, conta)
    antes = _sql(db, "SELECT COUNT(*) FROM lancamentos")
    r = cliente.post("/lancamentos/casamento/recusar",
                     data={"pendente_id": pend, "lancamento_id": linha, "voltar": A_PAGAR})
    assert r.status_code == 200 and "Correspondência recusada." in r.text
    assert _aba_ativa(r.text) == "a-pagar"
    assert 'id="correspondencias"' not in r.text
    assert 'id="correspondencias"' not in _aba(cliente, "a-pagar").text  # continua recusada
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos") == antes


def test_recusar_correspondencia_invalida(cliente: TestClient, db: Path, conta: int) -> None:
    r = cliente.post("/lancamentos/casamento/recusar",
                     data={"pendente_id": "x", "lancamento_id": "1", "voltar": A_PAGAR})
    assert r.status_code == 400 and "inválido" in r.text and "Traceback" not in r.text


def test_painel_tem_botoes_confirmar_e_recusar(cliente: TestClient, db: Path, conta: int) -> None:
    _com_correspondencia(cliente, db, conta)
    painel = _aba(cliente, "a-pagar").text.split('id="correspondencias"')[1]
    assert "Confirmar" in painel and "Recusar" in painel


def test_confirmar_correspondencia_invalida(cliente: TestClient, db: Path, conta: int) -> None:
    antes = _sql(db, "SELECT COUNT(*) FROM lancamentos")
    r = cliente.post("/lancamentos/casamento/confirmar",
                     data={"pendente_id": "x", "lancamento_id": "1", "voltar": A_PAGAR})
    assert r.status_code == 400 and "inválido" in r.text and "Traceback" not in r.text
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos") == antes


def test_fatura_na_aba_a_pagar_oferece_casar_pagamento(cliente: TestClient, db: Path,
                                                       conta: int) -> None:
    test_fatura_na_aba_a_pagar_sem_botao_pagar(cliente, db, conta)
    con = conectar(db)
    try:
        repo.inserir_manual(con, conta_id=conta, data="2026-11-09", descricao="PAGTO FATURA CARTAO",
                            valor_centavos=-10000, tipo="pagamento_fatura", categoria_id=None,
                            compensado=True, observacao=None)
        con.commit()
    finally:
        con.close()
    assert "Casar pagamento" in _aba(cliente, "a-pagar").text


def test_selects_de_categoria_do_novo_lancamento_com_busca(cliente: TestClient,
                                                           conta: int) -> None:
    html = cliente.get("/lancamentos").text
    for ident in ("n-cat-saida", "n-cat-entrada"):
        tag = re.search(rf'<select id="{ident}"[^>]*>', html)
        assert tag and "data-busca" in tag.group(0), ident


# --- parcelas previstas pela fatura (não órfãs) na aba A pagar ---------------------------

def _previstas(db: Path) -> tuple[int, int]:
    """Cartão sem fatura importada: duas previstas não órfãs (uma vencida, uma futura)."""
    con = conectar(db)
    try:
        cartao = contas.criar_conta(con, "Cartao Prev", "cartao", final_numero="9999")
        grupo = "22222222-2222-2222-2222-222222222222"
        vencida = repo.inserir_prevista(
            con, conta_id=cartao, data="2026-10-01", descricao="SOFA", valor_centavos=-3000,
            tipo="saida", categoria_id=None, grupo_parcela_uuid=grupo, parcela_atual=2,
            parcela_total=4)
        futura = repo.inserir_prevista(
            con, conta_id=cartao, data="2026-12-05", descricao="SOFA", valor_centavos=-3000,
            tipo="saida", categoria_id=None, grupo_parcela_uuid=grupo, parcela_atual=3,
            parcela_total=4)
        con.commit()
        return vencida, futura
    finally:
        con.close()


def test_previstas_aparecem_so_na_aba_a_pagar(cliente: TestClient, db: Path) -> None:
    vencida, futura = _previstas(db)
    html = _aba(cliente, "a-pagar").text
    for lid in (vencida, futura):
        assert f'id="lancamento-{lid}"' in html
    assert "/lancamentos/grupo/22222222-2222-2222-2222-222222222222/" not in html
    for aba in ("todos", "realizados"):
        txt = _aba(cliente, aba, de="", ate="").text
        for lid in (vencida, futura):
            assert f'id="lancamento-{lid}"' not in txt


def test_selo_e_faixa_batem_com_as_vencidas_listadas(cliente: TestClient, db: Path,
                                                     conta: int) -> None:
    vencida, _ = _previstas(db)
    _manual(db, conta, "2026-10-02", "Boleto vencido", -5000)
    html = _aba(cliente, "a-pagar").text
    linhas = re.findall(r'<tr class="linha[^"]*linha-vencida[^"]*" id="lancamento-(\d+)"', html)
    assert str(vencida) in linhas and len(linhas) == 2
    assert re.search(r'id="selo-vencidas"[^>]*>2<', html)
    assert "2 vencidas" in re.sub(r"\s+", " ", html)


# --- ações de grupo, formulário e escape -------------------------------------------------

GRUPO_RUIM = "nao-e-uuid"


def test_grupo_invalido_da_erro_em_portugues_e_nao_altera(cliente: TestClient, db: Path,
                                                          conta: int) -> None:
    _parcelamento(db, conta, n=3, inicio="2026-11-05")
    antes = _sql(db, "SELECT id, valor_centavos FROM lancamentos ORDER BY id")
    for acao, dados in (("excluir", {}), ("editar", {"valor_total": "10,00", "n_parcelas": ""})):
        r = cliente.post(f"/lancamentos/grupo/{GRUPO_RUIM}/{acao}", data={**dados, "voltar": A_PAGAR})
        assert r.status_code in (400, 404) and "Traceback" not in r.text
        assert re.search(r'class="alerta[^"]*erro', r.text)
    assert _sql(db, "SELECT id, valor_centavos FROM lancamentos ORDER BY id") == antes


def test_editar_grupo_com_valor_invalido_nao_altera(cliente: TestClient, db: Path,
                                                    conta: int) -> None:
    grupo = _parcelamento(db, conta, n=3, inicio="2026-11-05")
    antes = _sql(db, "SELECT id, valor_centavos FROM lancamentos ORDER BY id")
    r = cliente.post(f"/lancamentos/grupo/{grupo}/editar",
                     data={"valor_total": "abc", "n_parcelas": "", "voltar": A_PAGAR})
    assert r.status_code == 400 and "Valor inválido" in r.text
    assert _sql(db, "SELECT id, valor_centavos FROM lancamentos ORDER BY id") == antes


@pytest.mark.parametrize("valor,n", [("0,02", "3"), ("100,00", "0"), ("100,00", "abc"),
                                     ("100,00", "1000")])
def test_novo_parcelado_invalido_nao_grava(cliente: TestClient, db: Path, conta: int,
                                           valor: str, n: str) -> None:
    r = cliente.post("/lancamentos/novo", data={
        "modo": "parcelado", "data": "2026-11-05", "descricao": "Geladeira", "valor": valor,
        "sinal": "saida", "conta_id": str(conta), "n_parcelas": n, "voltar": A_PAGAR})
    assert r.status_code == 400 and "Traceback" not in r.text
    assert re.search(r'class="alerta[^"]*erro', r.text)
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos") == [(0,)]


def test_descricao_pendente_e_escapada_na_aba(cliente: TestClient, db: Path, conta: int) -> None:
    _manual(db, conta, "2026-11-10", "<script>alert(1)</script>", -5000)
    html = _aba(cliente, "a-pagar").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_pagar_com_linha_no_extrato_avisa(cliente: TestClient, db: Path, conta: int) -> None:
    _importar_linhas(db, conta, [("2026-10-20", "DEB AUT INTERNET FIBRA", -10000)])
    pid = _manual(db, conta, "2026-10-20", "Internet", -10000)
    r = cliente.post(f"/lancamentos/{pid}/pagar",
                     data={"valor": "", "data": "2026-10-20", "voltar": A_PAGAR})
    assert r.status_code == 200 and "Pagamento registrado." in r.text
    assert "Existe uma linha no extrato que parece ser este pagamento" in r.text
    outro = _manual(db, conta, "2026-10-21", "Academia", -9000)
    r = cliente.post(f"/lancamentos/{outro}/pagar",
                     data={"valor": "", "data": "2026-10-21", "voltar": A_PAGAR})
    assert "Existe uma linha no extrato" not in r.text
