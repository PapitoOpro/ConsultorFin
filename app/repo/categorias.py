"""Acesso a dados de categorias, regras e totais mensais (sem lógica de negócio)."""
import sqlite3
import uuid
from typing import Any

from app.db import chave_nome


def criar(con: sqlite3.Connection, nome: str, pai_id: int | None = None,
          tipo: str = "saida") -> int:
    """Insere categoria (sem commit); nome_chave sustenta a unicidade entre irmãs."""
    cur = con.execute(
        "INSERT INTO categorias (uuid, nome, pai_id, tipo, nome_chave) VALUES (?,?,?,?,?)",
        (str(uuid.uuid4()), nome, pai_id, tipo, chave_nome(nome)),
    )
    return int(cur.lastrowid or 0)


def obter(con: sqlite3.Connection, categoria_id: int) -> sqlite3.Row | None:
    row: sqlite3.Row | None = con.execute(
        "SELECT id, nome, pai_id, tipo FROM categorias WHERE id = ?", (categoria_id,)
    ).fetchone()
    return row


def nome_existe(con: sqlite3.Connection, nome: str, pai_id: int | None,
                ignorar_id: int | None = None) -> bool:
    """Há irmã com o mesmo nome (sem caixa, acento e espaços extras)?"""
    return con.execute(
        "SELECT 1 FROM categorias WHERE COALESCE(pai_id, 0) = ? AND nome_chave = ? AND id != ?",
        (pai_id or 0, chave_nome(nome), ignorar_id or 0),
    ).fetchone() is not None


def uso(con: sqlite3.Connection, categoria_id: int) -> dict[str, int]:
    row = con.execute(
        "SELECT (SELECT COUNT(*) FROM lancamentos WHERE categoria_id = :c) AS lancamentos,"
        " (SELECT COUNT(*) FROM recorrentes WHERE categoria_id = :c) AS recorrentes,"
        " (SELECT COUNT(*) FROM regras_categoria WHERE categoria_id = :c) AS regras,"
        " (SELECT COUNT(*) FROM fatura_itens WHERE categoria_id = :c) AS itens_fatura",
        {"c": categoria_id},
    ).fetchone()
    return {"lancamentos": int(row["lancamentos"]), "recorrentes": int(row["recorrentes"]),
            "regras": int(row["regras"]), "itens_fatura": int(row["itens_fatura"])}


def arvore(con: sqlite3.Connection) -> list[dict[str, Any]]:
    """Raízes (com 'filhas') e contagem de uso por nó; ordem por nome (sem acento)."""
    linhas = con.execute(
        "SELECT id, nome, pai_id, tipo FROM categorias ORDER BY sem_acento(nome), id"
    ).fetchall()
    nos: dict[int, dict[str, Any]] = {
        int(r["id"]): {"id": int(r["id"]), "nome": str(r["nome"]), "pai_id": r["pai_id"],
                       "tipo": r["tipo"], "uso": uso(con, int(r["id"])), "filhas": []}
        for r in linhas
    }
    raizes: list[dict[str, Any]] = []
    for no in nos.values():
        if no["pai_id"] is None:
            raizes.append(no)
        else:
            nos[int(no["pai_id"])]["filhas"].append(no)
    return raizes


