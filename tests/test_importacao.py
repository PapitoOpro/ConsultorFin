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


def test_simular_importacao_nao_escreve_e_bate_com_importar(con):
    from app.importacao.servico import simular_importacao

    conta = _conta(con)
    antes = _total(con)
    sim = simular_importacao(con, conta, "extrato.csv", FIXTURE.read_bytes(), ParserItauCsv())
    assert (sim.total_lidos, sim.novos, sim.ignorados, sim.erros) == (18, 18, 0, [])
    assert _total(con) == antes
    assert con.execute("SELECT COUNT(*) FROM importacoes").fetchone()[0] == 0
    real = _importar_fixture(con, conta)
    assert (real.novos, real.ignorados) == (sim.novos, sim.ignorados)


def _duas_importacoes(con, conta):
    a = importar(con, conta, "a.csv", b"a", ParserFalso([LinhaImportada("2026-10-01", "PIX", -100),
                                                        LinhaImportada("2026-10-02", "X", -200)]), HOJE)
    b = importar(con, conta, "b.csv", b"b", ParserFalso([LinhaImportada("2026-10-02", "X", -200),
                                                        LinhaImportada("2026-10-03", "Y", -300)]), HOJE)
    return a.importacao_id, b.importacao_id


def test_desfazer_so_a_importacao_mais_recente_da_conta(con):
    conta = _conta(con)
    a, b = _duas_importacoes(con, conta)
    with pytest.raises(ValueError, match=rf"Desfaça primeiro a importação mais recente \(#{b}, 23/10/2026\)"):
        desfazer_importacao(con, a)
    assert _total(con) == 3
    assert [r[0] for r in con.execute("SELECT desfeita FROM importacoes ORDER BY id")] == [0, 0]


def test_desfazer_mais_recente_e_depois_a_anterior(con):
    conta = _conta(con)
    a, b = _duas_importacoes(con, conta)
    desfazer_importacao(con, b)
    assert {r[0] for r in con.execute("SELECT descricao FROM lancamentos")} == {"PIX", "X"}
    desfazer_importacao(con, a)
    assert _total(con) == 0
    with pytest.raises(ValueError, match="já foi desfeita"):
        desfazer_importacao(con, a)


def test_desfazer_independe_entre_contas(con):
    conta, outra = _conta(con), _conta(con)
    a = importar(con, conta, "a.csv", b"a", ParserFalso([LinhaImportada("2026-10-01", "P", -1)]), HOJE)
    importar(con, outra, "b.csv", b"b", ParserFalso([LinhaImportada("2026-10-01", "P", -1)]), HOJE)
    desfazer_importacao(con, a.importacao_id)
    assert _total(con) == 1


def test_desfazer_inexistente(con):
    from app.importacao.servico import ImportacaoNaoEncontrada
    with pytest.raises(ImportacaoNaoEncontrada):
        desfazer_importacao(con, 99)


def test_recusa_lancamentos_anteriores_ao_saldo_inicial(con):
    from app.importacao.servico import LancamentosAnterioresAoSaldoInicial, simular_importacao

    conta = criar_conta(con, "Itaú", "corrente", saldo_inicial_centavos=140914,
                        data_saldo_inicial="2026-10-23")
    for chamada in (lambda: _importar_fixture(con, conta),
                    lambda: simular_importacao(con, conta, "extrato.csv", FIXTURE.read_bytes(),
                                               ParserItauCsv())):
        with pytest.raises(LancamentosAnterioresAoSaldoInicial) as exc:
            chamada()
        msg = str(exc.value)
        assert isinstance(exc.value, ValueError)
        assert "18 lançamentos" in msg and "23/10/2026" in msg and "25/07/2026" in msg
        assert "R$ 2.148,08" in msg and "24/07/2026" in msg
    assert _total(con) == 0
    assert con.execute("SELECT COUNT(*) FROM importacoes").fetchone()[0] == 0


def test_lancamento_na_data_do_saldo_inicial_e_aceito(con):
    conta = criar_conta(con, "Itaú", "corrente", data_saldo_inicial="2026-10-01")
    r = importar(con, conta, "a.csv", b"a", ParserFalso([LinhaImportada("2026-10-01", "PIX", -1)]), HOJE)
    assert r.novos == 1


def test_conta_sem_data_saldo_inicial_importa(con):
    r = _importar_fixture(con, _conta(con))
    assert r.novos == 18


def test_simular_importacao_conta_ignorados_e_arquivo_repetido(con):
    from app.importacao.servico import simular_importacao

    conta = _conta(con)
    l = LinhaImportada("2026-10-01", "PIX", -1000)
    importar(con, conta, "a.csv", b"a", ParserFalso([l]), HOJE)
    sim = simular_importacao(con, conta, "b.csv", b"b", ParserFalso([l, l, LinhaImportada("2026-10-02", "X", 5)]))
    assert (sim.total_lidos, sim.novos, sim.ignorados) == (3, 2, 1)
    with pytest.raises(ArquivoJaImportado, match="Este arquivo já foi importado em 23/10/2026"):
        simular_importacao(con, conta, "a.csv", b"a", ParserFalso([l]))
