"""Normalização de descrições para a chave de deduplicação (independente do formato)."""
import re
import unicodedata

_NAO_ALFANUMERICO = re.compile(r"[\W_]+")


def normalizar_descricao(texto: str) -> str:
    """Sem acentos, minúsculas; todo trecho que não é letra/dígito vira um único espaço."""
    base = unicodedata.normalize("NFKD", texto.casefold())
    sem_marcas = "".join(c for c in base if not unicodedata.combining(c))
    return _NAO_ALFANUMERICO.sub(" ", sem_marcas).strip()
