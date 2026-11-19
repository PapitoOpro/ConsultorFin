"""Acesso a dados de investimentos (sem commit: a transação é do chamador)."""
import sqlite3
import uuid


def criar_importacao(con: sqlite3.Connection, *, arquivo_nome: str, conta_corretora: str,
                     periodo_inicio: str, periodo_fim: str, total_lidos: int, total_novos: int,
                     criado_em: str) -> int:
    cur = con.execute(
        "INSERT INTO inv_importacoes (arquivo_nome, conta_corretora, periodo_inicio, periodo_fim,"
        " total_lidos, total_novos, criado_em) VALUES (?,?,?,?,?,?,?)",
        (arquivo_nome, conta_corretora, periodo_inicio, periodo_fim, total_lidos, total_novos,
         criado_em))
    return int(cur.lastrowid)


def atualizar_total_novos(con: sqlite3.Connection, importacao_id: int, total_novos: int) -> None:
    con.execute("UPDATE inv_importacoes SET total_novos = ? WHERE id = ?",
                (total_novos, importacao_id))


def inserir_lancamento(con: sqlite3.Connection, *, conta_corretora: str, data: str,
                       data_liquidacao: str, descricao: str, valor_centavos: int,
                       saldo_centavos: int, categoria: str, ordem: int, chave: str,
                       importacao_id: int) -> bool:
    """True se inseriu; False se a chave já existia (reimportação)."""
    cur = con.execute(
        "INSERT OR IGNORE INTO inv_lancamentos (uuid, conta_corretora, data, data_liquidacao,"
        " descricao, valor_centavos, saldo_centavos, categoria, ordem, chave, importacao_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (str(uuid.uuid4()), conta_corretora, data, data_liquidacao, descricao, valor_centavos,
         saldo_centavos, categoria, ordem, chave, importacao_id))
    return cur.rowcount == 1


def todos(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Todos os lançamentos em ordem cronológica."""
    return con.execute("SELECT * FROM inv_lancamentos ORDER BY data, ordem, id").fetchall()


def listar(con: sqlite3.Connection, categoria: str | None = None, mes: str | None = None,
           limite: int = 500) -> list[sqlite3.Row]:
    """Mais recentes primeiro; `mes` no formato AAAA-MM."""
    onde: list[str] = []
    params: list[object] = []
    if categoria:
        onde.append("categoria = ?")
        params.append(categoria)
    if mes:
        onde.append("substr(data, 1, 7) = ?")
        params.append(mes)
    sql = "SELECT * FROM inv_lancamentos" + (" WHERE " + " AND ".join(onde) if onde else "")
    return con.execute(sql + " ORDER BY data DESC, ordem DESC, id DESC LIMIT ?",
                       (*params, limite)).fetchall()


def listar_importacoes(con: sqlite3.Connection, limite: int = 10) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM inv_importacoes ORDER BY id DESC LIMIT ?",
                       (limite,)).fetchall()
