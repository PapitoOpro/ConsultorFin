"""Normalização de descrições para a chave de deduplicação (independente do formato)."""
import hashlib
import re
import unicodedata

_NAO_ALFANUMERICO = re.compile(r"[\W_]+")


def normalizar_descricao(texto: str) -> str:
    """Sem acentos, minúsculas; todo trecho que não é letra/dígito vira um único espaço."""
    base = unicodedata.normalize("NFKD", texto.casefold())
    sem_marcas = "".join(c for c in base if not unicodedata.combining(c))
    return _NAO_ALFANUMERICO.sub(" ", sem_marcas).strip()


def so_letras(texto: str) -> str:
    """Só as letras (sem acentos, minúsculas): absorve acento quebrado e datas coladas,
    que mudam de um formato de extrato (CSV, OFX) para outro."""
    return re.sub(r"[^a-z]", "", normalizar_descricao(texto))


def hash_chave_v2(conta_uuid: str, data: str, descricao_normalizada: str, valor_centavos: int,
                  ocorrencia: int) -> str:
    """Fórmula única da chave v2 (prefixo "v2|" separa o espaço de chaves do da legada)."""
    base = "|".join(("v2", conta_uuid, data, descricao_normalizada, str(valor_centavos),
                     str(ocorrencia)))
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def hash_chave_legada(conta_uuid: str, data: str, descricao: str, valor_centavos: int,
                      ocorrencia: int) -> str:
    """Chave anterior à v7 (descrição literal, sem prefixo). Nunca alterar."""
    base = "|".join((conta_uuid, data, descricao, str(valor_centavos), str(ocorrencia)))
    return hashlib.sha256(base.encode("utf-8")).hexdigest()
