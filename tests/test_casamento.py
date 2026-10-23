from datetime import date

import pytest

from app.dominio.casamento import Casamento, confirmar_casamento, propor_casamentos
from app.dominio.saldo import conciliar, saldo_em
from app.importacao.base import LinhaImportada, ResultadoLeitura
from app.importacao.servico import desfazer_importacao, importar
from app.repo.contas import criar_conta
from app.repo.lancamentos import inserir_manual

HOJE = date(2026, 10, 23)


class ParserFalso:
    def __init__(self, linhas: list[LinhaImportada], saldos: dict[str, int] | None = None) -> None:
        self.linhas = linhas
        self.saldos = saldos or {}

    def ler(self, conteudo: bytes) -> ResultadoLeitura:
        return ResultadoLeitura(linhas=list(self.linhas), saldos_dia=self.saldos)


def _conta(con) -> int:
    return criar_conta(con, "Itaú", "corrente", saldo_inicial_centavos=100000,
                       data_saldo_inicial="2026-10-01")


def _pend(con, conta: int, data: str, desc: str, valor: int, estimado: bool = False) -> int:
    pid = inserir_manual(con, conta_id=conta, data=data, descricao=desc, valor_centavos=valor,
                         tipo="saida" if valor < 0 else "entrada", categoria_id=None,
                         compensado=False, observacao=None)
    if estimado:
        con.execute("UPDATE lancamentos SET estimado = 1 WHERE id = ?", (pid,))
    con.commit()
    return pid


def _imp(con, conta: int, linhas: list[tuple[str, str, int]], nome: str = "a.csv",
         saldos: dict[str, int] | None = None) -> int:
    parser = ParserFalso([LinhaImportada(*l) for l in linhas], saldos)
    return importar(con, conta, nome, nome.encode(), parser, HOJE).importacao_id


def _linha_id(con, descricao: str) -> int:
    return con.execute("SELECT id FROM lancamentos WHERE descricao = ? AND origem = 'importado'",
                       (descricao,)).fetchone()[0]


def test_propoe_luz_com_linha_seis_dias_depois(con) -> None:
    conta = _conta(con)
    pid = _pend(con, conta, "2026-10-10", "Luz", -21000, estimado=True)
    imp = _imp(con, conta, [("2026-10-16", "ENEL DISTRIBUICAO LUZ", -23000)])
    props = propor_casamentos(con, conta, imp)
    assert len(props) == 1
    assert (props[0].pendente_id, props[0].lancamento_id) == (pid, _linha_id(con, "ENEL DISTRIBUICAO LUZ"))
    assert props[0].pontuacao > 0


def test_nao_propoe_fora_dos_criterios(con) -> None:
    conta = _conta(con)
    _pend(con, conta, "2026-10-10", "Luz", -21000)
    imp = _imp(con, conta, [
        ("2026-10-25", "LUZ", -21000),    # 15 dias
        ("2026-10-11", "LUZ", 21000),     # sinal oposto
        ("2026-10-11", "LUZ B", -30000),  # valor longe
    ])
    assert propor_casamentos(con, conta, imp) == []


def test_tolerancia_minima_de_500_centavos(con) -> None:
    conta = _conta(con)
    _pend(con, conta, "2026-10-10", "Taxa", -2000)  # 10% = 200 -> vale 500
    imp = _imp(con, conta, [("2026-10-10", "Taxa", -2500)])
    assert len(propor_casamentos(con, conta, imp)) == 1
    imp2 = _imp(con, conta, [("2026-10-10", "Taxa", -2501)], nome="b.csv")
    assert propor_casamentos(con, conta, imp2) == []


def test_confirmar_quita_uma_so_vez_e_nao_duplica(con) -> None:
    conta = _conta(con)
    pid = _pend(con, conta, "2026-10-10", "Luz", -21000, estimado=True)
    imp = _imp(con, conta, [("2026-10-16", "LUZ", -23000)])
    lid = _linha_id(con, "LUZ")
    chave = con.execute("SELECT chave_deduplicacao FROM lancamentos WHERE id = ?", (lid,)).fetchone()[0]
    confirmar_casamento(con, pid, lid)
    p = con.execute("SELECT * FROM lancamentos WHERE id = ?", (pid,)).fetchone()
    assert (p["valor_centavos"], p["compensado"], p["data_compensacao"], p["estimado"],
            p["importacao_id"]) == (-23000, 1, "2026-10-16", 0, imp)
    assert con.execute("SELECT COUNT(*) FROM lancamentos WHERE id = ?", (lid,)).fetchone()[0] == 0
    q = con.execute("SELECT pendente_id, importacao_id FROM chaves_quitadas WHERE chave = ?",
                    (chave,)).fetchone()
    assert (q["pendente_id"], q["importacao_id"]) == (pid, imp)
    assert saldo_em(con, conta, "2026-10-31") == 100000 - 23000
    assert con.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 1


