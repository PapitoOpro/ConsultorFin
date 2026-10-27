"""Integração da fatura com as demais telas/fluxos (dados inventados)."""
import json
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.consultor import cli
from app.db import conectar, migrar
from app.dominio import contas_pagar_acoes as acoes
from app.dominio import faturas as dom
from app.dominio import faturas_pagamento as pag
from app.dominio import lancamentos as dom_lanc
from app.dominio.casamento import confirmar_casamento
from app.dominio.categorias import despesas_do_mes
from app.dominio.parcelas import marcar_pago
from app.importacao.base import LinhaImportada, ResultadoLeitura
from app.importacao.fatura.modelo import FaturaLida, ItemFatura
from app.importacao.servico import desfazer_importacao, importar
from app.main import create_app
from app.repo import contas as repo_contas
from app.repo import faturas as repo_faturas
from app.repo import lancamentos as repo_lanc

HOJE = date(2026, 10, 27)
MSG_FATURA = "Fatura de cartão: case o pagamento do banco com a fatura, em Lançamentos."


def _fatura_lida(venc: str = "2026-10-10", valor: int = -30000,
                 itens: list[ItemFatura] | None = None) -> FaturaLida:
    itens = itens or [ItemFatura(data="2026-09-15", descricao="LOJA INVENTADA",
                                 valor_centavos=valor, tipo="compra", parcela_atual=None,
                                 parcela_total=None, final_cartao="9999", categoria_banco=None)]
    f = FaturaLida(emissor="Banco Teste", bandeira="Visa", produto="Gold", final_cartao="9999",
                   data_vencimento=venc, data_fechamento=None, total_impresso_centavos=0,
                   ajustes_resumo_centavos=0, itens=itens)
    f.total_impresso_centavos = f.calculado_centavos
    return f


def _setup(con: sqlite3.Connection, categorias: list[int | None] | None = None
           ) -> tuple[int, int, dom.ResumoFatura]:
    cc = repo_contas.criar_conta(con, "Corrente Teste", "corrente")
    cartao = repo_contas.criar_conta(con, "Cartao Teste", "cartao", final_numero="9999")
    r = dom.importar_fatura(con, _fatura_lida(), "h1", "f.pdf", cartao, HOJE,
                            categorias or [None])
    return cc, cartao, r


def _pagto(con: sqlite3.Connection, conta: int, data: str = "2026-10-09",
           valor: int = -30000) -> int:
    lid = repo_lanc.inserir_manual(con, conta_id=conta, data=data, descricao="PAG FATURA X",
                                   valor_centavos=valor, tipo="pagamento_fatura",
                                   categoria_id=None, compensado=True, observacao=None)
    con.commit()
    return lid


def _linha(con: sqlite3.Connection, lid: int) -> tuple[Any, ...]:
    row = repo_lanc.obter(con, lid)
    return (row["compensado"], row["data_compensacao"], row["valor_centavos"])


# --- 1. a linha da fatura nunca é "marcada como paga" à mão ----------------------------------

def test_pagar_linha_da_fatura_recusado_em_todos_os_caminhos(con: sqlite3.Connection) -> None:
    _, _, r = _setup(con)
    antes = _linha(con, r.lancamento_id)
    with pytest.raises(dom_lanc.ErroLancamento, match="case o pagamento do banco"):
        acoes.pagar(con, str(r.lancamento_id), "100,00", "2026-10-10", HOJE)
    with pytest.raises(dom_lanc.ErroLancamento, match="case o pagamento do banco"):
        dom_lanc.pagar_pendente(con, r.lancamento_id, 10000, date(2026, 10, 10), hoje=HOJE)
    with pytest.raises(ValueError, match="case o pagamento do banco"):
        marcar_pago(con, r.lancamento_id, None, date(2026, 10, 10))
    assert _linha(con, r.lancamento_id) == antes == (0, None, -30000)


def test_casamento_generico_nao_quita_linha_da_fatura(con: sqlite3.Connection) -> None:
    _, cartao, r = _setup(con)
    imp = con.execute("INSERT INTO importacoes (arquivo_nome, arquivo_hash, conta_id, criado_em)"
                      " VALUES ('x','y',?,'2026-10-11')", (cartao,)).lastrowid
    linha = repo_lanc.inserir_importado(con, cartao, int(imp), "2026-10-10", "PAGTO", -30000,
                                        "saida", "k-cartao")
    con.commit()
    with pytest.raises(ValueError, match="case o pagamento do banco"):
        confirmar_casamento(con, r.lancamento_id, linha)
    assert _linha(con, r.lancamento_id) == (0, None, -30000)
    assert repo_lanc.obter(con, linha) is not None


def test_caixa_conta_fatura_uma_vez_apos_casar(con: sqlite3.Connection) -> None:
    cc, _, r = _setup(con)
    lid = _pagto(con, cc)
    assert despesas_do_mes(con, 2026, 10) == 30000  # pagamento sem casar conta
    pag.confirmar_pagamento(con, r.fatura_id, lid)
    assert despesas_do_mes(con, 2026, 10) == 30000  # agora conta a fatura, não o pagamento


@pytest.fixture
def db_web(tmp_path: Path) -> Path:
    caminho = tmp_path / "t.db"
    c = conectar(caminho)
    try:
        migrar(c)
        _setup(c)
        c.commit()
    finally:
        c.close()
    return caminho


@pytest.fixture
def cliente(db_web: Path) -> Iterator[TestClient]:
    app = create_app(db_web)
    app.state.hoje_fn = lambda: HOJE
    with TestClient(app) as c:
        yield c


def _id_fatura(db: Path) -> int:
    c = conectar(db)
    try:
        return int(c.execute("SELECT id FROM lancamentos WHERE tipo = 'fatura'").fetchone()[0])
    finally:
        c.close()


