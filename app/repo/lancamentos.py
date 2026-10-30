"""Acesso a dados de lançamentos (sem commit: a transação é do chamador)."""
import sqlite3
import uuid

from app.db import sem_acento


def contar_parecidos(con: sqlite3.Connection, conta_id: int, data: str, valor_centavos: int,
                     letras: str, excluir_importacao: int | None = None) -> int:
    """Lançamentos importados da conta no mesmo dia e valor cuja descrição, só em letras, é `letras`."""
    from app.dominio.normalizacao import so_letras
    rows = con.execute(
        "SELECT descricao FROM lancamentos WHERE conta_id = ? AND data = ? AND valor_centavos = ?"
        " AND importacao_id IS NOT NULL AND (? IS NULL OR importacao_id <> ?)",
        (conta_id, data, valor_centavos, excluir_importacao, excluir_importacao)).fetchall()
    return sum(1 for r in rows if so_letras(str(r["descricao"])) == letras)


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


# Conciliação (Ruling 2): importados + pendentes casados com o extrato (importacao_id definido).
_FILTRO_CONCILIACAO = "origem = 'importado' OR importacao_id IS NOT NULL"


def soma_conciliacao_ate(con: sqlite3.Connection, conta_id: int, data_iso: str) -> int:
    """Soma dos compensados que contam na conciliação com data_compensacao <= data_iso."""
    row = con.execute(
        "SELECT COALESCE(SUM(valor_centavos), 0) AS s FROM lancamentos"
        f" WHERE conta_id = ? AND compensado = 1 AND ({_FILTRO_CONCILIACAO})"
        " AND data_compensacao <= ?",
        (conta_id, data_iso),
    ).fetchone()
    return int(row["s"])


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


def candidatos_casamento_da_conta(con: sqlite3.Connection, conta_id: int) -> list[sqlite3.Row]:
    """Pendentes e "pagos à mão" (compensados, não importados e sem importação) da conta;
    nunca a linha da fatura (ela só é paga casando o pagamento do banco)."""
    return con.execute(
        "SELECT * FROM lancamentos WHERE conta_id = ? AND tipo <> 'fatura' AND (compensado = 0"
        " OR (compensado = 1 AND origem <> 'importado' AND importacao_id IS NULL)) ORDER BY id",
        (conta_id,),
    ).fetchall()


def importados_da_conta(
    con: sqlite3.Connection, conta_id: int, data_min: str | None = None,
    data_max: str | None = None,
) -> list[sqlite3.Row]:
    """Linhas importadas livres (ainda não casadas) da conta, de todas as importações; a
    vinculada a holerite também (o casamento reaponta o holerite). `data_min`/`data_max`
    (ISO, inclusivos) limitam a janela de datas."""
    return con.execute(
        "SELECT * FROM lancamentos WHERE conta_id = ? AND origem = 'importado'"
        " AND (? IS NULL OR data >= ?) AND (? IS NULL OR data <= ?) ORDER BY id",
        (conta_id, data_min, data_min, data_max, data_max),
    ).fetchall()


def importados_da_importacao(con: sqlite3.Connection, importacao_id: int) -> list[sqlite3.Row]:
    """Linhas importadas ainda existentes (as casadas já foram removidas); a vinculada a
    holerite também (o casamento reaponta o holerite)."""
    return con.execute(
        "SELECT * FROM lancamentos WHERE importacao_id = ? AND origem = 'importado'"
        " ORDER BY id",
        (importacao_id,),
    ).fetchall()


def quitar_pendente(
    con: sqlite3.Connection, pendente_id: int, data_iso: str, valor_centavos: int,
    importacao_id: int | None,
) -> None:
    con.execute(
        "UPDATE lancamentos SET compensado = 1, data_compensacao = ?, estimado = 0,"
        " valor_centavos = ?, importacao_id = ? WHERE id = ?",
        (data_iso, valor_centavos, importacao_id, pendente_id),
    )


def excluir(con: sqlite3.Connection, lancamento_id: int) -> None:
    con.execute("DELETE FROM lancamentos WHERE id = ?", (lancamento_id,))


