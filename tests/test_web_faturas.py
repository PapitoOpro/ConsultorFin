"""Importar fatura de cartão em PDF pelo painel: envio (+ senha), prévia e confirmação."""
import io
import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import conectar
from app.importacao.fatura.modelo import FaturaLida, ItemFatura
from app.main import create_app
from app.repo import contas

SENHA_SECRETA = "segredo-Qx81"


def _fatura(impresso: int = 14200, avisos: list[str] | None = None) -> FaturaLida:
    return FaturaLida(
        emissor="Itaú", bandeira="Visa", produto="Platinum", final_cartao="1234",
        data_vencimento="2026-10-10", data_fechamento="2026-10-01",
        total_impresso_centavos=impresso, ajustes_resumo_centavos=0,
        itens=[
            ItemFatura("2026-09-05", "LOJA FICTICIA", -10000, "compra", None, None, "1234", None),
            ItemFatura("2026-09-10", "MERCADO INVENTADO", -5050, "compra", 2, 3, "1234", None),
            ItemFatura("2026-09-12", "ESTORNO TESTE", 1000, "credito", None, None, "1234", None),
            ItemFatura("2026-09-20", "IOF TESTE", -150, "encargo", None, None, "1234", None),
        ],
        avisos=avisos or [])


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
def cliente(db: Path) -> Iterator[TestClient]:
    with TestClient(create_app(db)) as c:
        yield c


