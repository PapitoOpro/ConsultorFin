import pytest

from app.importacao.fatura.modelo import (
    Conferencia, EmissorNaoSuportado, FaturaIlegivel, FaturaLida, ItemFatura,
    PdfInvalido, PdfProtegido, PdfSemTexto, SenhaIncorreta,
    inferir_ano, valor_br_para_centavos,
)
from app.importacao.fatura.texto import abrir_pdf
from tests.faturas_util import paginas_de_fixture


def _pdf_minimo(texto: str | None) -> bytes:
    conteudo = f"BT /F1 12 Tf 50 700 Td ({texto}) Tj ET" if texto else ""
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
    import io
    w = pypdf.PdfWriter(clone_from=pypdf.PdfReader(io.BytesIO(_pdf_minimo("DD/MM TESTE 9,99"))))
    w.encrypt(senha)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_valor_br() -> None:
    assert valor_br_para_centavos("1.234,56") == 123456
    assert valor_br_para_centavos("-9,99") == -999
    assert valor_br_para_centavos("R$ 0,05") == 5
    assert valor_br_para_centavos("R$ 9,99") == 999
    with pytest.raises(ValueError):
        valor_br_para_centavos("abc")


def test_inferir_ano() -> None:
    assert inferir_ano("28/12", "2027-01-10") == "2026-12-28"
    assert inferir_ano("05/01", "2027-01-10") == "2027-01-05"
    assert inferir_ano("29/02", "2028-03-05") == "2028-02-29"
    assert inferir_ano("29/02", "2029-01-05") == "2028-02-29"


def _item(valor: int) -> ItemFatura:
    return ItemFatura("2026-01-05", "X", valor, "compra", None, None, None, None)


def test_conferencia_confere_e_diferenca() -> None:
    f = FaturaLida("itau", None, None, "1234", "2026-02-10", None, 1500, 0,
                   [_item(-1000), _item(-500)], [])
    assert f.calculado_centavos == 1500
    c = f.conferencia()
    assert c.confere and c.diferenca_centavos == 0
    c2 = Conferencia(calculado_centavos=1000, impresso_centavos=1300, ajustes_resumo_centavos=100)
    assert c2.diferenca_centavos == 200 and not c2.confere


def test_hierarquia_de_excecoes() -> None:
    for exc in (PdfInvalido, PdfProtegido, SenhaIncorreta, PdfSemTexto, EmissorNaoSuportado):
        assert issubclass(exc, FaturaIlegivel) and issubclass(exc, ValueError)


def test_abrir_pdf_valido_devolve_paginas() -> None:
    paginas = abrir_pdf(_pdf_minimo("DD/MM TESTE 9,99"))
    assert len(paginas) == 1 and paginas[0].numero == 1
    assert "DD/MM TESTE 9,99" in paginas[0].texto
    assert "DD/MM TESTE 9,99" in paginas[0].esquerda
    assert paginas[0].direita.strip() == ""


def test_abrir_pdf_com_senha_correta() -> None:
    paginas = abrir_pdf(_pdf_criptografado("x"), "x")
    assert "TESTE" in paginas[0].texto


def test_abrir_pdf_sem_senha_em_pdf_protegido_levanta_PdfProtegido() -> None:
    with pytest.raises(PdfProtegido):
        abrir_pdf(_pdf_criptografado("x"))


def test_senha_errada() -> None:
    with pytest.raises(SenhaIncorreta):
        abrir_pdf(_pdf_criptografado("x"), "errada")


def test_nao_pdf_levanta_PdfInvalido() -> None:
    with pytest.raises(PdfInvalido):
        abrir_pdf(b"isto nao e um pdf")


def test_pdf_sem_texto_levanta_PdfSemTexto() -> None:
    with pytest.raises(PdfSemTexto):
        abrir_pdf(_pdf_minimo(None))


def test_senha_nao_aparece_na_mensagem_de_erro() -> None:
    senha = "segredo-UNICO-123"
    with pytest.raises(SenhaIncorreta) as e:
        abrir_pdf(_pdf_criptografado("x"), senha)
    assert senha not in str(e.value) and senha not in repr(e.value)
    with pytest.raises(PdfInvalido) as e2:
        abrir_pdf(b"lixo", senha)
    assert senha not in str(e2.value)


def test_paginas_de_fixture(tmp_path, monkeypatch) -> None:
    import tests.faturas_util as u
    (tmp_path / "t.txt").write_text(
        "@@pagina\nlinha A\n@@esq\nE1\n@@dir\nD1\n@@pagina\nsó texto\n", encoding="utf-8")
    monkeypatch.setattr(u, "PASTA", tmp_path)
    p = u.paginas_de_fixture("t")
    assert [x.numero for x in p] == [1, 2]
    assert (p[0].esquerda, p[0].direita) == ("E1", "D1")
    assert (p[1].esquerda, p[1].direita) == ("", "") and p[1].texto == "só texto"
