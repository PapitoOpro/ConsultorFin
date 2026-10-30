"""Domínio de importação de holerite (dados inventados)."""
import sqlite3
from datetime import date

import pytest

from app.dominio import holerites as dom
from app.dominio.casamento import confirmar_casamento, propor_casamentos
from app.dominio.categorias import consumo_do_mes, criar_categoria, criar_regra, despesas_do_mes
from app.dominio.projecao import projetar
from app.dominio.saldo import saldo_atual
from app.importacao.holerite.modelo import HoleriteLido, ItemHolerite
from app.repo import configuracoes
from app.repo import contas as repo_contas
from app.repo import holerites as repo

HOJE = date(2026, 10, 30)


def _h(tipo: str = "mensal", comp: str = "2026-09", liquido: int = 380000,
       itens: list[ItemHolerite] | None = None) -> HoleriteLido:
    its = itens if itens is not None else [
        ItemHolerite("111", "SALARIO FICTICIO", "22,00", "provento", liquido + 120000),
        ItemHolerite("211", "IMPOSTO TESTE", "7,50", "desconto", 120000)]
    return HoleriteLido(tipo, comp, "ACME", liquido + 120000, 120000, liquido, its)


def _conta(con: sqlite3.Connection, nome: str = "Corrente") -> int:
    return repo_contas.criar_conta(con, nome, "corrente")


def _credito(con: sqlite3.Connection, conta: int, data: str, valor: int,
             chave: str = "c1") -> int:
    cur = con.execute(
        "INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
        " chave_deduplicacao, origem, compensado, data_compensacao)"
        " VALUES (?,?,?,?,?,'entrada',?,'importado',1,?)",
        (chave, conta, data, "CREDITO SAL", valor, chave, data))
    return int(cur.lastrowid)


def _imp(con: sqlite3.Connection, h: HoleriteLido, conta: int, lanc: int | None = None,
         hash_: str = "h1", substituir: bool = False,
         cats: list[int | None] | None = None, manter: bool = True) -> dom.ResumoHolerite:
    c = cats if cats is not None else [None] * len(h.itens)
    return dom.importar_holerite(con, h, hash_, "a.pdf", conta, HOJE, c, lanc,
                                 substituir=substituir, manter_vinculo=manter)


def _contagem(con: sqlite3.Connection) -> int:
    return int(con.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0])


