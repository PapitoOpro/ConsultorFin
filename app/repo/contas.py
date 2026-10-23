"""Acesso a dados de contas."""
import sqlite3
import uuid


def criar_conta(
    con: sqlite3.Connection,
    nome: str,
    tipo: str,
    instituicao: str | None = None,
    final_numero: str | None = None,
    saldo_inicial_centavos: int = 0,
    data_saldo_inicial: str | None = None,
) -> int:
    cur = con.execute(
        "INSERT INTO contas (uuid, nome, instituicao, tipo, final_numero,"
        " saldo_inicial_centavos, data_saldo_inicial) VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), nome, instituicao, tipo, final_numero,
         saldo_inicial_centavos, data_saldo_inicial),
    )
    con.commit()
    return int(cur.lastrowid)


def uuid_da_conta(con: sqlite3.Connection, conta_id: int) -> str:
    row = con.execute("SELECT uuid FROM contas WHERE id = ?", (conta_id,)).fetchone()
    if row is None:
        raise ValueError(f"Conta {conta_id} não encontrada.")
    return str(row["uuid"])


def saldo_inicial_da_conta(con: sqlite3.Connection, conta_id: int) -> tuple[int, str | None]:
    """Retorna (saldo_inicial_centavos, data_saldo_inicial ISO ou None)."""
    row = con.execute(
        "SELECT saldo_inicial_centavos, data_saldo_inicial FROM contas WHERE id = ?",
        (conta_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Conta {conta_id} não encontrada.")
    return int(row["saldo_inicial_centavos"]), row["data_saldo_inicial"]
