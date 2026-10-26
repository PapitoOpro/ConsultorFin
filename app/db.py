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


def chave_nome(nome: str | None) -> str:
    """Chave de comparação de nomes: sem caixa, acento e espaços nas pontas/repetidos."""
    return " ".join(sem_acento(nome).split())


def _inserir_categoria(con: sqlite3.Connection, nome: str, pai_id: int | None, tipo: str) -> int:
    cur = con.execute(
        "INSERT INTO categorias (uuid, nome, pai_id, tipo, nome_chave) VALUES (?,?,?,?,?)",
        (str(uuid.uuid4()), nome, pai_id, tipo, chave_nome(nome)),
    )
    return int(cur.lastrowid or 0)


def _carregar_padrao(con: sqlite3.Connection) -> None:
    """Carrega a lista padrão; reaproveita (sem duplicar) o que já existe. Idempotente."""
    from app.dominio.taxonomia import PADRAO  # só dados

    raizes: dict[str, int] = {}
    for tipo, cat, sub in PADRAO:
        chave = chave_nome(cat)
        if chave not in raizes:
            row = con.execute(
                "SELECT id FROM categorias WHERE pai_id IS NULL AND nome_chave = ?", (chave,)
            ).fetchone()
            if row is None:
                raizes[chave] = _inserir_categoria(con, cat, None, tipo)
            else:
                raizes[chave] = int(row[0])
                con.execute("UPDATE categorias SET tipo = ? WHERE id = ?", (tipo, raizes[chave]))
        existe = con.execute(
            "SELECT 1 FROM categorias WHERE pai_id = ? AND nome_chave = ?",
            (raizes[chave], chave_nome(sub)),
        ).fetchone()
        if existe is None:
            _inserir_categoria(con, sub, raizes[chave], tipo)
    con.execute(
        "UPDATE categorias SET tipo = (SELECT p.tipo FROM categorias p WHERE p.id = categorias.pai_id)"
        " WHERE pai_id IS NOT NULL"
    )


def _migracao_6(con: sqlite3.Connection) -> None:
    """Categorias com tipo (entrada/saída), nomes únicos entre irmãos e lista padrão.

    Unicidade: coluna nome_chave (mantida pelo código, via chave_nome) + índice único comum em
    (COALESCE(pai_id, 0), nome_chave); evita depender de função Python registrada na conexão."""
    con.execute(
        "ALTER TABLE categorias ADD COLUMN tipo TEXT CHECK (tipo IN ('entrada','saida'))"
    )
    con.execute("ALTER TABLE categorias ADD COLUMN nome_chave TEXT")
    # Terceiro nível legado (sub sob sub) vira filha da raiz, mantendo o id e as referências.
    pais = {int(r["id"]): r["pai_id"] for r in con.execute("SELECT id, pai_id FROM categorias")}

    def raiz_de(i: int) -> int:
        while pais[i] is not None:
            i = int(pais[i])
        return i

    for i, pai in pais.items():
        if pai is not None and pais[int(pai)] is not None:
            con.execute("UPDATE categorias SET pai_id = ? WHERE id = ?", (raiz_de(i), i))
    linhas = con.execute(
        "SELECT id, nome, COALESCE(pai_id, 0) AS pai FROM categorias ORDER BY id"
    ).fetchall()
    # Nomes originais ficam reservados: renomear nunca colide com o nome literal de outra irmã.
    reservados = {(int(r["pai"]), chave_nome(str(r["nome"]))) for r in linhas}
    vistos: set[tuple[int, str]] = set()
    for r in linhas:
        pai = int(r["pai"])
        base = " ".join(str(r["nome"]).split())
        nome, chave = base, chave_nome(base)
        n = 2
        while (pai, chave) in vistos:  # duplicata legada entre irmãs: renomeia a posterior
            nome = f"{base} ({n})"
            chave = chave_nome(nome)
            while (pai, chave) in reservados:
                n += 1
                nome = f"{base} ({n})"
                chave = chave_nome(nome)
            n += 1
        vistos.add((pai, chave))
        con.execute("UPDATE categorias SET nome = ?, nome_chave = ? WHERE id = ?", (nome, chave, r["id"]))
    con.execute(
        "CREATE UNIQUE INDEX ux_categorias_irmas ON categorias (COALESCE(pai_id, 0), nome_chave)"
    )
    con.execute("UPDATE categorias SET tipo = 'saida' WHERE pai_id IS NULL")
    # Antigas categorias padrão sem uso (lançamentos, recorrentes, regras, filhas) são removidas;
    # as em uso (ou com o nome da lista nova, reaproveitadas na carga) permanecem.
    # Lê a lista viva de propósito: mudanças futuras na lista exigem uma nova migração.
    from app.dominio.taxonomia import PADRAO

    novas = {chave_nome(c) for _, c, _ in PADRAO}
    for nome in CATEGORIAS_INICIAIS:
        if chave_nome(nome) in novas:
            continue
        con.execute(
            "DELETE FROM categorias WHERE pai_id IS NULL AND nome_chave = ?"
            " AND NOT EXISTS (SELECT 1 FROM lancamentos WHERE categoria_id = categorias.id)"
            " AND NOT EXISTS (SELECT 1 FROM recorrentes WHERE categoria_id = categorias.id)"
            " AND NOT EXISTS (SELECT 1 FROM regras_categoria WHERE categoria_id = categorias.id)"
            " AND NOT EXISTS (SELECT 1 FROM categorias f WHERE f.pai_id = categorias.id)",
            (chave_nome(nome),),
        )
    _carregar_padrao(con)


