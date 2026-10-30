"""Acesso a dados de faturas de cartão e seus itens (sem commit: a transação é do chamador)."""
import sqlite3
import uuid


def criar(con: sqlite3.Connection, *, conta_id: int, competencia: str, data_vencimento: str,
          data_fechamento: str | None, emissor: str | None, bandeira: str | None,
          produto: str | None, total_impresso_centavos: int, total_calculado_centavos: int,
          ajustes_resumo_centavos: int, arquivo_hash: str, arquivo_nome: str | None,
          criado_em: str) -> int:
    cur = con.execute(
        "INSERT INTO faturas (uuid, conta_id, competencia, data_vencimento, data_fechamento,"
        " emissor, bandeira, produto, total_impresso_centavos, total_calculado_centavos,"
        " ajustes_resumo_centavos, arquivo_hash, arquivo_nome, criado_em)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), conta_id, competencia, data_vencimento, data_fechamento, emissor,
         bandeira, produto, total_impresso_centavos, total_calculado_centavos,
         ajustes_resumo_centavos, arquivo_hash, arquivo_nome, criado_em),
    )
    return int(cur.lastrowid)


def obter(con: sqlite3.Connection, fatura_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM faturas WHERE id = ?", (fatura_id,)).fetchone()


def por_conta_competencia(con: sqlite3.Connection, conta_id: int,
                          competencia: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM faturas WHERE conta_id = ? AND competencia = ?",
        (conta_id, competencia),
    ).fetchone()


def por_lancamento(con: sqlite3.Connection, lancamento_id: int) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM faturas WHERE lancamento_id = ?", (lancamento_id,)
    ).fetchone()


def por_pagamento(con: sqlite3.Connection, lancamento_id: int) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM faturas WHERE pagamento_lancamento_id = ?", (lancamento_id,)
    ).fetchone()


def definir_lancamento(con: sqlite3.Connection, fatura_id: int, lancamento_id: int) -> None:
    con.execute("UPDATE faturas SET lancamento_id = ? WHERE id = ?", (lancamento_id, fatura_id))


def atualizar_totais(con: sqlite3.Connection, fatura_id: int, impresso: int, calculado: int,
                     ajustes: int, arquivo_hash: str, arquivo_nome: str | None) -> None:
    con.execute(
        "UPDATE faturas SET total_impresso_centavos = ?, total_calculado_centavos = ?,"
        " ajustes_resumo_centavos = ?, arquivo_hash = ?, arquivo_nome = ? WHERE id = ?",
        (impresso, calculado, ajustes, arquivo_hash, arquivo_nome, fatura_id),
    )


def inserir_item(con: sqlite3.Connection, fatura_id: int, *, ordem: int, data: str,
                 descricao: str, valor_centavos: int, tipo: str, categoria_id: int | None,
                 categoria_banco: str | None, parcela_atual: int | None,
                 parcela_total: int | None, grupo_parcela_uuid: str | None,
                 final_cartao: str | None) -> int:
    cur = con.execute(
        "INSERT INTO fatura_itens (uuid, fatura_id, ordem, data, descricao, valor_centavos,"
        " tipo, categoria_id, categoria_banco, parcela_atual, parcela_total,"
        " grupo_parcela_uuid, final_cartao) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), fatura_id, ordem, data, descricao, valor_centavos, tipo,
         categoria_id, categoria_banco, parcela_atual, parcela_total, grupo_parcela_uuid,
         final_cartao),
    )
    return int(cur.lastrowid)


def itens(con: sqlite3.Connection, fatura_id: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM fatura_itens WHERE fatura_id = ? ORDER BY ordem, id", (fatura_id,)
    ).fetchall()