def todas(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Todas as categorias, ordenadas por nome sem acento (para montar seletores)."""
    return con.execute(
        "SELECT id, nome, pai_id, tipo FROM categorias ORDER BY sem_acento(nome), id"
    ).fetchall()


def atualizar_tipo(con: sqlite3.Connection, categoria_id: int, tipo: str) -> None:
    """Muda o tipo da categoria e de suas filhas (sem commit)."""
    con.execute("UPDATE categorias SET tipo = ? WHERE id = ? OR pai_id = ?",
                (tipo, categoria_id, categoria_id))


def mover(con: sqlite3.Connection, categoria_id: int, novo_pai_id: int) -> None:
    con.execute("UPDATE categorias SET pai_id = ? WHERE id = ?", (novo_pai_id, categoria_id))


def reapontar(con: sqlite3.Connection, de_id: int, para_id: int) -> dict[str, int]:
    """Aponta lançamentos, recorrentes, regras e itens de fatura de uma categoria para outra
    (sem commit)."""
    return {
        "lancamentos": con.execute(
            "UPDATE lancamentos SET categoria_id = ? WHERE categoria_id = ?", (para_id, de_id)
        ).rowcount,
        "recorrentes": con.execute(
            "UPDATE recorrentes SET categoria_id = ? WHERE categoria_id = ?", (para_id, de_id)
        ).rowcount,
        "regras": con.execute(
            "UPDATE regras_categoria SET categoria_id = ? WHERE categoria_id = ?", (para_id, de_id)
        ).rowcount,
        "itens_fatura": con.execute(
            "UPDATE fatura_itens SET categoria_id = ? WHERE categoria_id = ?", (para_id, de_id)
        ).rowcount,
    }


def limpar(con: sqlite3.Connection, categoria_id: int) -> dict[str, int]:
    """Zera a categoria em lançamentos/recorrentes/itens de fatura e apaga as regras dela
    (sem commit)."""
    return {
        "lancamentos": con.execute(
            "UPDATE lancamentos SET categoria_id = NULL WHERE categoria_id = ?", (categoria_id,)
        ).rowcount,
        "recorrentes": con.execute(
            "UPDATE recorrentes SET categoria_id = NULL WHERE categoria_id = ?", (categoria_id,)
        ).rowcount,
        "regras": con.execute(
            "DELETE FROM regras_categoria WHERE categoria_id = ?", (categoria_id,)
        ).rowcount,
        "itens_fatura": con.execute(
            "UPDATE fatura_itens SET categoria_id = NULL WHERE categoria_id = ?", (categoria_id,)
        ).rowcount,
    }


def listar_raiz(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT id, nome, pai_id, tipo FROM categorias WHERE pai_id IS NULL ORDER BY nome"
    ).fetchall()


def listar_filhas(con: sqlite3.Connection, pai_id: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT id, nome, pai_id, tipo FROM categorias WHERE pai_id = ? ORDER BY nome", (pai_id,)
    ).fetchall()


def existe(con: sqlite3.Connection, categoria_id: int) -> bool:
    return con.execute("SELECT 1 FROM categorias WHERE id = ?", (categoria_id,)).fetchone() is not None


def renomear(con: sqlite3.Connection, categoria_id: int, nome: str) -> bool:
    cur = con.execute("UPDATE categorias SET nome = ?, nome_chave = ? WHERE id = ?",
                      (nome, chave_nome(nome), categoria_id))
    return cur.rowcount > 0


def tem_filhas(con: sqlite3.Connection, categoria_id: int) -> bool:
    return con.execute(
        "SELECT 1 FROM categorias WHERE pai_id = ?", (categoria_id,)
    ).fetchone() is not None


def remover(con: sqlite3.Connection, categoria_id: int) -> bool:
    """Apaga só a linha da categoria (sem commit); o domínio trata o uso antes."""
    cur = con.execute("DELETE FROM categorias WHERE id = ?", (categoria_id,))
    return cur.rowcount > 0


def id_por_nome(con: sqlite3.Connection, nome: str) -> int | None:
    """Categoria cujo nome casa (sem caixa, acento e espaços extras); raízes primeiro."""
    row = con.execute(
        "SELECT id FROM categorias WHERE nome_chave = ? ORDER BY pai_id IS NOT NULL, id LIMIT 1",
        (chave_nome(nome),),
    ).fetchone()
    return None if row is None else int(row["id"])


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
    # A linha da fatura nunca tem categoria (quem tem são os itens).
    sql = "SELECT id, descricao FROM lancamentos WHERE categoria_id IS NULL AND tipo <> 'fatura'"
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
    """Soma (com sinal) dos compensados com data_compensacao em [inicio, fim): contas não-cartão
    e, nas contas-cartão, só a linha da fatura ('fatura' em `tipos`; conta na data do pagamento).
    Pagamento de fatura casado com uma fatura fica fora (a linha da fatura é que conta)."""
    marcas = ",".join("?" for _ in tipos)
    row = con.execute(
        "SELECT COALESCE(SUM(l.valor_centavos), 0) AS s FROM lancamentos l"
        " JOIN contas c ON c.id = l.conta_id"
        " WHERE l.compensado = 1 AND (c.tipo != 'cartao' OR l.tipo = 'fatura')"
        f" AND l.tipo IN ({marcas})"
        " AND l.data_compensacao >= ? AND l.data_compensacao < ?"
        " AND NOT EXISTS (SELECT 1 FROM faturas f WHERE f.pagamento_lancamento_id = l.id)",
        (*tipos, inicio_iso, fim_iso),
    ).fetchone()
    return int(row["s"])


def total_itens_fatura_do_mes(con: sqlite3.Connection, inicio_iso: str, fim_iso: str) -> int:
    """Soma (com sinal: compra negativa, crédito positivo) dos itens de fatura com data da
    compra em [inicio, fim)."""
    row = con.execute(
        "SELECT COALESCE(SUM(valor_centavos), 0) AS s FROM fatura_itens"
        " WHERE data >= ? AND data < ?",
        (inicio_iso, fim_iso),
    ).fetchone()
    return int(row["s"])
