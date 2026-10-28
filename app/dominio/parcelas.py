"""Parcelamento: divisão, criação, pagamento e edição/exclusão de parcelas pendentes."""
import uuid
from datetime import date
from typing import Any

from app.db import transacao
from app.dominio.dinheiro import somar_meses
from app.repo import lancamentos as repo

MSG_TOTAL_MENOR_QUE_PARCELAS = "O valor total deve ser de pelo menos R$ 0,01 por parcela."
MSG_FATURA_PAGAR = "Fatura de cartão: case o pagamento do banco com a fatura, em Lançamentos."
TIPO_FATURA = "fatura"  # gerado pela importação de fatura; fora de TIPOS (não editável)


def dividir_em_parcelas(total_centavos: int, n: int) -> list[int]:
    """Divide em n parcelas inteiras; a última absorve a diferença (10000/3 -> 3333,3333,3334)."""
    if n < 1:
        raise ValueError("O número de parcelas deve ser pelo menos 1.")
    if total_centavos <= 0:
        raise ValueError("O valor total deve ser maior que zero.")
    if total_centavos < n:
        raise ValueError(MSG_TOTAL_MENOR_QUE_PARCELAS)
    base = total_centavos // n
    return [base] * (n - 1) + [total_centavos - base * (n - 1)]


def _inserir_parcelas(
    con: Any, *, grupo: str, descricao: str, valores: list[int], primeiro_vencimento: date,
    dia: int, conta_id: int, categoria_id: int | None, offset: int, total_parcelas: int,
) -> list[int]:
    return [
        repo.inserir_manual(
            con, conta_id=conta_id, data=somar_meses(primeiro_vencimento, k, dia).isoformat(),
            descricao=descricao, valor_centavos=-v, tipo="saida", categoria_id=categoria_id,
            compensado=False, observacao=None, grupo_parcela_uuid=grupo,
            parcela_atual=offset + k + 1, parcela_total=total_parcelas)
        for k, v in enumerate(valores)
    ]


def criar_parcelamento(
    con: Any, *, descricao: str, valor_total_centavos: int, n_parcelas: int,
    primeiro_vencimento: date, conta_id: int, categoria_id: int | None,
) -> list[int]:
    """Cria n saídas pendentes (valor negativo) com vencimentos mensais no mesmo dia (clamp fim de mês)."""
    if not descricao.strip():
        raise ValueError("A descrição não pode ser vazia.")
    valores = dividir_em_parcelas(valor_total_centavos, n_parcelas)
    with transacao(con):
        return _inserir_parcelas(
            con, grupo=str(uuid.uuid4()), descricao=descricao.strip(), valores=valores,
            primeiro_vencimento=primeiro_vencimento, dia=primeiro_vencimento.day,
            conta_id=conta_id, categoria_id=categoria_id, offset=0, total_parcelas=n_parcelas)


def marcar_pago(
    con: Any, lancamento_id: int, valor_real_centavos: int | None, data_pagamento: date
) -> None:
    """Marca como compensado. `valor_real_centavos` é uma magnitude positiva; o sinal
    original do lançamento (saída negativa, entrada positiva) é preservado. None mantém o valor."""
    row = repo.obter(con, lancamento_id)
    if row is None:
        raise ValueError("Lançamento não encontrado.")
    if row["tipo"] == TIPO_FATURA:
        raise ValueError(MSG_FATURA_PAGAR)
    valor = int(row["valor_centavos"])
    if valor_real_centavos is not None:
        if valor_real_centavos <= 0:
            raise ValueError("O valor real deve ser maior que zero.")
        valor = valor_real_centavos if valor > 0 else -valor_real_centavos
    with transacao(con):
        repo.marcar_compensado(con, lancamento_id, data_pagamento.isoformat(), valor)


def excluir_grupo_parcelas(con: Any, grupo_uuid: str) -> None:
    """Remove só as parcelas pendentes do grupo; as pagas ficam intactas."""
    with transacao(con):
        repo.excluir_pendentes_do_grupo(con, grupo_uuid)


def editar_grupo_parcelas(
    con: Any, grupo_uuid: str, *, valor_total_centavos: int | None = None,
    n_parcelas: int | None = None,
) -> None:
    """Regera só as parcelas pendentes. Regra: `valor_total_centavos` é o novo total das
    PENDENTES (None = soma atual das pendentes); `n_parcelas` é a nova quantidade de pendentes
    (None = quantidade atual). A primeira pendente mantém seu vencimento; o dia do mês original
    do grupo é preservado; a numeração continua após as pagas e `parcela_total` do grupo todo
    vira pagas + n. Pagas não são alteradas (exceto parcela_total)."""
    todas = repo.parcelas_do_grupo(con, grupo_uuid)
    pendentes = [r for r in todas if not r["compensado"]]
    if not pendentes:
        raise ValueError("O grupo não tem parcelas pendentes para editar.")
    n = len(pendentes) if n_parcelas is None else n_parcelas
    total = (-sum(int(r["valor_centavos"]) for r in pendentes)
             if valor_total_centavos is None else valor_total_centavos)
    valores = dividir_em_parcelas(total, n)
    base = pendentes[0]
    pagas = len(todas) - len(pendentes)
    with transacao(con):
        repo.excluir_pendentes_do_grupo(con, grupo_uuid)
        _inserir_parcelas(
            con, grupo=grupo_uuid, descricao=base["descricao"], valores=valores,
            primeiro_vencimento=date.fromisoformat(base["data"]),
            dia=date.fromisoformat(todas[0]["data"]).day, conta_id=int(base["conta_id"]),
            categoria_id=base["categoria_id"], offset=pagas, total_parcelas=pagas + n)
        repo.atualizar_parcela_total_do_grupo(con, grupo_uuid, pagas + n)
