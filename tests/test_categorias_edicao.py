import sqlite3

import pytest

from app.dominio.categorias import (
    ErroCategoria, alterar_tipo, arvore_para_tela, criar_categoria, criar_regra,
    criar_subcategoria, mover_subcategoria, remover_categoria, renomear_categoria,
)


def _conta(con: sqlite3.Connection) -> None:
    con.execute("INSERT INTO contas (uuid, nome, tipo) VALUES ('c', 'C', 'corrente')")
    con.commit()


def _lanc(con: sqlite3.Connection, n: str, cat: int | None) -> None:
    con.execute("INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
                " categoria_id, chave_deduplicacao, origem)"
                " VALUES (?,1,'2026-10-10','x',-1,'saida',?,?,'manual')", (n, cat, n))
    con.commit()


def _rec(con: sqlite3.Connection, n: str, cat: int | None) -> None:
    con.execute("INSERT INTO recorrentes (uuid, nome, tipo, dia, valor_estimado_centavos,"
                " categoria_id, conta_id) VALUES (?, 'R', 'saida', 5, 100, ?, 1)", (n, cat))
    con.commit()


def _cat(con: sqlite3.Connection, id_: int) -> sqlite3.Row:
    return con.execute("SELECT * FROM categorias WHERE id = ?", (id_,)).fetchone()


@pytest.mark.parametrize("variante", ["pet", "PET", "  Pet ", "Pét", "P  e t"])
def test_criar_categoria_duplicada_recusada(con: sqlite3.Connection, variante: str) -> None:
    criar_categoria(con, "Pet", "saida")
    if variante == "P  e t":
        criar_categoria(con, variante, "saida")  # espaços internos são diferença real
        return
    with pytest.raises(ErroCategoria, match="(?i)já existe"):
        criar_categoria(con, variante, "entrada")  # raízes: grupo único, qualquer tipo


def test_subcategoria_duplicada_so_entre_irmaos(con: sqlite3.Connection) -> None:
    a = criar_categoria(con, "A", "saida")
    b = criar_categoria(con, "B", "saida")
    criar_subcategoria(con, a, "Manutenção")
    with pytest.raises(ErroCategoria, match="(?i)já existe"):
        criar_subcategoria(con, a, " manutencao ")
    criar_subcategoria(con, b, "Manutenção")  # outro pai: permitido


def test_renomear_duplicada_recusada_e_mesmo_nome_ok(con: sqlite3.Connection) -> None:
    a = criar_categoria(con, "A", "saida")
    b = criar_categoria(con, "B", "saida")
    with pytest.raises(ErroCategoria, match="(?i)já existe"):
        renomear_categoria(con, b, " á ")
    renomear_categoria(con, a, "A")  # a si mesma
    renomear_categoria(con, a, "Á")  # só caixa/acento: permitido para si mesma
    assert _cat(con, a)["nome"] == "Á"


def test_subcategoria_sob_subcategoria_recusada(con: sqlite3.Connection) -> None:
    a = criar_categoria(con, "A", "saida")
    s = criar_subcategoria(con, a, "S")
    with pytest.raises(ErroCategoria, match="subcategoria"):
        criar_subcategoria(con, s, "T")


def test_subcategoria_herda_tipo_e_alterar_tipo_propaga(con: sqlite3.Connection) -> None:
    a = criar_categoria(con, "A", "saida")
    s = criar_subcategoria(con, a, "S")
    assert _cat(con, s)["tipo"] == "saida"
    alterar_tipo(con, a, "entrada")
    assert (_cat(con, a)["tipo"], _cat(con, s)["tipo"]) == ("entrada", "entrada")
    with pytest.raises(ErroCategoria):
        alterar_tipo(con, s, "saida")  # subcategoria segue o pai
    with pytest.raises(ErroCategoria):
        alterar_tipo(con, a, "outro")


def test_mover_subcategoria(con: sqlite3.Connection) -> None:
    a = criar_categoria(con, "A", "saida")
    b = criar_categoria(con, "B", "saida")
    e = criar_categoria(con, "E", "entrada")
    s = criar_subcategoria(con, a, "S")
    mover_subcategoria(con, s, b)
    assert _cat(con, s)["pai_id"] == b
    with pytest.raises(ErroCategoria, match="tipo"):
        mover_subcategoria(con, s, e)
    with pytest.raises(ErroCategoria):
        mover_subcategoria(con, a, b)  # raiz não é movida
    outra = criar_subcategoria(con, a, "S")
    with pytest.raises(ErroCategoria, match="(?i)já existe"):
        mover_subcategoria(con, outra, b)


