import io
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app.db import conectar
from app.main import create_app
from app.repo import contas, lancamentos

FIXTURE = Path(__file__).parent / "fixtures" / "extrato_itau_exemplo.csv"
CSV_GENERICO = "Dia;Histórico;Quanto\n01/10/2026;LOJA A;-10,50\n02/10/2026;LOJA B;-20,00\n03/10/2026;SALARIO;100,00\n"
MAPA = {"mapa_data": "Dia", "mapa_descricao": "Histórico", "mapa_valor": "Quanto"}
XLS_ANTIGO = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "t.db"


@pytest.fixture
def cliente(db: Path) -> Iterator[TestClient]:
    with TestClient(create_app(db)) as c:
        yield c


def _conta(db: Path, saldo: int = 214808, data: str = "2026-07-24", final: str | None = None) -> int:
    con = conectar(db)
    try:
        return contas.criar_conta(con, "Itaú", "corrente", final_numero=final,
                                  saldo_inicial_centavos=saldo, data_saldo_inicial=data)
    finally:
        con.close()


def _consulta(db: Path, sql: str) -> list[Any]:
    con = conectar(db)
    try:
        return [tuple(r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()


def _total(db: Path) -> int:
    return int(_consulta(db, "SELECT COUNT(*) FROM lancamentos")[0][0])


def _upload(c: TestClient, nome: str, conteudo: bytes, conta_id: int | None = None, **extra: str):
    dados = dict(extra)
    if conta_id is not None:
        dados["conta_id"] = str(conta_id)
    return c.post("/importar/previa", files={"arquivo": (nome, conteudo)}, data=dados)


def _token(html: str) -> str:
    m = re.search(r'name="token" value="([^"]+)"', html)
    assert m, html
    return m.group(1)


def _confirmar(c: TestClient, html: str, conta: int, **extra: str):
    return c.post("/importar/confirmar", data={"token": _token(html), "conta_id": str(conta), **extra})


def test_get_importar_vazio(cliente: TestClient) -> None:
    r = cliente.get("/importar")
    assert r.status_code == 200
    assert "Arraste o extrato" in r.text
    assert "Nenhuma importação ainda." in r.text


def test_previa_fixture_formatada_e_resumo(cliente: TestClient, db: Path) -> None:
    conta = _conta(db)
    r = _upload(cliente, "extrato.csv", FIXTURE.read_bytes(), conta)
    assert r.status_code == 200
    assert "22/10/2026" in r.text and "- R$ 343,54" in r.text and "PIX TRANSF RR Supo22/10" in r.text
    assert "18 novos" in r.text and "0 já existentes" in r.text and "0 com erro" in r.text
    assert _total(db) == 0


def test_conta_detectada_por_final_numero(cliente: TestClient, db: Path) -> None:
    _conta(db, final="1111-1")
    alvo = _conta(db, final="00000-0")
    r = _upload(cliente, "extrato.csv", FIXTURE.read_bytes())
    assert f'<option value="{alvo}" selected>' in r.text
    assert "Conta detectada pelo arquivo" in r.text


def test_sem_conta_escolhida_pede_para_escolher(cliente: TestClient, db: Path) -> None:
    _conta(db)
    _conta(db)
    r = _upload(cliente, "extrato.csv", FIXTURE.read_bytes())
    assert r.status_code == 200
    assert "Escolha a conta de destino" in r.text
    assert "18 novos" not in r.text


def test_confirmar_mostra_resultado_e_concilia(cliente: TestClient, db: Path) -> None:
    conta = _conta(db)
    prev = _upload(cliente, "extrato.csv", FIXTURE.read_bytes(), conta)
    r = _confirmar(cliente, prev.text, conta)
    assert r.status_code == 200
    assert "18 novos" in r.text and "0 ignorados" in r.text
    assert "Saldo confere com o banco" in r.text
    assert _total(db) == 18


def test_conciliacao_divergente_mostra_data_e_valores(cliente: TestClient, db: Path) -> None:
    conta = _conta(db, saldo=200000)
    prev = _upload(cliente, "extrato.csv", FIXTURE.read_bytes(), conta)
    r = _confirmar(cliente, prev.text, conta)
    assert "Saldo não confere com o banco" in r.text
    assert "Primeira divergência em" in r.text
    assert "Esperado" in r.text and "Calculado" in r.text


def test_mesmo_arquivo_duas_vezes(cliente: TestClient, db: Path) -> None:
    conta = _conta(db)
    prev = _upload(cliente, "extrato.csv", FIXTURE.read_bytes(), conta)
    _confirmar(cliente, prev.text, conta)
    r = _upload(cliente, "extrato.csv", FIXTURE.read_bytes(), conta)
    assert r.status_code == 200
    assert f"Este arquivo já foi importado em {date.today().strftime('%d/%m/%Y')}" in r.text
    assert 'action="/importar/confirmar"' not in r.text
    assert _total(db) == 18


def test_confirmar_revalida_arquivo_repetido(cliente: TestClient, db: Path) -> None:
    conta = _conta(db)
    p1 = _upload(cliente, "extrato.csv", FIXTURE.read_bytes(), conta)
    p2 = _upload(cliente, "extrato.csv", FIXTURE.read_bytes(), conta)
    _confirmar(cliente, p1.text, conta)
    r = _confirmar(cliente, p2.text, conta)
    assert "já foi importado" in r.text
    assert _total(db) == 18


@pytest.mark.parametrize("nome,conteudo,msg", [
    ("extrato.csv", b"", "O arquivo está vazio."),
    ("extrato.txt", b"abc", "Formato não aceito. Envie um arquivo .csv ou .xlsx."),
    ("antigo.xls", XLS_ANTIGO, "Formato não aceito. Envie um arquivo .csv ou .xlsx."),
    ("disfarcado.xlsx", XLS_ANTIGO, "Planilhas .xls (Excel antigo) não são aceitas."),
    ("extrato.csv", b"x" * (5 * 1024 * 1024 + 1), "O arquivo excede o limite de 5 MB."),
    ("planilha.xlsx", b"PK\x03\x04lixo", "Não foi possível ler o arquivo."),
    ("lixo.csv", b"\x00\x01\x02\x03", "Não foi possível ler o arquivo."),
], ids=["vazio", "extensao", "xls", "xls-disfarcado", "grande", "xlsx-lixo", "csv-lixo"])
def test_arquivos_invalidos(cliente: TestClient, db: Path, nome: str, conteudo: bytes, msg: str) -> None:
    conta = _conta(db)
    r = _upload(cliente, nome, conteudo, conta)
    assert r.status_code in (400, 413)
    assert msg in r.text
    assert "Traceback" not in r.text
    assert _total(db) == 0


def test_confirmar_token_invalido(cliente: TestClient, db: Path) -> None:
    conta = _conta(db)
    r = cliente.post("/importar/confirmar", data={"token": "naoexiste", "conta_id": str(conta)})
    assert r.status_code == 400
    assert "Envio expirado" in r.text
    assert _total(db) == 0


def test_confirmar_conta_inexistente(cliente: TestClient, db: Path) -> None:
    conta = _conta(db)
    prev = _upload(cliente, "extrato.csv", FIXTURE.read_bytes(), conta)
    r = _confirmar(cliente, prev.text, 999)
    assert r.status_code == 400
    assert "Conta não encontrada." in r.text
    assert _total(db) == 0


def test_desfazer_remove_lancamentos(cliente: TestClient, db: Path) -> None:
    conta = _conta(db)
    prev = _upload(cliente, "extrato.csv", FIXTURE.read_bytes(), conta)
    _confirmar(cliente, prev.text, conta)
    hist = cliente.get("/importar")
    assert "extrato.csv" in hist.text and "25/07/2026" in hist.text and "22/10/2026" in hist.text
    r = cliente.post("/importar/1/desfazer", follow_redirects=True)
    assert r.status_code == 200
    assert "Importação desfeita." in r.text
    assert _total(db) == 0
    assert "Desfeita" in cliente.get("/importar").text
    assert cliente.post("/importar/99/desfazer").status_code == 404


def test_csv_generico_mapeamento_e_importacao(cliente: TestClient, db: Path) -> None:
    conta = _conta(db, saldo=0, data="2026-10-01")
    r = _upload(cliente, "banco.csv", CSV_GENERICO.encode("utf-8"), conta)
    assert r.status_code == 200
    assert "Mapeamento de colunas" in r.text
    for col in ("Dia", "Histórico", "Quanto"):
        assert f'<option value="{col}"' in r.text
    token = _token(r.text)
    prev = cliente.post("/importar/previa", data={"token": token, "conta_id": str(conta), **MAPA})
    assert prev.status_code == 200
    assert "3 novos" in prev.text and "01/10/2026" in prev.text and "- R$ 10,50" in prev.text
    assert _total(db) == 0
    res = cliente.post("/importar/confirmar", data={"token": token, "conta_id": str(conta), **MAPA})
    assert "3 novos" in res.text
    assert "Este arquivo não traz saldos" in res.text
    vals = [r_[0] for r_ in _consulta(db, "SELECT valor_centavos FROM lancamentos ORDER BY data")]
    assert vals == [-1050, -2000, 10000]


def test_mapeamento_com_coluna_inexistente(cliente: TestClient, db: Path) -> None:
    conta = _conta(db)
    r = _upload(cliente, "banco.csv", CSV_GENERICO.encode(), conta)
    prev = cliente.post("/importar/previa", data={"token": _token(r.text), "conta_id": str(conta),
                                                  **{**MAPA, "mapa_valor": "Inexistente"}})
    assert "Coluna &#39;Inexistente&#39; não encontrada no arquivo." in prev.text
    assert 'action="/importar/confirmar"' not in prev.text


def test_xlsx_formato_brasileiro_ponta_a_ponta(cliente: TestClient, db: Path) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Dia", "Histórico", "Quanto"])
    ws.append(["01/10/2026", "ALUGUEL", "-1.234,56"])
    ws.append(["02/10/2026", "SALARIO", "5.000,00"])
    buf = io.BytesIO()
    wb.save(buf)
    conta = _conta(db, saldo=0, data="2026-10-01")
    r = _upload(cliente, "plan.xlsx", buf.getvalue(), conta)
    token = _token(r.text)
    prev = cliente.post("/importar/previa", data={"token": token, "conta_id": str(conta), **MAPA})
    assert "- R$ 1.234,56" in prev.text and "R$ 5.000,00" in prev.text and "2 novos" in prev.text
    cliente.post("/importar/confirmar", data={"token": token, "conta_id": str(conta), **MAPA})
    vals = [r_[0] for r_ in _consulta(db, "SELECT valor_centavos FROM lancamentos ORDER BY data")]
    assert vals == [-123456, 500000]


def test_proposta_de_casamento_e_confirmacao(cliente: TestClient, db: Path) -> None:
    conta = _conta(db)
    con = conectar(db)
    try:
        pend = lancamentos.inserir_manual(
            con, conta_id=conta, data="2026-10-20", descricao="UBER TRIP", valor_centavos=-2735,
            tipo="saida", categoria_id=None, compensado=False, observacao=None)
        con.commit()
    finally:
        con.close()
    prev = _upload(cliente, "extrato.csv", FIXTURE.read_bytes(), conta)
    r = _confirmar(cliente, prev.text, conta)
    assert "Propostas de casamento" in r.text
    m = re.search(r'name="pendente_id" value="(\d+)">\s*<input type="hidden" name="lancamento_id" value="(\d+)"', r.text)
    assert m and int(m.group(1)) == pend
    assert "UBER TRIP" in r.text and "UBER *TRIP" in r.text
    ok = cliente.post("/importar/casamento/confirmar",
                      data={"pendente_id": m.group(1), "lancamento_id": m.group(2)},
                      headers={"HX-Request": "true"})
    assert ok.status_code == 200
    assert "Casamento confirmado" in ok.text
    row = _consulta(db, f"SELECT compensado, valor_centavos FROM lancamentos WHERE id = {pend}")[0]
    assert row == (1, -2735)
    assert _total(db) == 18  # 18 linhas - 1 casada + 1 pendente quitado
    novamente = cliente.post("/importar/casamento/confirmar",
                             data={"pendente_id": m.group(1), "lancamento_id": m.group(2)})
    assert "alerta" in novamente.text


def test_criar_conta_com_sugestao_de_saldo(cliente: TestClient, db: Path) -> None:
    r = _upload(cliente, "extrato.csv", FIXTURE.read_bytes())
    assert "Cadastre uma conta" in r.text
    assert 'value="2026-07-24"' in r.text and 'value="R$ 2.148,08"' in r.text
    token = _token(r.text)
    r2 = cliente.post("/importar/conta", data={
        "nome": "Itaú CC", "tipo": "corrente", "saldo_inicial": "R$ 2.148,08",
        "data_saldo_inicial": "2026-07-24", "final_numero": "", "token": token})
    assert r2.status_code == 200
    assert "18 novos" in r2.text
    c = _consulta(db, "SELECT nome, saldo_inicial_centavos, data_saldo_inicial FROM contas")[0]
    assert c == ("Itaú CC", 214808, "2026-07-24")


def test_criar_conta_invalida(cliente: TestClient) -> None:
    base = {"nome": "X", "tipo": "corrente", "saldo_inicial": "1,00", "data_saldo_inicial": "2026-07-24"}
    r = cliente.post("/importar/conta", data={**base, "nome": " "})
    assert r.status_code == 400 and "Informe o nome da conta." in r.text
    r = cliente.post("/importar/conta", data={**base, "saldo_inicial": "abc"})
    assert r.status_code == 400 and "Saldo inicial inválido." in r.text
    r = cliente.post("/importar/conta", data={**base, "data_saldo_inicial": "x"})
    assert r.status_code == 400 and "Data do saldo inicial inválida." in r.text
    r = cliente.post("/importar/conta", data={**base, "tipo": "qualquer"})
    assert r.status_code == 400 and "Tipo de conta inválido." in r.text


def test_desfazer_so_na_mais_recente_e_pede_confirmacao(cliente: TestClient, db: Path) -> None:
    conta = _conta(db, saldo=0, data="2026-10-01")
    for nome, linhas in (("a.csv", "01/10/2026;A;1,00\n"), ("b.csv", "02/10/2026;B;2,00\n")):
        r = _upload(cliente, nome, ("Dia;Histórico;Quanto\n" + linhas).encode(), conta)
        cliente.post("/importar/confirmar", data={"token": _token(r.text), "conta_id": str(conta), **MAPA})
    hist = cliente.get("/importar").text
    assert 'action="/importar/2/desfazer"' in hist and 'action="/importar/1/desfazer"' not in hist
    assert "return confirm('Desfazer esta importação?" in hist
    r = cliente.post("/importar/1/desfazer")
    assert r.status_code == 400
    assert "Desfaça primeiro a importação mais recente (#2," in r.text
    assert _total(db) == 2
    assert "Importação desfeita." in cliente.post("/importar/2/desfazer").text
    assert 'action="/importar/1/desfazer"' in cliente.get("/importar").text
    assert "Importação desfeita." in cliente.post("/importar/1/desfazer").text
    assert _total(db) == 0


HDR_ITAU = ("Itaú Unibanco;;;;\nExtrato conta corrente;;;;\nagência: 0000     conta: 00000-0;;;;\n"
            "data;lançamento;ag./origem;valor (R$);saldos (R$)\n")


def _texto(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_saldo_de_hoje_bloqueia_importacao_e_ajuste_resolve(db: Path) -> None:
    app = create_app(db)
    app.state.hoje_fn = lambda: date(2026, 10, 23)
    with TestClient(app) as c:
        c.post("/contas-a-pagar/conta", data={
            "nome": "Itaú", "tipo": "corrente", "instituicao": "", "final_numero": "00000-0",
            "saldo_inicial": "1.409,14", "data_saldo_inicial": "2026-10-23"})
        conta = _consulta(db, "SELECT id FROM contas")[0][0]
        prev = _upload(c, "extrato.csv", FIXTURE.read_bytes())
        t = _texto(prev.text)
        assert "18 lançamentos com data anterior à data do saldo inicial da conta (23/10/2026)" in t
        assert "25/07/2026" in t and "R$ 2.148,08" in t
        assert 'action="/importar/confirmar"' not in prev.text
        assert "Ajustar saldo inicial da conta" in prev.text
        assert 'value="2026-07-24"' in prev.text and 'value="R$ 2.148,08"' in prev.text
        direto = _confirmar(c, prev.text, conta)
        assert direto.status_code == 400 and "Nada foi importado" in direto.text
        assert _total(db) == 0
        assert _consulta(db, "SELECT COUNT(*) FROM importacoes")[0][0] == 0
        ajuste = c.post("/importar/conta/saldo", data={
            "conta_id": str(conta), "saldo_inicial": "R$ 2.148,08",
            "data_saldo_inicial": "2026-07-24", "token": _token(prev.text)})
        assert ajuste.status_code == 200
        assert "Saldo inicial da conta atualizado." in ajuste.text
        assert "18 novos" in ajuste.text and 'action="/importar/confirmar"' in ajuste.text
        r = _confirmar(c, ajuste.text, conta)
        assert "Saldo confere com o banco" in r.text
        assert "R$ 1.409,14" in c.get("/").text


def test_ajustar_saldo_inicial_invalido(cliente: TestClient, db: Path) -> None:
    conta = _conta(db)
    base = {"conta_id": str(conta), "saldo_inicial": "1,00", "data_saldo_inicial": "2026-07-24"}
    r = cliente.post("/importar/conta/saldo", data={**base, "saldo_inicial": "abc"})
    assert r.status_code == 400 and "Saldo inicial inválido." in r.text
    r = cliente.post("/importar/conta/saldo", data={**base, "data_saldo_inicial": "x"})
    assert r.status_code == 400 and "Data do saldo inicial inválida." in r.text
    r = cliente.post("/importar/conta/saldo", data={**base, "conta_id": "999"})
    assert r.status_code == 400 and "Conta não encontrada." in r.text
    r = cliente.post("/importar/conta/saldo", data=base)
    assert r.status_code == 200 and "Saldo inicial da conta atualizado." in r.text
    assert _consulta(db, "SELECT saldo_inicial_centavos FROM contas")[0][0] == 100


def test_pagina_importar_oferece_ajuste_de_saldo(cliente: TestClient, db: Path) -> None:
    assert "Ajustar saldo inicial da conta" not in cliente.get("/importar").text
    _conta(db)
    assert "Ajustar saldo inicial da conta" in cliente.get("/importar").text


def test_resultado_sem_dias_comparaveis_mostra_aviso(cliente: TestClient, db: Path) -> None:
    conta = _conta(db, saldo=100000, data="2026-10-23")
    csv = (HDR_ITAU + "23/10/2026;PIX X;;-10,00;\n"
           "22/10/2026;SALDO TOTAL DISPONÍVEL DIA;;;1.000,00\n").encode("latin-1")
    prev = _upload(cliente, "x.csv", csv, conta)
    r = _confirmar(cliente, prev.text, conta)
    assert r.status_code == 200
    assert "1 novos" in r.text
    assert ("Não foi possível conferir o saldo: nenhum saldo do extrato é posterior à data do "
            "saldo inicial da conta (23/10/2026).") in r.text
    assert "Saldo confere com o banco" not in r.text


def test_nome_do_arquivo_escapado(cliente: TestClient, db: Path) -> None:
    conta = _conta(db)
    r = _upload(cliente, "<b>x.csv", FIXTURE.read_bytes(), conta)
    assert "<b>x.csv" not in r.text
    assert "&lt;b&gt;x.csv" in r.text


def test_importar_usa_relogio_injetavel(db: Path) -> None:
    app = create_app(db)
    app.state.hoje_fn = lambda: date(2026, 10, 20)
    with TestClient(app) as c:
        conta = _conta(db)
        prev = _upload(c, "extrato.csv", FIXTURE.read_bytes(), conta)
        _confirmar(c, prev.text, conta)
        r = _upload(c, "extrato.csv", FIXTURE.read_bytes(), conta)
    assert "Este arquivo já foi importado em 20/10/2026" in r.text


def test_casamento_com_campo_nao_inteiro_e_amigavel(cliente: TestClient) -> None:
    r = cliente.post("/importar/casamento/confirmar", data={"pendente_id": "abc", "lancamento_id": "1"})
    assert r.status_code == 200  # fragmento HTMX com a mensagem, como os demais erros do casamento
    assert "Identificador inválido." in r.text
    assert "detail" not in r.text


def test_falha_na_conferencia_apos_importar_mostra_aviso(
        cliente: TestClient, db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.web.rotas import importar as rota

    def quebra(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(rota, "conciliar", quebra)
    conta = _conta(db)
    prev = _upload(cliente, "extrato.csv", FIXTURE.read_bytes(), conta)
    r = _confirmar(cliente, prev.text, conta)
    assert r.status_code == 200
    assert "18 novos" in r.text
    assert ("A importação foi concluída, mas não foi possível conferir o saldo nem procurar "
            "correspondências agora.") in r.text
    assert "boom" not in r.text
    assert _total(db) == 18


def test_desfazer_erro_inesperado_e_amigavel(
        cliente: TestClient, db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.web.rotas import importar as rota

    def quebra(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(rota, "desfazer_importacao", quebra)
    r = cliente.post("/importar/1/desfazer")
    assert r.status_code == 500 and "Não foi possível desfazer a importação." in r.text
