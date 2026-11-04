"""Consultas de leitura da tela Início (sem commit)."""
import sqlite3


def despesas_detalhadas(con: sqlite3.Connection, inicio_iso: str, fim_iso: str,
                        por_pagamento: bool = True) -> list[sqlite3.Row]:
    """Caixa por categoria (`por_pagamento=False`: consumo, itens pela data da compra):
    saídas e pagamentos de fatura não casados, compensados, de contas não-cartão, com data_compensacao em [inicio, fim); mais os itens de
    fatura cuja fatura foi paga (linha da fatura compensada) em [inicio, fim) — o item entra
    no mês do pagamento (crédito com valor negativo, abate). `origem`: 'lancamento' ou
    'fatura_item' (os ids são de tabelas diferentes). Categoria e categoria-pai (NULL = sem)."""
    filtro_itens = (
        " WHERE fl.compensado = 1 AND fl.data_compensacao >= :ini AND fl.data_compensacao < :fim"
        if por_pagamento else " WHERE i.data >= :ini AND i.data < :fim")
    return con.execute(
        "SELECT l.id AS id, 'lancamento' AS origem, l.data_compensacao AS data, l.descricao,"
        " -l.valor_centavos AS valor, c.nome AS conta_nome,"
        " cat.id AS cat_id, cat.nome AS cat_nome, pai.id AS pai_id, pai.nome AS pai_nome"
        " FROM lancamentos l JOIN contas c ON c.id = l.conta_id"
        " LEFT JOIN categorias cat ON cat.id = l.categoria_id"
        " LEFT JOIN categorias pai ON pai.id = cat.pai_id"
        " WHERE l.compensado = 1 AND c.tipo != 'cartao'"
        " AND l.tipo IN ('saida', 'pagamento_fatura')"
        " AND l.data_compensacao >= :ini AND l.data_compensacao < :fim"
        " AND NOT EXISTS (SELECT 1 FROM faturas f WHERE f.pagamento_lancamento_id = l.id)"
        " UNION ALL"
        " SELECT i.id AS id, 'fatura_item' AS origem, i.data AS data, i.descricao,"
        " -i.valor_centavos AS valor, fc.nome AS conta_nome,"
        " cat.id AS cat_id, cat.nome AS cat_nome, pai.id AS pai_id, pai.nome AS pai_nome"
        " FROM fatura_itens i"
        " JOIN faturas fa ON fa.id = i.fatura_id JOIN contas fc ON fc.id = fa.conta_id"
        " LEFT JOIN lancamentos fl ON fl.id = fa.lancamento_id"
        " LEFT JOIN categorias cat ON cat.id = i.categoria_id"
        " LEFT JOIN categorias pai ON pai.id = cat.pai_id"
        + filtro_itens +
        " ORDER BY data, origem, id",
        {"ini": inicio_iso, "fim": fim_iso},
    ).fetchall()


def total_lancamentos(con: sqlite3.Connection) -> int:
    return int(con.execute("SELECT COUNT(*) AS n FROM lancamentos").fetchone()["n"])


def ultimos_efetivados(con: sqlite3.Connection, limite: int) -> list[sqlite3.Row]:
    """Lançamentos compensados mais recentes (data de compensação, depois id), de qualquer conta.
    Traz a conta e a categoria com a categoria-pai (NULL = sem categoria)."""
    return con.execute(
        "SELECT l.id, COALESCE(l.data_compensacao, l.data) AS data, l.descricao,"
        " l.valor_centavos, l.tipo, c.nome AS conta_nome, c.tipo AS conta_tipo,"
        " cat.nome AS cat_nome, pai.nome AS pai_nome"
        " FROM lancamentos l JOIN contas c ON c.id = l.conta_id"
        " LEFT JOIN categorias cat ON cat.id = l.categoria_id"
        " LEFT JOIN categorias pai ON pai.id = cat.pai_id"
        " WHERE l.compensado = 1"
        " ORDER BY COALESCE(l.data_compensacao, l.data) DESC, l.id DESC LIMIT ?",
        (limite,),
    ).fetchall()


def saidas_pendentes_proximas(con: sqlite3.Connection, limite: int) -> list[sqlite3.Row]:
    """Saídas pendentes (valor < 0) por data e id: as vencidas vêm naturalmente primeiro."""
    return con.execute(
        "SELECT l.id, l.data, l.descricao, l.valor_centavos, l.estimado, l.origem, l.conta_id,"
        " c.nome AS conta_nome, c.tipo AS conta_tipo,"
        " cat.nome AS cat_nome, pai.nome AS pai_nome"
        " FROM lancamentos l JOIN contas c ON c.id = l.conta_id"
        " LEFT JOIN categorias cat ON cat.id = l.categoria_id"
        " LEFT JOIN categorias pai ON pai.id = cat.pai_id"
        " WHERE l.compensado = 0 AND l.valor_centavos < 0"
        " ORDER BY l.data, l.id LIMIT ?",
        (limite,),
    ).fetchall()


def existe_compensado_antes(con: sqlite3.Connection, antes_iso: str) -> bool:
    """Há algum lançamento compensado anterior a `antes_iso` (AAAA-MM-DD)?"""
    return con.execute(
        "SELECT 1 FROM lancamentos WHERE compensado = 1"
        " AND COALESCE(data_compensacao, data) < ? LIMIT 1", (antes_iso,)).fetchone() is not None
