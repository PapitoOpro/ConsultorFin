"""Fila de faturas de cartão vindas do Gmail (sem commit: a transação é do chamador)."""
import sqlite3


def inserir(con: sqlite3.Connection, *, gmail_msg_id: str, anexo_id: str, emissor: str | None,
            cartao_final: str | None, vencimento: str | None, pdf_hash: str,
            agora: str) -> int | None:
    """None se já existe a mesma (mensagem, anexo)."""
    cur = con.execute(
        "INSERT OR IGNORE INTO fila_faturas (gmail_msg_id, anexo_id, emissor, cartao_final,"
        " vencimento, pdf_hash, criado_em) VALUES (?,?,?,?,?,?,?)",
        (gmail_msg_id, anexo_id, emissor, cartao_final, vencimento, pdf_hash, agora),
    )
    return int(cur.lastrowid) if cur.rowcount else None


def obter(con: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM fila_faturas WHERE id = ?", (item_id,)).fetchone()


def listar_pendentes(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM fila_faturas WHERE estado = 'pendente' ORDER BY criado_em, id"
    ).fetchall()


def definir_estado(con: sqlite3.Connection, item_id: int, estado: str) -> None:
    con.execute("UPDATE fila_faturas SET estado = ? WHERE id = ?", (estado, item_id))


def expirados(con: sqlite3.Connection, limite_iso: str) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM fila_faturas WHERE estado = 'pendente' AND criado_em < ?"
        " ORDER BY criado_em, id",
        (limite_iso,),
    ).fetchall()


def hash_ja_importado(con: sqlite3.Connection, pdf_hash: str) -> bool:
    return con.execute(
        "SELECT 1 FROM faturas WHERE arquivo_hash = ? LIMIT 1", (pdf_hash,)
    ).fetchone() is not None


def fatura_existente(con: sqlite3.Connection, cartao_final: str, vencimento: str) -> bool:
    return con.execute(
        "SELECT 1 FROM faturas f JOIN contas c ON c.id = f.conta_id"
        " WHERE c.final_numero = ? AND f.data_vencimento = ? LIMIT 1",
        (cartao_final, vencimento),
    ).fetchone() is not None


def obter_por_origem(con: sqlite3.Connection, gmail_msg_id: str, anexo_id: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM fila_faturas WHERE gmail_msg_id = ? AND anexo_id = ?",
        (gmail_msg_id, anexo_id),
    ).fetchone()