def registrar_chave_quitada(
    con: sqlite3.Connection, chave: str, pendente_id: int, importacao_id: int | None,
    valor_original_centavos: int | None = None, estimado_original: int | None = None,
    compensado_original: int | None = None, data_compensacao_original: str | None = None,
) -> None:
    con.execute(
        "INSERT INTO chaves_quitadas (chave, pendente_id, importacao_id,"
        " valor_original_centavos, estimado_original, compensado_original,"
        " data_compensacao_original) VALUES (?,?,?,?,?,?,?)",
        (chave, pendente_id, importacao_id, valor_original_centavos, estimado_original,
         compensado_original, data_compensacao_original),
    )


_ORIGINAL = ("(SELECT c.{col} FROM chaves_quitadas c"
             " WHERE c.pendente_id = lancamentos.id AND c.importacao_id = :imp)")


def reabrir_pendentes_quitados_da_importacao(con: sqlite3.Connection, importacao_id: int) -> int:
    """Desfaz os casamentos da importação, restaurando o estado guardado em chaves_quitadas:
    pago à mão (compensado_original = 1) volta a pago com a data original; os demais voltam
    a pendentes. valor/estimado NULL = sem original: mantém o atual."""
    pago = f"COALESCE({_ORIGINAL.format(col='compensado_original')}, 0) = 1"
    cur = con.execute(
        f"UPDATE lancamentos SET compensado = CASE WHEN {pago} THEN 1 ELSE 0 END,"
        f" data_compensacao = CASE WHEN {pago}"
        f"   THEN {_ORIGINAL.format(col='data_compensacao_original')} ELSE NULL END,"
        " importacao_id = NULL,"
        f" valor_centavos = COALESCE({_ORIGINAL.format(col='valor_original_centavos')},"
        "   valor_centavos),"
        f" estimado = COALESCE({_ORIGINAL.format(col='estimado_original')}, estimado)"
        " WHERE importacao_id = :imp AND origem <> 'importado'",
        {"imp": importacao_id},
    )
    return cur.rowcount


def apagar_chaves_quitadas_da_importacao(con: sqlite3.Connection, importacao_id: int) -> None:
    con.execute("DELETE FROM chaves_quitadas WHERE importacao_id = ?", (importacao_id,))


def saidas_pendentes_entre(
    con: sqlite3.Connection, inicio_iso: str | None, fim_iso: str | None
) -> list[sqlite3.Row]:
    """Saídas pendentes (valor < 0) com inicio <= data <= fim (limites None = abertos), por data."""
    return con.execute(
        "SELECT * FROM lancamentos WHERE compensado = 0 AND valor_centavos < 0"
        " AND (? IS NULL OR data >= ?) AND (? IS NULL OR data <= ?) ORDER BY data, id",
        (inicio_iso, inicio_iso, fim_iso, fim_iso),
    ).fetchall()


def pendentes_ate(con: sqlite3.Connection, fim_iso: str) -> list[sqlite3.Row]:
    """Todos os pendentes (entradas e saídas) com data <= fim_iso, por data."""
    return con.execute(
        "SELECT * FROM lancamentos WHERE compensado = 0 AND data <= ? ORDER BY data, id",
        (fim_iso,),
    ).fetchall()


# --- Extrato (tela Lançamentos) -------------------------------------------------------------

def _escapar_like(texto: str) -> str:
    return texto.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def _where(
    *, de: str | None, ate: str | None, conta_id: int | None, categoria_id: int | None,
    sem_categoria: bool, tipo: str | None, texto: str | None, compensado: bool | None,
) -> tuple[str, list[object]]:
    """WHERE montado só de fragmentos fixos; valores sempre como parâmetros."""
    # Parcelas previstas pela fatura ficam em Contas a pagar; aqui só as que não vieram na fatura.
    partes: list[str] = [
        "(l.origem <> 'fatura_prevista' OR EXISTS (SELECT 1 FROM faturas f"
        " WHERE f.conta_id = l.conta_id AND f.competencia >= substr(l.data, 1, 7)))"]
    params: list[object] = []
    if de is not None:
        partes.append("l.data >= ?")
        params.append(de)
    if ate is not None:
        partes.append("l.data <= ?")
        params.append(ate)
    if conta_id is not None:
        partes.append("l.conta_id = ?")
        params.append(conta_id)
    if sem_categoria:
        # A linha da fatura nunca tem categoria (os itens têm): não é "sem categoria".
        partes.append("l.categoria_id IS NULL AND l.tipo <> 'fatura'")
    elif categoria_id is not None:
        partes.append("(l.categoria_id = ? OR l.categoria_id IN"
                      " (SELECT id FROM categorias WHERE pai_id = ?))")
        params.extend((categoria_id, categoria_id))
    if tipo is not None:
        partes.append("l.tipo = ?")
        params.append(tipo)
    if texto:
        partes.append("sem_acento(l.descricao) LIKE ? ESCAPE '!'")
        params.append("%" + _escapar_like(sem_acento(texto)) + "%")
    if compensado is not None:
        partes.append("l.compensado = ?")
        params.append(int(compensado))
    return (" WHERE " + " AND ".join(partes)) if partes else "", params