def test_reimportar_apos_casar_nao_recria_linha(con) -> None:
    conta = _conta(con)
    pid = _pend(con, conta, "2026-10-10", "Luz", -21000)
    linhas = [("2026-10-16", "LUZ", -23000)]
    imp = _imp(con, conta, linhas)
    confirmar_casamento(con, pid, _linha_id(con, "LUZ"))
    r = importar(con, conta, "b.csv", b"b",
                 ParserFalso([LinhaImportada(*linhas[0]), LinhaImportada("2026-10-17", "X", -100)]),
                 HOJE)
    assert (r.novos, r.ignorados) == (1, 1)
    assert con.execute("SELECT COUNT(*) FROM lancamentos WHERE descricao = 'LUZ'").fetchone()[0] == 0
    assert imp != r.importacao_id


def test_confirmar_duas_vezes_falha(con) -> None:
    conta = _conta(con)
    pid = _pend(con, conta, "2026-10-10", "Luz", -21000)
    pid2 = _pend(con, conta, "2026-10-11", "Luz 2", -21000)
    _imp(con, conta, [("2026-10-16", "LUZ", -23000)])
    lid = _linha_id(con, "LUZ")
    confirmar_casamento(con, pid, lid)
    with pytest.raises(ValueError):
        confirmar_casamento(con, pid, lid)          # mesmo par
    with pytest.raises(ValueError):
        confirmar_casamento(con, pid2, lid)         # linha já usada
    p2 = con.execute("SELECT compensado FROM lancamentos WHERE id = ?", (pid2,)).fetchone()[0]
    assert p2 == 0


def test_confirmar_valida_existencia_e_estado(con) -> None:
    conta = _conta(con)
    pid = _pend(con, conta, "2026-10-10", "Luz", -21000)
    _imp(con, conta, [("2026-10-16", "LUZ", -23000)])
    lid = _linha_id(con, "LUZ")
    with pytest.raises(ValueError, match="não encontrad"):
        confirmar_casamento(con, 9999, lid)
    with pytest.raises(ValueError, match="não encontrad"):
        confirmar_casamento(con, pid, 9999)
    with pytest.raises(ValueError):
        confirmar_casamento(con, lid, lid)  # "pendente" já compensado
    with pytest.raises(ValueError):
        confirmar_casamento(con, pid, pid)  # "linha" não importada
    assert con.execute("SELECT COUNT(*) FROM chaves_quitadas").fetchone()[0] == 0


def test_duas_linhas_para_um_pendente_so_a_melhor(con) -> None:
    conta = _conta(con)
    pid = _pend(con, conta, "2026-10-10", "Luz", -21000)
    imp = _imp(con, conta, [("2026-10-12", "LUZ ENEL", -21000), ("2026-10-19", "OUTRO", -22000)])
    props = propor_casamentos(con, conta, imp)
    assert len(props) == 1
    assert props[0].pendente_id == pid and props[0].lancamento_id == _linha_id(con, "LUZ ENEL")


def test_dois_pendentes_para_uma_linha_so_uma_proposta(con) -> None:
    conta = _conta(con)
    p1 = _pend(con, conta, "2026-10-10", "Luz", -21000)
    _pend(con, conta, "2026-10-14", "Luz extra", -21500)
    imp = _imp(con, conta, [("2026-10-10", "LUZ", -21000)])
    props = propor_casamentos(con, conta, imp)
    assert len(props) == 1
    assert props[0].pendente_id == p1


def test_atribuicao_gulosa_uma_por_par(con) -> None:
    conta = _conta(con)
    p1 = _pend(con, conta, "2026-10-10", "Luz", -21000)
    p2 = _pend(con, conta, "2026-10-10", "Internet", -10000)
    imp = _imp(con, conta, [("2026-10-10", "INTERNET", -10000), ("2026-10-10", "LUZ", -21000)])
    props = {(c.pendente_id, c.lancamento_id) for c in propor_casamentos(con, conta, imp)}
    assert props == {(p1, _linha_id(con, "LUZ")), (p2, _linha_id(con, "INTERNET"))}


def test_ignora_pendentes_de_outra_conta_e_ja_compensados(con) -> None:
    conta = _conta(con)
    outra = criar_conta(con, "Outra", "corrente")
    _pend(con, outra, "2026-10-10", "Luz", -21000)
    inserir_manual(con, conta_id=conta, data="2026-10-10", descricao="Luz", valor_centavos=-21000,
                   tipo="saida", categoria_id=None, compensado=True, observacao=None)
    con.commit()
    imp = _imp(con, conta, [("2026-10-10", "LUZ", -21000)])
    assert propor_casamentos(con, conta, imp) == []


