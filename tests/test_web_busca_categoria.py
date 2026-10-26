import json
import re
import shutil
import subprocess
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import conectar
from app.dominio.lancamentos import criar_lancamento_manual
from app.main import create_app
from app.repo import contas

RAIZ = Path(__file__).parent.parent
JS = RAIZ / "app" / "web" / "static" / "js" / "categoria-busca.js"
SCRIPT_TAG = '<script src="/static/js/categoria-busca.js"'
TUDO = {"de": "2000-01-01", "ate": "2100-01-01"}


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "t.db"


@pytest.fixture
def cliente(db: Path) -> Iterator[TestClient]:
    app = create_app(db)
    app.state.hoje_fn = lambda: date(2026, 10, 23)
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


def _novo(db: Path, conta: int, desc: str, categoria_id: int | None = None) -> int:
    con = conectar(db)
    try:
        criar_lancamento_manual(con, conta_id=conta, data=date(2026, 10, 1), descricao=desc,
                                valor_centavos=-1000, categoria_id=categoria_id, compensado=True)
        con.commit()
        return int(con.execute("SELECT id FROM lancamentos WHERE descricao = ?", (desc,)).fetchone()[0])
    finally:
        con.close()


def _cat(db: Path, nome: str) -> int:
    con = conectar(db)
    try:
        return int(con.execute("SELECT id FROM categorias WHERE nome = ?", (nome,)).fetchone()[0])
    finally:
        con.close()


def _selects(html: str) -> dict[str, str]:
    """Mapa id (ou name) -> tag de abertura de cada <select>."""
    out: dict[str, str] = {}
    for m in re.finditer(r"<select\b[^>]*>", html):
        tag = m.group(0)
        chave = re.search(r'\bid="([^"]+)"', tag) or re.search(r'\bname="([^"]+)"', tag)
        assert chave
        out.setdefault(chave.group(1), tag)
    return out


def test_script_servido_sem_urls_nem_innerhtml(cliente: TestClient) -> None:
    r = cliente.get("/static/js/categoria-busca.js")
    assert r.status_code == 200
    assert "http://" not in r.text and "https://" not in r.text
    assert "innerHTML" not in r.text and "outerHTML" not in r.text
    assert "ConsultorFinBusca" in r.text


def test_lancamentos_selects_de_categoria_com_busca(cliente: TestClient, conta: int) -> None:
    r = cliente.get("/lancamentos", params=TUDO)
    assert r.status_code == 200 and SCRIPT_TAG in r.text
    sel = _selects(r.text)
    for chave in ("f-cat", "l-cat", "n-cat-saida", "n-cat-entrada"):
        assert "data-busca" in sel[chave], chave
    for chave in ("f-conta", "f-tipo", "f-status", "n-conta"):
        assert "data-busca" not in sel[chave], chave
    # fallback sem JS: opções completas continuam no HTML
    assert r.text.count("<optgroup") > 20 and "Saúde › Dentista" in r.text


def test_linha_com_busca_inline_e_regra(cliente: TestClient, db: Path, conta: int) -> None:
    _novo(db, conta, "X")
    html = cliente.get("/lancamentos", params=TUDO).text
    for rotulo in ('aria-label="Categoria"', 'aria-label="Categoria da regra"'):
        tags = [t for t in re.findall(r"<select\b[^>]*>", html) if rotulo in t]
        assert tags and all("data-busca" in t for t in tags), rotulo


def test_fragmento_inline_htmx_com_busca(cliente: TestClient, db: Path, conta: int) -> None:
    lid = _novo(db, conta, "Y")
    cid = _cat(db, "Dentista")
    r = cliente.post(f"/lancamentos/{lid}/categoria", data={"categoria_id": str(cid)},
                     headers={"HX-Request": "true"})
    assert r.status_code == 200
    tag = re.search(r"<select\b[^>]*>", r.text)
    assert tag and "data-busca" in tag.group(0) and 'hx-trigger="change"' in tag.group(0)
    assert f'<option value="{cid}" selected>' in r.text


def test_contas_a_pagar_selects_com_busca(cliente: TestClient, conta: int) -> None:
    r = cliente.get("/contas-a-pagar")
    assert r.status_code == 200 and SCRIPT_TAG in r.text
    sel = _selects(r.text)
    for chave in ("p-cat", "r-cat-saida", "r-cat-entrada"):
        assert "data-busca" in sel[chave], chave
    assert "data-busca" not in sel["p-conta"]
    assert "Saúde › Dentista" in r.text


def test_categorias_substituir_por_com_busca(cliente: TestClient, db: Path, conta: int) -> None:
    con = conectar(db)
    try:
        sub = int(con.execute("SELECT f.id FROM categorias f JOIN categorias p ON p.id = f.pai_id "
                              "WHERE f.nome = 'Aluguel' AND p.nome = 'Moradia'").fetchone()[0])
    finally:
        con.close()
    _novo(db, conta, "Aluguel out", sub)
    r = cliente.post(f"/categorias/{sub}/excluir", data={"aba": "saida"})
    assert r.status_code == 200 and SCRIPT_TAG in r.text
    assert "data-busca" in _selects(r.text)["subst"]


# --- lógica pura (Node) ---------------------------------------------------

