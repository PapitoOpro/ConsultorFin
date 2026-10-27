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
    commit: bool = True,
) -> int:
    """commit=False: não confirma (a transação é do chamador)."""
    cur = con.execute(
        "INSERT INTO contas (uuid, nome, instituicao, tipo, final_numero,"
        " saldo_inicial_centavos, data_saldo_inicial) VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), nome, instituicao, tipo, final_numero,
         saldo_inicial_centavos, data_saldo_inicial),
    )
    if commit:
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


def atualizar_saldo_inicial(con: sqlite3.Connection, conta_id: int, saldo_inicial_centavos: int,
                            data_saldo_inicial: str) -> None:
    """Sem commit: a transação é do chamador."""
    con.execute(
        "UPDATE contas SET saldo_inicial_centavos = ?, data_saldo_inicial = ? WHERE id = ?",
        (saldo_inicial_centavos, data_saldo_inicial, conta_id),
    )


def ids_nao_cartao(con: sqlite3.Connection) -> list[int]:
    """Ids das contas cujo tipo não é 'cartao'."""
    rows = con.execute("SELECT id FROM contas WHERE tipo <> 'cartao' ORDER BY id").fetchall()
    return [int(r["id"]) for r in rows]


def listar(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM contas ORDER BY nome, id").fetchall()


def obter(con: sqlite3.Connection, conta_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM contas WHERE id = ?", (conta_id,)).fetchone()


def ids_por_final_numero(con: sqlite3.Connection) -> list[tuple[int, str]]:
    """(id, final_numero) das contas que têm final_numero preenchido."""
    rows = con.execute(
        "SELECT id, final_numero FROM contas WHERE final_numero IS NOT NULL AND final_numero <> ''"
        " ORDER BY id"
    ).fetchall()
    return [(int(r["id"]), str(r["final_numero"])) for r in rows]
