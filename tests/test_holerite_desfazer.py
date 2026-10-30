"""Desfazer importação de extrato com crédito vinculado a holerite (dados inventados)."""
import sqlite3
from datetime import date

from app.dominio import holerites as dom
from app.dominio.categorias import criar_categoria
from app.importacao.base import LinhaImportada, ResultadoLeitura
from app.importacao.holerite.modelo import HoleriteLido, ItemHolerite
from app.importacao.servico import desfazer_importacao, importar
from app.repo import contas as repo_contas
from app.repo import holerites as repo

HOJE = date(2026, 10, 30)


class _Parser:
    def __init__(self, linhas: list[LinhaImportada]) -> None:
        self.linhas = linhas

    def ler(self, conteudo: bytes) -> ResultadoLeitura:
        return ResultadoLeitura(linhas=list(self.linhas), saldos_dia={})


def _h() -> HoleriteLido:
    return HoleriteLido("mensal", "2026-09", "ACME", 500000, 120000, 380000,
                        [ItemHolerite("111", "SALARIO FICTICIO", None, "provento", 500000),
                         ItemHolerite("211", "IMPOSTO TESTE", None, "desconto", 120000)])


def _extrato(con: sqlite3.Connection, conta: int) -> int:
    parser = _Parser([LinhaImportada("2026-10-02", "CREDITO SAL", 380000)])
    return importar(con, conta, "e.csv", b"e", parser, HOJE).importacao_id


def _importar_holerite(con: sqlite3.Connection, conta: int) -> dom.ResumoHolerite:
    p = dom.preparar_previa(con, _h(), "h1", conta, HOJE)
    assert len(p.creditos_candidatos) == 1
    return dom.importar_holerite(con, _h(), "h1", "a.pdf", conta, HOJE, [None, None],
                                 p.creditos_candidatos[0].lancamento_id)


def test_desfazer_libera_vinculo_e_recria_prevista(con: sqlite3.Connection) -> None:
    conta = repo_contas.criar_conta(con, "Corrente", "corrente")
    sal = criar_categoria(con, "Salário", "entrada")
    imp = _extrato(con, conta)
    r = _importar_holerite(con, conta)
    desfazer_importacao(con, imp, HOJE)
    hol = repo.obter(con, r.holerite_id)
    assert hol["lancamento_id"] != r.lancamento_id
    nova = con.execute("SELECT * FROM lancamentos WHERE id = ?", (hol["lancamento_id"],)
                       ).fetchone()
    assert (nova["origem"], nova["compensado"], nova["estimado"], nova["tipo"],
            nova["valor_centavos"], nova["data"], nova["conta_id"], nova["categoria_id"]) == (
        "holerite", 0, 1, "entrada", 380000, "2026-10-02", conta, sal)
    assert con.execute("SELECT 1 FROM lancamentos WHERE id = ?",
                       (r.lancamento_id,)).fetchone() is None
    # reimportar o mesmo extrato volta a oferecer o crédito para vincular
    _extrato(con, conta)
    assert len(dom.preparar_previa(con, _h(), "h2", conta, HOJE).creditos_candidatos) == 1


def test_desfazer_com_holerite_antigo_fica_sem_credito(con: sqlite3.Connection) -> None:
    conta = repo_contas.criar_conta(con, "Corrente", "corrente")
    imp = _extrato(con, conta)
    r = _importar_holerite(con, conta)
    desfazer_importacao(con, imp, date(2027, 3, 1))  # data prevista já bem passada
    assert repo.obter(con, r.holerite_id)["lancamento_id"] is None
    assert con.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0
    assert dom.listar_por_ano(con, 2026)[0].credito == "sem"


def test_desfazer_sem_holerite_vinculado_nao_cria_nada(con: sqlite3.Connection) -> None:
    conta = repo_contas.criar_conta(con, "Corrente", "corrente")
    imp = _extrato(con, conta)
    desfazer_importacao(con, imp, HOJE)
    assert con.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0
    assert dom.liberar_vinculos_da_importacao(con, imp, HOJE) == 0
