"""Task 3 do redesign: Lançamentos, Importar, Contas a pagar e Categorias no novo design."""
import re
from collections.abc import Iterator
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import conectar
from app.dominio.lancamentos import criar_lancamento_manual
from app.dominio.parcelas import criar_parcelamento
from app.dominio.recorrentes import criar_recorrente
from app.main import create_app
from app.repo import contas

PAGINAS = ["/lancamentos", "/importar", "/contas-a-pagar", "/categorias"]
VAZIOS = ["/lancamentos", "/importar", "/contas-a-pagar"]
HOJE = date.today()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "t.db"


@pytest.fixture
def vazio(db: Path) -> Iterator[TestClient]:
    with TestClient(create_app(db)) as c:
        yield c


@pytest.fixture
def cheio(db: Path) -> Iterator[TestClient]:
    with TestClient(create_app(db)) as c:
        con = conectar(db)
        try:
            conta = contas.criar_conta(con, "Itaú", "corrente", saldo_inicial_centavos=0,
                                       data_saldo_inicial="2026-01-01")
            con.commit()
            criar_lancamento_manual(con, conta_id=conta, data=HOJE, descricao="Mercado",
                                    valor_centavos=-1000, compensado=True)
            con.commit()
            criar_parcelamento(con, descricao="Geladeira", valor_total_centavos=30000, n_parcelas=3,
                               primeiro_vencimento=HOJE, conta_id=conta, categoria_id=None)
            con.commit()
            criar_recorrente(con, nome="Aluguel", tipo="saida", dia=10, valor_estimado_centavos=100000,
                             conta_id=conta, categoria_id=None)
            con.commit()
        finally:
            con.close()
        yield c


class _Analisa(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.pilha: list[tuple[str, dict[str, str | None]]] = []
        self.botoes_sem_nome: list[str] = []
        self.tabelas_fora: int = 0
        self.formularios: list[dict[str, str | None]] = []
        self._texto_botao: list[str] | None = None
        self._attrs_botao: dict[str, str | None] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag in ("br", "input", "meta", "link", "img", "hr", "path", "circle", "rect", "line", "polyline", "polygon"):
            if tag == "input" and a.get("type") == "hidden":
                return
            return
        if tag == "table" and not any("tabela-rolavel" in (p[1].get("class") or "") for p in self.pilha):
            self.tabelas_fora += 1
        if tag == "form":
            self.formularios.append(a)
        if tag == "button":
            self._texto_botao, self._attrs_botao = [], a
        self.pilha.append((tag, a))

    def handle_endtag(self, tag: str) -> None:
        if tag == "button" and self._texto_botao is not None:
            nome = "".join(self._texto_botao).strip() or self._attrs_botao.get("aria-label") or ""
            if not nome:
                self.botoes_sem_nome.append(str(self._attrs_botao))
            self._texto_botao = None
        for i in range(len(self.pilha) - 1, -1, -1):
            if self.pilha[i][0] == tag:
                del self.pilha[i:]
                break

    def handle_data(self, data: str) -> None:
        if self._texto_botao is not None:
            self._texto_botao.append(data)


def _analisa(html: str) -> _Analisa:
    p = _Analisa()
    p.feed(html)
    return p


@pytest.mark.parametrize("rota", PAGINAS)
def test_cabecalho_com_titulo_e_subtitulo_sem_url_externa(cheio: TestClient, rota: str) -> None:
    html = cheio.get(rota).text
    cab = re.search(r'<header class="cabecalho-pagina">(.*?)</header>', html, re.S)
    assert cab, rota
    assert "<h1" in cab.group(1) and 'class="subtitulo"' in cab.group(1)
    assert not re.search(r"https?://", html), rota


@pytest.mark.parametrize("rota", PAGINAS)
def test_botoes_com_nome_e_tabelas_em_contenedor_rolavel(cheio: TestClient, rota: str) -> None:
    a = _analisa(cheio.get(rota).text)
    assert a.botoes_sem_nome == []
    assert a.tabelas_fora == 0


def test_formularios_mantem_action_e_method(cheio: TestClient) -> None:
    def forms(rota: str) -> set[tuple[str | None, str | None]]:
        return {(f.get("action"), (f.get("method") or "get").lower()) for f in _analisa(cheio.get(rota).text).formularios}

    lanc = forms("/lancamentos")
    for par in [("/lancamentos", "get"), ("/lancamentos/novo", "post"), ("/lancamentos/lote", "post")]:
        assert par in lanc
    assert any(a and a.endswith("/regra") for a, _ in lanc) and any(a and a.endswith("/pagar") for a, _ in lanc)
    imp = forms("/importar")
    for par in [("/importar/previa", "post"), ("/importar/conta", "post"), ("/importar/conta/saldo", "post")]:
        assert par in imp
    cp = forms("/contas-a-pagar")
    for par in [("/contas-a-pagar", "get"), ("/contas-a-pagar/parcelamento", "post"),
                ("/contas-a-pagar/recorrente", "post"), ("/contas-a-pagar/conta", "post")]:
        assert par in cp
    assert any(a and "/grupo/" in a and a.endswith("/excluir") for a, _ in cp)
    cat = forms("/categorias")
    assert ("/categorias/criar", "post") in cat


@pytest.mark.parametrize("rota", VAZIOS)
def test_estado_vazio_usa_componente_vazio(vazio: TestClient, rota: str) -> None:
    assert 'class="vazio' in vazio.get(rota).text


def test_upload_mantem_campos_e_indicador_de_carregamento(vazio: TestClient) -> None:
    html = vazio.get("/importar").text
    assert 'enctype="multipart/form-data"' in html and 'name="arquivo"' in html
    assert "data-carregando" in html and 'class="spinner"' in html
    assert 'class="etapas"' in html and 'aria-current="step"' in html


def test_abas_de_categoria_como_controle_segmentado(vazio: TestClient) -> None:
    html = vazio.get("/categorias?aba=entrada").text
    assert "filtro-periodo" in html
    assert re.findall(r'<a [^>]*aria-current="true"[^>]*>Entradas</a>', html)


def test_mensagem_de_sucesso_usa_alerta_de_sucesso(cheio: TestClient) -> None:
    conta = re.search(r'<option value="(\d+)">Itaú', cheio.get("/lancamentos").text)
    assert conta
    r = cheio.post("/lancamentos/novo", data={
        "data": HOJE.isoformat(), "descricao": "Pão", "valor": "5,00", "sinal": "saida",
        "conta_id": conta.group(1), "voltar": "/lancamentos"})
    assert r.status_code == 200 and 'class="alerta sucesso"' in r.text


def test_dropzone_com_input_oculto_visualmente_e_foco_no_input(vazio: TestClient) -> None:
    html = vazio.get("/importar").text
    assert re.search(r'<input id="arquivo" class="sr-only" name="arquivo" type="file"', html)
    css = (Path(__file__).resolve().parent.parent / "app/web/static/css/tema.css").read_text(encoding="utf-8")
    assert ".dropzone:has(+ input:focus-visible)" in css


def test_celular_colunas_secundarias_viram_texto_junto_da_descricao(cheio: TestClient) -> None:
    html = cheio.get("/contas-a-pagar").text
    assert 'class="mob-info">Vence em' in html and '<th class="sec">Vencimento</th>' in html
    lanc = cheio.get("/lancamentos").text
    assert 'class="mob-valor' in lanc
