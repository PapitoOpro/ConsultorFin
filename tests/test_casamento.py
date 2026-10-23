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


def test_ignora_pendentes_de_outra_conta_e_ja_casados(con) -> None:
    # C1: um compensado manual SEM importação agora é candidato (pago à mão); só os já
    # casados (importacao_id definido) e os de outra conta ficam de fora.
    conta = _conta(con)
    outra = criar_conta(con, "Outra", "corrente")
    _pend(con, outra, "2026-10-10", "Luz", -21000)
    pid = _pend(con, conta, "2026-10-10", "Luz", -21000)
    imp0 = _imp(con, conta, [("2026-10-10", "LUZ", -21000)], nome="0.csv")
    confirmar_casamento(con, pid, _linha_id(con, "LUZ"))
    imp = _imp(con, conta, [("2026-10-11", "LUZ X", -21000)])
    assert imp0 != imp
    assert propor_casamentos(con, conta, imp) == []


# --- C1: pago à mão e depois importado ---------------------------------------------------------

def _pago_a_mao(con, conta: int, data: str, desc: str, valor: int, pago_em: date) -> int:
    from app.dominio.parcelas import marcar_pago
    pid = _pend(con, conta, data, desc, valor)
    marcar_pago(con, pid, None, pago_em)
    return pid


def test_pago_a_mao_e_depois_importado_nao_conta_duas_vezes(con) -> None:
    from app.dominio.saldo import saldo_atual
    from app.importacao.servico import propostas_com_detalhes
    conta = _conta(con)
    pid = _pago_a_mao(con, conta, "2026-10-29", "Internet", -10000, date(2026, 10, 29))
    saldos = {"2026-10-29": 90000}
    imp = _imp(con, conta, [("2026-10-29", "DEB AUT INTERNET FIBRA", -10000)], saldos=saldos)
    props = propostas_com_detalhes(con, conta, imp)
    assert [(p.pendente_id, p.lancamento_id) for p in props] == [
        (pid, _linha_id(con, "DEB AUT INTERNET FIBRA"))]
    confirmar_casamento(con, props[0].pendente_id, props[0].lancamento_id)
    assert saldo_atual(con, conta, date(2026, 10, 30)) == 90000
    assert conciliar(con, conta, saldos).ok is True
    assert con.execute("SELECT COUNT(*) FROM lancamentos WHERE valor_centavos = -10000"
                       ).fetchone()[0] == 1
    p = con.execute("SELECT * FROM lancamentos WHERE id = ?", (pid,)).fetchone()
    assert (p["compensado"], p["importacao_id"], p["data_compensacao"]) == (1, imp, "2026-10-29")


def test_undo_restaura_pago_a_mao(con) -> None:
    conta = _conta(con)
    pid = _pago_a_mao(con, conta, "2026-10-25", "Internet", -10000, date(2026, 10, 27))
    imp = _imp(con, conta, [("2026-10-28", "INTERNET", -10050)])
    confirmar_casamento(con, pid, _linha_id(con, "INTERNET"))
    p = con.execute("SELECT * FROM lancamentos WHERE id = ?", (pid,)).fetchone()
    assert (p["valor_centavos"], p["data_compensacao"], p["importacao_id"]) == (-10050, "2026-10-28", imp)
    q = con.execute("SELECT compensado_original, data_compensacao_original FROM chaves_quitadas"
                    ).fetchone()
    assert tuple(q) == (1, "2026-10-27")
    desfazer_importacao(con, imp)
    p = con.execute("SELECT * FROM lancamentos WHERE id = ?", (pid,)).fetchone()
    assert (p["compensado"], p["data_compensacao"], p["importacao_id"], p["valor_centavos"],
            p["estimado"]) == (1, "2026-10-27", None, -10000, 0)
    assert saldo_em(con, conta, "2026-10-31") == 100000 - 10000
    assert con.execute("SELECT COUNT(*) FROM chaves_quitadas").fetchone()[0] == 0


def test_undo_de_pendente_casado_grava_original_zero(con) -> None:
    conta = _conta(con)
    pid = _pend(con, conta, "2026-10-10", "Luz", -21000)
    imp = _imp(con, conta, [("2026-10-16", "LUZ", -23000)])
    confirmar_casamento(con, pid, _linha_id(con, "LUZ"))
    q = con.execute("SELECT compensado_original, data_compensacao_original FROM chaves_quitadas"
                    ).fetchone()
    assert tuple(q) == (0, None)
    desfazer_importacao(con, imp)
    p = con.execute("SELECT compensado, data_compensacao FROM lancamentos WHERE id = ?",
                    (pid,)).fetchone()
    assert tuple(p) == (0, None)


def test_manual_compensado_em_conta_importada_e_candidato(con) -> None:
    from app.dominio.lancamentos import criar_lancamento_manual
    conta = _conta(con)
    _imp(con, conta, [("2026-10-05", "PADARIA", -900)], nome="a.csv")
    mid = criar_lancamento_manual(con, conta_id=conta, data=date(2026, 10, 12), descricao="Farmácia",
                                  valor_centavos=-4500, compensado=True)
    imp = _imp(con, conta, [("2026-10-12", "FARMACIA EXEMPLO", -4500)], nome="b.csv")
    props = propor_casamentos(con, conta, imp)
    assert [(c.pendente_id, c.lancamento_id) for c in props] == [
        (mid, _linha_id(con, "FARMACIA EXEMPLO"))]


