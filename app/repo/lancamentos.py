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
