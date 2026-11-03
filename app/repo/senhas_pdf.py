"""SQL das senhas de PDF cifradas (sem commit: a transação é do chamador)."""
import sqlite3


def salvar(con: sqlite3.Connection, emissor: str, cartao_final: str, nonce: bytes, cifra: bytes,
           agora: str) -> None:
    con.execute(
        "INSERT INTO senhas_pdf (emissor, cartao_final, nonce, cifra, criado_em)"
        " VALUES (?,?,?,?,?)"
        " ON CONFLICT (emissor, cartao_final) DO UPDATE SET"
        " nonce = excluded.nonce, cifra = excluded.cifra, criado_em = excluded.criado_em",
        (emissor, cartao_final, nonce, cifra, agora))


def linhas(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT id, emissor, cartao_final, nonce, cifra FROM senhas_pdf"
                       " ORDER BY emissor, cartao_final").fetchall()


def listar(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Só identificação do cartão: nunca nonce nem cifra."""
    return con.execute("SELECT id, emissor, cartao_final FROM senhas_pdf"
                       " ORDER BY emissor, cartao_final").fetchall()


def apagar(con: sqlite3.Connection, item_id: int) -> bool:
    return con.execute("DELETE FROM senhas_pdf WHERE id = ?", (item_id,)).rowcount > 0
