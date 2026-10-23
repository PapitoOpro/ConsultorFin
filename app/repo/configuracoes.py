"""Acesso a dados de configurações (chave/valor)."""
import sqlite3


def obter(con: sqlite3.Connection, chave: str) -> str | None:
    row = con.execute("SELECT valor FROM configuracoes WHERE chave = ?", (chave,)).fetchone()
    return None if row is None else row["valor"]


def definir(con: sqlite3.Connection, chave: str, valor: str) -> None:
    con.execute(
        "INSERT INTO configuracoes (chave, valor) VALUES (?, ?)"
        " ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor",
        (chave, valor),
    )
    con.commit()
