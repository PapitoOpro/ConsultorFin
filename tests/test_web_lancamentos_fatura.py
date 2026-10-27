"""Fatura expansível em Lançamentos: itens, categoria por item e casamento (dados inventados)."""
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import conectar
from app.dominio.faturas import importar_fatura
from app.importacao.fatura.modelo import FaturaLida, ItemFatura
from app.main import create_app
from app.repo import contas
from app.repo import lancamentos as repo_lanc

TUDO = {"de": "2000-01-01", "ate": "2100-01-01"}
VOLTAR = "de=2000-01-01&ate=2100-01-01"
XSS = "<script>alert(1)</script> LOJA"


@dataclass
class Cenario:
    corrente: int
    cartao: int
    fatura_id: int
    fatura_lanc: int
    pagamento: int
    pagamento_solto: int
    itens: list[int]


def _fatura() -> FaturaLida:
    f = FaturaLida(
        emissor="Banco Teste", bandeira="Visa", produto="Gold", final_cartao="1234",
        data_vencimento="2026-10-10", data_fechamento="2026-10-01",
        total_impresso_centavos=0, ajustes_resumo_centavos=0,
        itens=[
            ItemFatura("2026-09-05", "LOJA FICTICIA", -10000, "compra", None, None, "1234", None),
            ItemFatura("2026-09-10", "MERCADO INVENTADO", -6000, "compra", 2, 3, "5678", None),
            ItemFatura("2026-09-12", "ESTORNO TESTE", 1000, "credito", None, None, "1234", None),
            ItemFatura("2026-09-20", XSS, -500, "compra", None, None, "1234", None),
        ])
    f.total_impresso_centavos = f.calculado_centavos  # 15500
    return f


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "t.db"


@pytest.fixture
def cliente(db: Path) -> Iterator[TestClient]:
    app = create_app(db)
    app.state.hoje_fn = lambda: date(2026, 10, 27)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def cen(cliente: TestClient, db: Path) -> Cenario:
    con = conectar(db)
    try:
        cc = contas.criar_conta(con, "Corrente Teste", "corrente")
        cartao = contas.criar_conta(con, "Cartao Teste", "cartao", final_numero="1234")
        r = importar_fatura(con, _fatura(), "h1", "f.pdf", cartao, date(2026, 10, 27),
                            [None, None, None, None])
        pag = repo_lanc.inserir_manual(con, conta_id=cc, data="2026-10-09",
                                       descricao="PAGTO FATURA CARTAO", valor_centavos=-15500,
                                       tipo="pagamento_fatura", categoria_id=None,
                                       compensado=True, observacao=None)
        solto = repo_lanc.inserir_manual(con, conta_id=cc, data="2026-09-09",
                                         descricao="PAGTO OUTRO CARTAO", valor_centavos=-4200,
                                         tipo="pagamento_fatura", categoria_id=None,
                                         compensado=True, observacao=None)
        con.commit()
        itens = [int(x["id"]) for x in con.execute(
            "SELECT id FROM fatura_itens WHERE fatura_id = ? ORDER BY ordem", (r.fatura_id,))]
        return Cenario(cc, cartao, r.fatura_id, r.lancamento_id, pag, solto, itens)
    finally:
        con.close()


