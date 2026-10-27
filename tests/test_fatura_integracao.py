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
from app.dominio.categorias import (
    ErroCategoria, criar_categoria, criar_subcategoria, despesas_do_mes, remover_categoria,
)
from app.dominio.parcelas import marcar_pago
from app.importacao.base import LinhaImportada, ResultadoLeitura
from app.importacao.fatura.modelo import FaturaLida, ItemFatura
from app.importacao.servico import desfazer_importacao, importar
from app.main import create_app
from app.repo import categorias as repo_categorias
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


# --- 3. categorias usadas por itens de fatura ------------------------------------------------

def _duas_subcategorias(con: sqlite3.Connection) -> tuple[int, int]:
    pai = criar_categoria(con, "Pai Teste", "saida")
    return criar_subcategoria(con, pai, "Sub A"), criar_subcategoria(con, pai, "Sub B")


def _cat_dos_itens(con: sqlite3.Connection) -> list[Any]:
    return [r[0] for r in con.execute("SELECT categoria_id FROM fatura_itens ORDER BY id")]


def test_uso_conta_itens_de_fatura(con: sqlite3.Connection) -> None:
    a, _ = _duas_subcategorias(con)
    _setup(con, [a])
    assert repo_categorias.uso(con, a)["itens_fatura"] == 1


def test_remover_categoria_de_item_sem_escolha_recusado(con: sqlite3.Connection) -> None:
    a, _ = _duas_subcategorias(con)
    _setup(con, [a])
    with pytest.raises(ErroCategoria, match="1 item"):
        remover_categoria(con, a)
    assert _cat_dos_itens(con) == [a]


def test_remover_categoria_de_item_substituir_por(con: sqlite3.Connection) -> None:
    a, b = _duas_subcategorias(con)
    _setup(con, [a])
    assert remover_categoria(con, a, substituir_por=b)["itens_fatura"] == 1
    assert _cat_dos_itens(con) == [b]


def test_remover_categoria_de_item_deixar_sem(con: sqlite3.Connection) -> None:
    a, _ = _duas_subcategorias(con)
    _setup(con, [a])
    assert remover_categoria(con, a, deixar_sem=True)["itens_fatura"] == 1
    assert _cat_dos_itens(con) == [None]


def test_web_excluir_categoria_de_item_mostra_contagem(cliente: TestClient, db_web: Path) -> None:
    c = conectar(db_web)
    try:
        a, _ = _duas_subcategorias(c)
        c.execute("UPDATE fatura_itens SET categoria_id = ?", (a,))
        c.commit()
    finally:
        c.close()
    html = cliente.get("/categorias?aba=saida").text
    assert "1 item de fatura" in html
    r = cliente.post(f"/categorias/{a}/excluir", data={"aba": "saida"})
    assert r.status_code == 200 and "1 item de fatura" in r.text
    r = cliente.post(f"/categorias/{a}/excluir", data={"aba": "saida", "acao": "sem"})
    assert r.status_code == 200 and "Categoria excluída." in r.text


# --- 4. previstas órfãs: sinalizar e descartar -----------------------------------------------

def _item(desc: str, valor: int, parcela: tuple[int, int] | None = None) -> ItemFatura:
    return ItemFatura(data="2026-09-15", descricao=desc, valor_centavos=valor, tipo="compra",
                      parcela_atual=parcela[0] if parcela else None,
                      parcela_total=parcela[1] if parcela else None, final_cartao="9999",
                      categoria_banco=None)


def _com_orfa(con: sqlite3.Connection) -> tuple[int, int, int]:
    """Fatura de out (venc. dia 12) cria previstas de nov/dez; a de nov (venc. dia 10) não traz
    o item: a prevista de nov (dia 12) é órfã, a de dez não. Devolve (órfã, dez, conta)."""
    cartao = repo_contas.criar_conta(con, "Cartao Teste", "cartao", final_numero="9999")
    dom.importar_fatura(con, _fatura_lida("2026-10-12", itens=[_item("SOFA Y", -10000, (1, 3))]),
                        "h1", "f1.pdf", cartao, HOJE, [None])
    dom.importar_fatura(con, _fatura_lida("2026-11-10", itens=[_item("OUTRA", -500)]),
                        "h2", "f2.pdf", cartao, HOJE, [None])
    prev = con.execute("SELECT id, data FROM lancamentos WHERE origem = 'fatura_prevista'"
                       " ORDER BY data").fetchall()
    assert [p["data"] for p in prev] == ["2026-11-12", "2026-12-12"]
    return int(prev[0]["id"]), int(prev[1]["id"]), cartao


