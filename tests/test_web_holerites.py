"""Telas de holerite: envio, prévia, confirmação, lista e itens (dados inventados)."""
import logging
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import conectar
from app.importacao.holerite.modelo import HoleriteIlegivel, HoleriteLido, ItemHolerite
from app.main import create_app
from app.repo import contas
from tests.holerite_util import CNPJ, CPF, NOME, palavras_sinteticas, pdf_minimo

PDF = b"%PDF-1.4 sintetico"
HOJE = date(2026, 10, 30)


def _h(liquido: int = 380000, comp: str = "2026-09", tipo: str = "mensal",
       confere: bool = True) -> HoleriteLido:
    itens = [ItemHolerite("111", "SALARIO FICTICIO", "22,00", "provento", liquido + 120000),
             ItemHolerite("211", "IMPOSTO TESTE", "7,50", "desconto", 120000)]
    return HoleriteLido(tipo, comp, "ACME INVENTADA", liquido + 120000,
                        120000 if confere else 100000, liquido, itens)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "t.db"


@pytest.fixture
def cliente(db: Path) -> Iterator[TestClient]:
    with TestClient(create_app(db)) as c:
        c.app.state.hoje_fn = lambda: HOJE  # type: ignore[attr-defined]
        yield c


@pytest.fixture
def lido(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    estado: dict[str, Any] = {"h": _h(), "chamadas": 0}

    def falso(conteudo: bytes) -> HoleriteLido:
        estado["chamadas"] += 1
        return estado["h"]

    monkeypatch.setattr("app.web.rotas.holerites.ler_holerite", falso)
    return estado


def _consulta(db: Path, sql: str, *args: Any) -> list[tuple[Any, ...]]:
    con = conectar(db)
    try:
        return [tuple(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def _conta(db: Path, nome: str = "Corrente", tipo: str = "corrente") -> int:
    con = conectar(db)
    try:
        return contas.criar_conta(con, nome, tipo)
    finally:
        con.close()


def _credito(db: Path, conta: int, data: str = "2026-10-02", valor: int = 380000,
             chave: str = "c1") -> int:
    con = conectar(db)
    try:
        cur = con.execute(
            "INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
            " chave_deduplicacao, origem, compensado, data_compensacao)"
            " VALUES (?,?,?,?,?,'entrada',?,'importado',1,?)",
            (chave, conta, data, "CREDITO SAL", valor, chave, data))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def _enviar(c: TestClient, conta: int, conteudo: bytes = PDF, nome: str = "holerite.pdf") -> Any:
    return c.post("/holerites/previa", data={"conta_id": str(conta)},
                  files={"arquivo": (nome, conteudo, "application/pdf")})


def _token(html: str) -> str:
    m = re.search(r'action="/holerites/confirmar".*?name="token" value="([^"]+)"', html, re.S)
    assert m, html
    return m.group(1)


def _confirmar(c: TestClient, html: str, cats: list[str] | None = None, **extra: str) -> Any:
    dados: dict[str, Any] = {"token": _token(html), "categoria": cats or ["", ""], **extra}
    return c.post("/holerites/confirmar", data=dados, follow_redirects=False)


def _importado(cliente: TestClient, db: Path, **kw: Any) -> int:
    conta = _conta(db)
    assert _confirmar(cliente, _enviar(cliente, conta).text, **kw).status_code == 303
    return int(_consulta(db, "SELECT id FROM holerites")[0][0])


def test_importar_mostra_secao_holerite(cliente: TestClient, db: Path) -> None:
    _conta(db, "Minha corrente")
    _conta(db, "Meu cartão", "cartao")
    r = cliente.get("/importar")
    assert r.status_code == 200
    assert "Arraste o extrato" in r.text
    assert 'action="/holerites/previa"' in r.text
    assert "Importar holerite (PDF)" in r.text
    assert "Ver prévia do holerite" in r.text
    form = r.text[r.text.index('action="/holerites/previa"'):]
    form = form[:form.index("</form>")]
    assert "Minha corrente" in form and "Meu cartão" not in form
    assert 'accept=".pdf,application/pdf"' in form and 'type="password"' not in form


def test_previa_mostra_totais_itens_conferencia_e_credito_encontrado(
        cliente: TestClient, db: Path, lido: dict[str, Any]) -> None:
    conta = _conta(db)
    lid = _credito(db, conta)
    r = _enviar(cliente, conta)
    assert r.status_code == 200
    for t in ("SALARIO FICTICIO", "IMPOSTO TESTE", "Confere", "R$ 3.800,00", "R$ 5.000,00",
              "R$ 1.200,00", "09/2026", "02/10/2026", "Crédito no extrato"):
        assert t in r.text, t
    assert "Divergente" not in r.text
    assert 'name="categoria"' in r.text and "data-busca" in r.text
    radio = re.search(r'<input[^>]*name="credito"[^>]*value="%d"[^>]*>' % lid, r.text)
    assert radio and "checked" in radio.group(0)
    vazio = re.search(r'<input[^>]*name="credito"[^>]*value=""[^>]*>', r.text)
    assert vazio and "checked" not in vazio.group(0)
    assert 'name="substituir"' not in r.text
    assert _consulta(db, "SELECT COUNT(*) FROM holerites")[0][0] == 0  # prévia não grava


def test_previa_divergente_mostra_aviso(cliente: TestClient, db: Path,
                                        lido: dict[str, Any]) -> None:
    lido["h"] = _h(confere=False)
    lido["h"].avisos.append("Total divergente no holerite.")
    r = _enviar(cliente, _conta(db))
    assert "Divergente" in r.text and "Total divergente no holerite." in r.text


def test_previa_sem_credito_mostra_data_prevista(cliente: TestClient, db: Path,
                                                 lido: dict[str, Any]) -> None:
    r = _enviar(cliente, _conta(db))
    assert r.status_code == 200
    assert "prever entrada em 02/10/2026" in r.text
    vazio = re.search(r'<input[^>]*name="credito"[^>]*value=""[^>]*>', r.text)
    assert vazio and "checked" in vazio.group(0)


def test_previa_de_holerite_antigo_avisa_que_nao_preve_entrada(
        cliente: TestClient, db: Path, lido: dict[str, Any]) -> None:
    lido["h"] = _h(comp="2026-01")
    conta = _conta(db)
    r = _enviar(cliente, conta)
    assert r.status_code == 200
    assert ("Sem crédito no extrato e a data prevista já passou: não será criada entrada "
            "prevista.") in r.text
    assert "prever entrada em" not in r.text
    assert _confirmar(cliente, r.text).status_code == 303
    assert _consulta(db, "SELECT lancamento_id FROM holerites") == [(None,)]
    assert _consulta(db, "SELECT COUNT(*) FROM lancamentos")[0][0] == 0
    assert "sem crédito no extrato" in cliente.get("/holerites?ano=2026").text


def _pendente(db: Path, conta: int, valor: int, data: str = "2026-10-03") -> int:
    con = conectar(db)
    try:
        cur = con.execute(
            "INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
            " chave_deduplicacao, origem, compensado, estimado) VALUES ('p1', ?, ?,"
            " 'SALARIO RECORRENTE', ?, 'entrada', 'p1', 'manual', 0, 1)", (conta, data, valor))
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def test_previa_oferece_entrada_prevista_ja_cadastrada_e_a_marca(
        cliente: TestClient, db: Path, lido: dict[str, Any]) -> None:
    conta = _conta(db)
    pid = _pendente(db, conta, 379000)
    r = _enviar(cliente, conta)
    assert "Entrada prevista já cadastrada" in r.text and "SALARIO RECORRENTE" in r.text
    radio = re.search(r'<input[^>]*name="credito"[^>]*value="%d"[^>]*>' % pid, r.text)
    assert radio and "checked" in radio.group(0)
    assert _confirmar(cliente, r.text, credito=str(pid)).status_code == 303
    assert _consulta(db, "SELECT lancamento_id FROM holerites") == [(pid,)]
    assert _consulta(db, "SELECT COUNT(*) FROM lancamentos")[0][0] == 1
    assert "previsto para 03/10" in cliente.get("/holerites?ano=2026").text


def test_salario_recorrente_marca_a_ocorrencia_proxima_da_data_prevista(
        cliente: TestClient, db: Path, lido: dict[str, Any]) -> None:
    from app.dominio.recorrentes import criar_recorrente, gerar_recorrentes
    conta = _conta(db)
    con = conectar(db)
    try:
        rid = criar_recorrente(con, nome="Salário", tipo="entrada", dia=2,
                               valor_estimado_centavos=380000, conta_id=conta, categoria_id=None)
        gerar_recorrentes(con, date(2026, 10, 1), 2)
    finally:
        con.close()
    nov = _consulta(db, "SELECT id FROM lancamentos WHERE recorrente_id = ? AND data = ?",
                    rid, "2026-11-02")[0][0]
    lido["h"] = _h(comp="2026-10")
    r = _enviar(cliente, conta)
    marcados = re.findall(r'<input[^>]*name="credito"[^>]*checked[^>]*>', r.text)
    assert len(marcados) == 1 and f'value="{nov}"' in marcados[0]
    assert r.text.count("Entrada prevista já cadastrada") == 2
    assert _confirmar(cliente, r.text, credito=str(nov)).status_code == 303
    assert _consulta(db, "SELECT lancamento_id FROM holerites") == [(nov,)]
    assert _consulta(db, "SELECT COUNT(*) FROM lancamentos WHERE origem = 'holerite'")[0][0] == 0


def test_credito_do_extrato_tem_prioridade_sobre_entrada_prevista(
        cliente: TestClient, db: Path, lido: dict[str, Any]) -> None:
    conta = _conta(db)
    _pendente(db, conta, 379000)
    lid = _credito(db, conta)
    r = _enviar(cliente, conta)
    radio = re.search(r'<input[^>]*name="credito"[^>]*value="%d"[^>]*>' % lid, r.text)
    assert radio and "checked" in radio.group(0)


def test_varios_creditos_nao_marca_nenhum_e_prever_fica_marcado(
        cliente: TestClient, db: Path, lido: dict[str, Any]) -> None:
    conta = _conta(db)
    _credito(db, conta, chave="a")
    _credito(db, conta, "2026-10-03", chave="b")
    r = _enviar(cliente, conta)
    marcados = re.findall(r'<input[^>]*name="credito"[^>]*checked[^>]*>', r.text)
    assert len(marcados) == 1 and 'value=""' in marcados[0]


def test_confirmar_grava_e_redireciona(cliente: TestClient, db: Path,
                                       lido: dict[str, Any]) -> None:
    conta = _conta(db)
    cat = int(_consulta(db, "SELECT id FROM categorias WHERE pai_id IS NOT NULL ORDER BY id")[0][0])
    previa = _enviar(cliente, conta).text
    r = _confirmar(cliente, previa, cats=[str(cat), ""], credito="")
    assert r.status_code == 303 and r.headers["location"] == "/holerites?ano=2026"
    assert _consulta(db, "SELECT competencia, tipo, liquido_centavos FROM holerites") == [
        ("2026-09", "mensal", 380000)]
    assert _consulta(db, "SELECT categoria_id FROM holerite_itens ORDER BY ordem")[0] == (cat,)
    assert _consulta(db, "SELECT COUNT(*) FROM lancamentos WHERE origem = 'holerite'")[0][0] == 1
    assert cliente.get(r.headers["location"]).status_code == 200


def test_confirmar_vincula_o_credito_escolhido(cliente: TestClient, db: Path,
                                               lido: dict[str, Any]) -> None:
    conta = _conta(db)
    lid = _credito(db, conta)
    previa = _enviar(cliente, conta).text
    assert _confirmar(cliente, previa, credito=str(lid)).status_code == 303
    assert _consulta(db, "SELECT lancamento_id FROM holerites") == [(lid,)]
    assert _consulta(db, "SELECT COUNT(*) FROM lancamentos WHERE origem = 'holerite'")[0][0] == 0
    html = cliente.get("/holerites?ano=2026").text
    assert "recebido em 02/10" in html


def test_credito_invalido_nao_grava(cliente: TestClient, db: Path, lido: dict[str, Any]) -> None:
    previa = _enviar(cliente, _conta(db)).text
    for ruim in ("abc", "999999"):
        r = _confirmar(cliente, previa, credito=ruim)
        assert r.status_code == 400 and 'action="/holerites/confirmar"' in r.text
    assert _consulta(db, "SELECT COUNT(*) FROM holerites")[0][0] == 0


@pytest.mark.parametrize("cats", [["abc", ""], ["999999", ""], [""]])
def test_categoria_invalida_nao_grava(cliente: TestClient, db: Path, lido: dict[str, Any],
                                      cats: list[str]) -> None:
    previa = _enviar(cliente, _conta(db)).text
    r = _confirmar(cliente, previa, cats=cats)
    assert r.status_code == 400 and "ategoria" in r.text
    assert _consulta(db, "SELECT COUNT(*) FROM holerites")[0][0] == 0


def test_confirmar_token_expirado_pede_novo_envio(cliente: TestClient) -> None:
    r = cliente.post("/holerites/confirmar", data={"token": "inexistente", "categoria": ["", ""]},
                     follow_redirects=False)
    assert r.status_code == 400 and "expirad" in r.text
    assert r.headers["cache-control"] == "no-store"


def test_token_usado_e_descartado(cliente: TestClient, db: Path, lido: dict[str, Any]) -> None:
    previa = _enviar(cliente, _conta(db)).text
    assert _confirmar(cliente, previa).status_code == 303
    r = _confirmar(cliente, previa)
    assert r.status_code == 400 and "expirad" in r.text


def test_arquivo_nao_pdf_recusado_sem_gravar(cliente: TestClient, db: Path,
                                             lido: dict[str, Any]) -> None:
    conta = _conta(db)
    r = _enviar(cliente, conta, nome="holerite.txt")
    assert r.status_code == 400 and "Formato não aceito" in r.text
    r = _enviar(cliente, conta, b"%PDF" + b"0" * (5 * 1024 * 1024))
    assert r.status_code == 413 and "5 MB" in r.text
    r = _enviar(cliente, conta, b"   ")
    assert r.status_code == 400 and "vazio" in r.text
    r = cliente.post("/holerites/previa", data={"conta_id": str(conta)})
    assert r.status_code == 400 and "Selecione" in r.text
    assert lido["chamadas"] == 0
    assert _consulta(db, "SELECT COUNT(*) FROM holerites")[0][0] == 0


def test_conta_obrigatoria_e_sem_cartao(cliente: TestClient, db: Path,
                                        lido: dict[str, Any]) -> None:
    cartao = _conta(db, "Cartão X", "cartao")
    for valor in (str(cartao), "", "abc", "99999"):
        r = cliente.post("/holerites/previa", data={"conta_id": valor},
                         files={"arquivo": ("h.pdf", PDF, "application/pdf")})
        assert r.status_code == 400 and "conta corrente" in r.text.lower()
    assert lido["chamadas"] == 0


def test_confirmacao_dupla_retorna_ja_importado(cliente: TestClient, db: Path,
                                                lido: dict[str, Any]) -> None:
    conta = _conta(db)
    p1 = _enviar(cliente, conta).text
    p2 = _enviar(cliente, conta).text
    assert _confirmar(cliente, p1).status_code == 303
    r = _confirmar(cliente, p2)
    assert r.status_code == 400 and "já foi importado" in r.text
    assert _consulta(db, "SELECT COUNT(*) FROM holerites")[0][0] == 1
    novo = _enviar(cliente, conta).text
    assert "já foi importado" in novo and 'action="/holerites/confirmar"' not in novo


def test_substituir_exige_marcacao(cliente: TestClient, db: Path, lido: dict[str, Any]) -> None:
    conta = _conta(db)
    assert _confirmar(cliente, _enviar(cliente, conta).text).status_code == 303
    lido["h"] = _h(liquido=150000)
    previa = _enviar(cliente, conta, b"%PDF-1.4 outro").text
    assert 'name="substituir"' in previa
    r = _confirmar(cliente, previa)
    assert r.status_code == 400 and "substituir" in r.text
    assert _consulta(db, "SELECT liquido_centavos FROM holerites") == [(380000,)]
    r = _confirmar(cliente, previa, substituir="1")
    assert r.status_code == 303
    assert _consulta(db, "SELECT liquido_centavos FROM holerites") == [(150000,)]
    assert _consulta(db, "SELECT COUNT(*) FROM holerite_itens")[0][0] == 2


def test_substituir_prever_com_vinculo_valido_desfaz_o_vinculo(
        cliente: TestClient, db: Path, lido: dict[str, Any]) -> None:
    conta = _conta(db)
    lid = _credito(db, conta)
    assert _confirmar(cliente, _enviar(cliente, conta).text, credito=str(lid)).status_code == 303
    lido["h"] = _h()
    lido["h"].itens[0] = ItemHolerite("111", "SALARIO NOVO", "22,00", "provento", 500000)
    previa = _enviar(cliente, conta, b"%PDF-1.4 outro").text
    marcado = re.search(r'<input[^>]*name="credito"[^>]*value="%d"[^>]*>' % lid, previa)
    assert marcado and "checked" in marcado.group(0)  # vínculo atual vem marcado
    assert _confirmar(cliente, previa, credito="", substituir="1").status_code == 303
    assert _consulta(db, "SELECT lancamento_id FROM holerites")[0][0] != lid
    assert _consulta(db, "SELECT COUNT(*) FROM lancamentos WHERE origem = 'holerite'")[0][0] == 1


def test_lista_por_ano_e_expande_itens(cliente: TestClient, db: Path,
                                       lido: dict[str, Any]) -> None:
    conta = _conta(db)
    assert _confirmar(cliente, _enviar(cliente, conta).text).status_code == 303
    lido["h"] = _h(comp="2025-12", liquido=100000)
    assert _confirmar(cliente, _enviar(cliente, conta, b"%PDF-1.4 b").text).status_code == 303
    r = cliente.get("/holerites?ano=2026")
    assert r.status_code == 200
    assert "09/2026" in r.text and "12/2025" not in r.text
    assert "R$ 5.000,00" in r.text and "R$ 1.200,00" in r.text and "R$ 3.800,00" in r.text
    assert "previsto para 02/10" in r.text
    assert "aria-controls" in r.text and "/itens" in r.text
    hid = int(_consulta(db, "SELECT id FROM holerites WHERE competencia = '2026-09'")[0][0])
    frag = cliente.get(f"/holerites/{hid}/itens")
    assert frag.status_code == 200 and frag.headers["cache-control"] == "no-store"
    assert "SALARIO FICTICIO" in frag.text and "IMPOSTO TESTE" in frag.text
    assert "hx-post" in frag.text and "<html" not in frag.text
    assert "12/2025" in cliente.get("/holerites?ano=2025").text
    assert "Nenhum holerite" in cliente.get("/holerites?ano=2024").text


def test_lista_vazia_e_ano_invalido(cliente: TestClient) -> None:
    assert "Nenhum holerite" in cliente.get("/holerites").text
    assert cliente.get("/holerites?ano=abc").status_code == 200


def test_itens_de_holerite_inexistente(cliente: TestClient) -> None:
    r = cliente.get("/holerites/999/itens")
    assert r.status_code == 404 and "não encontrado" in r.text
    assert cliente.get("/holerites/abc/itens").status_code == 400


def test_categoria_do_item_altera_e_responde_fragmento(cliente: TestClient, db: Path,
                                                       lido: dict[str, Any]) -> None:
    hid = _importado(cliente, db)
    item = int(_consulta(db, "SELECT id FROM holerite_itens WHERE holerite_id = ? ORDER BY ordem",
                         hid)[0][0])
    cat = int(_consulta(db, "SELECT id FROM categorias WHERE pai_id IS NOT NULL ORDER BY id")[0][0])
    r = cliente.post(f"/holerites/itens/{item}/categoria", data={"categoria_id": str(cat)})
    assert r.status_code == 200 and "<html" not in r.text
    assert re.search(r'<option value="%d" selected' % cat, r.text)
    assert _consulta(db, "SELECT categoria_id FROM holerite_itens WHERE id = ?", item) == [(cat,)]
    r = cliente.post(f"/holerites/itens/{item}/categoria", data={"categoria_id": ""})
    assert r.status_code == 200
    assert _consulta(db, "SELECT categoria_id FROM holerite_itens WHERE id = ?", item) == [(None,)]
    r = cliente.post(f"/holerites/itens/{item}/categoria", data={"categoria_id": "999999"})
    assert r.status_code == 400 and "Categoria" in r.text
    r = cliente.post("/holerites/itens/999999/categoria", data={"categoria_id": ""})
    assert r.status_code in (400, 404) and "não encontrado" in r.text


def test_alterar_categoria_nao_muda_totais_de_caixa(cliente: TestClient, db: Path,
                                                    lido: dict[str, Any]) -> None:
    hid = _importado(cliente, db)
    antes = _consulta(db, "SELECT SUM(valor_centavos), COUNT(*) FROM lancamentos")
    item = int(_consulta(db, "SELECT id FROM holerite_itens WHERE holerite_id = ?", hid)[0][0])
    cat = int(_consulta(db, "SELECT id FROM categorias WHERE pai_id IS NOT NULL ORDER BY id")[0][0])
    cliente.post(f"/holerites/itens/{item}/categoria", data={"categoria_id": str(cat)})
    assert _consulta(db, "SELECT SUM(valor_centavos), COUNT(*) FROM lancamentos") == antes


def test_respostas_com_cache_no_store(cliente: TestClient, db: Path,
                                      lido: dict[str, Any]) -> None:
    conta = _conta(db)
    r = _enviar(cliente, conta)
    assert r.headers["cache-control"] == "no-store"
    assert _enviar(cliente, conta, nome="x.txt").headers["cache-control"] == "no-store"
    assert _confirmar(cliente, r.text, cats=["x", "x"]).headers["cache-control"] == "no-store"
    assert cliente.get("/holerites").headers["cache-control"] == "no-store"


def test_ilegivel_mostra_a_propria_mensagem(cliente: TestClient, db: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    def ilegivel(conteudo: bytes) -> HoleriteLido:
        raise HoleriteIlegivel("Layout de holerite não suportado.")

    monkeypatch.setattr("app.web.rotas.holerites.ler_holerite", ilegivel)
    r = _enviar(cliente, _conta(db))
    assert r.status_code == 400 and "Layout de holerite não suportado." in r.text


def test_erro_inesperado_na_leitura_nao_vaza(cliente: TestClient, db: Path,
                                             monkeypatch: pytest.MonkeyPatch,
                                             caplog: pytest.LogCaptureFixture) -> None:
    def explode(conteudo: bytes) -> HoleriteLido:
        raise RuntimeError("conteudo-sigiloso")

    monkeypatch.setattr("app.web.rotas.holerites.ler_holerite", explode)
    caplog.set_level(logging.DEBUG)
    r = _enviar(cliente, _conta(db))
    assert r.status_code == 400 and "Não foi possível ler o holerite" in r.text
    assert "conteudo-sigiloso" not in r.text + caplog.text and "RuntimeError" in caplog.text


def test_confirmar_erro_inesperado_mensagem_generica(
        cliente: TestClient, db: Path, lido: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    previa = _enviar(cliente, _conta(db)).text

    def explode(*a: Any, **k: Any) -> Any:
        raise ValueError("detalhe-interno-sigiloso")

    monkeypatch.setattr("app.web.rotas.holerites.importar_holerite", explode)
    caplog.set_level(logging.DEBUG)
    r = _confirmar(cliente, previa)
    assert r.status_code == 500
    assert "Não foi possível importar o holerite. Nada foi gravado." in r.text
    assert "detalhe-interno-sigiloso" not in r.text + caplog.text
    assert "event=holerite_importacao_falhou erro=ValueError" in caplog.text


def test_confirmar_erro_de_regra_mostra_a_mensagem(
        cliente: TestClient, db: Path, lido: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch) -> None:
    from app.dominio.holerites import ErroHolerite
    previa = _enviar(cliente, _conta(db)).text

    def regra(*a: Any, **k: Any) -> Any:
        raise ErroHolerite("Regra inventada violada.")

    monkeypatch.setattr("app.web.rotas.holerites.importar_holerite", regra)
    r = _confirmar(cliente, previa)
    assert r.status_code == 400 and "Regra inventada violada." in r.text


def test_pagina_nao_vaza_dados_pessoais(cliente: TestClient, db: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    """Leitor real sobre palavras sintéticas que contêm nome, CPF e CNPJ."""
    monkeypatch.setattr("app.importacao.holerite.leitor.abrir_pdf",
                        lambda conteudo: [palavras_sinteticas("mensal")])
    conta = _conta(db)
    previa = _enviar(cliente, conta)
    assert previa.status_code == 200 and "SALARIO FICTICIO" in previa.text
    guardados = list(cliente.app.state.holerites._itens.values())  # type: ignore[attr-defined]
    assert len(guardados) == 1  # antes da confirmação (o token ainda existe)
    for p in guardados:
        campos = {k: v for k, v in vars(p).items()}
        assert not any(isinstance(v, (bytes, bytearray)) for v in campos.values())
        assert "nome" not in campos and "conteudo" not in campos
    assert _confirmar(cliente, previa.text, cats=[""] * 11).status_code == 303
    hid = int(_consulta(db, "SELECT id FROM holerites")[0][0])
    paginas = [previa.text, cliente.get("/holerites").text, cliente.get("/importar").text,
               cliente.get(f"/holerites/{hid}/itens").text]
    for html in paginas:
        for dado in (NOME, CPF, CNPJ, "123.456.789", "00.000.000", "FULANO"):
            assert dado not in html, dado


def test_pdf_real_minimo_sem_texto_e_ilegivel(cliente: TestClient, db: Path) -> None:
    r = _enviar(cliente, _conta(db), pdf_minimo(None))
    assert r.status_code == 400 and r.headers["cache-control"] == "no-store"


def test_armazem_de_holerites_expira_e_tem_limite(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.web import uploads
    armazem = uploads.ArmazemHolerites()
    agora = [1000.0]
    monkeypatch.setattr(uploads.time, "monotonic", lambda: agora[0])
    token = armazem.guardar("h" * 64, _h(), 7)
    p = armazem.obter(token)
    assert p is not None and p.conta_id == 7 and p.holerite.competencia == "2026-09"
    assert not hasattr(p, "conteudo") and not hasattr(p, "nome")
    agora[0] += uploads.TTL_SEGUNDOS + 1
    assert armazem.obter(token) is None
    tokens = [armazem.guardar("h", _h(), 1) for _ in range(uploads.MAX_ITENS + 3)]
    assert armazem.obter(tokens[0]) is None and armazem.obter(tokens[-1]) is not None


def test_menu_tem_link_holerites(cliente: TestClient) -> None:
    assert 'href="/holerites"' in cliente.get("/").text


def test_nome_do_arquivo_nunca_e_guardado_logado_nem_exibido(
        cliente: TestClient, db: Path, lido: dict[str, Any],
        caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    sentinela = "MARIA SANTOS - Mes"
    conta = _conta(db)
    r = _enviar(cliente, conta, nome=f"{sentinela}.pdf")
    assert r.status_code == 200 and sentinela not in r.text and "MARIA" not in r.text
    assert "Arquivo PDF enviado" in r.text
    guardados = list(cliente.app.state.holerites._itens.values())  # type: ignore[attr-defined]
    assert "MARIA" not in repr(guardados)
    c = _confirmar(cliente, r.text)
    assert c.status_code == 303 and "MARIA" not in c.headers["location"] + c.text
    assert "MARIA" not in cliente.get("/holerites").text
    assert _consulta(db, "SELECT arquivo_nome FROM holerites") == [(None,)]
    # erro (sem conta) e prévia de arquivo já importado tampouco ecoam o nome
    erro = cliente.post("/holerites/previa", data={"conta_id": ""},
                        files={"arquivo": (f"{sentinela}.pdf", PDF, "application/pdf")})
    assert erro.status_code == 400 and "MARIA" not in erro.text
    assert "MARIA" not in _enviar(cliente, conta, nome=f"{sentinela}.pdf").text
    assert "MARIA" not in caplog.text and sentinela not in caplog.text