def _sql(db: Path, sql: str, *args: Any) -> list[Any]:
    con = conectar(db)
    try:
        return [tuple(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()


def _cat(db: Path, nome: str) -> int:
    return int(_sql(db, "SELECT id FROM categorias WHERE nome = ?", nome)[0][0])


def _get(c: TestClient, **q: str) -> Any:
    return c.get("/lancamentos", params={**TUDO, **q})


def _linha(html: str, lid: int) -> str:
    m = re.search(rf'<tr class="linha[^"]*" id="lancamento-{lid}".*?</tr>', html, re.S)
    assert m, lid
    return m.group(0)


def _total(html: str, nome: str) -> str:
    m = re.search(rf'id="total-{nome}"[^>]*>([^<]*)<', html)
    assert m, nome
    return m.group(1).strip()


def test_lista_mostra_fatura_sem_categoria_e_sem_itens(cliente: TestClient, cen: Cenario) -> None:
    r = _get(cliente)
    assert r.status_code == 200
    linha = _linha(r.text, cen.fatura_lanc)
    assert "linha-fatura" in linha
    assert "Fatura Cartao Teste · venc. 10/10" in linha
    assert 'name="categoria_id"' not in linha and 'name="ids"' not in linha
    assert "4 itens" in linha
    assert 'aria-expanded="false"' in linha
    assert f'aria-controls="itens-fatura-{cen.fatura_id}"' in linha
    assert f'hx-get="/faturas/{cen.fatura_id}/itens?voltar=' in linha
    assert "a pagar" in linha and "R$ 155,00" in linha
    assert "/tipo" not in linha and "/pagar" not in linha  # linha da fatura: tipo/pagar manual não
    assert "LOJA FICTICIA" not in r.text  # itens só ao expandir
    assert 'id="itens-fatura-' in r.text


def test_fragmento_itens_lista_todos_com_categoria_e_portador(cliente: TestClient,
                                                               cen: Cenario) -> None:
    r = cliente.get(f"/faturas/{cen.fatura_id}/itens", params={"voltar": VOLTAR})
    assert r.status_code == 200
    assert "<html" not in r.text
    for desc in ("LOJA FICTICIA", "MERCADO INVENTADO", "ESTORNO TESTE"):
        assert desc in r.text
    assert "2/3" in r.text and "final 5678" in r.text
    for iid in cen.itens:
        assert f'hx-post="/faturas/itens/{iid}/categoria"' in r.text
    assert re.search(r'class="valor entrada"[^>]*>R\$ 10,00', r.text)  # crédito como crédito
    assert "05/09/2026" in r.text
    assert f'action="/faturas/{cen.fatura_id}/itens/categoria-lote"' in r.text
    assert f'value="{VOLTAR.replace("&", "&amp;")}"' in r.text


def test_fragmento_itens_ids_invalidos(cliente: TestClient, cen: Cenario) -> None:
    assert cliente.get("/faturas/abc/itens").status_code == 400
    assert cliente.get("/faturas/99999/itens").status_code == 404
    assert cliente.get("/faturas/１２/itens").status_code == 400  # dígitos não ASCII


def test_html_escapado_nos_itens(cliente: TestClient, cen: Cenario) -> None:
    r = cliente.get(f"/faturas/{cen.fatura_id}/itens")
    assert "<script>alert(1)" not in r.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt; LOJA" in r.text


def test_alterar_categoria_item_persiste_e_nao_altera_total(cliente: TestClient, db: Path,
                                                            cen: Cenario) -> None:
    lazer = _cat(db, "Lazer e Entretenimento")
    alvo = cen.itens[0]
    r = cliente.post(f"/faturas/itens/{alvo}/categoria", data={"categoria_id": str(lazer)},
                     headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "<html" not in r.text
    assert f'<option value="{lazer}" selected>' in r.text
    assert f'hx-post="/faturas/itens/{alvo}/categoria"' in r.text
    assert _sql(db, "SELECT id, categoria_id FROM fatura_itens ORDER BY ordem") == [
        (cen.itens[0], lazer), (cen.itens[1], None), (cen.itens[2], None), (cen.itens[3], None)]
    assert _sql(db, "SELECT valor_centavos, categoria_id FROM lancamentos WHERE id = ?",
                cen.fatura_lanc) == [(-15500, None)]
    cliente.post(f"/faturas/itens/{alvo}/categoria", data={"categoria_id": ""})
    assert _sql(db, "SELECT categoria_id FROM fatura_itens WHERE id = ?", alvo) == [(None,)]


def test_alterar_categoria_item_erros(cliente: TestClient, cen: Cenario) -> None:
    r = cliente.post(f"/faturas/itens/{cen.itens[0]}/categoria", data={"categoria_id": "99999"})
    assert r.status_code == 400 and r.text == "Categoria não encontrada."
    r = cliente.post("/faturas/itens/99999/categoria", data={"categoria_id": ""})
    assert r.status_code == 404 and r.text == "Item da fatura não encontrado."
    assert cliente.post("/faturas/itens/x1/categoria", data={"categoria_id": ""}).status_code == 400
    r = cliente.post(f"/faturas/itens/{cen.itens[0]}/categoria", data={"categoria_id": "1;x"})
    assert r.status_code == 400 and "Traceback" not in r.text


def test_categorizar_itens_em_lote(cliente: TestClient, db: Path, cen: Cenario) -> None:
    mercado = _cat(db, "Alimentação")
    r = cliente.post(f"/faturas/{cen.fatura_id}/itens/categoria-lote", data={
        "ids": [str(cen.itens[0]), str(cen.itens[1])], "categoria_id": str(mercado),
        "voltar": VOLTAR})
    assert r.status_code == 200
    assert "2 itens categorizados." in r.text
    assert _sql(db, "SELECT COUNT(*) FROM fatura_itens WHERE categoria_id = ?", mercado) == [(2,)]
    linha = _linha(r.text, cen.fatura_lanc)
    assert 'aria-expanded="true"' in linha  # volta com a fatura aberta
    assert _sql(db, "SELECT valor_centavos FROM lancamentos WHERE id = ?",
                cen.fatura_lanc) == [(-15500,)]


def test_lote_itens_erros(cliente: TestClient, db: Path, cen: Cenario) -> None:
    mercado = _cat(db, "Alimentação")
    url = f"/faturas/{cen.fatura_id}/itens/categoria-lote"
    r = cliente.post(url, data={"categoria_id": str(mercado), "voltar": VOLTAR})
    assert r.status_code == 400 and "Selecione ao menos um item." in r.text
    r = cliente.post(url, data={"ids": ["99999"], "categoria_id": str(mercado)})
    assert r.status_code == 404 and "Algum item selecionado não pertence a esta fatura." in r.text
    r = cliente.post(url, data={"ids": [str(cen.itens[0])], "categoria_id": "abc"})
    assert r.status_code == 400 and "Categoria inválida." in r.text
    r = cliente.post("/faturas/abc/itens/categoria-lote", data={"ids": ["1"], "categoria_id": "1"})
    assert r.status_code == 400 and "Traceback" not in r.text
    assert _sql(db, "SELECT COUNT(*) FROM fatura_itens WHERE categoria_id IS NOT NULL") == [(0,)]


def test_aviso_pagamento_sem_fatura(cliente: TestClient, cen: Cenario) -> None:
    r = _get(cliente)
    assert "sem fatura importada" in _linha(r.text, cen.pagamento_solto)
    assert "sem fatura importada" in _linha(r.text, cen.pagamento)  # ainda não casado


def test_casar_pagamento_muda_selo_e_nao_conta_duas_vezes(cliente: TestClient, db: Path,
                                                          cen: Cenario) -> None:
    antes = _get(cliente)
    linha = _linha(antes.text, cen.fatura_lanc)
    assert "Casar pagamento" in linha
    assert f'action="/faturas/{cen.fatura_id}/pagamento/confirmar"' in linha
    assert f'value="{cen.pagamento}"' in linha and "09/10/2026" in linha
    saidas_antes = _total(antes.text, "saidas")
    r = cliente.post(f"/faturas/{cen.fatura_id}/pagamento/confirmar",
                     data={"lancamento_id": str(cen.pagamento), "voltar": VOLTAR})
    assert r.status_code == 200
    assert "Pagamento casado: fatura paga." in r.text
    linha = _linha(r.text, cen.fatura_lanc)
    assert "paga" in linha and "a pagar" not in linha
    assert "Desfazer pagamento" in linha and "Casar pagamento" not in linha
    assert "sem fatura importada" not in _linha(r.text, cen.pagamento)
    assert "sem fatura importada" in _linha(r.text, cen.pagamento_solto)
    assert _total(r.text, "saidas") == saidas_antes == "R$ 197,00"  # 155 + 42, sem duplicar
    assert _sql(db, "SELECT pagamento_lancamento_id FROM faturas") == [(cen.pagamento,)]


def test_desfazer_pagamento(cliente: TestClient, db: Path, cen: Cenario) -> None:
    cliente.post(f"/faturas/{cen.fatura_id}/pagamento/confirmar",
                 data={"lancamento_id": str(cen.pagamento), "voltar": VOLTAR})
    r = cliente.post(f"/faturas/{cen.fatura_id}/pagamento/desfazer", data={"voltar": VOLTAR})
    assert r.status_code == 200
    assert "Pagamento desfeito: a fatura voltou a pendente." in r.text
    linha = _linha(r.text, cen.fatura_lanc)
    assert "a pagar" in linha and "Casar pagamento" in linha
    assert _sql(db, "SELECT pagamento_lancamento_id FROM faturas") == [(None,)]
    r = cliente.post(f"/faturas/{cen.fatura_id}/pagamento/desfazer", data={"voltar": VOLTAR})
    assert r.status_code == 400 and "Esta fatura não está paga." in r.text


def test_confirmar_pagamento_erros_amigaveis(cliente: TestClient, db: Path, cen: Cenario) -> None:
    url = f"/faturas/{cen.fatura_id}/pagamento/confirmar"
    r = cliente.post(url, data={"lancamento_id": "abc", "voltar": VOLTAR})
    assert r.status_code == 400 and "Identificador inválido." in r.text
    r = cliente.post(url, data={"lancamento_id": str(cen.pagamento_solto), "voltar": VOLTAR})
    assert r.status_code == 400 and "O valor do pagamento difere do total da fatura." in r.text
    r = cliente.post("/faturas/99999/pagamento/confirmar",
                     data={"lancamento_id": str(cen.pagamento)})
    assert r.status_code == 404 and "Fatura não encontrada." in r.text
    assert _sql(db, "SELECT pagamento_lancamento_id FROM faturas") == [(None,)]


def test_fragmento_propostas(cliente: TestClient, cen: Cenario) -> None:
    r = cliente.get(f"/faturas/{cen.fatura_id}/pagamento", params={"voltar": VOLTAR})
    assert r.status_code == 200 and "<html" not in r.text
    assert f'value="{cen.pagamento}"' in r.text and "R$ 155,00" in r.text
    assert f'action="/faturas/{cen.fatura_id}/pagamento/confirmar"' in r.text
    assert cliente.get("/faturas/x/pagamento").status_code == 400
    assert cliente.get("/faturas/99999/pagamento").status_code == 404


def test_voltar_sobrevive_as_acoes(cliente: TestClient, cen: Cenario) -> None:
    voltar = f"de=2026-10-01&ate=2026-10-31&conta_id={cen.corrente}"
    r = cliente.post(f"/faturas/{cen.fatura_id}/pagamento/confirmar",
                     data={"lancamento_id": str(cen.pagamento), "voltar": voltar})
    assert r.status_code == 200
    assert f'<option value="{cen.corrente}" selected>' in r.text
    assert f'id="lancamento-{cen.fatura_lanc}"' not in r.text  # filtro da conta corrente mantido


def test_previstas_rotuladas(cliente: TestClient, db: Path, cen: Cenario) -> None:
    previstas = _sql(db, "SELECT id FROM lancamentos WHERE origem = 'fatura_prevista'")
    assert len(previstas) == 1  # parcela 3/3
    linha = _linha(_get(cliente).text, int(previstas[0][0]))
    assert "linha-prevista" in linha and ">prevista<" in linha


def test_tipo_fatura_no_filtro_e_nao_editavel(cliente: TestClient, db: Path, cen: Cenario) -> None:
    r = _get(cliente, tipo="fatura")
    assert r.status_code == 200
    assert '<option value="fatura" selected>Fatura</option>' in r.text
    assert re.findall(r'<tr class="linha[^"]*" id="lancamento-(\d+)"', r.text) == [
        str(cen.fatura_lanc)]
    r = cliente.post(f"/lancamentos/{cen.fatura_lanc}/tipo",
                     data={"tipo": "transferencia", "voltar": VOLTAR})
    assert r.status_code == 400
    r = cliente.post(f"/lancamentos/{cen.pagamento}/tipo", data={"tipo": "fatura", "voltar": VOLTAR})
    assert r.status_code == 400
    assert _sql(db, "SELECT tipo FROM lancamentos WHERE id IN (?, ?) ORDER BY id",
                cen.fatura_lanc, cen.pagamento) == [("fatura",), ("pagamento_fatura",)]


def test_linha_da_fatura_recusa_categoria(cliente: TestClient, db: Path, cen: Cenario) -> None:
    mercado = _cat(db, "Alimentação")
    r = cliente.post(f"/lancamentos/{cen.fatura_lanc}/categoria", data={"categoria_id": str(mercado)})
    assert r.status_code == 400
    r = cliente.post("/lancamentos/lote", data={"ids": [str(cen.fatura_lanc)],
                                                "categoria_id": str(mercado), "voltar": VOLTAR})
    assert r.status_code == 400
    assert _sql(db, "SELECT categoria_id FROM lancamentos WHERE id = ?", cen.fatura_lanc) == [(None,)]