def test_orfa_pela_competencia_nao_pelo_dia(con: sqlite3.Connection) -> None:
    orfa, _, _ = _com_orfa(con)
    assert [int(r["id"]) for r in dom.previstas_orfas(con)] == [orfa]
    assert dom.ids_previstas_orfas(con) == {orfa}


def test_descartar_prevista(con: sqlite3.Connection) -> None:
    orfa, dez, cartao = _com_orfa(con)
    dom_lanc.descartar_prevista(con, orfa)
    assert repo_lanc.obter(con, orfa) is None and repo_lanc.obter(con, dez) is not None
    assert dom.ids_previstas_orfas(con) == set()
    with pytest.raises(dom_lanc.NaoEncontrado):
        dom_lanc.descartar_prevista(con, orfa)


def test_descartar_recusa_o_que_nao_e_prevista(con: sqlite3.Connection) -> None:
    _, _, r = _setup(con)
    manual = repo_lanc.inserir_manual(con, conta_id=r.conta_id, data="2026-11-01",
                                      descricao="MANUAL", valor_centavos=-100, tipo="saida",
                                      categoria_id=None, compensado=False, observacao=None)
    con.commit()
    for lid in (manual, r.lancamento_id):
        with pytest.raises(dom_lanc.ErroLancamento, match="prevista"):
            dom_lanc.descartar_prevista(con, lid)
        assert repo_lanc.obter(con, lid) is not None


@pytest.fixture
def db_orfa(tmp_path: Path) -> tuple[Path, int, int]:
    caminho = tmp_path / "o.db"
    c = conectar(caminho)
    try:
        migrar(c)
        orfa, dez, _ = _com_orfa(c)
        c.commit()
    finally:
        c.close()
    return caminho, orfa, dez


_PERIODO = "de=2026-01-01&ate=2027-12-31"


def test_web_lancamentos_sinaliza_e_descarta_orfa(db_orfa: tuple[Path, int, int],
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    caminho, orfa, dez = db_orfa
    chamadas: list[int] = []
    original = repo_lanc.previstas_orfas

    def contar(c: Any) -> Any:
        chamadas.append(1)
        return original(c)

    monkeypatch.setattr(repo_lanc, "previstas_orfas", contar)
    app = create_app(caminho)
    app.state.hoje_fn = lambda: HOJE
    with TestClient(app) as cli_web:
        html = cli_web.get(f"/lancamentos?{_PERIODO}").text
        assert len(chamadas) == 1  # uma consulta para a página toda
        assert html.count("não apareceu na fatura") == 1
        assert f'action="/lancamentos/{orfa}/descartar"' in html
        assert f'action="/lancamentos/{dez}/descartar"' not in html
        r = cli_web.post(f"/lancamentos/{orfa}/descartar", data={"voltar": _PERIODO})
        assert r.status_code == 200 and "Parcela prevista descartada." in r.text
        assert "não apareceu na fatura" not in r.text
        r = cli_web.post(f"/lancamentos/{orfa}/descartar", data={"voltar": _PERIODO})
        assert r.status_code == 404
        fatura = int(_sql_um(caminho, "SELECT id FROM lancamentos WHERE tipo = 'fatura'"))
        r = cli_web.post(f"/lancamentos/{fatura}/descartar", data={"voltar": _PERIODO})
        assert r.status_code == 400
    assert _sql_um(caminho, f"SELECT COUNT(*) FROM lancamentos WHERE id = {dez}") == 1


def _sql_um(caminho: Path, sql: str) -> Any:
    c = conectar(caminho)
    try:
        return c.execute(sql).fetchone()[0]
    finally:
        c.close()


# --- 7. filtro "sem categoria" não lista a linha da fatura ----------------------------------

def test_filtro_sem_categoria_exclui_linha_da_fatura(con: sqlite3.Connection) -> None:
    cc, _, r = _setup(con)
    manual = repo_lanc.inserir_manual(con, conta_id=cc, data="2026-10-05", descricao="SEM CAT",
                                      valor_centavos=-100, tipo="saida", categoria_id=None,
                                      compensado=True, observacao=None)
    con.commit()
    filtros = dom_lanc.Filtros.de_parametros(
        {"de": "2026-10-01", "ate": "2026-10-31", "categoria_id": "sem"}, HOJE)
    pagina = dom_lanc.listar(con, filtros)
    assert [l.id for l in pagina.linhas] == [manual]
    assert pagina.total == 1
