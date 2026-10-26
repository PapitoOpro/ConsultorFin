import sqlite3

import pytest

from app import db
from app.db import MIGRACOES, conectar, migrar

TABELAS = {
    "contas", "categorias", "lancamentos", "importacoes", "regras_categoria",
    "recorrentes", "chaves_quitadas", "configuracoes",
}
CATEGORIAS = {"Moradia", "Saúde", "Educação", "Alimentação", "Salário e Trabalho"}


def _tabelas(con: sqlite3.Connection) -> set[str]:
    return {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_migrar_cria_tabelas_e_categorias(con: sqlite3.Connection) -> None:
    assert TABELAS <= _tabelas(con)
    nomes = {r["nome"] for r in con.execute("SELECT nome FROM categorias")}
    assert CATEGORIAS <= nomes
    cols = {r["name"] for r in con.execute("PRAGMA table_info(lancamentos)")}
    assert {"data_compensacao", "compensado", "estimado", "grupo_parcela_uuid",
            "parcela_atual", "parcela_total", "recorrente_id"} <= cols
    assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_chaves_quitadas_guarda_originais(con: sqlite3.Connection) -> None:
    cols = {r["name"] for r in con.execute("PRAGMA table_info(chaves_quitadas)")}
    assert {"valor_original_centavos", "estimado_original"} <= cols
    assert len(MIGRACOES) >= 4


def test_migrar_idempotente(con: sqlite3.Connection) -> None:
    migrar(con)
    assert con.execute("SELECT COUNT(*) FROM categorias").fetchone()[0] == 29 + 142
    assert con.execute("PRAGMA user_version").fetchone()[0] == len(MIGRACOES)


def _banco_v1() -> sqlite3.Connection:
    c = conectar(":memory:")
    c.execute("BEGIN")
    MIGRACOES[0](c)
    c.execute("PRAGMA user_version = 1")
    c.execute("COMMIT")
    return c


def test_migracao_incremental_v1_aplica_so_pendentes() -> None:
    c = _banco_v1()
    assert "recorrentes_pulados" not in _tabelas(c)
    c.execute("INSERT INTO categorias (uuid, nome) VALUES ('x', 'Extra')")
    c.commit()
    migrar(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == len(MIGRACOES)
    assert "recorrentes_pulados" in _tabelas(c)
    assert "competencia" in {r["name"] for r in c.execute("PRAGMA table_info(lancamentos)")}
    # v1 não foi reaplicada: categoria extra (não padrão) preservada como saída
    assert c.execute("SELECT tipo FROM categorias WHERE nome = 'Extra'").fetchone()[0] == "saida"
    total = c.execute("SELECT COUNT(*) FROM categorias").fetchone()[0]
    migrar(c)  # no-op
    assert c.execute("PRAGMA user_version").fetchone()[0] == len(MIGRACOES)
    assert c.execute("SELECT COUNT(*) FROM categorias").fetchone()[0] == total
    c.close()


def test_migracao_com_falha_faz_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    def falha(con: sqlite3.Connection) -> None:
        con.execute("CREATE TABLE tmp_falha (id INTEGER)")
        raise RuntimeError("boom")

    c = conectar(":memory:")
    migrar(c)
    versao = c.execute("PRAGMA user_version").fetchone()[0]
    monkeypatch.setattr(db, "MIGRACOES", [*MIGRACOES, falha])
    with pytest.raises(RuntimeError):
        migrar(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == versao
    assert "tmp_falha" not in _tabelas(c)
    assert not c.in_transaction
    c.close()


def test_migracao_3_preenche_competencia_de_recorrentes() -> None:
    c = _banco_v1()
    c.execute("INSERT INTO contas (uuid, nome, tipo) VALUES ('c', 'C', 'corrente')")
    c.execute("INSERT INTO recorrentes (uuid, nome, tipo, dia, valor_estimado_centavos, conta_id)"
              " VALUES ('r', 'Luz', 'saida', 10, 100, 1)")
    for u, rid in (("a", 1), ("b", None)):
        c.execute("INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
                  " chave_deduplicacao, origem, recorrente_id)"
                  " VALUES (?, 1, '2026-10-10', 'x', -1, 'saida', ?, 'manual', ?)", (u, u, rid))
    c.commit()
    migrar(c)
    comp = {r["uuid"]: r["competencia"]
            for r in c.execute("SELECT uuid, competencia FROM lancamentos")}
    assert comp == {"a": "2026-10", "b": None}
    c.close()


def test_migracao_5_atualiza_banco_v4_com_dados(tmp_path) -> None:
    caminho = tmp_path / "v4.db"
    c = conectar(caminho)
    for numero in range(4):
        c.execute("BEGIN")
        MIGRACOES[numero](c)
        c.execute(f"PRAGMA user_version = {numero + 1}")
        c.execute("COMMIT")
    c.execute("INSERT INTO contas (uuid, nome, tipo) VALUES ('c', 'C', 'corrente')")
    c.execute("INSERT INTO importacoes (arquivo_nome, arquivo_hash, conta_id, criado_em)"
              " VALUES ('a.csv', 'h', 1, '2026-10-23T00:00:00')")
    c.execute("INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
              " chave_deduplicacao, origem, importacao_id, data_compensacao, compensado)"
              " VALUES ('p', 1, '2026-10-10', 'Luz', -23000, 'saida', 'manual:p', 'manual', 1,"
              " '2026-10-16', 1)")
    c.execute("INSERT INTO chaves_quitadas (chave, pendente_id, importacao_id,"
              " valor_original_centavos, estimado_original) VALUES ('k', 1, 1, -21000, 1)")
    c.commit()
    c.close()

    c = conectar(caminho)
    migrar(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == len(MIGRACOES) >= 5
    cols = {r["name"] for r in c.execute("PRAGMA table_info(chaves_quitadas)")}
    assert {"compensado_original", "data_compensacao_original"} <= cols
    q = c.execute("SELECT * FROM chaves_quitadas").fetchone()
    assert (q["chave"], q["valor_original_centavos"], q["compensado_original"],
            q["data_compensacao_original"]) == ("k", -21000, None, None)
    from app.importacao.servico import desfazer_importacao
    desfazer_importacao(c, 1)
    p = c.execute("SELECT * FROM lancamentos WHERE uuid = 'p'").fetchone()
    assert (p["compensado"], p["data_compensacao"], p["importacao_id"], p["valor_centavos"],
            p["estimado"]) == (0, None, None, -21000, 1)
    c.close()


def _gerar_concorrente(caminho) -> list[object]:
    import threading
    from datetime import date

    from app.dominio.recorrentes import criar_recorrente, gerar_recorrentes
    from app.repo.contas import criar_conta

    c = conectar(caminho)
    migrar(c)
    cid = criar_conta(c, "CC", "corrente", saldo_inicial_centavos=0, data_saldo_inicial="2026-10-01")
    for i in range(30):
        criar_recorrente(c, nome=f"R{i}", tipo="saida", dia=5, valor_estimado_centavos=100,
                         conta_id=cid, categoria_id=None)
    c.close()
    resultados: list[object] = []
    barreira = threading.Barrier(4)

    def rodar() -> None:
        con = conectar(caminho)
        barreira.wait()
        try:
            resultados.append(gerar_recorrentes(con, date(2026, 10, 23), 12))
        except Exception as exc:  # noqa: BLE001 - o teste inspeciona a falha
            resultados.append(type(exc).__name__)
        finally:
            con.close()

    threads = [threading.Thread(target=rodar) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return resultados


def test_transacao_serializa_escritores_concorrentes(tmp_path) -> None:
    resultados = _gerar_concorrente(tmp_path / "c.db")
    assert sorted(resultados, key=str) == [0, 0, 0, 360]
    c = conectar(tmp_path / "c.db")
    assert c.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 360
    c.close()


def test_transacao_rollback_em_erro(con: sqlite3.Connection) -> None:
    with pytest.raises(RuntimeError):
        with db.transacao(con):
            con.execute("INSERT INTO configuracoes (chave, valor) VALUES ('x', '1')")
            raise RuntimeError("falha")
    assert con.execute("SELECT COUNT(*) FROM configuracoes WHERE chave = 'x'").fetchone()[0] == 0
    assert not con.in_transaction


def _banco_v5_com_dados(caminho) -> dict[str, int]:
    c = conectar(caminho)
    for numero in range(5):
        c.execute("BEGIN")
        MIGRACOES[numero](c)
        c.execute(f"PRAGMA user_version = {numero + 1}")
        c.execute("COMMIT")
    ids = {r["nome"]: r["id"] for r in c.execute("SELECT id, nome FROM categorias")}
    c.execute("INSERT INTO contas (uuid, nome, tipo) VALUES ('c', 'C', 'corrente')")
    for cat in ("Moradia", "Mercado"):
        c.execute("INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
                  " categoria_id, chave_deduplicacao, origem)"
                  " VALUES (?,1,'2026-10-10','x',-1,'saida',?,?,'manual')", (cat, ids[cat], cat))
        c.execute("INSERT INTO recorrentes (uuid, nome, tipo, dia, valor_estimado_centavos,"
                  " categoria_id, conta_id) VALUES (?, 'R', 'saida', 5, 100, ?, 1)", (cat, ids[cat]))
        c.execute("INSERT INTO regras_categoria (padrao, categoria_id) VALUES (?, ?)", (cat, ids[cat]))
    c.commit()
    c.close()
    return ids


def _contagens(c: sqlite3.Connection) -> tuple[int, ...]:
    return tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                 for t in ("categorias", "lancamentos", "recorrentes", "regras_categoria"))


def test_banco_novo_termina_com_lista_padrao(con: sqlite3.Connection) -> None:
    from app.dominio.taxonomia import PADRAO
    obtido = {(r["tipo"], r["pai"] or r["nome"], r["nome"] if r["pai"] else "")
              for r in con.execute(
                  "SELECT f.tipo, f.nome, p.nome AS pai FROM categorias f"
                  " LEFT JOIN categorias p ON p.id = f.pai_id")}
    esperado = set(PADRAO) | {(t, c, "") for t, c, _ in PADRAO}
    assert obtido == esperado
    assert con.execute("SELECT COUNT(*) FROM categorias").fetchone()[0] == 29 + 142
    nomes = {r["nome"] for r in con.execute("SELECT nome FROM categorias WHERE pai_id IS NULL")}
    assert not ({"Mercado", "Outros", "Lazer", "Assinaturas", "Transporte"} & nomes)
    assert con.execute("SELECT COUNT(*) FROM categorias WHERE tipo IS NULL").fetchone()[0] == 0


def test_migracao_6_preserva_categorias_em_uso(tmp_path) -> None:
    caminho = tmp_path / "v5.db"
    ids = _banco_v5_com_dados(caminho)
    c = conectar(caminho)
    migrar(c)
    assert c.execute("PRAGMA user_version").fetchone()[0] == len(MIGRACOES) >= 6
    assert c.execute("SELECT COUNT(*) FROM lancamentos WHERE categoria_id IS NULL").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM recorrentes WHERE categoria_id IS NULL").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM regras_categoria").fetchone()[0] == 2
    mor = c.execute("SELECT * FROM categorias WHERE nome = 'Moradia'").fetchone()
    assert mor["id"] == ids["Moradia"] and mor["tipo"] == "saida"
    assert c.execute("SELECT COUNT(*) FROM categorias WHERE pai_id = ?", (mor["id"],)).fetchone()[0] == 5
    merc = c.execute("SELECT * FROM categorias WHERE nome = 'Mercado'").fetchone()
    assert merc["id"] == ids["Mercado"] and merc["tipo"] == "saida" and merc["pai_id"] is None
    assert c.execute("SELECT 1 FROM categorias WHERE nome = 'Outros'").fetchone() is None
    antes = _contagens(c)
    migrar(c)
    assert _contagens(c) == antes
    migrar(c)
    assert _contagens(c) == antes
    c.close()


def test_migracao_6_funcao_e_idempotente_em_si(tmp_path) -> None:
    # reaplica só a carga/reconciliação (a parte de dados) sobre um banco já migrado
    caminho = tmp_path / "v5.db"
    _banco_v5_com_dados(caminho)
    c = conectar(caminho)
    migrar(c)
    antes = _contagens(c)
    db._carregar_padrao(c)
    assert _contagens(c) == antes
    c.close()


def test_nome_unico_entre_irmaos_no_banco(con: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO categorias (uuid, nome, nome_chave, tipo) VALUES ('u', 'MORADIA',"
                    " 'moradia', 'saida')")
