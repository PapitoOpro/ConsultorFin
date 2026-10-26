import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

WEB = Path(__file__).resolve().parent.parent / "app" / "web"
PAGINAS = {
    "/": "Início", "/lancamentos": "Lançamentos", "/importar": "Importar",
    "/contas-a-pagar": "Contas a pagar", "/categorias": "Categorias",
}
HREFS = ("/", "/lancamentos", "/importar", "/contas-a-pagar", "/categorias")


@pytest.fixture
def cliente(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(tmp_path / "t.db")) as c:
        yield c


def _css() -> str:
    return (WEB / "static" / "css" / "tema.css").read_text(encoding="utf-8")


def _token(nome: str) -> str:
    m = re.search(rf"^\s*{re.escape(nome)}:\s*(#[0-9A-Fa-f]{{6}})\s*;", _css(), re.M)
    assert m, nome
    return m.group(1)


def _lum(hex_: str) -> float:
    canais = [int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in canais]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _razao(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_tokens_novos_em_tema_css() -> None:
    css = _css()
    for v in (
        "--fundo: #080912", "--fundo-menu: #0D0D19", "--superficie: #111120",
        "--superficie-2: #171728", "--borda: #242438", "--texto: #F5F5FA",
        "--texto-secundario: #9292AB", "--texto-auxiliar: #6D6D88",
        "--primaria: #00D6A0", "--entrada: #00D6A0", "--saida: #FF5470",
        "--alerta: #FFB84D", "--info: #4DA3FF", "--roxo: #A78BFA",
        "--raio: 14px", "--raio-pequeno: 10px", "--sombra:", "--transicao:",
    ):
        assert v in css, v
    assert "prefers-reduced-motion" in css
    assert ":focus-visible" in css


@pytest.mark.parametrize("fundo", ["--fundo", "--fundo-menu", "--superficie", "--superficie-2"])
def test_contraste_texto(fundo: str) -> None:
    bg = _token(fundo)
    assert _razao(_token("--texto"), bg) >= 4.5
    assert _razao(_token("--texto-secundario"), bg) >= 4.5


def test_contraste_texto_auxiliar_decorativo() -> None:
    for fundo in ("--fundo", "--superficie"):
        assert _razao(_token("--texto-auxiliar"), _token(fundo)) >= 3.0


def test_contraste_cores_semanticas_sobre_superficie() -> None:
    for nome in ("--primaria", "--saida", "--alerta", "--info"):
        assert _razao(_token(nome), _token("--superficie")) >= 4.5, nome


def test_sem_urls_externas_no_shell() -> None:
    for p in (
        WEB / "templates" / "base.html", WEB / "templates" / "_icones.html",
        WEB / "static" / "css" / "tema.css", WEB / "static" / "js" / "graficos-tema.js",
    ):
        assert not re.search(r"https?://", p.read_text(encoding="utf-8")), p


def test_graficos_tema_js_seguro() -> None:
    js = (WEB / "static" / "js" / "graficos-tema.js").read_text(encoding="utf-8")
    assert "window.ConsultorFinTema" in js
    assert "innerHTML" not in js
    assert "prefers-reduced-motion" in js
    graficos = (WEB / "static" / "js" / "graficos.js").read_text(encoding="utf-8")
    assert "ConsultorFinTema" in graficos
    assert "innerHTML" not in graficos


@pytest.mark.parametrize("rota", list(PAGINAS))
def test_shell_pagina(cliente: TestClient, rota: str) -> None:
    r = cliente.get(rota)
    assert r.status_code == 200
    assert 'lang="pt-BR"' in r.text
    assert len(re.findall(r'<a [^>]*class="[^"]*\bitem-menu\b', r.text)) == 5
    for h in HREFS:
        assert f'href="{h}"' in r.text
    ativos = re.findall(r'<a [^>]*aria-current="page"[^>]*>', r.text)
    assert len(ativos) == 1
    assert f'href="{rota}"' in ativos[0]
    assert 'id="botao-menu"' in r.text and 'aria-expanded="false"' in r.text
    assert 'aria-controls="menu-principal"' in r.text
    assert 'id="menu-principal"' in r.text
    assert 'class="cabecalho-pagina"' in r.text
    assert r.text.count("<h1") == 1
    assert PAGINAS[rota] in re.search(r"<h1[^>]*>(.*?)</h1>", r.text, re.S).group(1)  # type: ignore[union-attr]
    assert '<script src="/static/js/graficos-tema.js"' in r.text


def test_graficos_tema_carregado_antes_de_graficos() -> None:
    base = (WEB / "templates" / "base.html").read_text(encoding="utf-8")
    assert base.index("graficos-tema.js") < base.index("block cabecalho %}")


def _icone(cliente: TestClient, expr: str) -> str:
    env = cliente.app.state.templates.env  # type: ignore[attr-defined]
    return str(env.from_string("{% from '_icones.html' import icone %}" + expr).render())


def test_macro_icone(cliente: TestClient) -> None:
    for nome in ("layout-dashboard", "receipt", "upload", "calendar-clock", "tags", "menu", "x",
                 "arrow-up-right", "arrow-down-right", "trending-up", "trending-down",
                 "alert-triangle", "check-circle", "info", "plus", "pencil", "trash",
                 "chevron-down", "inbox"):
        out = _icone(cliente, f"{{{{ icone('{nome}') }}}}")
        assert out.startswith("<svg") and 'aria-hidden="true"' in out, nome
        assert 'width="18"' in out
    assert 'width="24"' in _icone(cliente, "{{ icone('menu', 24) }}")


def test_macro_icone_desconhecido_vazio(cliente: TestClient) -> None:
    assert _icone(cliente, "{{ icone('nao-existe') }}").strip() == ""
    assert _icone(cliente, "{{ icone('<script>') }}").strip() == ""


def test_lucide_licenca_em_third_party() -> None:
    txt = (WEB / "static" / "THIRD_PARTY.txt").read_text(encoding="utf-8")
    assert "lucide" in txt.lower() and "ISC" in txt


# ---------- rodada de correções ----------

def _mistura(rgba: str, base: str) -> str:
    r, g, b, a = (float(x) for x in re.findall(r"[\d.]+", rgba)[:4])
    bg = [int(base[i:i + 2], 16) for i in (1, 3, 5)]
    out = [round(c * a + k * (1 - a)) for c, k in zip((r, g, b), bg)]
    return "#%02X%02X%02X" % tuple(out)


def _regra(seletor: str) -> str:
    m = re.search(rf"^{re.escape(seletor)} \{{([^}}]*)\}}", _css(), re.M)
    assert m, seletor
    return m.group(1)


def _var(texto: str) -> str:
    m = re.search(r"(?<![-\w])color:\s*var\((--[\w-]+)\)", texto)
    assert m, texto
    return _token(m.group(1))


@pytest.mark.parametrize("seletor", [
    ".selo", ".selo.entrada", ".selo.saida", ".selo.info", ".selo.roxo", ".selo.neutro",
    ".alerta", ".alerta.info", ".alerta.sucesso", ".alerta.erro",
])
def test_contraste_selos_e_alertas_sobre_fundo_tintado(seletor: str) -> None:
    regra = _regra(seletor)
    texto = _var(regra)
    fundo = re.search(r"background:\s*(rgba\([^)]*\)|var\(--[\w-]+\))", regra)
    assert fundo
    cor_fundo = fundo.group(1)
    for base in ("--superficie", "--fundo"):
        bg = _token(cor_fundo[4:-1]) if cor_fundo.startswith("var(") else _mistura(cor_fundo, _token(base))
        assert _razao(texto, bg) >= 4.5, (seletor, base)


def test_contraste_botao_primario_e_kpi_destaque() -> None:
    assert _razao(_token("--fundo"), _token("--primaria")) >= 4.5
    for fundo in ("--primaria", "--primaria-escura"):
        assert _razao(_token("--sobre-primaria"), _token(fundo)) >= 4.5
        assert _razao(_token("--sobre-primaria-forte"), _token(fundo)) >= 4.5


def test_sem_cores_hex_fora_de_root() -> None:
    css = re.sub(r":root \{.*?\n\}", "", _css(), count=1, flags=re.S)
    assert not re.findall(r"#[0-9A-Fa-f]{3,8}\b", css)


def test_css_sem_regras_de_irmaos_e_sem_media_duplicada() -> None:
    css = _css()
    assert ".bloco + " not in css and "form + .bloco" not in css
    assert css.count("@media (max-width: 700px)") == 1
    assert "overflow-x: auto" in _regra(".tabela-rolavel")
    assert "@media (max-width: 1280px) { .tabela-rolavel" not in css


@pytest.mark.parametrize("rota", list(PAGINAS))
def test_skip_link_e_regiao_principal(cliente: TestClient, rota: str) -> None:
    t = cliente.get(rota).text
    assert t.index('class="pular-conteudo"') < t.index('class="menu-lateral"')
    assert 'href="#conteudo"' in t and 'id="conteudo"' in t
    assert "<aside" not in t
    assert t.count('aria-label="Menu principal"') == 1 and "<nav class=\"menu-nav\"" in t


def test_tabelas_rolaveis_focaveis(cliente: TestClient) -> None:
    t = cliente.get("/importar").text
    for m in re.findall(r'<div class="tabela-rolavel"[^>]*>', t):
        assert 'tabindex="0"' in m and 'role="region"' in m and "aria-label=" in m


def test_radios_e_caixas_fora_das_regras_de_largura_e_altura() -> None:
    css = _css()
    excl = '[type="checkbox"], [type="radio"]'
    m = re.search(r"^\.filtros input:not\(([^)]*)\), \.filtros select \{ width: 100%; \}", css, re.M)
    assert m and m.group(1) == excl
    altura = re.search(r"^:is\([^)]*\) :is\(input:not\(([^)]*)\)", css, re.M)
    assert altura and 'type="checkbox"' in altura.group(1) and 'type="radio"' in altura.group(1)
    assert 'min-height: 0' in _regra('.filtros input[type="checkbox"], .filtros input[type="radio"]')
    assert 'width: 18px' in _regra('.filtros input[type="checkbox"], .filtros input[type="radio"]')
    base = _regra('input[type="checkbox"], input[type="radio"]')
    assert "accent-color: var(--primaria)" in base and "width: 18px" in base and "height: 18px" in base
    assert 'label:has(> input[type="checkbox"])' in css  # rótulo inline com área de toque
    # nenhuma regra de largura total/altura mínima atinge radio/caixa sem exclusão
    for linha in css.splitlines():
        if re.search(r"(?<![-.\w])input(?![-\w])", linha) and (re.search(r"(?<![-\w])width: 100%", linha) or "min-height: 38px" in linha):
            assert "checkbox" in linha, linha


def test_espacamento_unico_sem_margens_duplas() -> None:
    css = _css()
    assert "main.conteudo > * { margin: 0; }" in css
    assert ".abas { display: flex; gap: 4px; margin-bottom" not in css
    assert "gap: var(--espaco)" in _regra(".lista-categorias")
    assert "margin-top" not in _regra(".lista-categorias")


def test_estados_vazios_usam_componente(cliente: TestClient) -> None:
    t = cliente.get("/contas-a-pagar").text
    assert 'class="vazio vazio-compacto"' in t and "Nenhuma conta vencida." in t
    t = cliente.get("/lancamentos").text
    assert "Nenhum lançamento encontrado para este filtro." in t and 'class="vazio vazio-compacto"' in t
    env = cliente.app.state.templates.env  # type: ignore[attr-defined]
    out = env.from_string('{% from "_macros.html" import estado_vazio %}{{ estado_vazio("Nenhuma categoria de entrada.", "tags") }}').render()
    assert 'class="vazio-icone"' in out and "<svg" in out and "Nenhuma categoria de entrada." in out


def test_summary_acoes_inline_com_marcador() -> None:
    regra = _regra("details.linha-acoes summary")
    assert "inline-flex" in regra and "list-style: none" in regra
