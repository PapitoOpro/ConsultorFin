"""Titulares dos holerites (sem commit: a transação é do chamador)."""
import sqlite3


def listar(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT id, nome FROM titulares ORDER BY id").fetchall()


def obter(con: sqlite3.Connection, titular_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT id, nome FROM titulares WHERE id = ?", (titular_id,)).fetchone()


def criar(con: sqlite3.Connection, nome: str) -> int:
    return int(con.execute("INSERT INTO titulares (nome) VALUES (?)", (nome.strip(),)).lastrowid)


def ultimo_usado(con: sqlite3.Connection) -> int:
    """Titular do holerite mais recente (padrão do seletor de importação); sem holerites, o 1."""
    row = con.execute("SELECT titular_id FROM holerites ORDER BY id DESC LIMIT 1").fetchone()
    return int(row[0]) if row else 1
