"""Acesso a dados de categorias, regras e totais mensais (sem lógica de negócio)."""
import sqlite3
import uuid


def criar(con: sqlite3.Connection, nome: str, pai_id: int | None = None) -> int:
    cur = con.execute(
        "INSERT INTO categorias (uuid, nome, pai_id) VALUES (?,?,?)",
        (str(uuid.uuid4()), nome, pai_id),
    )
    con.commit()
    return int(cur.lastrowid)


def listar_raiz(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT id, nome, pai_id FROM categorias WHERE pai_id IS NULL ORDER BY nome"
    ).fetchall()


def listar_filhas(con: sqlite3.Connection, pai_id: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT id, nome, pai_id FROM categorias WHERE pai_id = ? ORDER BY nome", (pai_id,)
    ).fetchall()


def existe(con: sqlite3.Connection, categoria_id: int) -> bool:
    return con.execute("SELECT 1 FROM categorias WHERE id = ?", (categoria_id,)).fetchone() is not None


def renomear(con: sqlite3.Connection, categoria_id: int, nome: str) -> bool:
    cur = con.execute("UPDATE categorias SET nome = ? WHERE id = ?", (nome, categoria_id))
    con.commit()
    return cur.rowcount > 0


def tem_filhas(con: sqlite3.Connection, categoria_id: int) -> bool:
    return con.execute(
        "SELECT 1 FROM categorias WHERE pai_id = ?", (categoria_id,)
    ).fetchone() is not None


def remover(con: sqlite3.Connection, categoria_id: int) -> bool:
    """Remove a categoria; lançamentos e recorrentes ficam sem categoria, regras são removidas."""
    con.execute("UPDATE lancamentos SET categoria_id = NULL WHERE categoria_id = ?", (categoria_id,))
    con.execute("UPDATE recorrentes SET categoria_id = NULL WHERE categoria_id = ?", (categoria_id,))
    con.execute("DELETE FROM regras_categoria WHERE categoria_id = ?", (categoria_id,))
    cur = con.execute("DELETE FROM categorias WHERE id = ?", (categoria_id,))
    con.commit()
    return cur.rowcount > 0


def inserir_regra(con: sqlite3.Connection, padrao: str, categoria_id: int, prioridade: int) -> int:
    cur = con.execute(
        "INSERT INTO regras_categoria (padrao, categoria_id, prioridade) VALUES (?,?,?)",
        (padrao, categoria_id, prioridade),
    )
    con.commit()
    return int(cur.lastrowid)


def listar_regras_ordenadas(con: sqlite3.Connection) -> list[tuple[str, int]]:
    """(padrao, categoria_id) por prioridade desc, depois id asc."""
    rows = con.execute(
        "SELECT padrao, categoria_id FROM regras_categoria ORDER BY prioridade DESC, id ASC"
    ).fetchall()
    return [(str(r["padrao"]), int(r["categoria_id"])) for r in rows]


def lancamentos_sem_categoria(
    con: sqlite3.Connection, importacao_id: int | None
) -> list[tuple[int, str]]:
    sql = "SELECT id, descricao FROM lancamentos WHERE categoria_id IS NULL"
    params: tuple[int, ...] = ()
    if importacao_id is not None:
        sql += " AND importacao_id = ?"
        params = (importacao_id,)
    return [(int(r["id"]), str(r["descricao"])) for r in con.execute(sql, params).fetchall()]


def definir_categoria_do_lancamento(
    con: sqlite3.Connection, lancamento_id: int, categoria_id: int
) -> None:
    con.execute("UPDATE lancamentos SET categoria_id = ? WHERE id = ?", (categoria_id, lancamento_id))


def total_compensado_do_mes(
    con: sqlite3.Connection, tipos: tuple[str, ...], inicio_iso: str, fim_iso: str
) -> int:
    """Soma (com sinal) dos compensados de contas não-cartão com data_compensacao em [inicio, fim)."""
    marcas = ",".join("?" for _ in tipos)
    row = con.execute(
        "SELECT COALESCE(SUM(l.valor_centavos), 0) AS s FROM lancamentos l"
        " JOIN contas c ON c.id = l.conta_id"
        f" WHERE l.compensado = 1 AND c.tipo != 'cartao' AND l.tipo IN ({marcas})"
        " AND l.data_compensacao >= ? AND l.data_compensacao < ?",
        (*tipos, inicio_iso, fim_iso),
    ).fetchone()
    return int(row["s"])