def _migracao_7(con: sqlite3.Connection) -> None:
    """Chave de deduplicação independente do formato (CSV x OFX).

    Recalcula `chave_deduplicacao` das linhas importadas: sha256(conta_uuid | data |
    descricao_normalizada | valor | ocorrencia), ocorrencia = posição por id dentro de
    (conta, data, descricao_normalizada, valor). Não toca `manual:` nem `chaves_quitadas`.
    Tudo em memória, com checagem de unicidade antes de escrever; idempotente."""
    import hashlib

    from app.dominio.normalizacao import normalizar_descricao

    linhas = con.execute(
        "SELECT l.id, l.data, l.descricao, l.valor_centavos, l.chave_deduplicacao, c.uuid AS cu"
        " FROM lancamentos l JOIN contas c ON c.id = l.conta_id"
        " WHERE l.origem = 'importado' ORDER BY l.id"
    ).fetchall()
    ocorrencias: dict[tuple[str, str, str, int], int] = {}
    novas: dict[int, str] = {}
    for r in linhas:
        grupo = (str(r["cu"]), str(r["data"]), normalizar_descricao(str(r["descricao"])),
                 int(r["valor_centavos"]))
        ocorrencias[grupo] = ocorrencias.get(grupo, 0) + 1
        base = "|".join((grupo[0], grupo[1], grupo[2], str(grupo[3]), str(ocorrencias[grupo])))
        novas[int(r["id"])] = hashlib.sha256(base.encode("utf-8")).hexdigest()
    if len(set(novas.values())) != len(novas):
        raise RuntimeError("Migração v7: chaves novas repetidas; nada foi alterado.")
    ocupadas = {str(r[0]) for r in con.execute(
        "SELECT chave_deduplicacao FROM lancamentos WHERE origem <> 'importado'")}
    if ocupadas & set(novas.values()):
        raise RuntimeError("Migração v7: chave nova já usada por outra linha; nada foi alterado.")
    antigas = {int(r["id"]): str(r["chave_deduplicacao"]) for r in linhas}
    mudam = [(i, k) for i, k in novas.items() if k != antigas[i]]
    # Duas fases: uma chave nova pode ser a antiga de outra linha que também muda.
    con.executemany("UPDATE lancamentos SET chave_deduplicacao = ? WHERE id = ?",
                    [(f"v7-tmp:{i}", i) for i, _ in mudam])
    con.executemany("UPDATE lancamentos SET chave_deduplicacao = ? WHERE id = ?",
                    [(k, i) for i, k in mudam])


MIGRACOES: list[Callable[[sqlite3.Connection], None]] = [
    _migracao_1, _migracao_2, _migracao_3, _migracao_4, _migracao_5, _migracao_6,
    _migracao_7,
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
