"""Fila de faturas do Gmail na tela Importar (dados inventados)."""
import hashlib
import io
import re
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import config
from app.db import conectar
from app.dominio.faturas import importar_fatura
from app.importacao.fatura.modelo import FaturaLida, ItemFatura
from app.main import create_app
from app.repo import contas
from app.repo import fila_faturas as repo

AGORA = datetime.now().isoformat(timespec="seconds")
PDF = b"%PDF-1.4 sintetico fila"


def _fatura() -> FaturaLida:
    return FaturaLida(
        emissor="Itaú", bandeira="Visa", produto="Platinum", final_cartao="1234",
        data_vencimento="2026-10-10", data_fechamento="2026-10-01",
        total_impresso_centavos=10000, ajustes_resumo_centavos=0,
        itens=[ItemFatura("2026-09-05", "LOJA FICTICIA", -10000, "compra", None, None, "1234",
                          None)])


def _pdf_minimo(texto: str) -> bytes:
    conteudo = f"BT /F1 12 Tf 50 700 Td ({texto}) Tj ET"
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(conteudo)} >>\nstream\n{conteudo}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offs:
        out += f"{o:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def _pdf_criptografado(senha: str) -> bytes:
    pypdf = pytest.importorskip("pypdf")
    w = pypdf.PdfWriter(clone_from=pypdf.PdfReader(io.BytesIO(_pdf_minimo("FATURA TESTE 9,99"))))
    w.encrypt(senha)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


class _ParserFalso:
    emissor = "Itaú"

    def reconhece(self, paginas: list[Any]) -> bool:
        return True

    def ler(self, paginas: list[Any]) -> FaturaLida:
        return _fatura()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "t.db"


