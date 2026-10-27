"""Domínio de importação de fatura (dados inventados)."""
import sqlite3
from datetime import date

import pytest

from app.dominio import faturas as dom
from app.dominio.categorias import criar_categoria, criar_regra
from app.importacao.fatura.modelo import FaturaLida, ItemFatura
from app.repo import contas as repo_contas
from app.repo import faturas as repo_faturas

HOJE = date(2026, 10, 27)


def _item(desc: str, valor: int, data: str = "2026-09-20", parcela: tuple[int, int] | None = None,
          categoria_banco: str | None = None, tipo: str = "compra") -> ItemFatura:
    return ItemFatura(data=data, descricao=desc, valor_centavos=valor, tipo=tipo,
                      parcela_atual=parcela[0] if parcela else None,
                      parcela_total=parcela[1] if parcela else None,
                      final_cartao="9999", categoria_banco=categoria_banco)


def _fatura(itens: list[ItemFatura], venc: str = "2026-10-10") -> FaturaLida:
    f = FaturaLida(emissor="Banco Teste", bandeira="Visa", produto="Gold", final_cartao="9999",
                   data_vencimento=venc, data_fechamento=None, total_impresso_centavos=0,
                   ajustes_resumo_centavos=0, itens=itens)
    f.total_impresso_centavos = f.calculado_centavos
    return f


def _conta(con: sqlite3.Connection) -> int:
    return repo_contas.criar_conta(con, "Cartao Teste", "cartao", final_numero="9999")


def _importar(con: sqlite3.Connection, f: FaturaLida, conta: int, h: str = "h1",
              substituir: bool = False, categorias: list[int | None] | None = None
              ) -> dom.ResumoFatura:
    cats = categorias if categorias is not None else [None] * len(f.itens)
    return dom.importar_fatura(con, f, h, "f.pdf", conta, HOJE, cats, substituir=substituir)


def _previstas(con: sqlite3.Connection) -> list[sqlite3.Row]:
    return con.execute("SELECT * FROM lancamentos WHERE origem = 'fatura_prevista'"
                       " ORDER BY data, id").fetchall()