@pytest.fixture
def fatura_fixa(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """ler_fatura devolve a fatura do dicionário (o teste pode trocar) e guarda a senha recebida."""
    estado: dict[str, Any] = {"fatura": _fatura(), "senhas": []}

    def falso(conteudo: bytes, senha: str | None = None) -> FaturaLida:
        estado["senhas"].append(senha)
        return estado["fatura"]

    monkeypatch.setattr("app.web.rotas.faturas.ler_fatura", falso)
    return estado


def _consulta(db: Path, sql: str, *args: Any) -> list[tuple[Any, ...]]:
    con = conectar(db)
    try:
        return [tuple(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def _conta_cartao(db: Path) -> int:
    con = conectar(db)
    try:
        return contas.criar_conta(con, "Meu cartão", "cartao", final_numero="1234")
    finally:
        con.close()


def _categoria(db: Path) -> int:
    return int(_consulta(db, "SELECT id FROM categorias WHERE pai_id IS NOT NULL ORDER BY id LIMIT 1")[0][0])


def _enviar(c: TestClient, conteudo: bytes = b"%PDF-1.4 sintetico", nome: str = "fatura.pdf",
            senha: str = "", rota: str = "/faturas/previa") -> Any:
    return c.post(rota, files={"arquivo": (nome, conteudo, "application/pdf")}, data={"senha": senha})


def _token(html: str, acao: str = "/faturas/confirmar") -> str:
    m = re.search(r'action="' + re.escape(acao) + r'".*?name="token" value="([^"]+)"', html, re.S)
    assert m, html
    return m.group(1)


def _confirmar(c: TestClient, html: str, cats: list[str] | None = None, **extra: str) -> Any:
    dados: dict[str, Any] = {"token": _token(html), "categoria": cats or ["", "", "", ""], **extra}
    return c.post("/faturas/confirmar", data=dados, follow_redirects=False)


def test_importar_tem_secao_de_fatura_separada(cliente: TestClient) -> None:
    r = cliente.get("/importar")
    assert r.status_code == 200
    assert "Arraste o extrato" in r.text  # fluxo do extrato continua
    assert 'action="/faturas/previa"' in r.text
    assert "Importar fatura do cartão (PDF)" in r.text
    campo = re.search(r'<input[^>]*name="senha"[^>]*>', r.text)
    assert campo and 'type="password"' in campo.group(0) and 'autocomplete="off"' in campo.group(0)
    assert "value=" not in campo.group(0)


def test_previa_mostra_itens_conferencia_ok_e_criar_conta(cliente: TestClient,
                                                          fatura_fixa: dict[str, Any]) -> None:
    r = _enviar(cliente)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    for desc in ("LOJA FICTICIA", "MERCADO INVENTADO", "ESTORNO TESTE", "IOF TESTE"):
        assert desc in r.text
    assert "2/3" in r.text
    assert "Confere" in r.text and "Divergente" not in r.text
    assert "R$ 142,00" in r.text  # total impresso
    assert "10/10/2026" in r.text  # vencimento
    assert "Criar conta-cartão" in r.text
    assert "Itaú Visa Platinum final 1234" in r.text
    assert "data-busca" in r.text and 'name="categoria"' in r.text
    assert fatura_fixa["senhas"] == [None]


def test_previa_divergente_mostra_diferenca_e_avisos(cliente: TestClient,
                                                     fatura_fixa: dict[str, Any]) -> None:
    fatura_fixa["fatura"] = _fatura(impresso=15000, avisos=["Linha não reconhecida na página 2."])
    r = _enviar(cliente)
    assert r.status_code == 200
    assert "Divergente" in r.text
    assert "R$ 8,00" in r.text  # diferença
    assert "Linha não reconhecida na página 2." in r.text


def test_senha_nunca_ecoada_nem_guardada(cliente: TestClient, fatura_fixa: dict[str, Any],
                                         caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    r = _enviar(cliente, senha=SENHA_SECRETA)
    assert r.status_code == 200
    assert fatura_fixa["senhas"] == [SENHA_SECRETA]
    assert SENHA_SECRETA not in r.text
    assert SENHA_SECRETA not in caplog.text
    app = cliente.app
    assert SENHA_SECRETA not in repr(app.state.faturas._itens)  # type: ignore[attr-defined]
    assert SENHA_SECRETA not in repr(app.state.uploads._itens)  # type: ignore[attr-defined]


def test_pdf_protegido_pede_senha_senha_errada_e_sucesso(cliente: TestClient,
                                                         monkeypatch: pytest.MonkeyPatch,
                                                         caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    monkeypatch.setattr("app.importacao.fatura.detectar", lambda paginas: _ParserFalso())
    r = _enviar(cliente, _pdf_criptografado("x"))
    assert r.status_code == 400
    assert r.headers["cache-control"] == "no-store"
    assert "protegido" in r.text
    campo = re.search(r'<input[^>]*name="senha"[^>]*>', r.text)
    assert campo and 'type="password"' in campo.group(0) and "value=" not in campo.group(0)
    assert "required" in campo.group(0)
    token = _token(r.text, "/faturas/previa")

    r = cliente.post("/faturas/previa", data={"token": token, "senha": SENHA_SECRETA})
    assert r.status_code == 400
    assert "Senha do PDF incorreta" in r.text
    assert SENHA_SECRETA not in r.text and SENHA_SECRETA not in caplog.text

    r = cliente.post("/faturas/previa", data={"token": _token(r.text, "/faturas/previa"), "senha": "x"})
    assert r.status_code == 200
    assert "LOJA FICTICIA" in r.text and "Confere" in r.text
    assert 'value="x"' not in r.text


def test_protegido_com_emissor_desconhecido_descarta_o_arquivo(cliente: TestClient) -> None:
    r = _enviar(cliente, _pdf_criptografado("x"))
    token = _token(r.text, "/faturas/previa")
    r = cliente.post("/faturas/previa", data={"token": token, "senha": "x"})
    assert r.status_code == 400 and "Emissor da fatura não reconhecido" in r.text
    assert cliente.app.state.uploads.obter(token) is None  # type: ignore[attr-defined]


def test_pdf_sem_protecao_nao_pede_senha(cliente: TestClient,
                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.importacao.fatura.detectar", lambda paginas: _ParserFalso())
    r = _enviar(cliente, _pdf_minimo("FATURA TESTE 9,99"))
    assert r.status_code == 200
    assert "LOJA FICTICIA" in r.text


def test_pdf_de_emissor_desconhecido_e_invalido(cliente: TestClient) -> None:
    r = _enviar(cliente, _pdf_minimo("DOCUMENTO QUALQUER"))
    assert r.status_code == 400
    assert "Emissor da fatura não reconhecido" in r.text
    r = _enviar(cliente, b"isto nao e pdf")
    assert r.status_code == 400
    assert "não é um PDF válido" in r.text
    assert "isto nao e pdf" not in r.text


def test_validacao_do_envio(cliente: TestClient, fatura_fixa: dict[str, Any]) -> None:
    r = _enviar(cliente, nome="fatura.txt")
    assert r.status_code == 400 and "Formato não aceito" in r.text
    r = _enviar(cliente, b"%PDF" + b"0" * (5 * 1024 * 1024))
    assert r.status_code == 413 and "5 MB" in r.text
    r = _enviar(cliente, b"   ")
    assert r.status_code == 400 and "vazio" in r.text
    r = cliente.post("/faturas/previa", data={"senha": ""})
    assert r.status_code == 400 and "Selecione" in r.text
    assert fatura_fixa["senhas"] == []


def test_erro_inesperado_mensagem_amigavel_loga_so_o_tipo(cliente: TestClient,
                                                          monkeypatch: pytest.MonkeyPatch,
                                                          caplog: pytest.LogCaptureFixture) -> None:
    def explode(conteudo: bytes, senha: str | None = None) -> FaturaLida:
        raise RuntimeError("conteudo-sigiloso")

    monkeypatch.setattr("app.web.rotas.faturas.ler_fatura", explode)
    caplog.set_level(logging.DEBUG)
    r = _enviar(cliente, senha=SENHA_SECRETA)
    assert r.status_code == 400
    assert "Não foi possível ler a fatura" in r.text
    assert "conteudo-sigiloso" not in r.text and "conteudo-sigiloso" not in caplog.text
    assert "RuntimeError" in caplog.text
    assert SENHA_SECRETA not in caplog.text


def test_confirmar_cria_conta_grava_e_redireciona(cliente: TestClient, db: Path,
                                                  fatura_fixa: dict[str, Any]) -> None:
    previa = _enviar(cliente).text
    r = _confirmar(cliente, previa, criar_conta="1")
    assert r.status_code == 303
    conta = _consulta(db, "SELECT id, tipo, final_numero FROM contas")
    assert len(conta) == 1 and conta[0][1:] == ("cartao", "1234")
    assert r.headers["location"] == f"/lancamentos?conta_id={conta[0][0]}&de=2026-10-01&ate=2026-10-31"
    assert _consulta(db, "SELECT COUNT(*) FROM faturas")[0][0] == 1
    assert _consulta(db, "SELECT COUNT(*) FROM fatura_itens")[0][0] == 4
    r = cliente.get(r.headers["location"])
    assert r.status_code == 200


def test_sem_conta_exige_criar_conta(cliente: TestClient, db: Path,
                                     fatura_fixa: dict[str, Any]) -> None:
    previa = _enviar(cliente).text
    r = _confirmar(cliente, previa)
    assert r.status_code == 400
    assert "Marque" in r.text and "conta-cartão" in r.text
    assert r.headers["cache-control"] == "no-store"
    assert _consulta(db, "SELECT COUNT(*) FROM faturas")[0][0] == 0
    assert _consulta(db, "SELECT COUNT(*) FROM contas")[0][0] == 0


def test_conta_cartao_detectada_e_categoria_escolhida_gravada(cliente: TestClient, db: Path,
                                                              fatura_fixa: dict[str, Any]) -> None:
    conta = _conta_cartao(db)
    cat = _categoria(db)
    previa = _enviar(cliente).text
    assert "Meu cartão" in previa and "Criar conta-cartão" not in previa
    r = _confirmar(cliente, previa, cats=[str(cat), "", "", ""])
    assert r.status_code == 303
    assert f"conta_id={conta}" in r.headers["location"]
    cats = _consulta(db, "SELECT categoria_id FROM fatura_itens ORDER BY ordem")
    assert cats[0] == (cat,)
    assert _consulta(db, "SELECT COUNT(*) FROM contas")[0][0] == 1


@pytest.mark.parametrize("cats", [["abc", "", "", ""], ["999999", "", "", ""], ["", ""],
                                  ["１", "", "", ""]])
def test_categoria_invalida_nao_grava(cliente: TestClient, db: Path, fatura_fixa: dict[str, Any],
                                      cats: list[str]) -> None:
    _conta_cartao(db)
    previa = _enviar(cliente).text
    r = _confirmar(cliente, previa, cats=cats)
    assert r.status_code == 400
    assert "ategoria" in r.text
    assert _consulta(db, "SELECT COUNT(*) FROM faturas")[0][0] == 0


def test_mesmo_arquivo_ja_importado_bloqueia(cliente: TestClient, db: Path,
                                            fatura_fixa: dict[str, Any]) -> None:
    _conta_cartao(db)
    assert _confirmar(cliente, _enviar(cliente).text).status_code == 303
    previa = _enviar(cliente).text
    assert "já foi importada" in previa
    assert 'action="/faturas/confirmar"' not in previa
    token = re.search(r'name="token" value="([^"]+)"', previa)
    assert token is None


def test_confirmar_fatura_ja_importada_mostra_mensagem(cliente: TestClient, db: Path,
                                                       fatura_fixa: dict[str, Any]) -> None:
    _conta_cartao(db)
    p1 = _enviar(cliente).text
    p2 = _enviar(cliente).text
    assert _confirmar(cliente, p1).status_code == 303
    r = _confirmar(cliente, p2)
    assert r.status_code == 400
    assert "já foi importada" in r.text
    assert _consulta(db, "SELECT COUNT(*) FROM faturas")[0][0] == 1


def test_hash_diferente_exige_substituir(cliente: TestClient, db: Path,
                                         fatura_fixa: dict[str, Any]) -> None:
    _conta_cartao(db)
    assert _confirmar(cliente, _enviar(cliente).text).status_code == 303
    fatura_fixa["fatura"] = _fatura(impresso=14200)
    previa = _enviar(cliente, b"%PDF-1.4 outro arquivo").text
    assert 'name="substituir"' in previa
    r = _confirmar(cliente, previa)
    assert r.status_code == 400
    assert "substituir" in r.text
    assert _consulta(db, "SELECT arquivo_nome FROM faturas") == [("fatura.pdf",)]
    r = _confirmar(cliente, previa, substituir="1")
    assert r.status_code == 303
    assert _consulta(db, "SELECT COUNT(*) FROM faturas")[0][0] == 1
    assert _consulta(db, "SELECT COUNT(*) FROM fatura_itens")[0][0] == 4


def test_token_expirado_ou_invalido(cliente: TestClient, fatura_fixa: dict[str, Any]) -> None:
    r = cliente.post("/faturas/confirmar", data={"token": "inexistente", "categoria": [""]},
                     follow_redirects=False)
    assert r.status_code == 400
    assert "expirad" in r.text
    r = cliente.post("/faturas/previa", data={"token": "inexistente", "senha": "x"})
    assert r.status_code == 400
    assert "expirad" in r.text


def test_token_usado_e_descartado(cliente: TestClient, db: Path, fatura_fixa: dict[str, Any]) -> None:
    previa = _enviar(cliente).text
    assert _confirmar(cliente, previa, criar_conta="1").status_code == 303
    r = _confirmar(cliente, previa, criar_conta="1")
    assert r.status_code == 400 and "expirad" in r.text


def test_armazem_de_faturas_expira_e_tem_limite(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.web import uploads
    armazem = uploads.ArmazemFaturas()
    agora = [1000.0]
    monkeypatch.setattr(uploads.time, "monotonic", lambda: agora[0])
    token = armazem.guardar("f.pdf", "h" * 64, _fatura())
    pendente = armazem.obter(token)
    assert pendente is not None and pendente.fatura.final_cartao == "1234"
    assert not hasattr(pendente, "conteudo") and not hasattr(pendente, "senha")
    agora[0] += uploads.TTL_SEGUNDOS + 1
    assert armazem.obter(token) is None
    tokens = [armazem.guardar("f.pdf", "h", _fatura()) for _ in range(uploads.MAX_ITENS + 3)]
    assert armazem.obter(tokens[0]) is None and armazem.obter(tokens[-1]) is not None


def test_upload_pdf_pelo_formulario_do_extrato_vai_para_a_fatura(cliente: TestClient,
                                                                 fatura_fixa: dict[str, Any]) -> None:
    r = _enviar(cliente, rota="/importar/previa")
    assert r.status_code == 200
    assert "LOJA FICTICIA" in r.text and 'action="/faturas/confirmar"' in r.text
