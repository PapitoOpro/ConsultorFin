"""Acesso ao SQLite e migrações versionadas por PRAGMA user_version."""
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from app import config

CATEGORIAS_INICIAIS: tuple[str, ...] = (
    "Moradia", "Mercado", "Transporte", "Saúde", "Lazer", "Assinaturas", "Educação", "Outros",
)

_SCHEMA_V1 = """
CREATE TABLE contas (
  id INTEGER PRIMARY KEY,
  uuid TEXT UNIQUE NOT NULL,
  nome TEXT NOT NULL,
  instituicao TEXT,
  tipo TEXT NOT NULL,
  final_numero TEXT,
  saldo_inicial_centavos INTEGER NOT NULL DEFAULT 0,
  data_saldo_inicial TEXT
);
CREATE TABLE categorias (
  id INTEGER PRIMARY KEY,
  uuid TEXT UNIQUE NOT NULL,
  nome TEXT NOT NULL,
  pai_id INTEGER REFERENCES categorias(id)
);
CREATE TABLE importacoes (
  id INTEGER PRIMARY KEY,
  arquivo_nome TEXT NOT NULL,
  arquivo_hash TEXT NOT NULL,
  conta_id INTEGER REFERENCES contas(id),
  periodo_inicio TEXT, periodo_fim TEXT,
  total_lidos INTEGER, total_novos INTEGER, total_ignorados INTEGER,
  criado_em TEXT NOT NULL,
  desfeita INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE recorrentes (
  id INTEGER PRIMARY KEY,
  uuid TEXT UNIQUE NOT NULL,
  nome TEXT NOT NULL,
  tipo TEXT NOT NULL CHECK (tipo IN ('entrada','saida')),
  dia INTEGER NOT NULL,
  valor_estimado_centavos INTEGER NOT NULL,
  categoria_id INTEGER REFERENCES categorias(id),
  conta_id INTEGER REFERENCES contas(id),
  ativa INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE lancamentos (
  id INTEGER PRIMARY KEY,
  uuid TEXT UNIQUE NOT NULL,
  conta_id INTEGER NOT NULL REFERENCES contas(id),
  data TEXT NOT NULL,
  descricao TEXT NOT NULL,
  valor_centavos INTEGER NOT NULL,
  tipo TEXT NOT NULL,
  categoria_id INTEGER REFERENCES categorias(id),
  chave_deduplicacao TEXT UNIQUE NOT NULL,
  origem TEXT NOT NULL,
  importacao_id INTEGER REFERENCES importacoes(id),
  observacao TEXT,
  data_compensacao TEXT,
  compensado INTEGER NOT NULL DEFAULT 1,
  estimado INTEGER NOT NULL DEFAULT 0,
  grupo_parcela_uuid TEXT,
  parcela_atual INTEGER,
  parcela_total INTEGER,
  recorrente_id INTEGER REFERENCES recorrentes(id)
);
CREATE TABLE regras_categoria (
  id INTEGER PRIMARY KEY,
  padrao TEXT NOT NULL,
  categoria_id INTEGER NOT NULL REFERENCES categorias(id),
  prioridade INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE chaves_quitadas (
  chave TEXT PRIMARY KEY,
  pendente_id INTEGER,
  importacao_id INTEGER
);
CREATE TABLE configuracoes (
  chave TEXT PRIMARY KEY,
  valor TEXT
);
"""


def _migracao_1(con: sqlite3.Connection) -> None:
    for sql in filter(str.strip, _SCHEMA_V1.split(";")):
        con.execute(sql)
    con.executemany(
        "INSERT INTO categorias (uuid, nome) VALUES (?, ?)",
        [(str(uuid.uuid4()), nome) for nome in CATEGORIAS_INICIAIS],
    )


# Lista numerada: MIGRACOES[i] leva o banco da versão i para i+1. Só acrescentar ao fim.
MIGRACOES: list[Callable[[sqlite3.Connection], None]] = [_migracao_1]


def conectar(caminho: Path | str | None = None) -> sqlite3.Connection:
    destino = config.DB_PATH if caminho is None else caminho
    if destino != ":memory:":
        Path(destino).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(destino))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def migrar(con: sqlite3.Connection) -> None:
    """Aplica migrações pendentes, cada uma atomicamente; idempotente."""
    versao = con.execute("PRAGMA user_version").fetchone()[0]
    for numero in range(versao, len(MIGRACOES)):
        try:
            con.execute("BEGIN")
            MIGRACOES[numero](con)
            con.execute(f"PRAGMA user_version = {numero + 1}")
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise


@contextmanager
def transacao(con: sqlite3.Connection) -> Iterator[None]:
    """BEGIN; COMMIT em sucesso; ROLLBACK e re-raise em exceção."""
    con.execute("BEGIN")
    try:
        yield
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")
