"""SQL somente leitura para relatórios (fronteira de segurança).

O SQL vem de um LLM e pode ter sido influenciado por texto hostil de extratos.
Defesas em camadas: conexão mode=ro, authorizer default-deny, tabela
`senhas_pdf` ilegível, timeout por progress handler e limite de linhas.
Mensagens de erro nunca ecoam o SQL.
"""
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import config
from app.relatorio.espec import ErroRelatorio

_TABELAS_PROIBIDAS = frozenset({"senhas_pdf"})
_FUNCOES_PROIBIDAS = frozenset({"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"})
_SQLITE_RECURSIVE: int = getattr(sqlite3, "SQLITE_RECURSIVE", 33)
_PASSOS_PROGRESSO = 1000


@dataclass(frozen=True)
class Resultado:
    colunas: tuple[str, ...]
    linhas: tuple[tuple[Any, ...], ...]
    truncado: bool


def _recusar(motivo: str) -> ErroRelatorio:
    print(f"event=relatorio_sql_recusado motivo={motivo.replace(' ', '_')}", file=sys.stderr)
    return ErroRelatorio(f"Consulta recusada: {motivo}.")


def _autorizador(acao: int, arg1: str | None, arg2: str | None,
                 banco: str | None, origem: str | None) -> int:
    if acao == sqlite3.SQLITE_READ:
        if (arg1 or "").lower() in _TABELAS_PROIBIDAS:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    if acao == sqlite3.SQLITE_FUNCTION:
        return sqlite3.SQLITE_DENY if (arg2 or "").lower() in _FUNCOES_PROIBIDAS else sqlite3.SQLITE_OK
    if acao in (sqlite3.SQLITE_SELECT, _SQLITE_RECURSIVE):
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def executar_select(caminho_db: Path, sql: str,
                    limite: int = config.LIMITE_LINHAS_RELATORIO,
                    timeout_s: float = 5.0) -> Resultado:
    if not sql or not sql.strip():
        raise _recusar("consulta vazia")
    prazo = time.monotonic() + timeout_s
    try:
        con = sqlite3.connect(f"file:{caminho_db.as_posix()}?mode=ro", uri=True)
    except sqlite3.Error:
        raise _recusar("banco indisponível") from None
    try:
        con.set_authorizer(_autorizador)
        con.set_progress_handler(lambda: 1 if time.monotonic() > prazo else 0, _PASSOS_PROGRESSO)
        try:
            cur = con.execute(sql)
            colunas = tuple(d[0] for d in (cur.description or ()))
            if not colunas:
                raise _recusar("só é permitido SELECT")
            linhas = cur.fetchmany(limite + 1)
        except ErroRelatorio:
            raise
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e).lower():
                raise _recusar("tempo limite excedido") from None
            raise _recusar("SQL inválido ou não permitido") from None
        except (sqlite3.Warning, sqlite3.ProgrammingError):
            raise _recusar("apenas uma instrução SELECT é permitida") from None
        except sqlite3.Error:
            raise _recusar("SQL inválido ou não permitido") from None
        return Resultado(colunas, tuple(tuple(r) for r in linhas[:limite]), len(linhas) > limite)
    finally:
        con.close()
