"""Acesso ao SQLite e migrações versionadas por PRAGMA user_version."""
import sqlite3
import unicodedata
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


def _migracao_2(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE recorrentes_pulados ("
        " recorrente_id INTEGER NOT NULL REFERENCES recorrentes(id),"
        " mes TEXT NOT NULL,"
        " PRIMARY KEY (recorrente_id, mes))"
    )


def _migracao_3(con: sqlite3.Connection) -> None:
    con.execute("ALTER TABLE lancamentos ADD COLUMN competencia TEXT")
    con.execute(
        "UPDATE lancamentos SET competencia = substr(data, 1, 7) WHERE recorrente_id IS NOT NULL"
    )


# Lista numerada: MIGRACOES[i] leva o banco da versão i para i+1. Só acrescentar ao fim.
def _migracao_4(con: sqlite3.Connection) -> None:
    con.execute("ALTER TABLE chaves_quitadas ADD COLUMN valor_original_centavos INTEGER")
    con.execute("ALTER TABLE chaves_quitadas ADD COLUMN estimado_original INTEGER")


def _migracao_5(con: sqlite3.Connection) -> None:
    # Estado do lançamento antes do casamento (pago à mão volta a pago à mão no desfazer).
    con.execute("ALTER TABLE chaves_quitadas ADD COLUMN compensado_original INTEGER")
    con.execute("ALTER TABLE chaves_quitadas ADD COLUMN data_compensacao_original TEXT")


MIGRACOES: list[Callable[[sqlite3.Connection], None]] = [
    _migracao_1, _migracao_2, _migracao_3, _migracao_4, _migracao_5,
]


def sem_acento(texto: str | None) -> str:
    """Minúsculas e sem acentos (busca insensível a caixa e acento)."""
    base = unicodedata.normalize("NFKD", (texto or "").casefold())
    return "".join(c for c in base if not unicodedata.combining(c))


def conectar(caminho: Path | str | None = None) -> sqlite3.Connection:
    destino = config.DB_PATH if caminho is None else caminho
    if destino != ":memory:":
        Path(destino).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(destino), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.create_function("sem_acento", 1, sem_acento, deterministic=True)
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
    """BEGIN IMMEDIATE; COMMIT em sucesso; ROLLBACK e re-raise em exceção.

    IMMEDIATE pega o lock de escrita já no início: escritores concorrentes esperam o
    busy timeout em vez de falhar com "database is locked" ao promover leitura em escrita."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        if con.in_transaction:
            con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")
