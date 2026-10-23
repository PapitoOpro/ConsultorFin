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
