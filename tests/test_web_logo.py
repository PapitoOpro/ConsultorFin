import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

WEB = Path(__file__).resolve().parent.parent / "app" / "web"
LOGO = WEB / "static" / "img" / "logo-consultorfin.svg"
URL_LOGO = "/static/img/logo-consultorfin.svg"


@pytest.fixture
def cliente(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(tmp_path / "t.db")) as c:
        yield c


def _base() -> str:
    return (WEB / "templates" / "base.html").read_text(encoding="utf-8")


def test_logo_servido_como_svg(cliente: TestClient) -> None:
    r = cliente.get(URL_LOGO)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")


def test_logo_nao_tem_conteudo_ativo_nem_url_externa() -> None:
    svg = LOGO.read_text(encoding="utf-8")
    assert "<script" not in svg.lower()
    assert "foreignobject" not in svg.lower()
    assert not re.search(r"\son\w+\s*=", svg, re.I)
    assert not re.search(r"""(?:xlink:)?href\s*=\s*["']\s*https?://""", svg, re.I)


def test_base_usa_img_da_logo_na_marca_e_como_favicon() -> None:
    base = _base()
    img = re.search(r'<a class="marca"[^>]*>\s*<img[^>]*>', base)
    assert img, "marca sem <img>"
    tag = img.group(0)
    assert f'src="{URL_LOGO}"' in tag and 'alt="ConsultorFin"' in tag
    assert "width=" in tag and "height=" in tag and 'decoding="async"' in tag
    assert f'<link rel="icon" type="image/svg+xml" href="{URL_LOGO}">' in base
    assert "marca-logo" not in base


def test_marca_nome_acessivel_igual_ao_texto_visivel() -> None:
    marca = re.search(r'<a class="marca"[^>]*>(.*?)</a>', _base(), re.S)
    assert marca and "aria-label" not in marca.group(0).split(">")[0]
    assert "ConsultorFin" in marca.group(1)


def test_sem_url_externa_em_base_e_css() -> None:
    css = (WEB / "static" / "css" / "tema.css").read_text(encoding="utf-8")
    for texto in (_base(), css):
        assert not re.search(r"(?:src|href|url\()\s*[=(]?\s*[\"']?https?://", texto, re.I)
