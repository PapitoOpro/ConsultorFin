"""Acesso a dados de lançamentos (sem commit: a transação é do chamador)."""
import sqlite3
import uuid


def chave_existe(con: sqlite3.Connection, chave: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM lancamentos WHERE chave_deduplicacao = ? "
        "UNION ALL SELECT 1 FROM chaves_quitadas WHERE chave = ? LIMIT 1",
        (chave, chave),
    ).fetchone()
    return row is not None


def inserir_importado(
    con: sqlite3.Connection, conta_id: int, importacao_id: int, data: str,
    descricao: str, valor_centavos: int, tipo: str, chave: str,
) -> int:
    cur = con.execute(
        "INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
        " chave_deduplicacao, origem, importacao_id, data_compensacao, compensado)"
        " VALUES (?,?,?,?,?,?,?,'importado',?,?,1)",
        (str(uuid.uuid4()), conta_id, data, descricao, valor_centavos, tipo, chave,
         importacao_id, data),
    )
    return int(cur.lastrowid)


def remover_importados_da_importacao(con: sqlite3.Connection, importacao_id: int) -> int:
    cur = con.execute(
        "DELETE FROM lancamentos WHERE importacao_id = ? AND origem = 'importado'",
        (importacao_id,),
    )
    return cur.rowcount


def soma_compensados_ate(con: sqlite3.Connection, conta_id: int, data_iso: str) -> int:
    """Soma dos lançamentos compensados com data_compensacao <= data_iso."""
    row = con.execute(
        "SELECT COALESCE(SUM(valor_centavos), 0) AS s FROM lancamentos"
        " WHERE conta_id = ? AND compensado = 1 AND data_compensacao <= ?",
        (conta_id, data_iso),
    ).fetchone()
    return int(row["s"])


# Ponto único de extensão da conciliação (Ruling 2, Task 9: incluir também
# "OR importacao_id IS NOT NULL").
_FILTRO_CONCILIACAO = "origem = 'importado'"


def somas_diarias_conciliacao(
    con: sqlite3.Connection, conta_id: int
) -> list[tuple[str, int]]:
    """(data_compensacao, soma) dos compensados que contam na conciliação, em ordem crescente."""
    rows = con.execute(
        "SELECT data_compensacao AS d, SUM(valor_centavos) AS s FROM lancamentos"
        f" WHERE conta_id = ? AND compensado = 1 AND ({_FILTRO_CONCILIACAO})"
        " AND data_compensacao IS NOT NULL GROUP BY data_compensacao ORDER BY data_compensacao",
        (conta_id,),
    ).fetchall()
    return [(str(r["d"]), int(r["s"])) for r in rows]


def inserir_manual(
    con: sqlite3.Connection, *, conta_id: int, data: str, descricao: str,
    valor_centavos: int, tipo: str, categoria_id: int | None, compensado: bool,
    observacao: str | None, grupo_parcela_uuid: str | None = None,
    parcela_atual: int | None = None, parcela_total: int | None = None,
) -> int:
    """Insere lançamento manual (sem commit). Chave: manual:<uuid>."""
    novo = str(uuid.uuid4())
    cur = con.execute(
        "INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
        " categoria_id, chave_deduplicacao, origem, observacao, data_compensacao, compensado,"
        " grupo_parcela_uuid, parcela_atual, parcela_total)"
        " VALUES (?,?,?,?,?,?,?,?,'manual',?,?,?,?,?,?)",
        (novo, conta_id, data, descricao, valor_centavos, tipo, categoria_id,
         f"manual:{novo}", observacao, data if compensado else None, int(compensado),
         grupo_parcela_uuid, parcela_atual, parcela_total),
    )
    return int(cur.lastrowid)


def obter(con: sqlite3.Connection, lancamento_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM lancamentos WHERE id = ?", (lancamento_id,)).fetchone()


def marcar_compensado(
    con: sqlite3.Connection, lancamento_id: int, data_iso: str, valor_centavos: int
) -> None:
    con.execute(
        "UPDATE lancamentos SET compensado = 1, data_compensacao = ?, estimado = 0,"
        " valor_centavos = ? WHERE id = ?",
        (data_iso, valor_centavos, lancamento_id),
    )


def parcelas_do_grupo(
    con: sqlite3.Connection, grupo_uuid: str, *, so_pendentes: bool = False
) -> list[sqlite3.Row]:
    filtro = " AND compensado = 0" if so_pendentes else ""
    return con.execute(
        "SELECT * FROM lancamentos WHERE grupo_parcela_uuid = ?" + filtro
        + " ORDER BY parcela_atual",
        (grupo_uuid,),
    ).fetchall()


def excluir_pendentes_do_grupo(con: sqlite3.Connection, grupo_uuid: str) -> int:
    cur = con.execute(
        "DELETE FROM lancamentos WHERE grupo_parcela_uuid = ? AND compensado = 0",
        (grupo_uuid,),
    )
    return cur.rowcount


def atualizar_parcela_total_do_grupo(
    con: sqlite3.Connection, grupo_uuid: str, parcela_total: int
) -> None:
    con.execute(
        "UPDATE lancamentos SET parcela_total = ? WHERE grupo_parcela_uuid = ?",
        (parcela_total, grupo_uuid),
    )
