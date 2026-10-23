"""Regras de lançamentos: chave de deduplicação e tipo inicial."""
import hashlib


def chave_deduplicacao(conta_uuid: str, data: str, descricao: str, valor_centavos: int,
                       ocorrencia: int) -> str:
    base = "|".join((conta_uuid, data, descricao, str(valor_centavos), str(ocorrencia)))
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def tipo_inicial(valor_centavos: int) -> str:
    return "entrada" if valor_centavos > 0 else "saida"
