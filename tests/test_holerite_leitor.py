import dataclasses

import pytest

from app.importacao.holerite import ler_holerite
from app.importacao.holerite.leitor import interpretar
from app.importacao.holerite.modelo import (
    HoleriteIlegivel, LayoutNaoSuportado, PdfComSenha, PdfInvalido, PdfSemTexto,
)
from app.importacao.holerite.texto import abrir_pdf, _cortar_via_duplicada
from app.importacao.holerite.texto import Palavra
from tests.holerite_util import (
    CARGO, CNPJ, CPF, EMPRESA, NOME, palavras_sinteticas, pdf_de_palavras, pdf_minimo,
)


def test_interpreta_adiantamento() -> None:
    h = interpretar(palavras_sinteticas("adiantamento"))
    assert (h.tipo, h.competencia, h.empresa) == ("adiantamento", "2026-10", EMPRESA)
    assert h.liquido_centavos == 140000
    assert [(i.natureza, i.valor_centavos) for i in h.itens] == [
        ("provento", 150000), ("desconto", 10000)]
    assert h.conferencia().confere and h.avisos == []


def test_interpreta_mensal() -> None:
    h = interpretar(palavras_sinteticas("mensal"))
    assert (h.tipo, h.competencia) == ("mensal", "2026-09")
    assert (h.total_vencimentos_centavos, h.total_descontos_centavos,
            h.liquido_centavos) == (500000, 120000, 380000)
    assert sum(i.natureza == "provento" for i in h.itens) == 6
    assert sum(i.natureza == "desconto" for i in h.itens) == 5
    c = h.conferencia()
    assert (c.soma_proventos, c.soma_descontos) == (500000, 120000) and c.confere
    assert all(i.valor_centavos > 0 for i in h.itens)


def test_natureza_pela_coluna() -> None:
    itens = [("1", "ALFA", "1,00", "desconto", "10,00"), ("2", "BETA", "1,00", "provento", "30,00")]
    h = interpretar(palavras_sinteticas("adiantamento", itens=itens, total_venc="30,00",
                                        total_desc="10,00", liquido="20,00"))
    assert [(i.descricao, i.natureza) for i in h.itens] == [("ALFA", "desconto"), ("BETA", "provento")]
    assert h.itens[0].codigo == "1" and h.conferencia().confere


def test_referencia_nao_vira_valor() -> None:
    itens = [("1", "HORAS", "2:30", "provento", "100,00"),
             ("2", "DIAS", "22,00", "provento", "50,00"),
             ("3", "PENSAO", "20,00", "desconto", "20,00")]
    h = interpretar(palavras_sinteticas("mensal", itens=itens, total_venc="150,00",
                                        total_desc="20,00", liquido="130,00"))
    assert [i.valor_centavos for i in h.itens] == [10000, 5000, 2000]
    assert [i.referencia for i in h.itens] == ["2:30", "22,00", "20,00"]
    assert h.conferencia().confere


def test_referencia_zero_e_mantida() -> None:
    h = interpretar(palavras_sinteticas("mensal"))
    alfa = next(i for i in h.itens if i.descricao == "ADICIONAL ALFA")
    assert (alfa.referencia, alfa.natureza, alfa.valor_centavos) == ("0,00", "provento", 5500)


def test_divergencia_gera_aviso() -> None:
    h = interpretar(palavras_sinteticas("adiantamento", liquido="1.000,00"))
    assert not h.conferencia().confere
    assert any("não confere" in a for a in h.avisos)


def test_layout_desconhecido_levanta() -> None:
    with pytest.raises(LayoutNaoSuportado) as e:
        interpretar(palavras_sinteticas("adiantamento", rotulo="Rescisao"))
    assert "Rescisao" not in str(e.value)
    with pytest.raises(LayoutNaoSuportado):
        interpretar([])


@pytest.mark.parametrize("rotulo", [
    "Adiantamento 13º Salário", "Adiantamento 13° Salário", "Folha Mensal Décimo Terceiro",
    "DÉCIMO TERCEIRO", "Folha de Férias", "FERIAS", "Rescisão", "RESCISAO",
    "Folha Complementar", "PLR", "Participação nos Lucros", "participacao"])
def test_tipos_nao_suportados_sao_recusados(rotulo: str) -> None:
    for tipo in ("adiantamento", "mensal"):
        with pytest.raises(LayoutNaoSuportado, match="13º salário, férias, rescisão"):
            interpretar(palavras_sinteticas(tipo, rotulo=rotulo))


def test_palavra_proibida_em_outra_linha_do_cabecalho_tambem_recusa() -> None:
    palavras = palavras_sinteticas("adiantamento") + [Palavra(286, 330, 26, "Férias")]
    with pytest.raises(LayoutNaoSuportado, match="férias"):
        interpretar(palavras)


def test_palavras_proibidas_fora_do_titulo_e_do_mes_nao_recusam() -> None:
    palavras = (palavras_sinteticas("mensal", empresa="ACME INVENTADA SALA 13A Ferias")
                + [Palavra(300, 360, 48, "Complementar")])  # linha do cargo
    h = interpretar(palavras)
    assert (h.tipo, h.competencia) == ("mensal", "2026-09") and h.conferencia().confere


@pytest.mark.parametrize("rotulo", ["Adiantamento Extra", "Mensal", "Folha", "Adiantamentos"])
def test_rotulo_precisa_ser_exatamente_o_tipo(rotulo: str) -> None:
    with pytest.raises(LayoutNaoSuportado, match="somente Adiantamento e Folha Mensal"):
        interpretar(palavras_sinteticas("adiantamento", rotulo=rotulo))


