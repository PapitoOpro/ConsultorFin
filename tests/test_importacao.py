from datetime import date
from pathlib import Path

import pytest

from app.importacao.base import LinhaImportada, ResultadoLeitura
from app.importacao.itau_csv import ParserItauCsv
from app.importacao.servico import ArquivoJaImportado, desfazer_importacao, importar
from app.repo.contas import criar_conta

FIXTURE = Path(__file__).parent / "fixtures" / "extrato_itau_exemplo.csv"
HOJE = date(2026, 10, 23)


class ParserFalso:
    def __init__(self, linhas: list[LinhaImportada]) -> None:
        self.linhas = linhas

    def ler(self, conteudo: bytes) -> ResultadoLeitura:
        return ResultadoLeitura(linhas=list(self.linhas))


def _conta(con) -> int:
    return criar_conta(con, "Itaú", "corrente")


def _total(con) -> int:
    return con.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0]


def _importar_fixture(con, conta):
    return importar(con, conta, "extrato.csv", FIXTURE.read_bytes(), ParserItauCsv(), HOJE)


def test_importar_fixture_cria_lancamentos(con):
    r = _importar_fixture(con, _conta(con))
    assert (r.total_lidos, r.novos, r.ignorados, r.erros) == (18, 18, 0, [])
    assert _total(con) == 18
    row = con.execute("SELECT * FROM lancamentos LIMIT 1").fetchone()
    assert (row["compensado"], row["origem"], row["data_compensacao"]) == (1, "importado", row["data"])
    imp = con.execute("SELECT * FROM importacoes").fetchone()
    assert (imp["periodo_inicio"], imp["periodo_fim"], imp["total_novos"]) == (
        "2026-07-25", "2026-10-22", 18)


def test_mesmo_arquivo_levanta(con):
    conta = _conta(con)
    _importar_fixture(con, conta)
    with pytest.raises(ArquivoJaImportado):
        _importar_fixture(con, conta)
    assert _total(con) == 18


def test_arquivos_sobrepostos_mesmo_total(con):
    conta = _conta(con)
    linhas = ParserItauCsv().ler(FIXTURE.read_bytes()).linhas
    datas = sorted({l.data for l in linhas})
    corte_a, corte_b = datas[len(datas) // 2], datas[len(datas) // 2 - 1]
    a = [l for l in linhas if l.data >= corte_b]
    b = [l for l in linhas if l.data <= corte_a]
    r1 = importar(con, conta, "a.csv", b"a", ParserFalso(a), HOJE)
    r2 = importar(con, conta, "b.csv", b"b", ParserFalso(b), HOJE)
    assert r2.ignorados > 0
    assert r1.novos + r2.novos == 18
    assert _total(con) == 18


def test_identicos_no_mesmo_dia_continuam_dois(con):
    conta = _conta(con)
    _importar_fixture(con, conta)
    dup = con.execute(
        "SELECT COUNT(*) FROM lancamentos GROUP BY data, descricao, valor_centavos "
        "ORDER BY 1 DESC LIMIT 1").fetchone()[0]
    assert dup == 2
    linhas = ParserItauCsv().ler(FIXTURE.read_bytes()).linhas
    importar(con, conta, "de-novo.csv", b"x", ParserFalso(linhas), HOJE)
    assert _total(con) == 18


def test_desfazer_preserva_manuais_e_permite_reimportar(con):
    conta = _conta(con)
    r = _importar_fixture(con, conta)
    con.execute(
        "INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
        " chave_deduplicacao, origem, importacao_id) VALUES ('m',?,'2026-10-01','manual',-100,"
        "'saida','k-manual','manual',?)", (conta, r.importacao_id))
    con.commit()
    desfazer_importacao(con, r.importacao_id)
    assert _total(con) == 1
    assert con.execute("SELECT desfeita FROM importacoes").fetchone()[0] == 1
    assert _importar_fixture(con, conta).novos == 18


def test_chaves_quitadas_contam_como_existentes(con):
    conta = _conta(con)
    linhas = ParserItauCsv().ler(FIXTURE.read_bytes()).linhas
    from app.dominio.lancamentos import chave_deduplicacao
    from app.repo.contas import uuid_da_conta
    l = linhas[0]
    k = chave_deduplicacao(uuid_da_conta(con, conta), l.data, l.descricao, l.valor_centavos, 1)
    con.execute("INSERT INTO chaves_quitadas (chave) VALUES (?)", (k,))
    con.commit()
    r = _importar_fixture(con, conta)
    assert (r.novos, r.ignorados) == (17, 1)


def test_rollback_em_falha(con):
    conta = _conta(con)
    ruim = [LinhaImportada("2026-10-01", "ok", -100), LinhaImportada("2026-10-02", None, -1)]  # type: ignore[arg-type]
    with pytest.raises(Exception):
        importar(con, conta, "r.csv", b"r", ParserFalso(ruim), HOJE)
    assert _total(con) == 0
    assert con.execute("SELECT COUNT(*) FROM importacoes").fetchone()[0] == 0


def test_erros_de_leitura_sao_devolvidos(con):
    conta = _conta(con)
    conteudo = FIXTURE.read_bytes() + b"xx;lixo;;1,00;\r\n"
    r = importar(con, conta, "e.csv", conteudo, ParserItauCsv(), HOJE)
    assert r.novos == 18 and len(r.erros) == 1