_ACUMULADO = (
    "WITH acumulado AS (SELECT a.id AS id, c.saldo_inicial_centavos + SUM(a.valor_centavos)"
    " OVER (PARTITION BY a.conta_id ORDER BY a.data_compensacao, a.id) AS saldo"
    " FROM lancamentos a JOIN contas c ON c.id = a.conta_id WHERE a.compensado = 1) "
)


def listar_extrato(
    con: sqlite3.Connection, *, de: str | None, ate: str | None, conta_id: int | None,
    categoria_id: int | None, sem_categoria: bool, tipo: str | None, texto: str | None,
    compensado: bool | None, limite: int, deslocamento: int, decrescente: bool = False,
) -> list[sqlite3.Row]:
    """Linhas do extrato por data/id, com conta, categoria e saldo acumulado da conta
    (saldo inicial + compensados até a linha; NULL nos pendentes). fatura_casada_id: fatura
    paga por esta linha (pagamento_fatura casado), senão NULL. fatura_id/fatura_pagamento_id/
    fatura_qtd_itens: só na linha da fatura (tipo 'fatura')."""
    where, params = _where(de=de, ate=ate, conta_id=conta_id, categoria_id=categoria_id,
                           sem_categoria=sem_categoria, tipo=tipo, texto=texto,
                           compensado=compensado)
    return con.execute(
        _ACUMULADO
        + "SELECT l.*, c.nome AS conta_nome, cat.nome AS categoria_nome, ac.saldo AS saldo_acumulado,"
        " (SELECT f.id FROM faturas f WHERE f.pagamento_lancamento_id = l.id) AS fatura_casada_id,"
        " fl.id AS fatura_id, fl.pagamento_lancamento_id AS fatura_pagamento_id,"
        " (SELECT COUNT(*) FROM fatura_itens i WHERE i.fatura_id = fl.id) AS fatura_qtd_itens"
        " FROM lancamentos l JOIN contas c ON c.id = l.conta_id"
        " LEFT JOIN categorias cat ON cat.id = l.categoria_id"
        " LEFT JOIN acumulado ac ON ac.id = l.id"
        " LEFT JOIN faturas fl ON fl.lancamento_id = l.id"
        + where + (" ORDER BY l.data DESC, l.id DESC" if decrescente else " ORDER BY l.data, l.id")
        + " LIMIT ? OFFSET ?",
        (*params, limite, deslocamento),
    ).fetchall()


# pagamento_fatura casado com uma fatura (a linha da fatura é que conta como saída).
_CASADO = "EXISTS (SELECT 1 FROM faturas f WHERE f.pagamento_lancamento_id = l.id)"