def _rodar_node(codigo: str) -> Any:
    node = shutil.which("node")
    if node is None and Path(r"C:\Program Files\nodejs\node.exe").exists():
        node = r"C:\Program Files\nodejs\node.exe"
    if node is None:
        pytest.skip("node não está no PATH; testes da lógica pura do JS ignorados")
    p = subprocess.run([node, "-e", codigo], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


PRE = "const B = require(%s);" % json.dumps(JS.as_posix())
ITENS = """const itens = [
 {id:'',rotulo:'Sem categoria',grupo:''},
 {id:'1',rotulo:'Alimentação › Mercado e supermercado',grupo:'Alimentação (saída)'},
 {id:'2',rotulo:'Alimentação › Restaurante',grupo:'Alimentação (saída)'},
 {id:'3',rotulo:'Saúde › Dentista',grupo:'Saúde (saída)'},
 {id:'4',rotulo:'Tarifas bancárias › Tarifa de conta',grupo:'Tarifas bancárias (saída)'},
 {id:'5',rotulo:'Cartão › Tarifa do cartão',grupo:'Cartão (saída)'},
 {id:'6',rotulo:'Lazer › Mercado de pulgas',grupo:'Lazer (saída)'},
 {id:'7',rotulo:'<script>alert("x")</script> › O\\'Neil "q"',grupo:'<b>'}
];"""


def _filtrar(consulta: str) -> list[dict[str, str]]:
    return _rodar_node(f"{PRE}{ITENS}console.log(JSON.stringify(B.filtrar(itens, {json.dumps(consulta)})));")  # type: ignore[no-any-return]


def test_node_normalizar() -> None:
    r = _rodar_node(PRE + "console.log(JSON.stringify([B.normalizar('  ALIMENTAÇÃO  Çá '), B.normalizar('')]));")
    assert r == ["alimentacao ca", ""]


def test_node_acentos_e_caixa() -> None:
    assert "Alimentação › Restaurante" in [i["rotulo"] for i in _filtrar("ALIMENTACAO")]
    assert [i["id"] for i in _filtrar("dentis")] == ["3"]


def test_node_multipalavra_and() -> None:
    assert [i["id"] for i in _filtrar("merc sup")] == ["1"]
    assert _filtrar("merc xyz") == []


def test_node_ranking_prefixo_antes_de_substring() -> None:
    assert [i["id"] for i in _filtrar("mercado")] == ["1", "6"]
    assert [i["id"] for i in _filtrar("tarifa")] == ["4", "5"]
    # "esta" é substring em Restaurante e prefixo em nada; "rest" é prefixo de Restaurante
    assert [i["id"] for i in _filtrar("rest")][0] == "2"
    r = _rodar_node(PRE + "const m=[{id:'a',rotulo:'X › Cartao de mercado',grupo:'X'},"
                    "{id:'b',rotulo:'Y › Mercado',grupo:'Y'}];"
                    "console.log(JSON.stringify(B.filtrar(m,'mercado').map(i=>i.id)));")
    assert r == ["b", "a"]


def test_node_sem_resultado_e_consulta_vazia() -> None:
    assert _filtrar("zzzz") == []
    assert len(_filtrar("")) == 8


def test_node_limite() -> None:
    r = _rodar_node(PRE + "const m=[];for(let i=0;i<200;i++)m.push({id:String(i),rotulo:'A › Item '+i,grupo:'A'});"
                    "console.log(JSON.stringify([B.filtrar(m,'item').length,B.filtrar(m,'item',5).length]));")
    assert r == [50, 5]


def test_node_opcao_vazia() -> None:
    assert [i["id"] for i in _filtrar("sem")] == [""]


def test_node_nomes_hostis_inalterados() -> None:
    esperado = ['<script>alert("x")</script> › O\'Neil "q"']
    assert [i["rotulo"] for i in _filtrar("script")] == esperado
    assert [i["rotulo"] for i in _filtrar("neil")] == esperado


def test_node_enter_so_confirma_escolha_explicita() -> None:
    r = _rodar_node(PRE + "console.log(JSON.stringify(["
                    "B.deveConfirmar('', false),"      # vazia e sem interação: não confirma
                    "B.deveConfirmar('   ', false),"
                    "B.deveConfirmar('merc', false),"  # digitou: confirma o destacado
                    "B.deveConfirmar('', true)]));")   # setas/hover/clique: confirma
    assert r == [False, False, True, True]


def _pos(rect: dict[str, float], largura: int, altura: int) -> dict[str, Any]:
    r = _rodar_node(PRE + f"console.log(JSON.stringify(B.posicionarLista({json.dumps(rect)}, {largura}, {altura})));")
    assert isinstance(r, dict)
    return r


def test_node_posicionar_lista_abaixo_quando_ha_espaco() -> None:
    r = _pos({"left": 100, "right": 300, "top": 100, "bottom": 138}, 1280, 900)
    assert r["lado"] == "abaixo" and r["top"] == 142 and r["left"] == 100
    assert r["minWidth"] == 200 and r["maxHeight"] == 320


def test_node_posicionar_lista_acima_na_ultima_linha() -> None:
    r = _pos({"left": 100, "right": 300, "top": 800, "bottom": 838}, 1280, 900)
    assert r["lado"] == "acima" and r["bottom"] == 104 and "top" not in r
    assert r["maxHeight"] == 320


def test_node_posicionar_lista_limita_altura_ao_espaco_do_lado_maior() -> None:
    r = _pos({"left": 10, "right": 210, "top": 300, "bottom": 338}, 390, 400)
    assert r["lado"] == "acima" and r["maxHeight"] == 292  # 300 - margem 8
    r2 = _pos({"left": 10, "right": 210, "top": 120, "bottom": 158}, 390, 400)
    assert r2["lado"] == "abaixo" and r2["maxHeight"] == 234  # 400 - 158 - 8


def test_node_posicionar_lista_nao_sai_da_janela() -> None:
    r = _pos({"left": 330, "right": 380, "top": 100, "bottom": 138}, 390, 900)
    assert r["left"] >= 8 and r["left"] + r["minWidth"] <= 390 - 8
    assert r["maxWidth"] <= 390 - 16