def test_conciliacao_continua_ok_apos_casamento(con) -> None:
    conta = _conta(con)
    pid = _pend(con, conta, "2026-10-10", "Luz", -21000, estimado=True)
    saldos = {"2026-10-16": 100000 - 23000}
    _imp(con, conta, [("2026-10-16", "LUZ", -23000)], saldos=saldos)
    assert conciliar(con, conta, saldos).ok is True
    confirmar_casamento(con, pid, _linha_id(con, "LUZ"))
    assert conciliar(con, conta, saldos).ok is True
    assert saldo_em(con, conta, "2026-10-16") == 77000


def test_desfazer_importacao_reabre_pendente_e_apaga_chaves(con) -> None:
    conta = _conta(con)
    pid = _pend(con, conta, "2026-10-10", "Luz", -21000, estimado=True)
    imp = _imp(con, conta, [("2026-10-16", "LUZ", -23000)])
    confirmar_casamento(con, pid, _linha_id(con, "LUZ"))
    desfazer_importacao(con, imp)
    p = con.execute("SELECT * FROM lancamentos WHERE id = ?", (pid,)).fetchone()
    assert (p["compensado"], p["data_compensacao"], p["importacao_id"], p["origem"]) == (0, None, None, "manual")
    assert con.execute("SELECT COUNT(*) FROM chaves_quitadas").fetchone()[0] == 0
    assert saldo_em(con, conta, "2026-10-31") == 100000
    r = importar(con, conta, "c.csv", b"c", ParserFalso([LinhaImportada("2026-10-16", "LUZ", -23000)]), HOJE)
    assert r.novos == 1


def test_casamento_dataclass() -> None:
    assert Casamento(1, 2, 0.5).pendente_id == 1


def test_undo_restaura_valor_e_estimado_originais(con) -> None:
    conta = _conta(con)
    pid = _pend(con, conta, "2026-10-10", "Luz", -21000, estimado=True)
    imp = _imp(con, conta, [("2026-10-16", "LUZ", -23000)])
    confirmar_casamento(con, pid, _linha_id(con, "LUZ"))
    desfazer_importacao(con, imp)
    p = con.execute("SELECT * FROM lancamentos WHERE id = ?", (pid,)).fetchone()
    assert (p["valor_centavos"], p["estimado"], p["compensado"]) == (-21000, 1, 0)


def test_undo_sem_originais_mantem_valor_e_estimado(con) -> None:
    conta = _conta(con)
    pid = _pend(con, conta, "2026-10-10", "Luz", -21000, estimado=True)
    imp = _imp(con, conta, [("2026-10-16", "LUZ", -23000)])
    confirmar_casamento(con, pid, _linha_id(con, "LUZ"))
    con.execute("UPDATE chaves_quitadas SET valor_original_centavos = NULL, estimado_original = NULL")
    con.commit()
    desfazer_importacao(con, imp)
    p = con.execute("SELECT * FROM lancamentos WHERE id = ?", (pid,)).fetchone()
    assert (p["valor_centavos"], p["estimado"], p["compensado"]) == (-23000, 0, 0)


def test_undo_misto_casada_e_nao_casada(con) -> None:
    conta = _conta(con)
    pid = _pend(con, conta, "2026-10-10", "Luz", -21000, estimado=True)
    imp = _imp(con, conta, [("2026-10-16", "LUZ", -23000), ("2026-10-17", "PADARIA", -900)])
    confirmar_casamento(con, pid, _linha_id(con, "LUZ"))
    desfazer_importacao(con, imp)
    assert con.execute("SELECT COUNT(*) FROM lancamentos WHERE origem = 'importado'").fetchone()[0] == 0
    p = con.execute("SELECT * FROM lancamentos WHERE id = ?", (pid,)).fetchone()
    assert (p["compensado"], p["valor_centavos"], p["estimado"]) == (0, -21000, 1)
    assert con.execute("SELECT COUNT(*) FROM chaves_quitadas").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 1


def test_confirmar_entre_contas_diferentes_falha(con) -> None:
    conta = _conta(con)
    outra = criar_conta(con, "Outra", "corrente")
    pid = _pend(con, outra, "2026-10-10", "Luz", -21000)
    _imp(con, conta, [("2026-10-16", "LUZ", -23000)])
    with pytest.raises(ValueError, match="contas diferentes"):
        confirmar_casamento(con, pid, _linha_id(con, "LUZ"))


def test_similaridade_ignora_acento_e_caixa() -> None:
    from app.dominio.casamento import _similaridade
    assert _similaridade("LUZ ELÉTRICA", "luz eletrica") == 1.0