def totais_extrato(
    con: sqlite3.Connection, *, de: str | None, ate: str | None, conta_id: int | None,
    categoria_id: int | None, sem_categoria: bool, tipo: str | None, texto: str | None,
    compensado: bool | None, excluir_cartao: bool = False,
) -> sqlite3.Row:
    """Contagem e somas do filtro. Entradas/saídas: só compensados (linha da fatura e
    pagamento_fatura sem casamento contam como saída; o pagamento casado com uma fatura não,
    pois a fatura já conta; transferência fora). Pendentes: soma dos não compensados, exceto
    transferências. excluir_cartao: somas sem as linhas de contas 'cartao', exceto a linha da
    fatura (a contagem inclui todas as linhas)."""
    where, params = _where(de=de, ate=ate, conta_id=conta_id, categoria_id=categoria_id,
                           sem_categoria=sem_categoria, tipo=tipo, texto=texto,
                           compensado=compensado)
    cartao = " AND (c.tipo <> 'cartao' OR l.tipo = 'fatura')" if excluir_cartao else ""
    return con.execute(
        "SELECT COUNT(*) AS total,"
        " COALESCE(SUM(CASE WHEN l.compensado = 1 AND l.tipo = 'entrada'" + cartao
        + "   THEN l.valor_centavos END), 0) AS entradas,"
        " COALESCE(SUM(CASE WHEN l.compensado = 1"
        "   AND l.tipo IN ('saida', 'pagamento_fatura', 'fatura')" + cartao
        + f"   AND NOT {_CASADO} THEN l.valor_centavos END), 0) AS saidas,"
        " COALESCE(SUM(CASE WHEN l.compensado = 0 AND l.tipo NOT IN ('transferencia', 'ajuste')" + cartao
        + "   THEN l.valor_centavos END), 0) AS pendentes"
        " FROM lancamentos l JOIN contas c ON c.id = l.conta_id" + where,
        params,
    ).fetchone()


def atualizar_categoria(
    con: sqlite3.Connection, ids: list[int], categoria_id: int | None
) -> int:
    marcas = ",".join("?" for _ in ids)
    cur = con.execute(
        f"UPDATE lancamentos SET categoria_id = ? WHERE id IN ({marcas})",
        (categoria_id, *ids),
    )
    return cur.rowcount


def atualizar_tipo(con: sqlite3.Connection, lancamento_id: int, tipo: str) -> None:
    con.execute("UPDATE lancamentos SET tipo = ? WHERE id = ?", (tipo, lancamento_id))


def contar_faturas(con: sqlite3.Connection, ids: list[int]) -> int:
    """Quantos dos ids são linhas de fatura (tipo 'fatura')."""
    marcas = ",".join("?" for _ in ids)
    row = con.execute(
        f"SELECT COUNT(*) AS n FROM lancamentos WHERE tipo = 'fatura' AND id IN ({marcas})", ids
    ).fetchone()
    return int(row["n"])


def contar_existentes(con: sqlite3.Connection, ids: list[int]) -> int:
    marcas = ",".join("?" for _ in ids)
    row = con.execute(f"SELECT COUNT(*) AS n FROM lancamentos WHERE id IN ({marcas})", ids).fetchone()
    return int(row["n"])


