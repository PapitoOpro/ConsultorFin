import io
from datetime import date

import openpyxl

from app.importacao.excel import ParserExcel, listar_colunas

MAPA = {"data": "Data", "descricao": "Descrição", "valor": "Valor"}


def _xlsx(linhas: list[list[object]]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for l in linhas:
        ws.append(l)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_textos_brasileiros() -> None:
    conteudo = _xlsx([["Data", "Descrição", "Valor"],
                      ["24/07/2026", "MERCADO X", "-1.234,56"],
                      ["25/07/2026", "SALARIO", "5.000,00"]])
    r = ParserExcel(MAPA).ler(conteudo)
    assert r.erros == []
    assert [(l.data, l.descricao, l.valor_centavos) for l in r.linhas] == [
        ("2026-07-24", "MERCADO X", -123456), ("2026-07-25", "SALARIO", 500000)]


def test_xlsx_celulas_nativas_data_e_numero() -> None:
    conteudo = _xlsx([["Data", "Descrição", "Valor"],
                      [date(2026, 7, 24), "PADARIA", -12.3],
                      [date(2026, 7, 26), "PIX", 0.1 + 0.2]])
    r = ParserExcel(MAPA).ler(conteudo)
    assert [(l.data, l.valor_centavos) for l in r.linhas] == [("2026-07-24", -1230), ("2026-07-26", 30)]


def test_xlsx_erros_com_numero_da_linha_e_linhas_vazias_ignoradas() -> None:
    conteudo = _xlsx([["Data", "Descrição", "Valor"],
                      ["24/07/2026", "OK", "1,00"],
                      [None, None, None],
                      ["31/02/2026", "RUIM", "2,00"],
                      ["26/07/2026", "RUIM2", "abc"]])
    r = ParserExcel(MAPA).ler(conteudo)
    assert len(r.linhas) == 1
    assert [(e.linha, e.mensagem) for e in r.erros] == [
        (4, "Linha 4: data inválida '31/02/2026'"),
        (5, "Linha 5: valor inválido 'abc'")]


def test_csv_generico_ponto_e_virgula_e_latin1() -> None:
    texto = "Data;Descrição;Valor\n24/07/2026;CAFÉ;-3,50\n"
    r = ParserExcel(MAPA).ler(texto.encode("latin-1"))
    assert [(l.descricao, l.valor_centavos) for l in r.linhas] == [("CAFÉ", -350)]


def test_coluna_inexistente_e_arquivo_vazio() -> None:
    r = ParserExcel({"data": "X", "descricao": "Descrição", "valor": "Valor"}).ler(
        "Data;Descrição;Valor\n".encode())
    assert [e.mensagem for e in r.erros] == ["Coluna 'X' não encontrada no arquivo."]
    assert ParserExcel(MAPA).ler(b"").erros[0].mensagem == "Arquivo vazio."


def test_listar_colunas_e_arquivo_invalido() -> None:
    cols, amostra = listar_colunas("A;B;C\n1;2;3\n".encode())
    assert cols == ["A", "B", "C"] and amostra == [["1", "2", "3"]]
    r = ParserExcel(MAPA).ler(b"PK\x03\x04lixo")
    assert r.linhas == [] and r.erros[0].linha == 0
    assert "Não foi possível ler" in r.erros[0].mensagem