def test_remover_com_filhas_recusado(con: sqlite3.Connection) -> None:
    a = criar_categoria(con, "A", "saida")
    criar_subcategoria(con, a, "S")
    with pytest.raises(ErroCategoria, match="subcategorias"):
        remover_categoria(con, a)


def test_remover_em_uso_sem_escolha_recusado_com_contagens(con: sqlite3.Connection) -> None:
    _conta(con)
    a = criar_categoria(con, "A", "saida")
    _lanc(con, "l1", a)
    _lanc(con, "l2", a)
    _rec(con, "r1", a)
    criar_regra(con, "XYZ", a)
    with pytest.raises(ErroCategoria) as exc:
        remover_categoria(con, a)
    msg = str(exc.value)
    assert "2 lançamento" in msg and "1 conta" in msg and "1 regra" in msg
    assert _cat(con, a) is not None


def test_remover_sem_uso(con: sqlite3.Connection) -> None:
    a = criar_categoria(con, "A", "saida")
    assert remover_categoria(con, a) == {"lancamentos": 0, "recorrentes": 0, "regras": 0}
    assert _cat(con, a) is None
    with pytest.raises(ErroCategoria, match="não encontrada"):
        remover_categoria(con, a)


def test_remover_substituir_por(con: sqlite3.Connection) -> None:
    _conta(con)
    a = criar_categoria(con, "A", "saida")
    b = criar_categoria(con, "B", "saida")
    e = criar_categoria(con, "E", "entrada")
    _lanc(con, "l1", a)
    _rec(con, "r1", a)
    criar_regra(con, "XYZ", a)
    with pytest.raises(ErroCategoria, match="tipo"):
        remover_categoria(con, a, substituir_por=e)
    with pytest.raises(ErroCategoria):
        remover_categoria(con, a, substituir_por=a)
    res = remover_categoria(con, a, substituir_por=b)
    assert res == {"lancamentos": 1, "recorrentes": 1, "regras": 1}
    assert con.execute("SELECT categoria_id FROM lancamentos").fetchone()[0] == b
    assert con.execute("SELECT categoria_id FROM recorrentes").fetchone()[0] == b
    assert con.execute("SELECT categoria_id FROM regras_categoria").fetchone()[0] == b
    assert _cat(con, a) is None


def test_remover_deixar_sem_so_apaga_regras_da_categoria(con: sqlite3.Connection) -> None:
    _conta(con)
    a = criar_categoria(con, "A", "saida")
    b = criar_categoria(con, "B", "saida")
    _lanc(con, "l1", a)
    _lanc(con, "l2", b)
    _rec(con, "r1", a)
    criar_regra(con, "AAA", a)
    criar_regra(con, "BBB", b)
    res = remover_categoria(con, a, deixar_sem=True)
    assert res == {"lancamentos": 1, "recorrentes": 1, "regras": 1}
    assert [r[0] for r in con.execute("SELECT categoria_id FROM lancamentos ORDER BY id")] == [None, b]
    assert con.execute("SELECT categoria_id FROM recorrentes").fetchone()[0] is None
    assert [r["padrao"] for r in con.execute("SELECT padrao FROM regras_categoria")] == ["BBB"]


def test_remover_escolhas_conflitantes(con: sqlite3.Connection) -> None:
    a = criar_categoria(con, "A", "saida")
    b = criar_categoria(con, "B", "saida")
    with pytest.raises(ErroCategoria):
        remover_categoria(con, a, substituir_por=b, deixar_sem=True)


def test_remover_e_atomico_com_destino_inexistente(con: sqlite3.Connection) -> None:
    _conta(con)
    a = criar_categoria(con, "A", "saida")
    _lanc(con, "l1", a)
    criar_regra(con, "AAA", a)
    with pytest.raises(ErroCategoria):
        remover_categoria(con, a, substituir_por=99999)
    assert _cat(con, a) is not None
    assert con.execute("SELECT categoria_id FROM lancamentos").fetchone()[0] == a
    assert con.execute("SELECT COUNT(*) FROM regras_categoria").fetchone()[0] == 1
    assert not con.in_transaction


