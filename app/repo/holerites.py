"""Acesso a dados de holerites e seus itens (sem commit: a transação é do chamador)."""
import sqlite3
import uuid


def criar(con: sqlite3.Connection, *, conta_id: int, competencia: str, tipo: str,
          empresa: str | None, total_vencimentos: int, total_descontos: int, liquido: int,
          arquivo_hash: str, arquivo_nome: str | None, lancamento_id: int | None,
          criado_em: str) -> int:
    cur = con.execute(
        "INSERT INTO holerites (uuid, conta_id, competencia, tipo, empresa,"
        " total_vencimentos_centavos, total_descontos_centavos, liquido_centavos,"
        " arquivo_hash, arquivo_nome, lancamento_id, criado_em)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), conta_id, competencia, tipo, empresa, total_vencimentos,
         total_descontos, liquido, arquivo_hash, arquivo_nome, lancamento_id, criado_em),
    )
    return int(cur.lastrowid)


def obter(con: sqlite3.Connection, holerite_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM holerites WHERE id = ?", (holerite_id,)).fetchone()


def por_competencia_tipo(con: sqlite3.Connection, competencia: str,
                         tipo: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM holerites WHERE competencia = ? AND tipo = ?", (competencia, tipo)
    ).fetchone()


def listar(con: sqlite3.Connection, ano: int | None = None) -> list[sqlite3.Row]:
    if ano is None:
        return con.execute(
            "SELECT * FROM holerites ORDER BY competencia DESC, tipo, id").fetchall()
    return con.execute(
        "SELECT * FROM holerites WHERE substr(competencia, 1, 4) = ?"
        " ORDER BY competencia DESC, tipo, id",
        (f"{ano:04d}",),
    ).fetchall()


def atualizar_cabecalho(con: sqlite3.Connection, holerite_id: int, *, conta_id: int,
                        empresa: str | None, total_vencimentos: int, total_descontos: int,
                        liquido: int, arquivo_hash: str, arquivo_nome: str | None,
                        lancamento_id: int | None) -> None:
    con.execute(
        "UPDATE holerites SET conta_id = ?, empresa = ?, total_vencimentos_centavos = ?,"
        " total_descontos_centavos = ?, liquido_centavos = ?, arquivo_hash = ?,"
        " arquivo_nome = ?, lancamento_id = ? WHERE id = ?",
        (conta_id, empresa, total_vencimentos, total_descontos, liquido, arquivo_hash,
         arquivo_nome, lancamento_id, holerite_id),
    )


def inserir_item(con: sqlite3.Connection, holerite_id: int, *, ordem: int, codigo: str | None,
                 descricao: str, referencia: str | None, natureza: str, valor_centavos: int,
                 categoria_id: int | None) -> int:
    cur = con.execute(
        "INSERT INTO holerite_itens (uuid, holerite_id, ordem, codigo, descricao, referencia,"
        " natureza, valor_centavos, categoria_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), holerite_id, ordem, codigo, descricao, referencia, natureza,
         valor_centavos, categoria_id),
    )
    return int(cur.lastrowid)


def itens(con: sqlite3.Connection, holerite_id: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM holerite_itens WHERE holerite_id = ? ORDER BY ordem, id", (holerite_id,)
    ).fetchall()


def excluir_itens(con: sqlite3.Connection, holerite_id: int) -> None:
    con.execute("DELETE FROM holerite_itens WHERE holerite_id = ?", (holerite_id,))


def atualizar_categoria_item(con: sqlite3.Connection, item_id: int,
                             categoria_id: int | None) -> None:
    con.execute("UPDATE holerite_itens SET categoria_id = ? WHERE id = ?",
                (categoria_id, item_id))


def obter_item(con: sqlite3.Connection, item_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM holerite_itens WHERE id = ?", (item_id,)).fetchone()


def _escapar_like(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def total_por_descricao(con: sqlite3.Connection, ano: int, trecho: str) -> int:
    """Soma (centavos) dos descontos do ano cuja descrição, sem acento e em minúsculas,
    contém `trecho` (já normalizado pelo chamador)."""
    row = con.execute(
        "SELECT COALESCE(SUM(i.valor_centavos), 0) FROM holerite_itens i"
        " JOIN holerites h ON h.id = i.holerite_id"
        " WHERE i.natureza = 'desconto' AND substr(h.competencia, 1, 4) = ?"
        " AND sem_acento(i.descricao) LIKE ? ESCAPE '\\'",
        (f"{ano:04d}", f"%{_escapar_like(trecho.lower())}%"),
    ).fetchone()
    return int(row[0])


def definir_lancamento(con: sqlite3.Connection, holerite_id: int,
                       lancamento_id: int | None) -> None:
    con.execute("UPDATE holerites SET lancamento_id = ? WHERE id = ?",
                (lancamento_id, holerite_id))


def por_lancamento(con: sqlite3.Connection, lancamento_id: int) -> sqlite3.Row | None:
    return con.execute("SELECT * FROM holerites WHERE lancamento_id = ?",
                       (lancamento_id,)).fetchone()


def lancamentos_vinculados(con: sqlite3.Connection) -> set[int]:
    """Ids dos lançamentos ligados a algum holerite."""
    return {int(r[0]) for r in con.execute(
        "SELECT lancamento_id FROM holerites WHERE lancamento_id IS NOT NULL")}


def vinculados_a_linhas_da_importacao(con: sqlite3.Connection,
                                      importacao_id: int) -> list[sqlite3.Row]:
    """Holerites ligados a linhas importadas (origem 'importado') da importação."""
    return con.execute(
        "SELECT h.* FROM holerites h JOIN lancamentos l ON l.id = h.lancamento_id"
        " WHERE l.importacao_id = ? AND l.origem = 'importado' ORDER BY h.id",
        (importacao_id,),
    ).fetchall()
