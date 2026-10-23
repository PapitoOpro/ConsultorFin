"""Acesso a dados de importações (sem commit: a transação é do chamador)."""
import sqlite3
from datetime import datetime


def existe_ativa_com_hash(con: sqlite3.Connection, conta_id: int, arquivo_hash: str) -> bool:
    return con.execute(
        "SELECT 1 FROM importacoes WHERE conta_id = ? AND arquivo_hash = ? AND desfeita = 0",
        (conta_id, arquivo_hash),
    ).fetchone() is not None


def criar(con: sqlite3.Connection, conta_id: int, arquivo_nome: str, arquivo_hash: str,
          criado_em: datetime) -> int:
    cur = con.execute(
        "INSERT INTO importacoes (arquivo_nome, arquivo_hash, conta_id, criado_em)"
        " VALUES (?,?,?,?)",
        (arquivo_nome, arquivo_hash, conta_id, criado_em.isoformat(timespec="seconds")),
    )
    return int(cur.lastrowid)


def finalizar(con: sqlite3.Connection, importacao_id: int, periodo_inicio: str | None,
              periodo_fim: str | None, lidos: int, novos: int, ignorados: int) -> None:
    con.execute(
        "UPDATE importacoes SET periodo_inicio=?, periodo_fim=?, total_lidos=?,"
        " total_novos=?, total_ignorados=? WHERE id=?",
        (periodo_inicio, periodo_fim, lidos, novos, ignorados, importacao_id),
    )


def marcar_desfeita(con: sqlite3.Connection, importacao_id: int) -> bool:
    cur = con.execute("UPDATE importacoes SET desfeita = 1 WHERE id = ?", (importacao_id,))
    return cur.rowcount > 0


def obter(con: sqlite3.Connection, importacao_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM importacoes WHERE id = ?", (importacao_id,)).fetchone()


def ultima_ativa_da_conta(con: sqlite3.Connection, conta_id: int | None) -> sqlite3.Row | None:
    """Importação ativa (desfeita = 0) mais recente da conta."""
    return con.execute(
        "SELECT * FROM importacoes WHERE conta_id IS ? AND desfeita = 0 ORDER BY id DESC LIMIT 1",
        (conta_id,),
    ).fetchone()


def ids_ultimas_ativas(con: sqlite3.Connection) -> set[int]:
    """Id da importação ativa mais recente de cada conta (as únicas que podem ser desfeitas)."""
    rows = con.execute(
        "SELECT MAX(id) AS id FROM importacoes WHERE desfeita = 0 GROUP BY conta_id"
    ).fetchall()
    return {int(r["id"]) for r in rows}


def criada_em_da_ativa_com_hash(con: sqlite3.Connection, conta_id: int,
                                arquivo_hash: str) -> str | None:
    """ISO de criação da importação ativa com o hash, ou None."""
    row = con.execute(
        "SELECT criado_em FROM importacoes WHERE conta_id = ? AND arquivo_hash = ?"
        " AND desfeita = 0 ORDER BY id DESC LIMIT 1",
        (conta_id, arquivo_hash),
    ).fetchone()
    return None if row is None else str(row["criado_em"])


def listar_com_conta(con: sqlite3.Connection, limite: int = 50) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT i.*, c.nome AS conta_nome FROM importacoes i"
        " LEFT JOIN contas c ON c.id = i.conta_id ORDER BY i.id DESC LIMIT ?",
        (limite,),
    ).fetchall()