def obter_item(con: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM fatura_itens WHERE id = ?", (item_id,)).fetchone()


def excluir_itens(con: sqlite3.Connection, fatura_id: int) -> None:
    con.execute("DELETE FROM fatura_itens WHERE fatura_id = ?", (fatura_id,))


def atualizar_categoria_item(con: sqlite3.Connection, ids: list[int],
                             categoria_id: int | None) -> int:
    if not ids:
        return 0
    marcas = ",".join("?" * len(ids))
    cur = con.execute(
        f"UPDATE fatura_itens SET categoria_id = ? WHERE id IN ({marcas})",  # noqa: S608
        (categoria_id, *ids),
    )
    return cur.rowcount


def listar(con: sqlite3.Connection, conta_id: int | None = None) -> list[sqlite3.Row]:
    if conta_id is None:
        return con.execute("SELECT * FROM faturas ORDER BY data_vencimento DESC, id DESC"
                           ).fetchall()
    return con.execute(
        "SELECT * FROM faturas WHERE conta_id = ? ORDER BY data_vencimento DESC, id DESC",
        (conta_id,),
    ).fetchall()


def total_em_aberto(con: sqlite3.Connection, conta_id: int) -> int:
    """Soma (centavos, positivo) do total impresso das faturas da conta ainda não pagas
    (lançamento pendente). O impresso é o que o banco cobra (inclui saldo anterior/financiado)."""
    row = con.execute(
        "SELECT COALESCE(SUM(f.total_impresso_centavos), 0) FROM faturas f"
        " JOIN lancamentos l ON l.id = f.lancamento_id"
        " WHERE f.conta_id = ? AND l.compensado = 0", (conta_id,)).fetchone()
    return int(row[0])


def marcar_pagamento(con: sqlite3.Connection, fatura_id: int,
                     lancamento_id: int | None) -> None:
    con.execute("UPDATE faturas SET pagamento_lancamento_id = ? WHERE id = ?",
                (lancamento_id, fatura_id))


def contar_itens(con: sqlite3.Connection, ids: list[int]) -> int:
    if not ids:
        return 0
    marcas = ",".join("?" * len(ids))
    row = con.execute(
        f"SELECT COUNT(*) AS n FROM fatura_itens WHERE id IN ({marcas})", ids  # noqa: S608
    ).fetchone()
    return int(row["n"])


def atualizar_vencimento(con: sqlite3.Connection, fatura_id: int, data_vencimento: str,
                         data_fechamento: str | None) -> None:
    con.execute("UPDATE faturas SET data_vencimento = ?, data_fechamento = ? WHERE id = ?",
                (data_vencimento, data_fechamento, fatura_id))


def itens_parcelados_da_conta(con: sqlite3.Connection, conta_id: int,
                              excluir_fatura_id: int | None) -> list[sqlite3.Row]:
    """Itens com grupo de parcelas das faturas da conta (exceto `excluir_fatura_id`)."""
    return con.execute(
        "SELECT i.* FROM fatura_itens i JOIN faturas f ON f.id = i.fatura_id"
        " WHERE f.conta_id = ? AND i.fatura_id <> COALESCE(?, -1)"
        " AND i.grupo_parcela_uuid IS NOT NULL ORDER BY i.id",
        (conta_id, excluir_fatura_id),
    ).fetchall()


def a_pagar_por_total(con: sqlite3.Connection, total_impresso_centavos: int, venc_min: str,
                      venc_max: str) -> list[sqlite3.Row]:
    """Faturas sem pagamento casado com o total impresso e vencimento em [venc_min, venc_max],
    pelo vencimento."""
    return con.execute(
        "SELECT * FROM faturas WHERE pagamento_lancamento_id IS NULL"
        " AND total_impresso_centavos = ? AND data_vencimento >= ? AND data_vencimento <= ?"
        " ORDER BY data_vencimento, id",
        (total_impresso_centavos, venc_min, venc_max),
    ).fetchall()


def a_pagar(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Faturas com linha lançada e sem pagamento casado, pelo vencimento."""
    return con.execute(
        "SELECT * FROM faturas WHERE pagamento_lancamento_id IS NULL AND lancamento_id IS NOT NULL"
        " ORDER BY data_vencimento, id"
    ).fetchall()


def descasar_pagamentos_da_importacao(con: sqlite3.Connection, importacao_id: int) -> int:
    """Faturas pagas por lançamentos da importação ficam sem pagamento casado."""
    return con.execute(
        "UPDATE faturas SET pagamento_lancamento_id = NULL WHERE pagamento_lancamento_id IN"
        " (SELECT id FROM lancamentos WHERE importacao_id = ?)",
        (importacao_id,),
    ).rowcount


def categorias_usadas(con: sqlite3.Connection, conta_id: int) -> list[sqlite3.Row]:
    """Itens já categorizados de faturas da conta, da fatura mais recente para a mais antiga."""
    return con.execute(
        "SELECT i.descricao, i.categoria_id FROM fatura_itens i JOIN faturas f ON f.id = i.fatura_id"
        " WHERE f.conta_id = ? AND i.categoria_id IS NOT NULL"
        " ORDER BY f.data_vencimento DESC, i.id DESC",
        (conta_id,),
    ).fetchall()