def test_pago_a_mao_de_outra_conta_sinal_oposto_ou_fora_da_janela_nao_e_candidato(con) -> None:
    conta = _conta(con)
    outra = criar_conta(con, "Outra", "corrente")
    _pago_a_mao(con, outra, "2026-10-10", "Luz", -21000, date(2026, 10, 10))
    _pago_a_mao(con, conta, "2026-10-10", "Luz", 21000, date(2026, 10, 10))      # sinal oposto
    _pago_a_mao(con, conta, "2026-09-20", "Luz", -21000, date(2026, 9, 20))      # 20 dias antes
    imp = _imp(con, conta, [("2026-10-10", "LUZ", -21000)])
    assert propor_casamentos(con, conta, imp) == []
    with pytest.raises(ValueError, match="contas diferentes"):
        confirmar_casamento(con, 1, _linha_id(con, "LUZ"))


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


# --- I2: itens criados depois do extrato importado ---------------------------------------------

def test_aluguel_criado_depois_do_extrato_recebe_proposta(con) -> None:
    from app.dominio.casamento import linha_do_extrato_para
    from app.dominio.saldo import saldo_atual
    conta = _conta(con)
    _imp(con, conta, [("2026-10-05", "PIX ALUGUEL APTO", -150000), ("2026-10-06", "PADARIA", -900)])
    pid = _pend(con, conta, "2026-10-05", "Aluguel", -150000)
    props = propor_casamentos(con, conta)
    assert [(c.pendente_id, c.lancamento_id) for c in props] == [(pid, _linha_id(con, "PIX ALUGUEL APTO"))]
    assert linha_do_extrato_para(con, pid) is True
    confirmar_casamento(con, props[0].pendente_id, props[0].lancamento_id)
    assert con.execute("SELECT COUNT(*) FROM lancamentos WHERE valor_centavos = -150000"
                       ).fetchone()[0] == 1
    assert saldo_atual(con, conta, date(2026, 10, 31)) == 100000 - 150000 - 900
    assert propor_casamentos(con, conta) == []
    assert linha_do_extrato_para(con, pid) is False


def test_sem_proposta_entre_contas_diferentes(con) -> None:
    from app.dominio.casamento import linha_do_extrato_para
    conta = _conta(con)
    outra = criar_conta(con, "Outra", "corrente")
    _imp(con, conta, [("2026-10-05", "ALUGUEL", -150000)])
    pid = _pend(con, outra, "2026-10-05", "Aluguel", -150000)
    assert propor_casamentos(con, outra) == []
    assert propor_casamentos(con, conta) == []
    assert linha_do_extrato_para(con, pid) is False


# --- I3: casar/pagar conta fixa atualiza as estimativas futuras ---------------------------------

def _luz(con, conta: int) -> list[int]:
    from app.dominio.recorrentes import criar_recorrente, gerar_recorrentes
    criar_recorrente(con, nome="Luz", tipo="saida", dia=10, valor_estimado_centavos=21000,
                     conta_id=conta, categoria_id=None)
    gerar_recorrentes(con, HOJE.replace(day=1), 4)  # out, nov, dez, jan
    return [r[0] for r in con.execute(
        "SELECT id FROM lancamentos WHERE recorrente_id IS NOT NULL ORDER BY data")]


def _valores_futuros(con, ids: list[int]) -> list[int]:
    return [con.execute("SELECT valor_centavos FROM lancamentos WHERE id = ?", (i,)).fetchone()[0]
            for i in ids]


def test_casar_conta_fixa_atualiza_estimativas_futuras(con) -> None:
    conta = _conta(con)
    ids = _luz(con, conta)
    imp = _imp(con, conta, [("2026-10-12", "DA ENERGIA LUZ", -23000)])
    props = propor_casamentos(con, conta, imp)
    assert [c.pendente_id for c in props] == [ids[0]]
    confirmar_casamento(con, ids[0], props[0].lancamento_id)
    assert _valores_futuros(con, ids) == [-23000, -23000, -23000, -23000]


def test_pagar_conta_fixa_em_lancamentos_atualiza_estimativas_futuras(con) -> None:
    from app.dominio.lancamentos import pagar_pendente
    conta = _conta(con)
    ids = _luz(con, conta)
    pagar_pendente(con, ids[0], 23000, date(2026, 10, 10), hoje=HOJE)
    assert _valores_futuros(con, ids) == [-23000, -23000, -23000, -23000]
    est = [r[0] for r in con.execute(
        "SELECT estimado FROM lancamentos WHERE recorrente_id IS NOT NULL ORDER BY data")]
    assert est == [0, 1, 1, 1]


def test_casar_item_sem_recorrente_nao_mexe_nas_estimativas(con) -> None:
    conta = _conta(con)
    ids = _luz(con, conta)
    pid = _pend(con, conta, "2026-10-20", "Mercado", -30000)
    imp = _imp(con, conta, [("2026-10-20", "MERCADO", -31000)])
    confirmar_casamento(con, pid, _linha_id(con, "MERCADO"))
    assert imp
    assert _valores_futuros(con, ids) == [-21000, -21000, -21000, -21000]