def test_remover_rollback_se_falha_no_meio(con: sqlite3.Connection,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    from app.repo import categorias as repo
    _conta(con)
    a = criar_categoria(con, "A", "saida")
    b = criar_categoria(con, "B", "saida")
    _lanc(con, "l1", a)

    def falha(*_: object) -> bool:
        raise RuntimeError("boom")

    monkeypatch.setattr(repo, "remover", falha)
    with pytest.raises(RuntimeError):
        remover_categoria(con, a, substituir_por=b)
    assert con.execute("SELECT categoria_id FROM lancamentos").fetchone()[0] == a
    assert not con.in_transaction


def test_arvore_para_tela(con: sqlite3.Connection) -> None:
    _conta(con)
    a = criar_categoria(con, "ZZ Teste", "saida")
    s = criar_subcategoria(con, a, "Sub")
    _lanc(con, "l1", s)
    tela = arvore_para_tela(con)
    assert {"entrada", "saida"} == set(tela)
    cat = next(c for c in tela["saida"] if c["id"] == a)
    assert cat["filhas"][0]["nome"] == "Sub" and cat["filhas"][0]["uso"]["lancamentos"] == 1
    assert "Moradia" in [c["nome"] for c in tela["saida"]]
    assert "Salário e Trabalho" in [c["nome"] for c in tela["entrada"]]


def _falhar(monkeypatch: pytest.MonkeyPatch, nome: str) -> None:
    from app.repo import categorias as repo

    def falha(*_: object) -> bool:
        raise RuntimeError("boom")

    monkeypatch.setattr(repo, nome, falha)


def test_remover_em_transacao_do_chamador_reverte_so_a_operacao(
        con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    _conta(con)
    a = criar_categoria(con, "A", "saida")
    b = criar_categoria(con, "B", "saida")
    _lanc(con, "l1", a)
    criar_regra(con, "AAA", a)
    con.execute("INSERT INTO configuracoes (chave, valor) VALUES ('meu', '1')")  # tx implícita
    assert con.in_transaction
    _falhar(monkeypatch, "remover")
    with pytest.raises(RuntimeError):
        remover_categoria(con, a, substituir_por=b)
    assert con.in_transaction
    assert con.execute("SELECT categoria_id FROM lancamentos").fetchone()[0] == a
    assert con.execute("SELECT categoria_id FROM regras_categoria").fetchone()[0] == a
    assert _cat(con, a) is not None
    assert con.execute("SELECT COUNT(*) FROM configuracoes WHERE chave = 'meu'").fetchone()[0] == 1


def test_alterar_tipo_em_transacao_do_chamador_reverte(
        con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    a = criar_categoria(con, "A", "saida")
    s = criar_subcategoria(con, a, "S")
    con.execute("INSERT INTO configuracoes (chave, valor) VALUES ('meu', '1')")
    from app.repo import categorias as repo
    original = repo.atualizar_tipo

    def meio(c: sqlite3.Connection, cid: int, tipo: str) -> None:
        original(c, cid, tipo)
        raise RuntimeError("boom")

    monkeypatch.setattr(repo, "atualizar_tipo", meio)
    with pytest.raises(RuntimeError):
        alterar_tipo(con, a, "entrada")
    assert (_cat(con, a)["tipo"], _cat(con, s)["tipo"]) == ("saida", "saida")
    assert con.execute("SELECT COUNT(*) FROM configuracoes WHERE chave = 'meu'").fetchone()[0] == 1


def test_mover_em_transacao_do_chamador_reverte(
        con: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    a = criar_categoria(con, "A", "saida")
    b = criar_categoria(con, "B", "saida")
    s = criar_subcategoria(con, a, "S")
    con.execute("INSERT INTO configuracoes (chave, valor) VALUES ('meu', '1')")
    from app.repo import categorias as repo
    original = repo.mover

    def meio(c: sqlite3.Connection, cid: int, novo: int) -> None:
        original(c, cid, novo)
        raise RuntimeError("boom")

    monkeypatch.setattr(repo, "mover", meio)
    with pytest.raises(RuntimeError):
        mover_subcategoria(con, s, b)
    assert _cat(con, s)["pai_id"] == a
    assert con.execute("SELECT COUNT(*) FROM configuracoes WHERE chave = 'meu'").fetchone()[0] == 1