def test_rotulo_valido_em_maiusculas_ou_sem_cc_falha() -> None:
    assert interpretar(palavras_sinteticas("mensal", rotulo="FOLHA MENSAL")).tipo == "mensal"
    sem_cc = [p for p in palavras_sinteticas("adiantamento") if p.texto != "CC:"]
    with pytest.raises(LayoutNaoSuportado):
        interpretar(sem_cc)


def test_varias_paginas_com_tabela_recusa(monkeypatch: pytest.MonkeyPatch) -> None:
    pagina = palavras_sinteticas("mensal", duplicada=False)
    monkeypatch.setattr("app.importacao.holerite.leitor.abrir_pdf",
                        lambda conteudo: [pagina, pagina])
    with pytest.raises(LayoutNaoSuportado, match="mais de um holerite"):
        ler_holerite(b"%PDF")
    monkeypatch.setattr("app.importacao.holerite.leitor.abrir_pdf",
                        lambda conteudo: [pagina, [Palavra(10, 40, 10, "Observações")]])
    assert ler_holerite(b"%PDF").tipo == "mensal"  # 2ª página sem tabela: ok


def test_pdf_com_senha_tem_mensagem_propria() -> None:
    pypdf = pytest.importorskip("pypdf")
    import io
    w = pypdf.PdfWriter(clone_from=pypdf.PdfReader(io.BytesIO(
        pdf_de_palavras(palavras_sinteticas("mensal")))))
    w.encrypt("segredo")
    buf = io.BytesIO()
    w.write(buf)
    with pytest.raises(PdfComSenha) as e:
        ler_holerite(buf.getvalue())
    assert str(e.value) == ("PDF com senha não é suportado para holerite; remova a senha "
                            "antes de enviar.")
    assert isinstance(e.value, HoleriteIlegivel)


@pytest.mark.parametrize("linha, esperado", [
    (f"{EMPRESA} CNPJ: {CNPJ}", EMPRESA),
    (f"{EMPRESA} {CPF}", EMPRESA),
    (f"{EMPRESA} 12345678909 CNPJ 00000000000100", EMPRESA),
    ("123.456.789-09", None),
    (f"CNPJ: {CNPJ}", None),
])
def test_empresa_nunca_leva_cnpj_nem_cpf(linha: str, esperado: str | None) -> None:
    h = interpretar(palavras_sinteticas("adiantamento", empresa=linha))
    assert h.empresa == esperado
    assert not any(c.isdigit() for c in h.empresa or "")


def test_via_duplicada_e_cortada() -> None:
    p = palavras_sinteticas("mensal")
    assert len(_cortar_via_duplicada(p)) < len(p)
    assert len(_cortar_via_duplicada(palavras_sinteticas("mensal", duplicada=False))) == len(
        palavras_sinteticas("mensal", duplicada=False))


def test_ler_holerite_pdf_sem_texto() -> None:
    with pytest.raises(PdfSemTexto):
        ler_holerite(pdf_minimo(None))


def test_ler_holerite_arquivo_nao_pdf() -> None:
    with pytest.raises(PdfInvalido) as e:
        ler_holerite(b"conteudo-secreto-XYZ")
    assert "secreto" not in str(e.value) and "secreto" not in repr(e.value)


def test_ler_holerite_pdf_de_outro_layout() -> None:
    assert abrir_pdf(pdf_minimo("TESTE"))
    with pytest.raises(HoleriteIlegivel):
        ler_holerite(pdf_minimo("TESTE"))


def _fatura_sintetica() -> list[Palavra]:
    cab = [Palavra(10, 60, 100, "Descrição"), Palavra(300, 330, 100, "Valor")]
    corpo = [Palavra(10, 50, 112, "LOJA"), Palavra(300, 340, 112, "10,00"),
             Palavra(10, 60, 130, "Vencimento"), Palavra(100, 150, 130, "10/11/2026"),
             Palavra(10, 40, 150, "Total"), Palavra(42, 50, 150, "da"),
             Palavra(52, 80, 150, "fatura"), Palavra(300, 340, 150, "10,00")]
    return cab + corpo


def test_fatura_sintetica_levanta_layout_nao_suportado() -> None:
    with pytest.raises(LayoutNaoSuportado):
        interpretar(_fatura_sintetica())
    with pytest.raises(LayoutNaoSuportado):
        ler_holerite(pdf_de_palavras(_fatura_sintetica()))


def test_ler_holerite_caminho_feliz_a_partir_do_pdf() -> None:
    h = ler_holerite(pdf_de_palavras(palavras_sinteticas("mensal")))
    assert (h.tipo, h.competencia) == ("mensal", "2026-09")
    assert (h.total_vencimentos_centavos, h.total_descontos_centavos,
            h.liquido_centavos) == (500000, 120000, 380000)
    assert len(h.itens) == 11 and h.conferencia().confere


def test_valor_zero_nao_vira_item() -> None:
    itens = [("1", "ALFA", "1,00", "provento", "30,00"), ("2", "ZERADO", "1,00", "desconto", "0,00")]
    h = interpretar(palavras_sinteticas("adiantamento", itens=itens, total_venc="30,00",
                                        total_desc="0,00", liquido="30,00"))
    assert [i.descricao for i in h.itens] == ["ALFA"]


@pytest.mark.parametrize("mes", ["Março", "MARÇO", "março"])
def test_competencia_com_mes_acentuado_ou_maiusculo(mes: str) -> None:
    palavras = [dataclasses.replace(p, texto=mes) if p.texto == "Outubro" else p
                for p in palavras_sinteticas("adiantamento")]
    assert interpretar(palavras).competencia == "2026-03"


def test_sem_dados_pessoais_no_resultado() -> None:
    h = interpretar(palavras_sinteticas("mensal"))
    blob = repr(dataclasses.asdict(h))
    for segredo in (NOME, CPF, CNPJ, CARGO):
        assert segredo not in blob
