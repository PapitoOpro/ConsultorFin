import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import conectar
from app.dominio.categorias import criar_categoria, criar_regra, criar_subcategoria
from app.dominio.lancamentos import criar_lancamento_manual, opcoes_categorias
from app.dominio.recorrentes import criar_recorrente
from app.main import create_app
from app.repo import contas

XSS = "<script>alert(1)</script>"
ESC = "&lt;script&gt;alert(1)&lt;/script&gt;"


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "t.db"


@pytest.fixture
def cliente(db: Path) -> Iterator[TestClient]:
    app = create_app(db)
    app.state.hoje_fn = lambda: date(2026, 10, 23)
    with TestClient(app) as c:
        yield c


def _sql(db: Path, sql: str, *args: Any) -> list[Any]:
    con = conectar(db)
    try:
        return [tuple(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def _id(db: Path, nome: str, pai: str | None = None) -> int:
    if pai is None:
        return int(_sql(db, "SELECT id FROM categorias WHERE nome = ? AND pai_id IS NULL", nome)[0][0])
    return int(_sql(db, "SELECT f.id FROM categorias f JOIN categorias p ON p.id = f.pai_id "
                        "WHERE f.nome = ? AND p.nome = ?", nome, pai)[0][0])


def _existe(db: Path, cid: int) -> bool:
    return bool(_sql(db, "SELECT 1 FROM categorias WHERE id = ?", cid))


def _post(c: TestClient, caminho: str, **dados: str) -> Any:
    return c.post(caminho, data=dados)


@pytest.fixture
def conta(cliente: TestClient, db: Path) -> int:
    con = conectar(db)
    try:
        return contas.criar_conta(con, "Itaú", "corrente", saldo_inicial_centavos=0,
                                  data_saldo_inicial="2026-01-01")
    finally:
        con.close()


def test_tela_lista_taxonomia_e_menu(cliente: TestClient) -> None:
    r = cliente.get("/categorias")
    assert r.status_code == 200
    assert 'href="/categorias"' in r.text
    ent = cliente.get("/categorias?aba=entrada").text
    assert "Salário e Trabalho" in ent and "13º salário" in ent
    assert "Música e Equipamentos" not in ent
    sai = cliente.get("/categorias?aba=saida").text
    assert "Música e Equipamentos" in sai and "13º salário" not in sai
    assert 'href="/categorias?aba=entrada"' in sai


def test_aba_invalida_cai_no_padrao(cliente: TestClient) -> None:
    assert cliente.get("/categorias?aba=xx").status_code == 200


def test_criar_categoria_e_subcategoria(cliente: TestClient, db: Path) -> None:
    r = _post(cliente, "/categorias/criar", nome="Pets", tipo="saida", aba="saida")
    assert r.status_code == 200 and "Pets" in r.text
    pets = _id(db, "Pets")
    assert _sql(db, "SELECT tipo FROM categorias WHERE id = ?", pets) == [("saida",)]
    r = _post(cliente, f"/categorias/{pets}/subcategoria", nome="Ração", aba="saida")
    assert r.status_code == 200 and "Ração" in r.text
    assert _id(db, "Ração", "Pets")


def test_nome_duplicado_variantes_recusado(cliente: TestClient, db: Path) -> None:
    antes = _sql(db, "SELECT COUNT(*) FROM categorias")
    for nome in ("moradia", "  MORADIA ", "Moradía"):
        r = _post(cliente, "/categorias/criar", nome=nome, tipo="saida", aba="saida")
        assert r.status_code == 400 and "Já existe uma categoria" in r.text
    r = _post(cliente, "/categorias/criar", nome="   ", tipo="saida")
    assert r.status_code == 400 and "não pode ser vazio" in r.text
    r = _post(cliente, "/categorias/criar", nome="X", tipo="outro")
    assert r.status_code == 400 and "Tipo inválido" in r.text
    moradia = _id(db, "Moradia")
    r = _post(cliente, f"/categorias/{moradia}/subcategoria", nome="ALUGUEL")
    assert r.status_code == 400 and "Já existe" in r.text
    assert _sql(db, "SELECT COUNT(*) FROM categorias") == antes


def test_renomear(cliente: TestClient, db: Path) -> None:
    moradia = _id(db, "Moradia")
    r = _post(cliente, f"/categorias/{moradia}/renomear", nome="Casa", aba="saida")
    assert r.status_code == 200 and "Casa" in r.text
    assert _sql(db, "SELECT nome FROM categorias WHERE id = ?", moradia) == [("Casa",)]
    r = _post(cliente, f"/categorias/{moradia}/renomear", nome="alimentação")
    assert r.status_code == 400 and "Já existe" in r.text


def test_mudar_tipo_propaga(cliente: TestClient, db: Path) -> None:
    pai = _id(db, "Moradia")
    r = _post(cliente, f"/categorias/{pai}/tipo", tipo="entrada", aba="saida")
    assert r.status_code == 200
    assert _sql(db, "SELECT DISTINCT tipo FROM categorias WHERE id = ? OR pai_id = ?",
                pai, pai) == [("entrada",)]
    assert "Moradia" in cliente.get("/categorias?aba=entrada").text
    sub = _id(db, "Aluguel", "Moradia")
    r = _post(cliente, f"/categorias/{sub}/tipo", tipo="saida")
    assert r.status_code == 400 and "segue o da categoria" in r.text


def test_mover_subcategoria(cliente: TestClient, db: Path) -> None:
    sub = _id(db, "Aluguel", "Moradia")
    casa = _id(db, "Contas da Casa")
    r = _post(cliente, f"/categorias/{sub}/mover", destino_id=str(casa), aba="saida")
    assert r.status_code == 200
    assert _sql(db, "SELECT pai_id FROM categorias WHERE id = ?", sub) == [(casa,)]
    ent = _id(db, "Renda Extra")
    r = _post(cliente, f"/categorias/{sub}/mover", destino_id=str(ent))
    assert r.status_code == 400 and "mesmo tipo" in r.text
    assert _sql(db, "SELECT pai_id FROM categorias WHERE id = ?", sub) == [(casa,)]
    r = _post(cliente, f"/categorias/{_id(db, 'Moradia')}/mover", destino_id=str(casa))
    assert r.status_code == 400 and "Só subcategorias podem ser movidas" in r.text


def test_excluir_sem_uso(cliente: TestClient, db: Path) -> None:
    con = conectar(db)
    try:
        novo = criar_categoria(con, "Temporária", "saida")
        con.commit()
    finally:
        con.close()
    r = _post(cliente, f"/categorias/{novo}/excluir", aba="saida")
    assert r.status_code == 200 and not _existe(db, novo)


def test_excluir_com_subcategorias_recusado(cliente: TestClient, db: Path) -> None:
    pai = _id(db, "Moradia")
    r = _post(cliente, f"/categorias/{pai}/excluir")
    assert r.status_code == 400 and "subcategorias" in r.text
    assert _existe(db, pai)


def _usar(db: Path, conta: int, cat: int) -> None:
    con = conectar(db)
    try:
        criar_lancamento_manual(con, conta_id=conta, data=date(2026, 10, 1), descricao="Aluguel out",
                                valor_centavos=-100000, categoria_id=cat, compensado=True)
        criar_regra(con, "ALUGUEL", cat)
        criar_recorrente(con, nome="Aluguel", tipo="saida", dia=5, valor_estimado_centavos=100000,
                         conta_id=conta, categoria_id=cat)
        con.commit()
    finally:
        con.close()


def test_excluir_em_uso_exige_escolha_e_mostra_contagens(
        cliente: TestClient, db: Path, conta: int) -> None:
    sub = _id(db, "Aluguel", "Moradia")
    _usar(db, conta, sub)
    r = _post(cliente, f"/categorias/{sub}/excluir", aba="saida")
    assert r.status_code == 200 and _existe(db, sub)
    assert "1 lançamento" in r.text and "1 conta fixa" in r.text and "1 regra" in r.text
    assert "Substituir por" in r.text and "Deixar sem categoria" in r.text
    m = re.search(r'<select[^>]*name="substituir_por".*?</select>', r.text, re.S)
    assert m and "Moradia › Prestação do imóvel" in m.group(0)
    assert "Moradia › Aluguel" not in m.group(0) and "Salário" not in m.group(0)
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos WHERE categoria_id = ?", sub) == [(1,)]


def test_excluir_substituir_por(cliente: TestClient, db: Path, conta: int) -> None:
    sub = _id(db, "Aluguel", "Moradia")
    destino = _id(db, "Prestação do imóvel", "Moradia")
    _usar(db, conta, sub)
    r = _post(cliente, f"/categorias/{sub}/excluir", acao="substituir", substituir_por=str(destino))
    assert r.status_code == 200 and not _existe(db, sub)
    assert _sql(db, "SELECT COUNT(*) FROM lancamentos WHERE categoria_id = ?", destino) == [(1,)]
    assert _sql(db, "SELECT COUNT(*) FROM regras_categoria WHERE categoria_id = ?", destino) == [(1,)]
    assert _sql(db, "SELECT COUNT(*) FROM recorrentes WHERE categoria_id = ?", destino) == [(1,)]


def test_excluir_substituir_invalido(cliente: TestClient, db: Path, conta: int) -> None:
    sub = _id(db, "Aluguel", "Moradia")
    _usar(db, conta, sub)
    r = _post(cliente, f"/categorias/{sub}/excluir", acao="substituir", substituir_por="")
    assert r.status_code == 400
    outro_tipo = _id(db, "Salário", "Salário e Trabalho")
    r = _post(cliente, f"/categorias/{sub}/excluir", acao="substituir", substituir_por=str(outro_tipo))
    assert r.status_code == 400 and "mesmo tipo" in r.text
    r = _post(cliente, f"/categorias/{sub}/excluir", acao="substituir", substituir_por=str(sub))
    assert r.status_code == 400
    r = _post(cliente, f"/categorias/{sub}/excluir", acao="substituir", substituir_por="99999")
    assert r.status_code == 400 and "não encontrada" in r.text
    assert _existe(db, sub)


def test_excluir_deixar_sem_categoria(cliente: TestClient, db: Path, conta: int) -> None:
    sub = _id(db, "Aluguel", "Moradia")
    _usar(db, conta, sub)
    r = _post(cliente, f"/categorias/{sub}/excluir", acao="sem")
    assert r.status_code == 200 and not _existe(db, sub)
    assert _sql(db, "SELECT categoria_id FROM lancamentos") == [(None,)]
    assert _sql(db, "SELECT categoria_id FROM recorrentes") == [(None,)]
    assert _sql(db, "SELECT COUNT(*) FROM regras_categoria") == [(0,)]


@pytest.mark.parametrize("caminho,status,msg", [
    ("criar", 400, "não pode ser vazio"),
    ("abc/renomear", 400, "Categoria inválida."),
    ("9999999/renomear", 404, "Categoria não encontrada."),
    ("abc/excluir", 400, "Categoria inválida."),
    ("٣/tipo", 400, "Categoria inválida."),
    ("1;DROP/mover", 400, "Categoria inválida."),
    ("99999/subcategoria", 404, "Categoria não encontrada.")])
def test_ids_invalidos_amigaveis(cliente: TestClient, caminho: str, status: int, msg: str) -> None:
    r = _post(cliente, f"/categorias/{caminho}", nome="", tipo="saida", destino_id="1")
    assert r.status_code == status and "Traceback" not in r.text
    assert "Categorias" in r.text and msg in r.text


def test_destino_e_substituta_invalidos_no_feminino(cliente: TestClient, db: Path) -> None:
    sub = _id(db, "Aluguel", "Moradia")
    r = _post(cliente, f"/categorias/{sub}/mover", destino_id="x")
    assert r.status_code == 400 and "Categoria de destino inválida." in r.text


@pytest.mark.parametrize("nome", ["Ruim\x00", "Casa\u202eodnum", "A\u200bB", "y" * 81])
def test_nome_invalido_vira_400_sem_alterar(cliente: TestClient, db: Path, nome: str) -> None:
    antes = _sql(db, "SELECT COUNT(*) FROM categorias")
    r = _post(cliente, "/categorias/criar", nome=nome, tipo="saida", aba="saida")
    assert r.status_code == 400 and ("caracteres inválidos" in r.text or "até 80" in r.text)
    pai = _id(db, "Moradia")
    r = _post(cliente, f"/categorias/{pai}/subcategoria", nome=nome)
    assert r.status_code == 400
    r = _post(cliente, f"/categorias/{pai}/renomear", nome=nome)
    assert r.status_code == 400
    assert _sql(db, "SELECT COUNT(*) FROM categorias") == antes


def test_aba_entradas_vazia(cliente: TestClient, db: Path) -> None:
    con = conectar(db)
    try:
        con.execute("DELETE FROM categorias WHERE pai_id IS NOT NULL AND tipo = 'entrada'")
        con.execute("DELETE FROM categorias WHERE tipo = 'entrada'")
        con.commit()
    finally:
        con.close()
    r = cliente.get("/categorias?aba=entrada")
    assert r.status_code == 200 and "Nenhuma categoria de entrada." in r.text
    assert "Nenhuma categoria de saída." not in r.text


def test_aviso_de_mudanca_de_tipo_na_tela(cliente: TestClient) -> None:
    t = cliente.get("/categorias?aba=saida").text
    assert "Mudar o tipo também muda o tipo de todas as subcategorias." in t
    assert "Mudar o tipo desta categoria também muda o tipo de todas as subcategorias. Continuar?" in t


def test_destino_invalido_no_mover(cliente: TestClient, db: Path) -> None:
    sub = _id(db, "Aluguel", "Moradia")
    for destino in ("", "x", "99999"):
        r = _post(cliente, f"/categorias/{sub}/mover", destino_id=destino)
        assert r.status_code == (404 if destino == "99999" else 400)


def test_origin_cruzado_403(cliente: TestClient, db: Path) -> None:
    r = cliente.post("/categorias/criar", data={"nome": "Hack", "tipo": "saida"},
                     headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert not _sql(db, "SELECT 1 FROM categorias WHERE nome = 'Hack'")


def test_xss_escapado_nas_telas(cliente: TestClient, db: Path, conta: int) -> None:
    r = _post(cliente, "/categorias/criar", nome=XSS, tipo="saida", aba="saida")
    assert r.status_code == 200 and XSS not in r.text and ESC in r.text
    pai = _id(db, XSS)
    r = _post(cliente, f"/categorias/{pai}/subcategoria", nome=XSS)
    assert XSS not in r.text
    con = conectar(db)
    try:
        criar_lancamento_manual(con, conta_id=conta, data=date(2026, 10, 1), descricao="x",
                                valor_centavos=-100, categoria_id=_id(db, XSS, XSS), compensado=True)
        con.commit()
    finally:
        con.close()
    for url in ("/categorias?aba=saida", "/lancamentos?de=2000-01-01&ate=2100-01-01",
                "/contas-a-pagar"):
        t = cliente.get(url).text
        assert XSS not in t and ESC in t, url
    t = _post(cliente, f"/categorias/{_id(db, XSS, XSS)}/excluir").text
    assert XSS not in t and ESC in t


def test_opcoes_categorias_rotulos_e_ordem(db: Path, cliente: TestClient) -> None:
    con = conectar(db)
    try:
        todas = opcoes_categorias(con)
        ent = opcoes_categorias(con, "entrada")
        sai = opcoes_categorias(con, "saida")
    finally:
        con.close()
    rotulos = {r: t for _, r, t in todas}
    assert rotulos["Moradia"] == "saida" and rotulos["Moradia › Aluguel"] == "saida"
    assert rotulos["Salário e Trabalho › 13º salário"] == "entrada"
    assert {t for _, _, t in ent} == {"entrada"} and {t for _, _, t in sai} == {"saida"}
    assert len(ent) + len(sai) == len(todas)
    tipos = [t for _, _, t in todas]
    assert tipos == sorted(tipos)
    nomes = [r for _, r, _ in sai]
    assert nomes.index("Moradia") < nomes.index("Moradia › Aluguel")


def _select(html: str, sel_id: str) -> str:
    m = re.search(rf'<select id="{sel_id}".*?</select>', html, re.S)
    assert m, sel_id
    return m.group(0)


def test_seletores_hierarquicos_em_lancamentos(cliente: TestClient) -> None:
    t = cliente.get("/lancamentos?de=2000-01-01&ate=2100-01-01").text
    assert "<optgroup" in t
    assert "Moradia › Aluguel" in _select(t, "f-cat")
    assert "Moradia › Aluguel" in _select(t, "l-cat")
    saida = _select(t, "n-cat-saida")
    entrada = _select(t, "n-cat-entrada")
    assert "Moradia › Aluguel" in saida and "13º salário" not in saida
    assert "Salário e Trabalho › 13º salário" in entrada and "Moradia" not in entrada


def test_lancamento_manual_usa_categoria_do_sinal(
        cliente: TestClient, db: Path, conta: int) -> None:
    ent = _id(db, "13º salário", "Salário e Trabalho")
    sai = _id(db, "Aluguel", "Moradia")
    base = {"data": "2026-10-10", "descricao": "Teste", "valor": "10,00", "conta_id": str(conta),
            "voltar": ""}
    cats = {"categoria_entrada": str(ent), "categoria_saida": str(sai)}
    assert cliente.post("/lancamentos/novo", data={**base, "sinal": "entrada", **cats}).status_code == 200
    assert cliente.post("/lancamentos/novo", data={**base, "sinal": "saida", **cats}).status_code == 200
    assert _sql(db, "SELECT valor_centavos, categoria_id FROM lancamentos ORDER BY id") == [
        (1000, ent), (-1000, sai)]


def test_seletores_em_contas_a_pagar(cliente: TestClient) -> None:
    t = cliente.get("/contas-a-pagar").text
    assert "Moradia › Aluguel" in _select(t, "p-cat")
    assert "13º salário" not in _select(t, "p-cat")
    assert "Moradia › Aluguel" in _select(t, "r-cat-saida")
    assert "13º salário" in _select(t, "r-cat-entrada")
    assert "Moradia" not in _select(t, "r-cat-entrada")


def test_recorrente_usa_categoria_do_tipo(cliente: TestClient, db: Path, conta: int) -> None:
    ent = _id(db, "13º salário", "Salário e Trabalho")
    sai = _id(db, "Aluguel", "Moradia")
    r = cliente.post("/contas-a-pagar/recorrente", data={
        "nome": "Salário", "tipo": "entrada", "dia": "5", "valor_estimado": "100,00",
        "conta_id": str(conta), "categoria_entrada": str(ent), "categoria_saida": str(sai)})
    assert r.status_code == 200
    assert _sql(db, "SELECT categoria_id FROM recorrentes") == [(ent,)]


def test_mesmo_nome_de_subcategoria_em_pais_diferentes(db: Path, cliente: TestClient) -> None:
    con = conectar(db)
    try:
        a = criar_categoria(con, "A", "saida")
        criar_subcategoria(con, a, "Manutenção e reparos")
        con.commit()
        rot = [r for _, r, _ in opcoes_categorias(con)]
    finally:
        con.close()
    assert "A › Manutenção e reparos" in rot and "Moradia › Manutenção e reparos" in rot