@pytest.fixture
def dir_fila(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "fila"
    d.mkdir()
    monkeypatch.setattr(config, "DIR_FILA", d)
    return d


@pytest.fixture
def cliente(db: Path, dir_fila: Path) -> Iterator[TestClient]:
    app = create_app(db)
    app.state.hoje_fn = lambda: date(2026, 10, 27)
    with TestClient(app) as c:
        yield c


def _semear(db: Path, dir_fila: Path, conteudo: bytes = PDF, *, msg: str = "m1",
            emissor: str | None = "Itaú", final: str | None = "1234",
            venc: str | None = "2026-11-10", escrever: bool = True) -> int:
    con = conectar(db)
    try:
        item = repo.inserir(con, gmail_msg_id=msg, anexo_id="a1", emissor=emissor,
                            cartao_final=final, vencimento=venc,
                            pdf_hash=hashlib.sha256(conteudo).hexdigest(), agora=AGORA)
        con.commit()
    finally:
        con.close()
    assert item is not None
    if escrever:
        (dir_fila / f"{item}.pdf").write_bytes(conteudo)
    return item


def _estado(db: Path, item: int) -> str:
    con = conectar(db)
    try:
        return str(repo.obter(con, item)["estado"])
    finally:
        con.close()


def test_fila_lista_pendentes_sem_texto_de_email(cliente: TestClient, db: Path,
                                                 dir_fila: Path) -> None:
    _semear(db, dir_fila, emissor="Itaú <b>x</b>")
    _semear(db, dir_fila, b"%PDF outro", msg="m2", final=None, venc=None)
    r = cliente.get("/faturas/fila")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert "final 1234" in r.text and "10/11/2026" in r.text
    assert "final não identificado" in r.text and "vencimento não identificado" in r.text
    assert "<b>x</b>" not in r.text and "&lt;b&gt;" in r.text
    assert "Importar" in r.text and "Pular" in r.text


def test_fila_vazia_devolve_fragmento_vazio(cliente: TestClient) -> None:
    r = cliente.get("/faturas/fila")
    assert r.status_code == 200 and r.text.strip() == ""
    assert r.headers["cache-control"] == "no-store"


def test_importar_inclui_container_da_fila(cliente: TestClient) -> None:
    r = cliente.get("/importar")
    assert 'id="fila-faturas"' in r.text and 'hx-get="/faturas/fila"' in r.text
    assert 'hx-trigger="load, every 5s"' in r.text


def test_item_ja_importada_vem_com_pular_padrao(cliente: TestClient, db: Path,
                                                dir_fila: Path) -> None:
    con = conectar(db)
    try:
        cartao = contas.criar_conta(con, "Cartao", "cartao", final_numero="1234")
        importar_fatura(con, _fatura(), "outro-hash", "f.pdf", cartao, date(2026, 10, 27), [None])
    finally:
        con.close()
    item = _semear(db, dir_fila, venc="2026-10-10")
    r = cliente.get("/faturas/fila")
    assert "já importada" in r.text and "Importar mesmo assim" in r.text
    assert r.text.index("Pular") < r.text.index("Importar mesmo assim")
    assert _estado(db, item) == "pendente"


def test_pular_apaga_pdf_e_remove_do_fragmento(cliente: TestClient, db: Path,
                                               dir_fila: Path) -> None:
    item = _semear(db, dir_fila)
    outro = _semear(db, dir_fila, b"%PDF outro", msg="m2")
    r = cliente.post(f"/faturas/fila/{item}/pular")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert f"/faturas/fila/{item}/" not in r.text and f"/faturas/fila/{outro}/" in r.text
    assert _estado(db, item) == "pulada" and not (dir_fila / f"{item}.pdf").exists()
    assert (dir_fila / f"{outro}.pdf").exists()


def test_importar_devolve_formulario_de_senha_com_token(cliente: TestClient, db: Path,
                                                        dir_fila: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.importacao.fatura.detectar", lambda paginas: _ParserFalso())
    item = _semear(db, dir_fila, _pdf_criptografado("x"))
    r = cliente.post(f"/faturas/fila/{item}/importar")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert "Fatura Itaú.pdf" in r.text
    m = re.search(r'action="/faturas/previa".*?name="token" value="([^"]+)"', r.text, re.S)
    assert m
    campo = re.search(r'<input[^>]*name="senha"[^>]*>', r.text)
    assert campo and "required" in campo.group(0)
    r2 = cliente.post("/faturas/previa", data={"token": m.group(1), "senha": "x"})
    assert r2.status_code == 200 and "LOJA FICTICIA" in r2.text


def test_item_inexistente_404(cliente: TestClient, db: Path, dir_fila: Path) -> None:
    assert cliente.post("/faturas/fila/999/importar").status_code == 404
    assert cliente.post("/faturas/fila/999/pular").status_code == 404
    assert cliente.post("/faturas/fila/abc/pular").status_code == 422
    item = _semear(db, dir_fila)
    cliente.post(f"/faturas/fila/{item}/pular")
    assert cliente.post(f"/faturas/fila/{item}/importar").status_code == 404


def test_pdf_sumido_mensagem_clara(cliente: TestClient, db: Path, dir_fila: Path) -> None:
    item = _semear(db, dir_fila, escrever=False)
    r = cliente.post(f"/faturas/fila/{item}/importar")
    assert "O arquivo desta fatura expirou. Busque de novo." in r.text


def test_importada_pelo_fluxo_normal_some_da_fila(cliente: TestClient, db: Path, dir_fila: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.web.rotas.faturas.ler_fatura", lambda c, s=None: _fatura())
    item = _semear(db, dir_fila)
    r = cliente.post(f"/faturas/fila/{item}/importar")
    m = re.search(r'name="token" value="([^"]+)"', r.text)
    assert m
    prev = cliente.post("/faturas/previa", data={"token": m.group(1), "senha": ""})
    m2 = re.search(r'action="/faturas/confirmar".*?name="token" value="([^"]+)"', prev.text, re.S)
    assert m2
    ok = cliente.post("/faturas/confirmar", data={"token": m2.group(1), "categoria": [""],
                                                  "criar_conta": "1"}, follow_redirects=False)
    assert ok.status_code == 303
    r = cliente.get("/faturas/fila")
    assert r.text.strip() == ""
    assert _estado(db, item) == "importada" and not (dir_fila / f"{item}.pdf").exists()


def test_ids_estaveis_labels_por_item_e_aria_live(cliente: TestClient, db: Path,
                                                  dir_fila: Path) -> None:
    a = _semear(db, dir_fila, emissor="Itaú <i>x</i>")
    b = _semear(db, dir_fila, b"%PDF outro", msg="m2", emissor="Porto", final=None)
    r = cliente.get("/faturas/fila")
    for i in (a, b):
        assert f'id="fila-importar-{i}"' in r.text and f'id="fila-pular-{i}"' in r.text
    assert 'aria-label="Pular fatura Itaú &lt;i&gt;x&lt;/i&gt; final 1234"' in r.text
    assert 'aria-label="Pular fatura Porto final não identificado"' in r.text
    assert 'aria-label="Importar fatura Porto final não identificado"' in r.text
    assert 'aria-live="polite"' in cliente.get("/importar").text


def test_fila_sem_mudanca_devolve_204_para_preservar_foco(cliente: TestClient, db: Path,
                                                          dir_fila: Path) -> None:
    _semear(db, dir_fila)
    r = cliente.get("/faturas/fila")
    v = re.search(r'id="fila-versao" name="v" value="([^"]+)"', r.text)
    assert v
    r2 = cliente.get("/faturas/fila", params={"v": v.group(1)})
    assert r2.status_code == 204 and r2.headers["cache-control"] == "no-store"
    _semear(db, dir_fila, b"%PDF outro", msg="m2")
    assert cliente.get("/faturas/fila", params={"v": v.group(1)}).status_code == 200
