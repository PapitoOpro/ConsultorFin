"""Regras de lançamentos: chave de deduplicação e tipo inicial."""
import hashlib
from datetime import date
from typing import Any

from app.db import transacao
from app.repo import lancamentos as repo


def chave_deduplicacao(conta_uuid: str, data: str, descricao: str, valor_centavos: int,
                       ocorrencia: int) -> str:
    base = "|".join((conta_uuid, data, descricao, str(valor_centavos), str(ocorrencia)))
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def tipo_inicial(valor_centavos: int) -> str:
    return "entrada" if valor_centavos > 0 else "saida"


def criar_lancamento_manual(
    con: Any, *, conta_id: int, data: date, descricao: str, valor_centavos: int,
    categoria_id: int | None = None, compensado: bool = False,
    observacao: str | None = None,
) -> int:
    """Cria lançamento manual (entrada se valor > 0, saída se < 0). Se compensado, usa `data`."""
    if not descricao.strip():
        raise ValueError("A descrição não pode ser vazia.")
    if valor_centavos == 0:
        raise ValueError("O valor não pode ser zero.")
    with transacao(con):
        return repo.inserir_manual(
            con, conta_id=conta_id, data=data.isoformat(), descricao=descricao.strip(),
            valor_centavos=valor_centavos, tipo=tipo_inicial(valor_centavos),
            categoria_id=categoria_id, compensado=compensado, observacao=observacao)
