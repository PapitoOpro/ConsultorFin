"""Dependências FastAPI."""
from collections.abc import Iterator
from typing import Any

from fastapi import Request

from app.db import conectar


def get_con(request: Request) -> Iterator[Any]:
    """Conexão por requisição: COMMIT no sucesso, ROLLBACK em exceção, sempre fecha."""
    con = conectar(request.app.state.caminho_db)
    try:
        yield con
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()
