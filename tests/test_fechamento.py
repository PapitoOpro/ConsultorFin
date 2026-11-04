"""Card de abertura da tela Início: saldo projetado no fechamento do mês."""
import sqlite3
from datetime import date

from app.dominio.painel import fechamento_do_mes
from app.dominio.projecao import projetar
from app.repo import contas as repo_contas
from app.repo import lancamentos as repo_lanc

HOJE = date(2026, 11, 10)


def _lanc(con: sqlite3.Connection, conta: int, data: str, valor: int, compensado: bool) -> None:
    repo_lanc.inserir_manual(con, conta_id=conta, data=data, descricao="X", valor_centavos=valor,
                             tipo="entrada" if valor > 0 else "saida", categoria_id=None,
                             compensado=compensado, observacao=None)


def test_fechamento_soma_inicial_entradas_e_despesas_previstas(con: sqlite3.Connection) -> None:
    cc = repo_contas.criar_conta(con, "Corrente Teste", "corrente", saldo_inicial_centavos=320000,
                                 data_saldo_inicial="2026-10-01")
    _lanc(con, cc, "2026-11-05", 400000, True)    # recebida
    _lanc(con, cc, "2026-11-06", -150000, True)   # despesa realizada
    _lanc(con, cc, "2026-11-20", 150000, False)   # a receber
    _lanc(con, cc, "2026-11-25", -200000, False)  # a vencer
    _lanc(con, cc, "2026-10-28", -10000, False)   # vencida de outubro: entra no mês corrente
    _lanc(con, cc, "2026-12-02", -99900, False)   # outro mês: fora
    con.commit()
    f = fechamento_do_mes(con, HOJE, 2026, 11)
    assert (f.saldo_inicial_centavos, f.entradas_previstas_centavos) == (320000, 550000)
    assert (f.saidas_realizadas_centavos, f.saidas_a_vencer_centavos) == (150000, 210000)
    assert f.saldo_projetado_centavos == 510000
    assert f.comprometimento_pct == 360000 * 100 // 870000
    # mesmo número da projeção do mês corrente
    assert projetar(con, HOJE, 3)[0].saldo_final_centavos == 510000


def test_fechamento_mes_passado_ignora_vencidas_anteriores(con: sqlite3.Connection) -> None:
    cc = repo_contas.criar_conta(con, "Corrente Teste", "corrente", saldo_inicial_centavos=100000,
                                 data_saldo_inicial="2026-08-01")
    _lanc(con, cc, "2026-08-20", -5000, False)
    con.commit()
    assert fechamento_do_mes(con, HOJE, 2026, 10).saidas_a_vencer_centavos == 0
    assert fechamento_do_mes(con, HOJE, 2026, 8).saidas_a_vencer_centavos == 5000


def test_comprometimento_sem_base_e_none(con: sqlite3.Connection) -> None:
    repo_contas.criar_conta(con, "Corrente Teste", "corrente")
    assert fechamento_do_mes(con, HOJE, 2026, 11).comprometimento_pct is None
