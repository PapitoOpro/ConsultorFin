"""Consultas de leitura da tela Início (sem commit)."""
import sqlite3


def despesas_detalhadas(con: sqlite3.Connection, inicio_iso: str, fim_iso: str) -> list[sqlite3.Row]:
    """Saídas e pagamentos de fatura compensados de contas não-cartão com
    data_compensacao em [inicio, fim) — mesmo critério de `despesas_do_mes`.
    Traz categoria e categoria-pai (NULL = sem categoria)."""
    return con.execute(
        "SELECT l.id, l.data_compensacao AS data, l.descricao, -l.valor_centavos AS valor,"
        " cat.id AS cat_id, cat.nome AS cat_nome, pai.id AS pai_id, pai.nome AS pai_nome"
        " FROM lancamentos l JOIN contas c ON c.id = l.conta_id"
        " LEFT JOIN categorias cat ON cat.id = l.categoria_id"
        " LEFT JOIN categorias pai ON pai.id = cat.pai_id"
        " WHERE l.compensado = 1 AND c.tipo != 'cartao'"
        " AND l.tipo IN ('saida', 'pagamento_fatura')"
        " AND l.data_compensacao >= ? AND l.data_compensacao < ?"
        " ORDER BY l.data_compensacao, l.id",
        (inicio_iso, fim_iso),
    ).fetchall()


def total_lancamentos(con: sqlite3.Connection) -> int:
    return int(con.execute("SELECT COUNT(*) AS n FROM lancamentos").fetchone()["n"])