def resumo_grupos_parcelas(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Uma linha por grupo de parcelas: contagens e totais (magnitudes positivas)."""
    return con.execute(
        "SELECT grupo_parcela_uuid AS grupo, MIN(descricao) AS descricao, COUNT(*) AS n,"
        " SUM(compensado) AS pagas, COUNT(*) - SUM(compensado) AS pendentes,"
        " -SUM(valor_centavos) AS total_centavos,"
        " -COALESCE(SUM(CASE WHEN compensado = 0 THEN valor_centavos END), 0) AS restante_centavos,"
        " MIN(CASE WHEN compensado = 0 THEN data END) AS proxima_data"
        " FROM lancamentos WHERE grupo_parcela_uuid IS NOT NULL"
        " GROUP BY grupo_parcela_uuid"
        " ORDER BY proxima_data IS NULL, proxima_data, descricao, grupo_parcela_uuid"
    ).fetchall()


def entradas_pendentes_ate(con: sqlite3.Connection, fim_exclusivo_iso: str) -> list[sqlite3.Row]:
    """Entradas pendentes (valor > 0) com data < fim, por data."""
    return con.execute(
        "SELECT * FROM lancamentos WHERE compensado = 0 AND valor_centavos > 0 AND data < ?"
        " ORDER BY data, id",
        (fim_exclusivo_iso,),
    ).fetchall()


# --- Fatura de cartão ------------------------------------------------------------------------

def inserir_fatura(con: sqlite3.Connection, *, conta_id: int, data: str, descricao: str,
                   valor_centavos: int) -> int:
    """Linha única da fatura: tipo/origem 'fatura', pendente, sem categoria. Chave fatura:<uuid>."""
    novo = str(uuid.uuid4())
    cur = con.execute(
        "INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
        " chave_deduplicacao, origem, compensado) VALUES (?,?,?,?,?,'fatura',?,'fatura',0)",
        (novo, conta_id, data, descricao, valor_centavos, f"fatura:{novo}"),
    )
    return int(cur.lastrowid)


def atualizar_fatura(con: sqlite3.Connection, lancamento_id: int, *, data: str,
                     valor_centavos: int) -> None:
    con.execute("UPDATE lancamentos SET data = ?, valor_centavos = ? WHERE id = ?",
                (data, valor_centavos, lancamento_id))


def inserir_prevista(con: sqlite3.Connection, *, conta_id: int, data: str, descricao: str,
                     valor_centavos: int, tipo: str, categoria_id: int | None,
                     grupo_parcela_uuid: str, parcela_atual: int, parcela_total: int) -> int:
    """Parcela futura prevista pela fatura: pendente, estimada, origem 'fatura_prevista'."""
    novo = str(uuid.uuid4())
    cur = con.execute(
        "INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
        " categoria_id, chave_deduplicacao, origem, compensado, estimado, grupo_parcela_uuid,"
        " parcela_atual, parcela_total) VALUES (?,?,?,?,?,?,?,?,'fatura_prevista',0,1,?,?,?)",
        (novo, conta_id, data, descricao, valor_centavos, tipo, categoria_id,
         f"fatura_prevista:{novo}", grupo_parcela_uuid, parcela_atual, parcela_total),
    )
    return int(cur.lastrowid)


def inserir_holerite_previsto(con: sqlite3.Connection, *, conta_id: int, data: str,
                              descricao: str, valor_centavos: int,
                              categoria_id: int | None) -> int:
    """Entrada prevista pelo holerite: pendente, estimada, origem 'holerite'."""
    novo = str(uuid.uuid4())
    cur = con.execute(
        "INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
        " categoria_id, chave_deduplicacao, origem, compensado, estimado)"
        " VALUES (?,?,?,?,?,'entrada',?,?,'holerite',0,1)",
        (novo, conta_id, data, descricao, valor_centavos, categoria_id, f"holerite:{novo}"),
    )
    return int(cur.lastrowid)


def entradas_candidatas(con: sqlite3.Connection, conta_id: int, valor_centavos: int,
                        de_iso: str, ate_iso: str) -> list[sqlite3.Row]:
    """Entradas compensadas da conta com o valor exato e data em [de, ate] ainda não
    vinculadas a um holerite, por data."""
    return con.execute(
        "SELECT * FROM lancamentos WHERE conta_id = ? AND tipo = 'entrada' AND compensado = 1"
        " AND valor_centavos = ? AND data >= ? AND data <= ?"
        " AND id NOT IN (SELECT lancamento_id FROM holerites WHERE lancamento_id IS NOT NULL)"
        " ORDER BY data, id",
        (conta_id, valor_centavos, de_iso, ate_iso),
    ).fetchall()


def entradas_pendentes_candidatas(con: sqlite3.Connection, conta_id: int, de_iso: str,
                                  ate_iso: str) -> list[sqlite3.Row]:
    """Entradas pendentes da conta (não previstas por holerite) com data em [de, ate], ainda
    não vinculadas a um holerite, por data (ex.: salário recorrente já cadastrado)."""
    return con.execute(
        "SELECT * FROM lancamentos WHERE conta_id = ? AND tipo = 'entrada' AND compensado = 0"
        " AND origem <> 'holerite' AND data >= ? AND data <= ?"
        " AND id NOT IN (SELECT lancamento_id FROM holerites WHERE lancamento_id IS NOT NULL)"
        " ORDER BY data, id",
        (conta_id, de_iso, ate_iso),
    ).fetchall()


def previstas_pendentes_da_conta(con: sqlite3.Connection, conta_id: int) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM lancamentos WHERE conta_id = ? AND origem = 'fatura_prevista'"
        " AND compensado = 0 ORDER BY data, id",
        (conta_id,),
    ).fetchall()


def parcela_existe_no_grupo(con: sqlite3.Connection, grupo_uuid: str, parcela: int,
                            excluir_fatura_id: int | None = None) -> bool:
    """A parcela já existe no grupo: como lançamento (prevista/real) ou como item de fatura
    (exceto os da fatura `excluir_fatura_id`)."""
    return con.execute(
        "SELECT 1 FROM lancamentos WHERE grupo_parcela_uuid = :g AND parcela_atual = :p"
        " UNION ALL SELECT 1 FROM fatura_itens WHERE grupo_parcela_uuid = :g"
        " AND parcela_atual = :p AND fatura_id <> COALESCE(:f, -1) LIMIT 1",
        {"g": grupo_uuid, "p": parcela, "f": excluir_fatura_id},
    ).fetchone() is not None


def excluir_previstas_dos_grupos(con: sqlite3.Connection, grupos: list[str]) -> int:
    """Apaga as previstas pendentes dos grupos (as reais e as pagas ficam)."""
    if not grupos:
        return 0
    marcas = ",".join("?" * len(grupos))
    cur = con.execute(
        "DELETE FROM lancamentos WHERE origem = 'fatura_prevista' AND compensado = 0"
        f" AND grupo_parcela_uuid IN ({marcas})",  # noqa: S608
        grupos,
    )
    return cur.rowcount


def pagamentos_fatura_livres(con: sqlite3.Connection, valor_centavos: int, data_min: str,
                             data_max: str, parcial: bool = False) -> list[sqlite3.Row]:
    """Pagamentos de fatura (ou saídas ainda não classificadas) compensados de contas
    não-cartão, com o valor exato, data em [data_min, data_max] e ainda não casados.
    parcial: aceita também valores menores (em módulo) que o total, ex.: pagamento parcial."""
    comparacao = "l.valor_centavos >= ? AND l.valor_centavos < 0" if parcial else "l.valor_centavos = ?"
    return con.execute(
        "SELECT l.* FROM lancamentos l JOIN contas c ON c.id = l.conta_id"
        " WHERE l.tipo IN ('pagamento_fatura', 'saida') AND l.compensado = 1 AND c.tipo <> 'cartao'"
        f" AND {comparacao} AND l.data >= ? AND l.data <= ?"
        " AND NOT EXISTS (SELECT 1 FROM faturas f WHERE f.pagamento_lancamento_id = l.id)"
        " ORDER BY l.data, l.id",
        (valor_centavos, data_min, data_max),
    ).fetchall()


def existe_pendente_equivalente(con: sqlite3.Connection, conta_id: int, valor_centavos: int,
                                data_min: str, data_max: str) -> bool:
    """Há lançamento pendente (manual ou recorrente) da conta com o mesmo valor na janela?"""
    return con.execute(
        "SELECT 1 FROM lancamentos WHERE conta_id = ? AND compensado = 0 AND valor_centavos = ?"
        " AND tipo <> 'fatura' AND origem <> 'fatura_prevista' AND data >= ? AND data <= ? LIMIT 1",
        (conta_id, valor_centavos, data_min, data_max),
    ).fetchone() is not None


def definir_compensacao_fatura(con: sqlite3.Connection, lancamento_id: int,
                               data_compensacao: str | None) -> int:
    """Linha da fatura paga (data) ou de volta a pendente (None); devolve linhas alteradas."""
    return con.execute(
        "UPDATE lancamentos SET compensado = ?, data_compensacao = ? WHERE id = ?"
        " AND tipo = 'fatura'",
        (int(data_compensacao is not None), data_compensacao, lancamento_id),
    ).rowcount


def previstas_orfas(con: sqlite3.Connection) -> list[sqlite3.Row]:
    """Previstas pendentes cujo mês já foi coberto por uma fatura importada da mesma conta
    (competência da fatura >= mês da prevista) sem que um item a tenha baixado."""
    return con.execute(
        "SELECT l.* FROM lancamentos l WHERE l.origem = 'fatura_prevista' AND l.compensado = 0"
        " AND EXISTS (SELECT 1 FROM faturas f WHERE f.conta_id = l.conta_id"
        "   AND f.competencia >= substr(l.data, 1, 7)) ORDER BY l.data, l.id"
    ).fetchall()


def reabrir_faturas_pagas_pela_importacao(con: sqlite3.Connection, importacao_id: int) -> int:
    """Linhas de fatura pagas por lançamentos da importação voltam a pendentes."""
    return con.execute(
        "UPDATE lancamentos SET compensado = 0, data_compensacao = NULL WHERE tipo = 'fatura'"
        " AND id IN (SELECT f.lancamento_id FROM faturas f JOIN lancamentos p"
        "   ON p.id = f.pagamento_lancamento_id WHERE p.importacao_id = ?)",
        (importacao_id,),
    ).rowcount