def test_previa_nova_sem_credito_preve_data(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    p = dom.preparar_previa(con, _h("adiantamento", "2026-10"), "h1", conta, HOJE)
    assert (p.situacao, p.data_prevista, p.creditos_candidatos) == ("novo", "2026-10-20", [])
    p = dom.preparar_previa(con, _h("mensal", "2026-09"), "h1", conta, HOJE)
    assert p.data_prevista == "2026-10-02"


def test_previa_acha_credito_exato_na_janela(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    lid = _credito(con, conta, "2026-10-02", 380000)
    p = dom.preparar_previa(con, _h(), "h1", conta, HOJE)
    assert [c.lancamento_id for c in p.creditos_candidatos] == [lid]
    assert p.creditos_candidatos[0].valor_centavos == 380000


def test_previa_ignora_credito_de_outro_valor_e_fora_da_janela(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    _credito(con, conta, "2026-10-02", 380001, "a")
    _credito(con, conta, "2026-10-17", 380000, "b")  # 46 dias após 2026-09-01
    _credito(con, conta, "2026-08-31", 380000, "c")
    assert dom.preparar_previa(con, _h(), "h1", conta, HOJE).creditos_candidatos == []


def test_importar_sem_credito_cria_entrada_prevista_estimada(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    sal = criar_categoria(con, "Salário", "entrada")
    r = _imp(con, _h(), conta)
    assert not r.vinculado
    row = con.execute("SELECT * FROM lancamentos WHERE id = ?", (r.lancamento_id,)).fetchone()
    assert (row["compensado"], row["origem"], row["estimado"], row["valor_centavos"],
            row["categoria_id"], row["tipo"], row["data"]) == (
        0, "holerite", 1, 380000, sal, "entrada", "2026-10-02")
    assert repo.obter(con, r.holerite_id)["lancamento_id"] == r.lancamento_id
    assert r.itens == 2 and len(repo.itens(con, r.holerite_id)) == 2


def test_importar_com_credito_vincula_sem_criar_linha(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    sal = criar_categoria(con, "Salário", "entrada")
    lid = _credito(con, conta, "2026-10-02", 380000)
    antes = _contagem(con)
    r = _imp(con, _h(), conta, lid)
    assert r.vinculado and r.lancamento_id == lid and _contagem(con) == antes
    assert repo.obter(con, r.holerite_id)["lancamento_id"] == lid
    assert con.execute("SELECT categoria_id FROM lancamentos WHERE id = ?",
                       (lid,)).fetchone()[0] == sal


def test_credito_ja_vinculado_nao_reaproveita(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    lid = _credito(con, conta, "2026-10-02", 380000)
    _imp(con, _h(), conta, lid)
    outro = _h("adiantamento", "2026-10", liquido=380000)
    assert dom.preparar_previa(con, outro, "h2", conta, HOJE).creditos_candidatos == []
    with pytest.raises(dom.ErroHolerite):
        _imp(con, outro, conta, lid, hash_="h2")


def test_credito_fora_dos_candidatos_e_rejeitado_e_nada_grava(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    lid = _credito(con, conta, "2026-10-02", 999)
    antes = _contagem(con)
    with pytest.raises(dom.ErroHolerite):
        _imp(con, _h(), conta, lid)
    assert _contagem(con) == antes and repo.listar(con) == []


def test_mesmo_hash_levanta_ja_importado(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    _imp(con, _h(), conta)
    with pytest.raises(dom.HoleriteJaImportado):
        _imp(con, _h(), conta)
    assert dom.preparar_previa(con, _h(), "h1", conta, HOJE).situacao == "identico"


def test_hash_diferente_exige_substituir(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    _imp(con, _h(), conta)
    assert dom.preparar_previa(con, _h(), "h2", conta, HOJE).situacao == "substitui"
    with pytest.raises(dom.HoleriteDiferenteExistente):
        _imp(con, _h(), conta, hash_="h2")


def test_substituir_refaz_itens_e_mantem_categorias_por_descricao(
        con: sqlite3.Connection) -> None:
    conta = _conta(con)
    cat = criar_categoria(con, "Impostos Inventados")
    r = _imp(con, _h(), conta, cats=[None, cat])
    novo = _h(itens=[ItemHolerite("211", "IMPOSTO TESTE", "7,50", "desconto", 120000),
                     ItemHolerite("999", "OUTRO ITEM", None, "provento", 500000)])
    r2 = _imp(con, novo, conta, hash_="h2", substituir=True, cats=[None, None])
    assert r2.holerite_id == r.holerite_id
    its = repo.itens(con, r.holerite_id)
    assert [(i["descricao"], i["categoria_id"]) for i in its] == [
        ("IMPOSTO TESTE", cat), ("OUTRO ITEM", None)]
    assert repo.obter(con, r.holerite_id)["arquivo_hash"] == "h2"


def test_substituir_mantem_vinculo_e_atualiza_prevista(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    # prevista criada pelo holerite: atualiza valor sem duplicar
    r = _imp(con, _h(), conta)
    antes = _contagem(con)
    novo = _h(liquido=150000)
    r2 = _imp(con, novo, conta, hash_="h2", substituir=True)
    assert r2.lancamento_id == r.lancamento_id and _contagem(con) == antes
    row = con.execute("SELECT * FROM lancamentos WHERE id = ?", (r.lancamento_id,)).fetchone()
    assert row["valor_centavos"] == 150000 and row["compensado"] == 0
    # crédito real vinculado: o próprio crédito segue candidato e o vínculo é mantido
    c2 = _conta(con, "Outra")
    lid = _credito(con, c2, "2026-10-02", 380000, "x")
    ad = _h("adiantamento", "2026-10")
    r3 = _imp(con, ad, c2, lid)
    p = dom.preparar_previa(con, ad, "h9", c2, HOJE)
    assert p.situacao == "substitui" and [c.lancamento_id for c in p.creditos_candidatos] == [lid]
    r4 = _imp(con, ad, c2, lid, hash_="h9", substituir=True)
    assert r4.holerite_id == r3.holerite_id and r4.vinculado
    assert repo.obter(con, r4.holerite_id)["lancamento_id"] == lid
    # prevista antiga + escolha de um crédito: a prevista é removida, vínculo vai ao crédito
    lid2 = _credito(con, conta, "2026-10-02", 150000, "y")
    r5 = _imp(con, novo, conta, lid2, hash_="h3", substituir=True)
    assert r5.lancamento_id == lid2
    assert con.execute("SELECT 1 FROM lancamentos WHERE id = ?",
                       (r.lancamento_id,)).fetchone() is None


def test_itens_nao_entram_em_despesas_nem_consumo_nem_saldo(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    lid = _credito(con, conta, "2026-10-02", 380000)

    def medir() -> tuple[int, int, int, int, int]:
        return (despesas_do_mes(con, 2026, 10), consumo_do_mes(con, 2026, 10),
                despesas_do_mes(con, 2026, 9), consumo_do_mes(con, 2026, 9),
                saldo_atual(con, conta, HOJE))

    antes = medir()
    _imp(con, _h(), conta, lid)
    _imp(con, _h("adiantamento", "2026-10", liquido=140000), conta)
    assert medir() == antes
    assert antes[4] == 380000


def test_importar_e_atomico(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    antes = _contagem(con)
    # falha no meio da gravação, dentro de transação aberta: o savepoint desfaz tudo
    h = _h()
    h.itens[1] = ItemHolerite("211", "X", None, "natureza_invalida", 1)
    con.execute("BEGIN")
    with pytest.raises(sqlite3.Error):
        _imp(con, h, conta)
    con.execute("ROLLBACK")
    assert _contagem(con) == antes and repo.listar(con) == []


def test_dias_previstos_configuraveis(con: sqlite3.Connection) -> None:
    configuracoes.definir(con, "holerite_dia_adiantamento", "31")
    configuracoes.definir(con, "holerite_dia_mensal", "5")
    assert dom.data_prevista(con, "adiantamento", "2026-02") == "2026-02-28"
    assert dom.data_prevista(con, "mensal", "2026-12") == "2027-01-05"
    assert dom.data_prevista(con, "mensal", "2026-09") == "2026-10-05"


def test_sugere_categoria_por_regra_e_detalhe(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    cat = criar_categoria(con, "Impostos Inventados")
    criar_regra(con, "IMPOSTO", cat)
    p = dom.preparar_previa(con, _h(), "h1", conta, HOJE)
    assert p.categorias_sugeridas == [None, cat]
    r = _imp(con, _h(), conta, cats=p.categorias_sugeridas)
    hol, itens = dom.detalhe(con, r.holerite_id)
    assert len(itens) == 2 and hol["liquido_centavos"] == 380000
    dom.alterar_categoria_item(con, int(itens[0]["id"]), cat)
    assert repo.obter_item(con, int(itens[0]["id"]))["categoria_id"] == cat
    with pytest.raises(dom.ErroHolerite):
        dom.detalhe(con, 9999)


def test_credito_de_outra_conta_e_rejeitado(con: sqlite3.Connection) -> None:
    conta, outra = _conta(con), _conta(con, "Outra")
    lid = _credito(con, outra, "2026-10-02", 380000)
    with pytest.raises(dom.ErroHolerite):
        _imp(con, _h(), conta, lid)
    assert repo.listar(con) == []


def test_previa_substitui_com_prevista_antiga(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    _imp(con, _h(), conta)
    lid = _credito(con, conta, "2026-10-02", 380000)
    p = dom.preparar_previa(con, _h(), "h2", conta, HOJE)
    assert p.situacao == "substitui" and p.vinculo_atual is None
    assert [c.lancamento_id for c in p.creditos_candidatos] == [lid]


def test_substituir_prevista_ja_casada_mantem_vinculo_sem_duplicar(
        con: sqlite3.Connection) -> None:
    conta = _conta(con)
    r = _imp(con, _h(), conta)
    linha = _credito(con, conta, "2026-10-02", 380000)
    con.commit()
    confirmar_casamento(con, r.lancamento_id, linha)  # quita a prevista (origem segue holerite)
    antes = _contagem(con)
    p = dom.preparar_previa(con, _h(), "h2", conta, HOJE)
    assert p.vinculo_atual == r.lancamento_id
    assert [c.lancamento_id for c in p.creditos_candidatos] == [r.lancamento_id]
    r2 = _imp(con, _h(), conta, None, hash_="h2", substituir=True)
    assert r2.lancamento_id == r.lancamento_id and r2.vinculado and _contagem(con) == antes
    assert repo.obter(con, r.holerite_id)["lancamento_id"] == r.lancamento_id


def test_substituir_sem_id_mantem_credito_real_se_valor_igual(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    lid = _credito(con, conta, "2026-10-02", 380000)
    r = _imp(con, _h(), conta, lid)
    antes = _contagem(con)
    p = dom.preparar_previa(con, _h(), "h2", conta, HOJE)
    assert p.vinculo_atual == lid
    r2 = _imp(con, _h(), conta, None, hash_="h2", substituir=True)
    assert r2.lancamento_id == lid and r2.vinculado and _contagem(con) == antes
    # forçar "sem vínculo": cria prevista nova e solta o crédito
    r3 = _imp(con, _h(), conta, None, hash_="h3", substituir=True, manter=False)
    assert not r3.vinculado and r3.lancamento_id != lid and _contagem(con) == antes + 1


def test_substituir_com_liquido_alterado_solta_vinculo_e_cria_prevista(
        con: sqlite3.Connection) -> None:
    conta = _conta(con)
    lid = _credito(con, conta, "2026-10-02", 380000)
    r = _imp(con, _h(), conta, lid)
    novo = _h(liquido=150000)
    p = dom.preparar_previa(con, novo, "h2", conta, HOJE)
    assert p.vinculo_atual is None and p.creditos_candidatos == []
    r2 = _imp(con, novo, conta, None, hash_="h2", substituir=True)
    assert not r2.vinculado and r2.lancamento_id != lid
    linha = con.execute("SELECT * FROM lancamentos WHERE id = ?", (r2.lancamento_id,)).fetchone()
    assert (linha["origem"], linha["compensado"], linha["valor_centavos"]) == ("holerite", 0, 150000)
    assert con.execute("SELECT COUNT(*) FROM lancamentos WHERE origem = 'holerite'"
                       ).fetchone()[0] == 1
    assert repo.obter(con, r.holerite_id)["lancamento_id"] == r2.lancamento_id


def test_prevista_casada_com_valor_alterado_solta_vinculo(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    r = _imp(con, _h(), conta)
    linha = _credito(con, conta, "2026-10-02", 380000)
    con.commit()
    confirmar_casamento(con, r.lancamento_id, linha)
    r2 = _imp(con, _h(liquido=150000), conta, None, hash_="h2", substituir=True)
    assert r2.lancamento_id != r.lancamento_id and not r2.vinculado


def _holerite_lanc(con: sqlite3.Connection) -> int:
    return int(con.execute("SELECT COUNT(*) FROM lancamentos WHERE origem = 'holerite'"
                           ).fetchone()[0])


def test_holerite_antigo_sem_credito_nao_cria_prevista(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    antes = projetar(con, HOJE, 3)
    p = dom.preparar_previa(con, _h(comp="2026-01"), "h1", conta, HOJE)
    assert p.creditos_candidatos == [] and p.sem_prevista
    r = _imp(con, _h(comp="2026-01"), conta)
    assert r.lancamento_id is None and not r.vinculado
    assert _contagem(con) == 0 and repo.obter(con, r.holerite_id)["lancamento_id"] is None
    assert projetar(con, HOJE, 3) == antes
    [linha] = dom.listar_por_ano(con, 2026)
    assert (linha.credito, linha.credito_data) == ("sem", None)


def test_holerite_recente_ainda_cria_prevista(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    # mensal 2026-09: prevista em 2026-10-02; 2026-10-02 + 45 dias >= HOJE
    assert not dom.preparar_previa(con, _h(), "h1", conta, HOJE).sem_prevista
    r = _imp(con, _h(), conta)
    assert r.lancamento_id is not None and _holerite_lanc(con) == 1


def test_liquido_zero_nunca_cria_lancamento(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    h = _h(liquido=0)
    assert dom.preparar_previa(con, h, "h1", conta, HOJE).sem_prevista
    r = _imp(con, h, conta)
    assert r.lancamento_id is None and _contagem(con) == 0


def test_substituir_holerite_antigo_sem_vinculo_e_depois_vincular(
        con: sqlite3.Connection) -> None:
    conta = _conta(con)
    antigo = _h(comp="2026-01")
    r = _imp(con, antigo, conta)
    r2 = _imp(con, antigo, conta, hash_="h2", substituir=True)
    assert r2.holerite_id == r.holerite_id and r2.lancamento_id is None and _contagem(con) == 0
    # o crédito aparece depois (extrato importado): a substituição vincula
    lid = _credito(con, conta, "2026-02-02", 380000)
    p = dom.preparar_previa(con, antigo, "h3", conta, HOJE)
    assert [c.lancamento_id for c in p.creditos_candidatos] == [lid]
    r3 = _imp(con, antigo, conta, lid, hash_="h3", substituir=True)
    assert r3.vinculado and repo.obter(con, r.holerite_id)["lancamento_id"] == lid


def test_substituir_prevista_que_ficou_vencida_remove_a_prevista(
        con: sqlite3.Connection) -> None:
    conta = _conta(con)
    r = dom.importar_holerite(con, _h(), "h1", None, conta, date(2026, 10, 1), [None, None],
                              None)
    assert r.lancamento_id is not None
    r2 = _imp(con, _h(), conta, hash_="h2", substituir=True, manter=False)
    assert r2.lancamento_id is not None  # 2026-10-02 ainda dentro da janela em HOJE
    tarde = date(2026, 12, 31)
    r3 = dom.importar_holerite(con, _h(), "h3", None, conta, tarde, [None, None], None,
                               substituir=True, manter_vinculo=False)
    assert r3.lancamento_id is None and _contagem(con) == 0


def _pendente(con: sqlite3.Connection, conta: int, data: str, valor: int, chave: str = "m1",
              origem: str = "manual") -> int:
    cur = con.execute(
        "INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos, tipo,"
        " chave_deduplicacao, origem, compensado, estimado) VALUES (?,?,?,'SALARIO',?,"
        " 'entrada',?,?,0,1)", (chave, conta, data, valor, chave, origem))
    return int(cur.lastrowid)


def _entradas(con: sqlite3.Connection) -> int:
    return int(con.execute("SELECT COUNT(*) FROM lancamentos WHERE tipo = 'entrada'"
                           ).fetchone()[0])


def test_salario_recorrente_pendente_e_candidato_e_recebe_o_vinculo(
        con: sqlite3.Connection) -> None:
    conta = _conta(con)
    pid = _pendente(con, conta, "2026-10-03", 379000)  # dentro de max(R$ 2; 2%)
    _pendente(con, conta, "2026-10-03", 120000, "longe")  # valor fora da tolerância
    _pendente(con, conta, "2026-11-20", 380000, "tarde")  # fora da janela
    p = dom.preparar_previa(con, _h(), "h1", conta, HOJE)
    assert [(c.lancamento_id, c.pendente) for c in p.creditos_candidatos] == [(pid, True)]
    antes = _entradas(con)
    r = _imp(con, _h(), conta, pid)
    assert r.lancamento_id == pid and not r.vinculado
    assert _entradas(con) == antes and _holerite_lanc(con) == 0  # uma entrada só
    row = con.execute("SELECT * FROM lancamentos WHERE id = ?", (pid,)).fetchone()
    assert (row["valor_centavos"], row["data"], row["compensado"]) == (379000, "2026-10-03", 0)
    # substituição: o pendente vinculado segue candidato e o vínculo é mantido
    p2 = dom.preparar_previa(con, _h(), "h2", conta, HOJE)
    assert p2.vinculo_atual == pid
    assert _imp(con, _h(), conta, None, hash_="h2", substituir=True).lancamento_id == pid


def test_prevista_de_outro_holerite_nao_e_candidata(con: sqlite3.Connection) -> None:
    conta = _conta(con)
    _pendente(con, conta, "2026-10-02", 380000, origem="holerite")
    assert dom.preparar_previa(con, _h(), "h1", conta, HOJE).creditos_candidatos == []


def test_casamento_de_linha_vinculada_a_holerite_reaponta_o_holerite(
        con: sqlite3.Connection) -> None:
    conta = _conta(con)
    lid = _credito(con, conta, "2026-10-02", 380000)
    r = _imp(con, _h(), conta, lid)
    pid = _pendente(con, conta, "2026-10-01", 380000)  # salário recorrente lançado à parte
    con.commit()
    assert [(c.pendente_id, c.lancamento_id) for c in propor_casamentos(con, conta)] == [
        (pid, lid)]
    confirmar_casamento(con, pid, lid)
    assert repo.obter(con, r.holerite_id)["lancamento_id"] == pid
    p = con.execute("SELECT * FROM lancamentos WHERE id = ?", (pid,)).fetchone()
    assert (p["compensado"], p["data_compensacao"], p["valor_centavos"]) == (
        1, "2026-10-02", 380000)
    assert con.execute("SELECT 1 FROM lancamentos WHERE id = ?", (lid,)).fetchone() is None
    assert _entradas(con) == 1


def test_casamento_entre_dois_lados_de_holerites_nao_e_proposto_nem_aceito(
        con: sqlite3.Connection) -> None:
    conta = _conta(con)
    lid = _credito(con, conta, "2026-10-15", 380000)
    _imp(con, _h(), conta, lid)  # mensal 2026-09 vinculado ao crédito do extrato
    prevista = _imp(con, _h("adiantamento", "2026-10"), conta, hash_="h2").lancamento_id
    assert prevista is not None  # prevista em 2026-10-20, a 5 dias da linha
    con.commit()
    assert propor_casamentos(con, conta) == []
    with pytest.raises(ValueError, match="holerite"):
        confirmar_casamento(con, prevista, lid)
    assert con.execute("SELECT 1 FROM lancamentos WHERE id = ?", (lid,)).fetchone() is not None
