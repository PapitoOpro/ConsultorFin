import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import conectar
from app.dominio.recorrentes import criar_recorrente
from app.main import create_app
from app.repo import contas
from app.repo import lancamentos as repo

HOJE = date(2026, 10, 23)
URL = "/contas-a-pagar"
MSG_VALOR = "Valor inválido. Use o formato 1.234,56."
MSG_ALTO = "Valor muito alto. O máximo é R$ 10.000.000.000,00."
MSG_ZERO = "O valor deve ser maior que zero."
SELO = '<span class="selo">estimado</span>'


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
        return contas.criar_conta(con, "Itaú", "corrente", saldo_inicial_centavos=0,
                                  data_saldo_inicial="2026-01-01")
    finally:
        con.close()


def _sql(db: Path, sql: str, *args: Any) -> list[Any]:
    con = conectar(db)
    try:
        return [tuple(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def _categoria(db: Path) -> int:
    return int(_sql(db, "SELECT id FROM categorias WHERE nome = 'Moradia'")[0][0])


def _pendente(db: Path, conta_id: int, data: str, descricao: str, valor: int = -5000) -> int:
    con = conectar(db)
    try:
        i = repo.inserir_manual(con, conta_id=conta_id, data=data, descricao=descricao,
                                valor_centavos=valor, tipo="saida" if valor < 0 else "entrada",
                                categoria_id=None, compensado=False, observacao=None)
        con.commit()
        return i
    finally:
        con.close()


def _secao(html: str, ident: str) -> str:
    """Conteúdo da seção (ou da faixa de vazio, que substitui a seção sem itens)."""
    m = (re.search(rf'<section id="{ident}"[^>]*>(.*?)</section>', html, re.S)
         or re.search(rf'<p id="{ident}"[^>]*>(.*?)</p>', html, re.S))
    assert m, f"seção {ident} ausente"
    return m.group(1)


def _secao_opcional(html: str, ident: str) -> str:
    """Seções que só aparecem quando têm itens: vazio se ausente."""
    m = re.search(rf'<section id="{ident}"[^>]*>(.*?)</section>', html, re.S)
    return m.group(1) if m else ""


def _parcelamento(cliente: TestClient, conta: int, **extra: str) -> Any:
    dados = {"descricao": "Geladeira", "valor_total": "100,00", "n_parcelas": "3",
             "primeiro_vencimento": "2026-11-05", "conta_id": str(conta), "categoria_id": ""}
    dados.update(extra)
    return cliente.post(f"{URL}/parcelamento", data=dados)


def _recorrente(cliente: TestClient, conta: int, **extra: str) -> Any:
    dados = {"nome": "Aluguel", "tipo": "saida", "dia": "10", "valor_estimado": "1.500,00",
             "conta_id": str(conta), "categoria_id": ""}
    dados.update(extra)
    return cliente.post(f"{URL}/recorrente", data=dados)


def _n_lanc(db: Path) -> int:
    return int(_sql(db, "SELECT COUNT(*) FROM lancamentos")[0][0])


def _ids_rec(db: Path) -> list[int]:
    return [int(r[0]) for r in _sql(
        db, "SELECT id FROM lancamentos WHERE recorrente_id IS NOT NULL ORDER BY data")]


# --- página / seções ---------------------------------------------------------------------

def test_pagina_vazia_tem_todas_as_secoes(cliente: TestClient) -> None:
    r = cliente.get(URL)
    assert r.status_code == 200
    for ident in ("vencidas", "a-vencer", "proximos", "parcelamentos", "recorrentes", "contas"):
        _secao(r.text, ident)
    assert "Nenhuma conta vencida." in _secao(r.text, "vencidas")


def test_vencida_a_vencer_e_proximos_pelas_datas(cliente: TestClient, db: Path, conta: int) -> None:
    _pendente(db, conta, "2026-10-20", "CONTA VENCIDA")
    _pendente(db, conta, "2026-10-30", "CONTA SETE DIAS")      # hoje + 7
    _pendente(db, conta, "2026-10-31", "CONTA OITO DIAS")      # hoje + 8
    html = cliente.get(URL).text
    venc, avenc, prox = (_secao(html, s) for s in ("vencidas", "a-vencer", "proximos"))
    assert "CONTA VENCIDA" in venc and "CONTA VENCIDA" not in avenc + prox
    assert "CONTA SETE DIAS" in avenc and "CONTA SETE DIAS" not in venc + prox
    assert "CONTA OITO DIAS" in prox and "CONTA OITO DIAS" not in venc + avenc
    assert "1 vencida" in html and "R$ 50,00" in html.split('class="resumo-linha"')[1][:300]
    assert "20/10/2026" in venc and "- R$ 50,00" in venc


# --- parcelamento ------------------------------------------------------------------------

def test_parcelamento_100_em_3x(cliente: TestClient, db: Path, conta: int) -> None:
    r = _parcelamento(cliente, conta, categoria_id=str(_categoria(db)))
    assert r.status_code == 200
    assert "Parcelamento criado: 3 parcelas." in r.text
    assert _sql(db, "SELECT parcela_atual, parcela_total, valor_centavos, data, compensado"
                " FROM lancamentos ORDER BY parcela_atual") == [
        (1, 3, -3333, "2026-11-05", 0), (2, 3, -3333, "2026-12-05", 0),
        (3, 3, -3334, "2027-01-05", 0)]
    html = cliente.get(URL).text
    prox = _secao(html, "proximos")
    for rotulo in ("1/3", "2/3", "3/3"):
        assert f"Geladeira ({rotulo})" in prox
    assert prox.count("- R$ 33,33") >= 2 and prox.count("- R$ 33,34") >= 1
    grupo = _secao(html, "parcelamentos")
    assert "Geladeira" in grupo and "0/3 pagas" in grupo and "3 pendentes" in grupo
    assert "Total R$ 100,00" in grupo and "restante R$ 100,00" in grupo


def test_parcelamento_resumo_apos_pagar_uma(cliente: TestClient, db: Path, conta: int) -> None:
    _parcelamento(cliente, conta)
    pid = int(_sql(db, "SELECT id FROM lancamentos WHERE parcela_atual = 1")[0][0])
    r = cliente.post(f"{URL}/{pid}/pagar", data={"valor": "", "data": "2026-10-23"})
    assert r.status_code == 200 and "Pagamento registrado." in r.text
    grupo = _secao(r.text, "parcelamentos")
    assert "1/3 pagas" in grupo and "2 pendentes" in grupo and "restante R$ 66,67" in grupo


@pytest.mark.parametrize("valor,msg", [
    ("0", MSG_ZERO),
    ("-10,00", MSG_ZERO),
    ("abc", MSG_VALOR),
    ("1e30", MSG_VALOR),
    (str(10**13), MSG_ALTO),
    ("", MSG_VALOR),
])
def test_parcelamento_valor_invalido(cliente: TestClient, db: Path, conta: int,
                                     valor: str, msg: str) -> None:
    r = _parcelamento(cliente, conta, valor_total=valor)
    assert r.status_code == 400 and msg in r.text
    assert _n_lanc(db) == 0


@pytest.mark.parametrize("campo,valor,msg", [
    ("n_parcelas", "0", "O número de parcelas deve estar entre 1 e 120."),
    ("n_parcelas", "121", "O número de parcelas deve estar entre 1 e 120."),
    ("n_parcelas", "x", "O número de parcelas deve estar entre 1 e 120."),
    ("primeiro_vencimento", "2026-02-31", "Data inválida."),
    ("descricao", "   ", "Informe a descrição."),
    ("conta_id", "999", "Conta não encontrada."),
    ("conta_id", "abc", "Conta inválida."),
    ("categoria_id", "999", "Categoria não encontrada."),
])
def test_parcelamento_campos_invalidos(cliente: TestClient, db: Path, conta: int,
                                       campo: str, valor: str, msg: str) -> None:
    r = _parcelamento(cliente, conta, **{campo: valor})
    assert r.status_code == 400 and msg in r.text
    assert _n_lanc(db) == 0


def test_descricao_com_xss_e_escapada(cliente: TestClient, db: Path, conta: int) -> None:
    payload = "<script>alert(1)</script>"
    _parcelamento(cliente, conta, descricao=payload)
    html = cliente.get(URL).text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert payload not in html


def test_editar_e_excluir_grupo_so_pendentes(cliente: TestClient, db: Path, conta: int) -> None:
    _parcelamento(cliente, conta)
    grupo = str(_sql(db, "SELECT grupo_parcela_uuid FROM lancamentos LIMIT 1")[0][0])
    pid = int(_sql(db, "SELECT id FROM lancamentos WHERE parcela_atual = 1")[0][0])
    cliente.post(f"{URL}/{pid}/pagar", data={"valor": "", "data": "2026-10-23"})
    r = cliente.post(f"{URL}/grupo/{grupo}/editar", data={"valor_total": "60,00", "n_parcelas": "2"})
    assert r.status_code == 200 and "Parcelamento atualizado." in r.text
    assert _sql(db, "SELECT parcela_atual, parcela_total, valor_centavos, compensado FROM lancamentos"
                " ORDER BY parcela_atual") == [
        (1, 3, -3333, 1), (2, 3, -3000, 0), (3, 3, -3000, 0)]
    r = cliente.post(f"{URL}/grupo/{grupo}/excluir")
    assert r.status_code == 200 and "Parcelas pendentes excluídas." in r.text
    assert _sql(db, "SELECT parcela_atual, compensado FROM lancamentos") == [(1, 1)]


def test_editar_grupo_valor_invalido_nao_altera(cliente: TestClient, db: Path, conta: int) -> None:
    _parcelamento(cliente, conta)
    grupo = str(_sql(db, "SELECT grupo_parcela_uuid FROM lancamentos LIMIT 1")[0][0])
    antes = _sql(db, "SELECT valor_centavos FROM lancamentos ORDER BY id")
    r = cliente.post(f"{URL}/grupo/{grupo}/editar", data={"valor_total": "0", "n_parcelas": ""})
    assert r.status_code == 400 and MSG_ZERO in r.text
    assert _sql(db, "SELECT valor_centavos FROM lancamentos ORDER BY id") == antes


def test_grupo_inexistente_ou_invalido(cliente: TestClient) -> None:
    inexistente = "00000000-0000-4000-8000-000000000000"
    for acao in ("editar", "excluir"):
        r = cliente.post(f"{URL}/grupo/{inexistente}/{acao}", data={})
        assert r.status_code == 404 and "Parcelamento não encontrado." in r.text
        r = cliente.post(f"{URL}/grupo/nao-e-uuid/{acao}", data={})
        assert r.status_code == 400 and "Parcelamento inválido." in r.text


# --- recorrentes -------------------------------------------------------------------------

def test_conta_fixa_gera_pendentes_estimados(cliente: TestClient, db: Path, conta: int) -> None:
    r = _recorrente(cliente, conta)
    assert r.status_code == 200 and "Conta fixa cadastrada." in r.text
    assert _sql(db, "SELECT data, valor_centavos, estimado, compensado FROM lancamentos"
                " ORDER BY data") == [
        ("2026-10-10", -150000, 1, 0), ("2026-11-10", -150000, 1, 0),
        ("2026-12-10", -150000, 1, 0), ("2027-01-10", -150000, 1, 0),
        ("2027-02-10", -150000, 1, 0), ("2027-03-10", -150000, 1, 0)]
    html = cliente.get(URL).text
    assert html.count(SELO) == 6
    assert "10/10/2026" in _secao(html, "vencidas")
    assert _secao(html, "vencidas").count(SELO) == 1
    rec = _secao(html, "recorrentes")
    assert "Aluguel" in rec and "R$ 1.500,00" in rec and "ativa" in rec


def test_abrir_a_tela_e_idempotente(cliente: TestClient, db: Path, conta: int) -> None:
    _recorrente(cliente, conta)
    for _ in range(3):
        assert cliente.get(URL).status_code == 200
    assert _n_lanc(db) == 6


@pytest.mark.parametrize("horizonte", [3, 6, 12])
def test_horizonte_gera_meses(cliente: TestClient, db: Path, conta: int, horizonte: int) -> None:
    con = conectar(db)
    try:
        criar_recorrente(con, nome="Luz", tipo="saida", dia=15, valor_estimado_centavos=9000,
                         conta_id=conta, categoria_id=None)
    finally:
        con.close()
    r = cliente.get(f"{URL}?horizonte={horizonte}")
    assert r.status_code == 200
    assert _n_lanc(db) == horizonte
    assert f'<option value="{horizonte}" selected>' in r.text


def test_horizonte_padrao_e_6(cliente: TestClient) -> None:
    assert '<option value="6" selected>' in cliente.get(URL).text


@pytest.mark.parametrize("valor", ["5", "0", "abc", "", "-3", "6.0", "٦"])
def test_horizonte_invalido_400(cliente: TestClient, valor: str) -> None:
    r = cliente.get(URL, params={"horizonte": valor})
    assert r.status_code == 400 and "Horizonte inválido. Use 3, 6 ou 12 meses." in r.text


@pytest.mark.parametrize("campo,valor,msg", [
    ("dia", "32", "O dia deve estar entre 1 e 31."),
    ("dia", "0", "O dia deve estar entre 1 e 31."),
    ("dia", "x", "O dia deve estar entre 1 e 31."),
    ("tipo", "outro", "Tipo inválido."),
    ("nome", " ", "Informe o nome."),
    ("valor_estimado", "0", MSG_ZERO),
    ("valor_estimado", "-1,00", MSG_ZERO),
    ("valor_estimado", "texto", MSG_VALOR),
    ("valor_estimado", "1e30", MSG_VALOR),
    ("valor_estimado", str(10**13), MSG_ALTO),
    ("conta_id", "999", "Conta não encontrada."),
])
def test_recorrente_invalida_nao_cria_nada(cliente: TestClient, db: Path, conta: int,
                                           campo: str, valor: str, msg: str) -> None:
    r = _recorrente(cliente, conta, **{campo: valor})
    assert r.status_code == 400 and msg in r.text
    assert _n_lanc(db) == 0
    assert _sql(db, "SELECT COUNT(*) FROM recorrentes") == [(0,)]


def test_confirmar_valor_real_remove_estimado_e_atualiza_futuros(
        cliente: TestClient, db: Path, conta: int) -> None:
    _recorrente(cliente, conta)
    primeiro = _ids_rec(db)[0]
    r = cliente.post(f"{URL}/{primeiro}/pagar", data={"valor": "1.600,00", "data": "2026-10-23"})
    assert r.status_code == 200
    assert "Valor real confirmado e pagamento registrado." in r.text
    assert _sql(db, "SELECT data, valor_centavos, estimado, compensado FROM lancamentos"
                " ORDER BY data") == [("2026-10-10", -160000, 0, 1)] + [
        (d, -160000, 1, 0)
        for d in ("2026-11-10", "2026-12-10", "2027-01-10", "2027-02-10", "2027-03-10")]
    assert r.text.count(SELO) == 5
    assert "Nenhuma conta vencida." in _secao(r.text, "vencidas")
    assert "R$ 1.600,00" in _secao(r.text, "recorrentes")


def test_pagamento_duplo_e_recusado(cliente: TestClient, db: Path, conta: int) -> None:
    pid = _pendente(db, conta, "2026-10-20", "CONTA X", -5000)
    ok = cliente.post(f"{URL}/{pid}/pagar", data={"valor": "55,00", "data": "2026-10-23"})
    assert ok.status_code == 200
    r = cliente.post(f"{URL}/{pid}/pagar", data={"valor": "99,00", "data": "2026-10-24"})
    assert r.status_code == 400
    assert "Este lançamento já está pago. Nada foi alterado." in r.text
    assert _sql(db, "SELECT valor_centavos, data_compensacao FROM lancamentos") == [
        (-5500, "2026-10-23")]


def test_pagamento_duplo_de_recorrente_e_recusado(cliente: TestClient, db: Path, conta: int) -> None:
    _recorrente(cliente, conta)
    primeiro = _ids_rec(db)[0]
    cliente.post(f"{URL}/{primeiro}/pagar", data={"valor": "1.600,00", "data": "2026-10-23"})
    r = cliente.post(f"{URL}/{primeiro}/pagar", data={"valor": "2.000,00", "data": "2026-10-24"})
    assert r.status_code == 400 and "já está pago" in r.text
    assert _sql(db, "SELECT valor_centavos FROM lancamentos WHERE id = ?", primeiro) == [(-160000,)]


@pytest.mark.parametrize("valor,data,msg", [
    ("0", "2026-10-23", MSG_ZERO),
    ("-5,00", "2026-10-23", MSG_ZERO),
    ("abc", "2026-10-23", MSG_VALOR),
    ("1e30", "2026-10-23", MSG_VALOR),
    (str(10**13), "2026-10-23", MSG_ALTO),
    ("10,00", "", "Data inválida."),
    ("10,00", "31/02/2026", "Data inválida."),
])
def test_pagar_entradas_invalidas_nao_alteram(cliente: TestClient, db: Path, conta: int,
                                              valor: str, data: str, msg: str) -> None:
    pid = _pendente(db, conta, "2026-10-20", "CONTA X", -5000)
    r = cliente.post(f"{URL}/{pid}/pagar", data={"valor": valor, "data": data})
    assert r.status_code == 400 and msg in r.text
    assert _sql(db, "SELECT valor_centavos, compensado FROM lancamentos") == [(-5000, 0)]


def test_ids_desconhecidos_ou_invalidos(cliente: TestClient) -> None:
    r = cliente.post(f"{URL}/99999/pagar", data={"valor": "1,00", "data": "2026-10-23"})
    assert r.status_code == 404 and "Lançamento não encontrado." in r.text
    r = cliente.post(f"{URL}/abc/pagar", data={"valor": "1,00", "data": "2026-10-23"})
    assert r.status_code == 400 and "Identificador inválido." in r.text
    r = cliente.post(f"{URL}/99999/pular")
    assert r.status_code == 404 and "Lançamento não encontrado." in r.text
    r = cliente.post(f"{URL}/recorrente/99999/desativar")
    assert r.status_code == 404 and "Conta fixa não encontrada." in r.text
    r = cliente.post(f"{URL}/recorrente/%D9%A1/desativar")  # dígito árabe-índico
    assert r.status_code == 400 and "Identificador inválido." in r.text


def test_pular_mes_nao_recria(cliente: TestClient, db: Path, conta: int) -> None:
    _recorrente(cliente, conta)
    novembro = int(_sql(db, "SELECT id FROM lancamentos WHERE data = '2026-11-10'")[0][0])
    r = cliente.post(f"{URL}/{novembro}/pular")
    assert r.status_code == 200 and "Mês pulado." in r.text
    cliente.get(URL)
    assert _n_lanc(db) == 5
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos WHERE data = '2026-11-10'") == [(0,)]


def test_pular_lancamento_sem_recorrente_recusado(cliente: TestClient, db: Path, conta: int) -> None:
    pid = _pendente(db, conta, "2026-10-20", "AVULSA")
    r = cliente.post(f"{URL}/{pid}/pular")
    assert r.status_code == 400 and "não pertence a um recorrente" in r.text
    assert _n_lanc(db) == 1


def test_desativar_mantem_vencida_e_remove_futuras(cliente: TestClient, db: Path, conta: int) -> None:
    _recorrente(cliente, conta)
    rid = int(_sql(db, "SELECT id FROM recorrentes")[0][0])
    r = cliente.post(f"{URL}/recorrente/{rid}/desativar")
    assert r.status_code == 200 and "Conta fixa desativada." in r.text
    assert _sql(db, "SELECT data FROM lancamentos") == [("2026-10-10",)]
    assert "Aluguel" in _secao(r.text, "vencidas")
    assert "inativa" in _secao(r.text, "recorrentes")
    cliente.get(URL)
    assert _n_lanc(db) == 1


def test_receita_recorrente_aparece_e_confirma(cliente: TestClient, db: Path, conta: int) -> None:
    r = _recorrente(cliente, conta, nome="Salário", tipo="entrada", dia="5",
                    valor_estimado="5.000,00")
    assert r.status_code == 200 and "Receita recorrente cadastrada." in r.text
    receitas = _secao(r.text, "receitas")
    assert "Salário" in receitas and "R$ 5.000,00" in receitas and "estimado" in receitas
    rid = int(_sql(db, "SELECT id FROM lancamentos ORDER BY data LIMIT 1")[0][0])
    r = cliente.post(f"{URL}/{rid}/pagar", data={"valor": "5.200,00", "data": "2026-10-23"})
    assert r.status_code == 200
    assert _sql(db, "SELECT valor_centavos, estimado FROM lancamentos WHERE id = ?", rid) == [
        (520000, 0)]
    assert _sql(db, "SELECT DISTINCT valor_centavos FROM lancamentos WHERE compensado = 0") == [
        (520000,)]


# --- contas e cartões --------------------------------------------------------------------

def test_cadastrar_cartao(cliente: TestClient, db: Path) -> None:
    r = cliente.post(f"{URL}/conta", data={
        "nome": "Visa Itaú", "tipo": "cartao", "instituicao": "Itaú", "final_numero": "1234",
        "saldo_inicial": "0,00", "data_saldo_inicial": "2026-10-01"})
    assert r.status_code == 200 and "Conta cadastrada." in r.text
    assert _sql(db, "SELECT nome, tipo, instituicao, final_numero, saldo_inicial_centavos,"
                " data_saldo_inicial FROM contas") == [
        ("Visa Itaú", "cartao", "Itaú", "1234", 0, "2026-10-01")]
    secao = _secao(r.text, "contas")
    assert "Visa Itaú" in secao and "Cartão" in secao and "final 1234" in secao


@pytest.mark.parametrize("campo,valor,msg", [
    ("nome", " ", "Informe o nome da conta."),
    ("tipo", "outro", "Tipo de conta inválido."),
    ("saldo_inicial", "abc", MSG_VALOR),
    ("saldo_inicial", str(10**13), MSG_ALTO),
    ("data_saldo_inicial", "", "Data inválida."),
    ("final_numero", "<b>", "Final inválido. Use só letras, números e hífen (até 20)."),
])
def test_cadastrar_conta_invalida(cliente: TestClient, db: Path,
                                  campo: str, valor: str, msg: str) -> None:
    dados = {"nome": "Conta", "tipo": "corrente", "instituicao": "", "final_numero": "",
             "saldo_inicial": "10,00", "data_saldo_inicial": "2026-10-01"}
    dados[campo] = valor
    r = cliente.post(f"{URL}/conta", data=dados)
    assert r.status_code == 400 and msg in r.text
    assert _sql(db, "SELECT COUNT(*) FROM contas") == [(0,)]


def test_erro_inesperado_vira_mensagem_amigavel(cliente: TestClient, db: Path, conta: int,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    import app.dominio.contas_pagar_acoes as acoes

    def quebra(*a: Any, **k: Any) -> None:
        raise RuntimeError("segredo interno")
    monkeypatch.setattr(acoes, "criar_parcelamento", quebra)
    r = _parcelamento(cliente, conta)
    assert r.status_code == 500
    assert "Não foi possível concluir a operação. Nada foi alterado." in r.text
    assert "segredo interno" not in r.text


def test_formulario_de_conta_tem_dica_do_saldo_inicial(cliente: TestClient) -> None:
    contas_html = _secao(cliente.get(URL).text, "contas")
    assert "Informe o saldo na data do primeiro lançamento que você vai importar." in contas_html


def _importar_linhas(db: Path, conta_id: int, linhas: list[tuple[str, str, int]]) -> None:
    from app.importacao.base import LinhaImportada, ResultadoLeitura
    from app.importacao.servico import importar

    class _P:
        def ler(self, conteudo: bytes) -> ResultadoLeitura:
            return ResultadoLeitura(linhas=[LinhaImportada(*l) for l in linhas])

    con = conectar(db)
    try:
        importar(con, conta_id, "x.csv", repr(linhas).encode(), _P(), HOJE)
    finally:
        con.close()


def test_possiveis_correspondencias_listadas_e_confirmadas(
        cliente: TestClient, db: Path, conta: int) -> None:
    _importar_linhas(db, conta, [("2026-10-05", "PIX ALUGUEL APTO", -150000)])
    _recorrente(cliente, conta)  # Aluguel dia 10, R$ 1.500,00 (gera 10/10)
    r = cliente.get(URL)
    secao = _secao(r.text, "correspondencias")
    assert "Possíveis correspondências no extrato" in secao
    assert "PIX ALUGUEL APTO" in secao and "Aluguel" in secao and "05/10/2026" in secao
    m = re.search(r'name="pendente_id" value="(\d+)">\s*<input type="hidden" name="lancamento_id" '
                  r'value="(\d+)"', secao)
    assert m
    ok = cliente.post(f"{URL}/casamento/confirmar",
                      data={"pendente_id": m.group(1), "lancamento_id": m.group(2)})
    assert ok.status_code == 200 and "Correspondência confirmada." in ok.text
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos WHERE valor_centavos = -150000"
                " AND data <= '2026-10-31'")[0][0] == 1
    assert "PIX ALUGUEL APTO" not in _secao_opcional(ok.text, "correspondencias")
    ruim = cliente.post(f"{URL}/casamento/confirmar", data={"pendente_id": "x", "lancamento_id": "1"})
    assert ruim.status_code == 400 and "inválido" in ruim.text


def test_pagar_avisa_quando_ha_linha_no_extrato(cliente: TestClient, db: Path, conta: int) -> None:
    _importar_linhas(db, conta, [("2026-10-20", "DEB AUT INTERNET FIBRA", -10000)])
    pid = _pendente(db, conta, "2026-10-20", "Internet", -10000)
    r = cliente.post(f"{URL}/{pid}/pagar", data={"valor": "", "data": "2026-10-20"})
    assert "Pagamento registrado." in r.text
    assert ("Existe uma linha no extrato que parece ser este pagamento; confirme em Possíveis "
            "correspondências para não contar duas vezes.") in r.text
    assert "DEB AUT INTERNET FIBRA" in _secao(r.text, "correspondencias")
    outro = _pendente(db, conta, "2026-10-21", "Academia", -9000)
    r = cliente.post(f"{URL}/{outro}/pagar", data={"valor": "", "data": "2026-10-21"})
    assert "Existe uma linha no extrato" not in r.text


def test_parcelamento_total_menor_que_parcelas(cliente: TestClient, db: Path, conta: int) -> None:
    r = _parcelamento(cliente, conta, valor_total="0,02", n_parcelas="3")
    assert r.status_code == 400 and "O valor total deve ser de pelo menos R$ 0,01 por parcela." in r.text
    assert _n_lanc(db) == 0
    _parcelamento(cliente, conta)
    grupo = str(_sql(db, "SELECT grupo_parcela_uuid FROM lancamentos LIMIT 1")[0][0])
    antes = _sql(db, "SELECT valor_centavos FROM lancamentos ORDER BY id")
    r = cliente.post(f"{URL}/grupo/{grupo}/editar", data={"valor_total": "0,01", "n_parcelas": "3"})
    assert r.status_code == 400 and "pelo menos R$ 0,01 por parcela" in r.text
    assert _sql(db, "SELECT valor_centavos FROM lancamentos ORDER BY id") == antes


def test_pagar_com_data_futura_e_recusado(cliente: TestClient, db: Path, conta: int) -> None:
    pid = _pendente(db, conta, "2026-10-20", "CONTA X", -5000)
    r = cliente.post(f"{URL}/{pid}/pagar", data={"valor": "", "data": "2026-10-24"})
    assert r.status_code == 400 and "A data do pagamento não pode ser no futuro." in r.text
    assert _sql(db, "SELECT compensado FROM lancamentos") == [(0,)]
