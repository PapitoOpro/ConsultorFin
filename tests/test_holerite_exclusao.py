"""Excluir lançamento pendente vinculado a holerite libera o vínculo (dados inventados)."""
import sqlite3
from datetime import date

from app.dominio import holerites as dom
from app.dominio.lancamentos import excluir_manual
from app.dominio.recorrentes import criar_recorrente, desativar_recorrente, gerar_recorrentes, pular_mes
from app.importacao.holerite.modelo import HoleriteLido, ItemHolerite
from app.repo import contas as repo_contas
from app.repo import holerites as repo

HOJE = date(2026, 10, 30)


def _h() -> HoleriteLido:
    return HoleriteLido("mensal", "2026-09", "ACME", 500000, 120000, 380000,
                        [ItemHolerite("1", "SALARIO FICTICIO", None, "provento", 500000),
                         ItemHolerite("2", "IMPOSTO TESTE", None, "desconto", 120000)])


def _conta(con: sqlite3.Connection) -> int:
    return repo_contas.criar_conta(con, "Corrente", "corrente")


def _salario_recorrente(con: sqlite3.Connection, conta: int) -> tuple[int, int]:
    """Recorrente 'Salário' (dia 2) gerada a partir de out/2026; devolve (recorrente, pendente
    de 2026-10-02)."""
    rid = criar_recorrente(con, nome="Salário", tipo="entrada", dia=2,
                           valor_estimado_centavos=380000, conta_id=conta, categoria_id=None)
    gerar_recorrentes(con, date(2026, 10, 1), 2)
    pid = int(con.execute("SELECT id FROM lancamentos WHERE recorrente_id = ? AND data = ?",
                          (rid, "2026-10-02")).fetchone()[0])
    return rid, pid


def _vincular(con: sqlite3.Connection, conta: int, pid: int) -> int:
    r = dom.importar_holerite(con, _h(), "h1", None, conta, HOJE, [None, None], pid)
    assert r.lancamento_id == pid
    return r.holerite_id


def _lanc(con: sqlite3.Connection, hid: int) -> sqlite3.Row | None:
    lid = repo.obter(con, hid)["lancamento_id"]
    return None if lid is None else con.execute(
        "SELECT * FROM lancamentos WHERE id = ?", (lid,)).fetchone()


def test_pular_mes_de_salario_vinculado_recria_prevista(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    _, pid = _salario_recorrente(con, conta)
    hid = _vincular(con, conta, pid)
    pular_mes(con, pid, HOJE)
    nova = _lanc(con, hid)
    assert nova is not None and (nova["origem"], nova["compensado"], nova["data"]) == (
        "holerite", 0, "2026-10-02")
    assert con.execute("SELECT 1 FROM lancamentos WHERE id = ?", (pid,)).fetchone() is None


def test_pular_mes_tarde_deixa_holerite_sem_credito(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    _, pid = _salario_recorrente(con, conta)
    hid = _vincular(con, conta, pid)
    pular_mes(con, pid, date(2026, 12, 31))
    assert _lanc(con, hid) is None
    assert dom.listar_por_ano(con, 2026)[0].credito == "sem"


def test_desativar_recorrente_com_pendente_vinculado(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    rid, pid = _salario_recorrente(con, conta)
    hid = _vincular(con, conta, pid)
    desativar_recorrente(con, rid, date(2026, 10, 1))
    nova = _lanc(con, hid)
    assert nova is not None and nova["origem"] == "holerite" and nova["id"] != pid
    assert con.execute("SELECT COUNT(*) FROM lancamentos WHERE recorrente_id = ?",
                       (rid,)).fetchone()[0] == 0


def _manual(con: sqlite3.Connection, conta: int, chave: str) -> int:
    cur = con.execute(
        "INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
        " chave_deduplicacao, origem, compensado, estimado) VALUES (?,?,'2026-10-03','SALARIO',"
        " 379000,'entrada',?,'manual',0,1)", (chave, conta, chave))
    con.commit()
    return int(cur.lastrowid)


def test_excluir_manual_vinculado_libera_vinculo(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    mid = _manual(con, conta, "m1")
    hid = _vincular(con, conta, mid)
    excluir_manual(con, mid, HOJE)
    nova = _lanc(con, hid)
    assert nova is not None and nova["origem"] == "holerite" and nova["valor_centavos"] == 380000


def test_salario_mensal_recorrente_sugere_a_ocorrencia_mais_proxima(
        con: sqlite3.Connection) -> None:
    conta = _conta(con)
    rid, _ = _salario_recorrente(con, conta)  # ocorrências em 2026-10-02 e 2026-11-02
    nov = int(con.execute("SELECT id FROM lancamentos WHERE recorrente_id = ? AND data = ?",
                          (rid, "2026-11-02")).fetchone()[0])
    h = HoleriteLido("mensal", "2026-10", "ACME", 500000, 120000, 380000, _h().itens)
    p = dom.preparar_previa(con, h, "h1", conta, HOJE)
    assert p.data_prevista == "2026-11-02"
    assert [c.data for c in p.creditos_candidatos] == ["2026-11-02", "2026-10-02"]
    assert p.pendente_sugerido == nov
    antes = con.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0]
    r = dom.importar_holerite(con, h, "h1", None, conta, HOJE, [None, None], nov)
    assert r.lancamento_id == nov
    assert con.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == antes


def test_dois_pendentes_igualmente_proximos_nao_sugerem(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    _manual(con, conta, "a")
    _manual(con, conta, "b")
    assert dom.preparar_previa(con, _h(), "h1", conta, HOJE).pendente_sugerido is None


def test_excluir_sem_vinculo_continua_igual(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    mid = _manual(con, conta, "m2")
    excluir_manual(con, mid)
    assert con.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0
    rid, pid = _salario_recorrente(con, conta)
    pular_mes(con, pid)
    desativar_recorrente(con, rid, date(2026, 10, 1))
    assert con.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0
    assert repo.listar(con) == []
