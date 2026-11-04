import json
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def _retrato(**sobre: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "titulo": "Gastos de outubro", "periodo": {"de": "2026-10-01", "ate": "2026-10-31"},
        "observacao": "Observação", "gerado_em": "2026-10-23T10:30:00",
        "kpis": [{"rotulo": "Saídas", "formato": "moeda", "valor": 123456, "delta_pct": -5.0}],
        "graficos": [{"tipo": "barras_h", "titulo": "Por categoria", "formato": "moeda", "largura": "cheia",
                      "linhas": [{"nome": "Mercado", "valor": 5000, "serie": None}]}],
        "tabela": {"titulo": "Lançamentos", "colunas": ["Descrição", "Valor"],
                   "linhas": [["Padaria", 1250]], "truncado": False},
    }
    base.update(sobre)
    return base


@pytest.fixture
def cliente(tmp_path: Path) -> Iterator[TestClient]:
    app = create_app(tmp_path / "t.db")
    app.state.dir_relatorios = tmp_path / "rel"
    (tmp_path / "rel").mkdir()
    with TestClient(app) as c:
        yield c


def _salvar(cliente: TestClient, retrato: dict[str, Any]) -> str:
    rid = uuid.uuid4().hex
    pasta: Path = cliente.app.state.dir_relatorios  # type: ignore[attr-defined]
    (pasta / f"{rid}.json").write_text(json.dumps(retrato, ensure_ascii=False), encoding="utf-8")
    return rid


def test_relatorio_existente_200_com_csp(cliente: TestClient) -> None:
    r = cliente.get(f"/relatorio/{_salvar(cliente, _retrato())}")
    assert r.status_code == 200
    assert r.headers["content-security-policy"] == CSP
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"
    assert "Gastos de outubro" in r.text and 'id="dados-relatorio"' in r.text
    assert "Retrato gerado em 23/10/2026 10:30" in r.text
    assert "vs período anterior" in r.text


def test_texto_hostil_nao_executa(cliente: TestClient) -> None:
    t = _retrato(titulo="</script><script>alert(1)</script>")
    t["tabela"]["linhas"] = [["<img src=x onerror=alert(1)>", 1]]
    t["observacao"] = "<img src=x onerror=alert(1)>"
    r = cliente.get(f"/relatorio/{_salvar(cliente, t)}")
    assert r.status_code == 200
    assert "<script>alert(1)" not in r.text
    assert "<img src=x" not in r.text


@pytest.mark.parametrize("rid", ["..%2Fx", "ZZZ", "A" * 32, "a" * 32 + "%0A"])
def test_id_invalido_404(cliente: TestClient, rid: str) -> None:
    r = cliente.get(f"/relatorio/{rid}")
    assert r.status_code == 404


def test_expirado_ou_inexistente_404(cliente: TestClient) -> None:
    r = cliente.get(f"/relatorio/{uuid.uuid4().hex}")
    assert r.status_code == 404
    assert "Relatório não encontrado ou expirado." in r.text
    assert r.headers["cache-control"] == "no-store"


def test_sem_urls_externas(cliente: TestClient) -> None:
    r = cliente.get(f"/relatorio/{_salvar(cliente, _retrato())}")
    assert not re.search(r"https?://", r.text)


def test_relatorio_vazio_200(cliente: TestClient) -> None:
    t = _retrato()
    t["kpis"][0]["valor"] = None
    t["kpis"][0]["delta_pct"] = None
    t["graficos"][0]["linhas"] = []
    t["tabela"]["linhas"] = []
    r = cliente.get(f"/relatorio/{_salvar(cliente, t)}")
    assert r.status_code == 200


def test_lifespan_limpa_expirados(tmp_path: Path) -> None:
    import os
    import time
    pasta = tmp_path / "rel"
    pasta.mkdir()
    velho = pasta / f"{uuid.uuid4().hex}.json"
    velho.write_text("{}", encoding="utf-8")
    antigo = time.time() - 365 * 86400
    os.utime(velho, (antigo, antigo))
    app = create_app(tmp_path / "t.db")
    app.state.dir_relatorios = pasta
    with TestClient(app):
        pass
    assert not velho.exists()


def test_formatos_da_tabela_vao_no_json_embutido(cliente: TestClient) -> None:
    t = _retrato()
    t["tabela"] = {"titulo": "T", "colunas": ["Nome", "Mensalidade", "Total"], "formatos": ["texto", "moeda", "inteiro"],
                   "linhas": [["a", 123456, 12]], "truncado": False}
    r = cliente.get(f"/relatorio/{_salvar(cliente, t)}")
    m = re.search(r'id="dados-relatorio">(.*?)</script>', r.text, re.S)
    assert m and json.loads(m.group(1))["tabela"]["formatos"] == ["texto", "moeda", "inteiro"]


def test_js_sem_heuristica_de_nome_de_coluna() -> None:
    js = (Path(__file__).resolve().parent.parent / "app/web/static/js/relatorio.js").read_text(encoding="utf-8")
    assert "RE_MOEDA" not in js and "gasto|receita" not in js
    assert "formatos" in js