def test_importa_grava_fatura_itens_e_lancamento(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    cat = criar_categoria(con, "Mercadinho Inventado")
    criar_regra(con, "MERCADO XYZ", cat)
    f = _fatura([_item("MERCADO XYZ 01", -12345), _item("ESTORNO ABC", 1000, tipo="credito")])
    previa = dom.preparar_previa(con, f, "h1")
    assert previa.conta_id == conta and previa.situacao == "nova"
    assert previa.categorias_sugeridas == [cat, None]
    r = _importar(con, f, conta, categorias=previa.categorias_sugeridas)
    assert r.itens == 2 and r.previstas_criadas == 0
    lanc = con.execute("SELECT * FROM lancamentos WHERE id = ?", (r.lancamento_id,)).fetchone()
    assert (lanc["tipo"], lanc["origem"], lanc["compensado"], lanc["valor_centavos"],
            lanc["data"], lanc["categoria_id"], lanc["conta_id"]) == (
        "fatura", "fatura", 0, -11345, "2026-10-10", None, conta)
    assert str(lanc["chave_deduplicacao"]).startswith("fatura:")
    fat = repo_faturas.obter(con, r.fatura_id)
    assert fat["lancamento_id"] == r.lancamento_id and fat["competencia"] == "2026-10"
    assert fat["total_calculado_centavos"] == 11345
    assert [i["categoria_id"] for i in repo_faturas.itens(con, r.fatura_id)] == [cat, None]


def test_reenviar_mesmo_hash_levanta_FaturaJaImportada(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    f = _fatura([_item("LOJA A", -500)])
    _importar(con, f, conta)
    assert dom.preparar_previa(con, f, "h1").situacao == "identica"
    with pytest.raises(dom.FaturaJaImportada, match="já foi importada"):
        _importar(con, f, conta, substituir=True)


def test_hash_diferente_exige_substituir(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    _importar(con, _fatura([_item("LOJA A", -500)]), conta)
    f2 = _fatura([_item("LOJA A", -500), _item("LOJA B", -700)])
    assert dom.preparar_previa(con, f2, "h2").situacao == "substitui"
    with pytest.raises(dom.FaturaDiferenteExistente, match="substituir"):
        _importar(con, f2, conta, "h2")
    r = _importar(con, f2, conta, "h2", substituir=True)
    assert len(repo_faturas.itens(con, r.fatura_id)) == 2
    assert con.execute("SELECT COUNT(*) FROM faturas").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM lancamentos WHERE tipo = 'fatura'"
                       ).fetchone()[0] == 1
    lanc = con.execute("SELECT valor_centavos FROM lancamentos WHERE id = ?",
                       (r.lancamento_id,)).fetchone()
    assert lanc["valor_centavos"] == -1200
    assert repo_faturas.obter(con, r.fatura_id)["arquivo_hash"] == "h2"


def test_substituir_mantem_categorias_ja_definidas(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    cat = criar_categoria(con, "Cat Inventada")
    r1 = _importar(con, _fatura([_item("LOJA A", -500), _item("LOJA B", -700)]), conta)
    item_a = repo_faturas.itens(con, r1.fatura_id)[0]
    dom.alterar_categoria_item(con, int(item_a["id"]), cat)
    f2 = _fatura([_item("Loja  A", -500), _item("LOJA B", -700), _item("LOJA C", -100)])
    r2 = _importar(con, f2, conta, "h2", substituir=True)
    assert [i["categoria_id"] for i in repo_faturas.itens(con, r2.fatura_id)] == [cat, None, None]


def test_previstas_criadas_para_parcela_3_de_5(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    cat = criar_categoria(con, "Cat Parcela")
    f = _fatura([_item("TV PARCELADA", -10000, parcela=(3, 5))], venc="2026-01-31")
    r = _importar(con, f, conta, categorias=[cat])
    assert r.previstas_criadas == 2
    prev = _previstas(con)
    assert [p["data"] for p in prev] == ["2026-02-28", "2026-03-31"]
    assert [(p["parcela_atual"], p["parcela_total"]) for p in prev] == [(4, 5), (5, 5)]
    item = repo_faturas.itens(con, r.fatura_id)[0]
    for p in prev:
        assert (p["compensado"], p["estimado"], p["conta_id"], p["valor_centavos"],
                p["categoria_id"]) == (0, 1, conta, -10000, cat)
        assert p["grupo_parcela_uuid"] == item["grupo_parcela_uuid"] is not None


def test_fatura_seguinte_baixa_prevista(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    _importar(con, _fatura([_item("TV PARCELADA", -10000, parcela=(3, 5))]), conta)
    grupo = _previstas(con)[0]["grupo_parcela_uuid"]
    f2 = _fatura([_item("TV PARCELADA", -10000, parcela=(4, 5))], venc="2026-11-10")
    previa = dom.preparar_previa(con, f2, "h2")
    assert (previa.previstas_a_baixar, previa.previstas_a_criar) == (1, 0)
    r = _importar(con, f2, conta, "h2")
    assert (r.previstas_baixadas, r.previstas_criadas) == (1, 0)
    prev = _previstas(con)
    assert [(p["parcela_atual"], p["grupo_parcela_uuid"]) for p in prev] == [(5, grupo)]
    assert repo_faturas.itens(con, r.fatura_id)[0]["grupo_parcela_uuid"] == grupo
    assert dom.previstas_orfas(con) == []


def test_tolerancia_valor_da_prevista(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    _importar(con, _fatura([_item("CURSO X", -10000, parcela=(1, 3)),
                            _item("SOFA Y", -10000, parcela=(1, 3))]), conta)
    f2 = _fatura([_item("CURSO X", -10150, parcela=(2, 3)),
                  _item("SOFA Y", -10500, parcela=(2, 3))], venc="2026-11-10")
    r = _importar(con, f2, conta, "h2")
    assert r.previstas_baixadas == 1
    orfas = dom.previstas_orfas(con)
    assert [(o["descricao"], o["parcela_atual"]) for o in orfas] == [("SOFA Y", 2)]


def test_compras_identicas_viram_dois_itens(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    r = _importar(con, _fatura([_item("CAFE", -800), _item("CAFE", -800)]), conta)
    assert r.itens == 2 and len(repo_faturas.itens(con, r.fatura_id)) == 2


def test_criar_conta_cartao_nome_e_tipo(con: sqlite3.Connection) -> None:
    f = _fatura([_item("LOJA", -100)])
    cid = dom.criar_conta_cartao(con, f)
    conta = repo_contas.obter(con, cid)
    assert (conta["nome"], conta["tipo"], conta["final_numero"]) == (
        "Banco Teste Visa Gold final 9999", "cartao", "9999")
    assert dom.preparar_previa(con, f, "h").conta_id == cid


def test_categoria_banco_sugere_categoria_existente(con: sqlite3.Connection) -> None:
    cat = criar_categoria(con, "Zeladoria Ficticia")
    f = _fatura([_item("LOJA Q", -100, categoria_banco="ZELADORIA FICTÍCIA"),
                 _item("LOJA R", -100, categoria_banco="Inexistente Qualquer"),
                 _item("LOJA S", -100)])
    previa = dom.preparar_previa(con, f, "h")
    assert previa.conta_id is None and previa.situacao == "nova"
    assert previa.categorias_sugeridas == [cat, None, None]


def test_importacao_atomica(con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    conta = _conta(con)
    original = repo_faturas.inserir_item
    chamadas = {"n": 0}

    def falha(*args: object, **kwargs: object) -> int:
        chamadas["n"] += 1
        if chamadas["n"] == 2:
            raise RuntimeError("falha simulada")
        return original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(repo_faturas, "inserir_item", falha)
    with pytest.raises(RuntimeError):
        _importar(con, _fatura([_item("A", -100, parcela=(1, 2)), _item("B", -200)]), conta)
    assert not con.in_transaction
    for tabela in ("faturas", "fatura_itens", "lancamentos"):
        assert con.execute(f"SELECT COUNT(*) FROM {tabela}").fetchone()[0] == 0  # noqa: S608


def test_categorias_desalinhadas_rejeitadas(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    with pytest.raises(ValueError, match="categorias"):
        _importar(con, _fatura([_item("A", -100)]), conta, categorias=[])


def test_categorizar_itens_em_lote_e_aplicar_regras(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    cat = criar_categoria(con, "Cat Lote")
    r = _importar(con, _fatura([_item("PADARIA K", -100), _item("LOJA L", -200)]), conta)
    ids = [int(i["id"]) for i in repo_faturas.itens(con, r.fatura_id)]
    assert dom.categorizar_itens_em_lote(con, [ids[1]], cat) == 1
    criar_regra(con, "PADARIA", cat)
    assert dom.aplicar_regras_itens(con, r.fatura_id) == 1
    assert [i["categoria_id"] for i in repo_faturas.itens(con, r.fatura_id)] == [cat, cat]
    with pytest.raises(ValueError):
        dom.alterar_categoria_item(con, 999999, cat)
    with pytest.raises(ValueError):
        dom.categorizar_itens_em_lote(con, [ids[0], 999999], cat)
    dom.alterar_categoria_item(con, ids[0], None)
    assert repo_faturas.obter_item(con, ids[0])["categoria_id"] is None
    lanc = con.execute("SELECT categoria_id FROM lancamentos WHERE tipo = 'fatura'").fetchone()
    assert lanc["categoria_id"] is None
