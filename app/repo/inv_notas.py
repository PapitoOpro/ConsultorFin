"""Acesso a dados das notas de negociação (inv_notas, inv_notas_negocios)."""
import sqlite3

from app.importacao.nota_negociacao import NotaLida


def existe(con: sqlite3.Connection, numero: str, data: str) -> bool:
    return con.execute("SELECT 1 FROM inv_notas WHERE numero = ? AND data = ?",
                       (numero, data)).fetchone() is not None


def inserir(con: sqlite3.Connection, nota: NotaLida, criado_em: str) -> int:
    cur = con.execute(
        "INSERT INTO inv_notas (numero, data, ajuste_day_trade_centavos, custos_centavos,"
        " irrf_centavos, liquido_centavos, criado_em, tipo, operacoes_centavos, data_liquidacao)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (nota.numero, nota.data, nota.ajuste_day_trade_centavos, nota.custos_centavos,
         nota.irrf_day_trade_centavos, nota.liquido_centavos, criado_em, nota.tipo,
         nota.operacoes_centavos, nota.data_liquidacao))
    nota_id = int(cur.lastrowid)
    con.executemany(
        "INSERT INTO inv_notas_negocios (nota_id, ordem, lado, mercadoria, vencimento, quantidade,"
        " preco_centavos, tipo, valor_centavos, taxa_centavos) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [(nota_id, i, n.lado, n.mercadoria, n.vencimento, n.quantidade, n.preco_centavos, n.tipo,
          n.valor_centavos, n.taxa_centavos) for i, n in enumerate(nota.negocios)])
    return nota_id


def listar(con: sqlite3.Connection, de: str | None = None, ate: str | None = None,
           limite: int = 100) -> list[sqlite3.Row]:
    sql = ("SELECT n.*, (SELECT COUNT(*) FROM inv_notas_negocios g WHERE g.nota_id = n.id)"
           " AS negocios FROM inv_notas n WHERE 1=1")
    params: list[object] = []
    if de:
        sql += " AND n.data >= ?"
        params.append(de)
    if ate:
        sql += " AND n.data <= ?"
        params.append(ate)
    sql += " ORDER BY n.data DESC, n.id DESC LIMIT ?"
    params.append(limite)
    return con.execute(sql, params).fetchall()