def test_web_contas_a_pagar_sem_botao_pagar_na_fatura(cliente: TestClient, db_web: Path) -> None:
    lid = _id_fatura(db_web)
    html = cliente.get("/contas-a-pagar").text
    assert f'id="lancamento-{lid}"' in html
    assert f'action="/contas-a-pagar/{lid}/pagar"' not in html
    assert 'href="/lancamentos?tipo=fatura"' in html and "Casar pagamento" in html


def test_web_contas_a_pagar_recusa_pagar_fatura(cliente: TestClient, db_web: Path) -> None:
    lid = _id_fatura(db_web)
    r = cliente.post(f"/contas-a-pagar/{lid}/pagar",
                     data={"valor": "300,00", "data": "2026-10-10", "horizonte": ""})
    assert r.status_code == 400
    assert MSG_FATURA in r.text
    r = cliente.post(f"/lancamentos/{lid}/pagar",
                     data={"valor": "300,00", "data": "2026-10-10", "voltar": ""})
    assert r.status_code == 400
    assert MSG_FATURA in r.text
    c = conectar(db_web)
    try:
        assert _linha(c, lid) == (0, None, -30000)
    finally:
        c.close()


def test_consultor_pagar_fatura_recusado(db_web: Path, capsys: pytest.CaptureFixture[str]) -> None:
    lid = _id_fatura(db_web)
    capsys.readouterr()
    codigo = cli.main(["pagar", "--lancamento", str(lid), "--db", str(db_web),
                       "--hoje", "2026-10-27"])
    assert codigo == 1
    assert json.loads(capsys.readouterr().out) == {"erro": MSG_FATURA}


# --- 2. desfazer importação com pagamento casado ---------------------------------------------

class _ParserFalso:
    def __init__(self, linhas: list[LinhaImportada]) -> None:
        self.linhas = linhas

    def ler(self, conteudo: bytes) -> ResultadoLeitura:
        return ResultadoLeitura(linhas=list(self.linhas))


_EXTRATO = _ParserFalso([LinhaImportada("2026-10-09", "PAG FATURA CARTAO X", -30000)])


def _situacao_fatura(con: sqlite3.Connection, fatura_id: int) -> tuple[Any, ...]:
    fat = repo_faturas.obter(con, fatura_id)
    linha = repo_lanc.obter(con, int(fat["lancamento_id"]))
    return fat["pagamento_lancamento_id"], linha["compensado"], linha["data_compensacao"]


def test_desfazer_importacao_descasa_pagamento_da_fatura(con: sqlite3.Connection) -> None:
    cc, _, r = _setup(con)
    res = importar(con, cc, "extrato.csv", b"a", _EXTRATO, HOJE)
    lid = int(repo_lanc.importados_da_importacao(con, res.importacao_id)[0]["id"])
    pag.confirmar_pagamento(con, r.fatura_id, lid)
    assert despesas_do_mes(con, 2026, 10) == 30000
    desfazer_importacao(con, res.importacao_id)
    assert _situacao_fatura(con, r.fatura_id) == (None, 0, None)
    assert despesas_do_mes(con, 2026, 10) == 0
    res2 = importar(con, cc, "extrato.csv", b"a", _EXTRATO, HOJE)
    lid2 = int(repo_lanc.importados_da_importacao(con, res2.importacao_id)[0]["id"])
    assert [p.lancamento_id for p in pag.propor_pagamentos(con, r.fatura_id)] == [lid2]
    pag.confirmar_pagamento(con, r.fatura_id, lid2)
    assert _situacao_fatura(con, r.fatura_id) == (lid2, 1, "2026-10-09")
    assert despesas_do_mes(con, 2026, 10) == 30000


def test_desfazer_importacao_descasa_pagamento_quitado_pela_linha(con: sqlite3.Connection) -> None:
    """Pagamento lançado à mão (pendente), quitado pela linha do extrato e casado com a fatura:
    desfazer a importação volta o pagamento a pendente e a fatura a 'a pagar'."""
    cc, _, r = _setup(con)
    pend = repo_lanc.inserir_manual(con, conta_id=cc, data="2026-10-09",
                                    descricao="PAG FATURA CARTAO X", valor_centavos=-30000,
                                    tipo="pagamento_fatura", categoria_id=None,
                                    compensado=False, observacao=None)
    con.commit()
    res = importar(con, cc, "extrato.csv", b"a", _EXTRATO, HOJE)
    linha = int(repo_lanc.importados_da_importacao(con, res.importacao_id)[0]["id"])
    confirmar_casamento(con, pend, linha)
    pag.confirmar_pagamento(con, r.fatura_id, pend)
    desfazer_importacao(con, res.importacao_id)
    assert _situacao_fatura(con, r.fatura_id) == (None, 0, None)
    assert repo_lanc.obter(con, pend)["compensado"] == 0


def test_casamento_nao_apaga_linha_que_paga_fatura(con: sqlite3.Connection) -> None:
    cc, _, r = _setup(con)
    res = importar(con, cc, "extrato.csv", b"a", _EXTRATO, HOJE)
    lid = int(repo_lanc.importados_da_importacao(con, res.importacao_id)[0]["id"])
    pag.confirmar_pagamento(con, r.fatura_id, lid)
    pend = repo_lanc.inserir_manual(con, conta_id=cc, data="2026-10-09", descricao="OUTRA",
                                    valor_centavos=-30000, tipo="saida", categoria_id=None,
                                    compensado=False, observacao=None)
    con.commit()
    with pytest.raises(ValueError, match="pagamento de uma fatura"):
        confirmar_casamento(con, pend, lid)
    assert _situacao_fatura(con, r.fatura_id) == (lid, 1, "2026-10-09")
