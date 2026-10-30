"""Acesso a dados de recorrentes (sem commit: a transação é do chamador)."""
import sqlite3
import uuid


def inserir(
    con: sqlite3.Connection, *, nome: str, tipo: str, dia: int, valor_estimado_centavos: int,
    conta_id: int, categoria_id: int | None,
) -> int:
    cur = con.execute(
        "INSERT INTO recorrentes (uuid, nome, tipo, dia, valor_estimado_centavos,"
        " categoria_id, conta_id) VALUES (?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), nome, tipo, dia, valor_estimado_centavos, categoria_id, conta_id),
    )
    return int(cur.lastrowid)


def obter(con: sqlite3.Connection, recorrente_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM recorrentes WHERE id = ?", (recorrente_id,)).fetchone()


def listar_ativas(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM recorrentes WHERE ativa = 1 ORDER BY id").fetchall()


def desativar(con: sqlite3.Connection, recorrente_id: int) -> None:
    con.execute("UPDATE recorrentes SET ativa = 0 WHERE id = ?", (recorrente_id,))


def mes_ja_gerado(con: sqlite3.Connection, recorrente_id: int, mes: str) -> bool:
    """True se já existe lançamento (pendente ou pago) do recorrente na competência AAAA-MM."""
    row = con.execute(
        "SELECT 1 FROM lancamentos WHERE recorrente_id = ? AND competencia = ? LIMIT 1",
        (recorrente_id, mes),
    ).fetchone()
    return row is not None


def mes_pulado(con: sqlite3.Connection, recorrente_id: int, mes: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM recorrentes_pulados WHERE recorrente_id = ? AND mes = ?",
        (recorrente_id, mes),
    ).fetchone()
    return row is not None


def marcar_pulado(con: sqlite3.Connection, recorrente_id: int, mes: str) -> None:
    con.execute(
        "INSERT OR IGNORE INTO recorrentes_pulados (recorrente_id, mes) VALUES (?, ?)",
        (recorrente_id, mes),
    )


def ultimo_valor_real(con: sqlite3.Connection, recorrente_id: int) -> int | None:
    """Magnitude (positiva) do valor do último lançamento confirmado (estimado=0, compensado=1)."""
    row = con.execute(
        "SELECT valor_centavos FROM lancamentos WHERE recorrente_id = ? AND estimado = 0"
        " AND compensado = 1 ORDER BY data DESC, id DESC LIMIT 1",
        (recorrente_id,),
    ).fetchone()
    return None if row is None else abs(int(row["valor_centavos"]))


def inserir_lancamento_estimado(
    con: sqlite3.Connection, *, recorrente_id: int, conta_id: int, data: str, descricao: str,
    valor_centavos: int, tipo: str, categoria_id: int | None, competencia: str,
) -> int:
    novo = str(uuid.uuid4())
    cur = con.execute(
        "INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
        " categoria_id, chave_deduplicacao, origem, compensado, estimado, recorrente_id,"
        " competencia) VALUES (?,?,?,?,?,?,?,?,'manual',0,1,?,?)",
        (novo, conta_id, data, descricao, valor_centavos, tipo, categoria_id,
         f"manual:{novo}", recorrente_id, competencia),
    )
    return int(cur.lastrowid)


def atualizar_futuros_estimados(
    con: sqlite3.Connection, recorrente_id: int, depois_de_iso: str, valor_centavos: int
) -> int:
    """Define o valor dos pendentes ainda estimados com data > depois_de_iso."""
    cur = con.execute(
        "UPDATE lancamentos SET valor_centavos = ? WHERE recorrente_id = ? AND compensado = 0"
        " AND estimado = 1 AND data > ?",
        (valor_centavos, recorrente_id, depois_de_iso),
    )
    return cur.rowcount


def pendentes_futuros(con: sqlite3.Connection, recorrente_id: int, apos_iso: str) -> list[int]:
    """Ids dos pendentes que `excluir_pendentes_futuros` apagaria."""
    return [int(r[0]) for r in con.execute(
        "SELECT id FROM lancamentos WHERE recorrente_id = ? AND compensado = 0 AND data > ?",
        (recorrente_id, apos_iso))]


def excluir_pendentes_futuros(
    con: sqlite3.Connection, recorrente_id: int, apos_iso: str
) -> int:
    """Apaga pendentes (compensado=0) com data > apos_iso; vencidos e pagos permanecem."""
    cur = con.execute(
        "DELETE FROM lancamentos WHERE recorrente_id = ? AND compensado = 0 AND data > ?",
        (recorrente_id, apos_iso),
    )
    return cur.rowcount


def excluir_lancamento(con: sqlite3.Connection, lancamento_id: int) -> None:
    con.execute("DELETE FROM lancamentos WHERE id = ?", (lancamento_id,))


def conta_existe(con: sqlite3.Connection, conta_id: int) -> bool:
    return con.execute("SELECT 1 FROM contas WHERE id = ?", (conta_id,)).fetchone() is not None


def categoria_existe(con: sqlite3.Connection, categoria_id: int) -> bool:
    return (
        con.execute("SELECT 1 FROM categorias WHERE id = ?", (categoria_id,)).fetchone()
        is not None
    )


def listar_com_ultimo_real(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Todas as recorrentes (ativas primeiro) com nome da conta e último valor real (magnitude)."""
    return con.execute(
        "SELECT r.*, c.nome AS conta_nome, (SELECT ABS(l.valor_centavos) FROM lancamentos l"
        " WHERE l.recorrente_id = r.id AND l.estimado = 0 AND l.compensado = 1"
        " ORDER BY l.data DESC, l.id DESC LIMIT 1) AS ultimo_real_centavos"
        " FROM recorrentes r LEFT JOIN contas c ON c.id = r.conta_id"
        " ORDER BY r.ativa DESC, r.dia, r.nome, r.id"
    ).fetchall()
