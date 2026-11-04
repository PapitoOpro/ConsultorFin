import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from app.relatorio import montar
from app.relatorio.espec import ErroRelatorio, Espec, Grafico, Kpi, Tabela
from app.relatorio.montar import gerar_relatorio, ler_relatorio, limpar_expirados
from tests._consultor_base import criar_banco

AGORA = datetime(2026, 11, 3, 12, 0, 0)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return criar_banco(tmp_path / "t.db", com_extrato=True)


def _espec(kpis: tuple[Kpi, ...] = (), graficos: tuple[Grafico, ...] = (),
           tabela: Tabela | None = None, periodo: tuple[str, str] | None = None) -> Espec:
    return Espec("Teste", periodo, "obs", kpis, graficos, tabela)


def _graf(sql: str, tipo: str = "rosca") -> Grafico:
    return Grafico(tipo, "G", "moeda", "meia", sql)


def _values(n: int) -> str:
    return " UNION ALL ".join(f"SELECT 'c{i}' AS n, {100 * (i + 1)} AS v" for i in range(n))


def _ler(db: Path, tmp_path: Path, espec: Espec) -> dict[str, Any]:
    r = gerar_relatorio(db, espec, tmp_path / "rel", AGORA)
    ret = ler_relatorio(tmp_path / "rel", r["id"])
    assert ret is not None
    return ret


def test_gera_e_le_retrato(db: Path, tmp_path: Path) -> None:
    pasta = tmp_path / "rel"
    espec = _espec(
        kpis=(Kpi("Qtd", "inteiro", "SELECT COUNT(*) AS n FROM lancamentos", None),),
        graficos=(_graf("SELECT '2026-09-05' AS d, 500 AS v, 'a' AS s UNION ALL SELECT '2026-10', 700, 'a'",
                        "linha"),),
        tabela=Tabela("T", "SELECT 1 AS a, 'x' AS b"),
        periodo=("2026-10-01", "2026-10-31"),
    )
    r = gerar_relatorio(db, espec, pasta, AGORA)
    assert set(r) == {"id", "titulo", "url"}
    assert len(r["id"]) == 32 and r["url"] == f"/relatorio/{r['id']}" and r["titulo"] == "Teste"
    ret = ler_relatorio(pasta, r["id"])
    assert ret is not None
    assert ret["periodo"] == {"de": "2026-10-01", "ate": "2026-10-31"}
    assert ret["gerado_em"] == AGORA.isoformat()
    assert ret["kpis"][0]["valor"] > 0 and ret["kpis"][0]["delta_pct"] is None
    assert ret["graficos"][0]["linhas"] == [
        {"nome": "05/09/2026", "valor": 500, "serie": "a"},
        {"nome": "outubro de 2026", "valor": 700, "serie": "a"},
    ]
    assert ret["tabela"] == {"titulo": "T", "colunas": ["a", "b"], "formatos": ["texto", "texto"],
                              "linhas": [[1, "x"]], "truncado": False}
    assert not list(pasta.glob("*.tmp"))


def test_sem_periodo_nem_tabela(db: Path, tmp_path: Path) -> None:
    ret = _ler(db, tmp_path, _espec())
    assert ret["periodo"] is None and ret["tabela"] is None


def test_delta_pct(db: Path, tmp_path: Path) -> None:
    ret = _ler(db, tmp_path, _espec(kpis=(Kpi("K", "moeda", "SELECT 150", "SELECT 100"),)))
    assert ret["kpis"][0]["valor"] == 150 and ret["kpis"][0]["delta_pct"] == 50.0


def test_delta_anterior_negativo_usa_abs(db: Path, tmp_path: Path) -> None:
    ret = _ler(db, tmp_path, _espec(kpis=(Kpi("K", "moeda", "SELECT -50", "SELECT -100"),)))
    assert ret["kpis"][0]["delta_pct"] == 50.0


@pytest.mark.parametrize("anterior", ["SELECT 0", "SELECT NULL", "SELECT 1 WHERE 1=0"])
def test_delta_none_quando_anterior_zero_ou_vazio(db: Path, tmp_path: Path, anterior: str) -> None:
    ret = _ler(db, tmp_path, _espec(kpis=(Kpi("K", "moeda", "SELECT 150", anterior),)))
    assert ret["kpis"][0]["valor"] == 150 and ret["kpis"][0]["delta_pct"] is None


def test_kpi_valor_atual_none_sem_delta(db: Path, tmp_path: Path) -> None:
    ret = _ler(db, tmp_path, _espec(kpis=(Kpi("K", "moeda", "SELECT 1 WHERE 1=0", "SELECT 100"),)))
    assert ret["kpis"][0]["valor"] is None and ret["kpis"][0]["delta_pct"] is None


def test_kpi_valor_zero(db: Path, tmp_path: Path) -> None:
    ret = _ler(db, tmp_path, _espec(kpis=(Kpi("K", "moeda", "SELECT 0", "SELECT 100"),)))
    assert ret["kpis"][0]["valor"] == 0 and ret["kpis"][0]["delta_pct"] == -100.0


def test_rosca_agrupa_em_outros(db: Path, tmp_path: Path) -> None:
    linhas = _ler(db, tmp_path, _espec(graficos=(_graf(_values(8)),)))["graficos"][0]["linhas"]
    assert len(linhas) == 6 and linhas[-1]["nome"] == "Outros"
    assert [x["valor"] for x in linhas[:5]] == [800, 700, 600, 500, 400]
    assert linhas[-1]["valor"] == 300 + 200 + 100


def test_rosca_com_6_fatias_nao_agrupa(db: Path, tmp_path: Path) -> None:
    linhas = _ler(db, tmp_path, _espec(graficos=(_graf(_values(6)),)))["graficos"][0]["linhas"]
    assert len(linhas) == 6 and all(x["nome"] != "Outros" for x in linhas)


def test_rosca_uma_linha_e_valor_zero(db: Path, tmp_path: Path) -> None:
    ret = _ler(db, tmp_path, _espec(graficos=(_graf("SELECT 'a', 0"), _graf("SELECT 'b', 5"))))
    assert ret["graficos"][0]["linhas"] == [{"nome": "a", "valor": 0, "serie": None}]
    assert ret["graficos"][1]["linhas"][0]["valor"] == 5


def test_barras_nao_agrupa(db: Path, tmp_path: Path) -> None:
    linhas = _ler(db, tmp_path, _espec(graficos=(_graf(_values(8), "barras"),)))["graficos"][0]["linhas"]
    assert len(linhas) == 8


def test_periodo_sem_dados_nao_quebra(db: Path, tmp_path: Path) -> None:
    espec = _espec(
        kpis=(Kpi("K", "moeda", "SELECT SUM(valor_centavos) FROM lancamentos WHERE 1=0", None),),
        graficos=(_graf("SELECT categoria_id, valor_centavos FROM lancamentos WHERE 1=0"),),
        tabela=Tabela("T", "SELECT data FROM lancamentos WHERE 1=0"),
    )
    ret = _ler(db, tmp_path, espec)
    assert ret["kpis"][0]["valor"] is None
    assert ret["graficos"][0]["linhas"] == []
    assert ret["tabela"]["linhas"] == [] and ret["tabela"]["colunas"] == ["data"]


def test_valor_nao_numerico_gera_erro_em_portugues(db: Path, tmp_path: Path) -> None:
    with pytest.raises(ErroRelatorio, match="numérico"):
        gerar_relatorio(db, _espec(graficos=(_graf("SELECT 'a', 'texto'"),)), tmp_path / "r", AGORA)
    with pytest.raises(ErroRelatorio, match="numérico"):
        gerar_relatorio(db, _espec(kpis=(Kpi("K", "moeda", "SELECT 'x'", None),)), tmp_path / "r", AGORA)


def test_grafico_com_uma_coluna_gera_erro(db: Path, tmp_path: Path) -> None:
    with pytest.raises(ErroRelatorio, match="2 colunas"):
        gerar_relatorio(db, _espec(graficos=(_graf("SELECT 'a'"),)), tmp_path / "r", AGORA)


def test_nome_nulo_vira_traco(db: Path, tmp_path: Path) -> None:
    ret = _ler(db, tmp_path, _espec(graficos=(_graf("SELECT NULL, 3", "barras"),)))
    assert ret["graficos"][0]["linhas"][0]["nome"] == "—"


@pytest.mark.parametrize("rid", ["../x", "A" * 32, "a" * 31, "a" * 33, "", "..\\x", "a" * 31 + "/"])
def test_rid_invalido_devolve_none(tmp_path: Path, rid: str) -> None:
    assert ler_relatorio(tmp_path, rid) is None


def test_rid_inexistente_e_json_corrompido(tmp_path: Path) -> None:
    assert ler_relatorio(tmp_path, "a" * 32) is None
    (tmp_path / ("b" * 32 + ".json")).write_text("{quebrado", encoding="utf-8")
    assert ler_relatorio(tmp_path, "b" * 32) is None


def test_escrita_atomica_usa_os_replace(db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[tuple[str, str]] = []
    real = os.replace

    def espia(src: Any, dst: Any) -> None:
        assert Path(src).exists() and not Path(dst).exists()
        chamadas.append((str(src), str(dst)))
        real(src, dst)

    monkeypatch.setattr(montar.os, "replace", espia)
    r = gerar_relatorio(db, _espec(), tmp_path / "r", AGORA)
    assert len(chamadas) == 1 and chamadas[0][1].endswith(f"{r['id']}.json")
    assert chamadas[0][0] != chamadas[0][1]


def test_limpar_expirados(tmp_path: Path) -> None:
    velho, novo = tmp_path / ("a" * 32 + ".json"), tmp_path / ("b" * 32 + ".json")
    for p in (velho, novo):
        p.write_text(json.dumps({}), encoding="utf-8")
    agora = AGORA.timestamp()
    os.utime(velho, (agora - 31 * 86400, agora - 31 * 86400))
    os.utime(novo, (agora - 86400, agora - 86400))
    assert limpar_expirados(tmp_path, AGORA) == 1
    assert not velho.exists() and novo.exists()


def test_limpar_expirados_pasta_inexistente(tmp_path: Path) -> None:
    assert limpar_expirados(tmp_path / "nada", AGORA) == 0


def test_sql_recusado_propaga_erro_relatorio(db: Path, tmp_path: Path) -> None:
    espec = _espec(kpis=(Kpi("K", "moeda", "DELETE FROM lancamentos", None),))
    with pytest.raises(ErroRelatorio):
        gerar_relatorio(db, espec, tmp_path / "r", AGORA)
    assert not (tmp_path / "r").exists() or not list((tmp_path / "r").glob("*.json"))


def _estrito(txt: str) -> Any:
    def recusa(c: str) -> None:
        raise AssertionError(f"constante não-JSON: {c}")
    return json.loads(txt, parse_constant=recusa)


@pytest.mark.parametrize("sql", ["SELECT 1e999", "SELECT -1e999"])
def test_kpi_infinito_gera_erro(db: Path, tmp_path: Path, sql: str) -> None:
    with pytest.raises(ErroRelatorio, match="numérico"):
        gerar_relatorio(db, _espec(kpis=(Kpi("K", "moeda", sql, None),)), tmp_path / "r", AGORA)
    with pytest.raises(ErroRelatorio, match="numérico"):
        gerar_relatorio(db, _espec(kpis=(Kpi("K", "moeda", "SELECT 1", sql),)), tmp_path / "r", AGORA)


def test_grafico_infinito_gera_erro(db: Path, tmp_path: Path) -> None:
    with pytest.raises(ErroRelatorio, match="numérico"):
        gerar_relatorio(db, _espec(graficos=(_graf("SELECT 'a', 1e999"),)), tmp_path / "r", AGORA)


def test_tabela_celulas_infinito_e_binario(db: Path, tmp_path: Path) -> None:
    pasta = tmp_path / "r"
    espec = _espec(tabela=Tabela("T", "SELECT 1e999 AS a, -1e999 AS b, x'ff' AS c, 5 AS d"))
    r = gerar_relatorio(db, espec, pasta, AGORA)
    assert _estrito((pasta / f"{r['id']}.json").read_text(encoding="utf-8"))["tabela"]["linhas"] == [
        [None, None, "<binário>", 5]]


def test_falha_de_serializacao_nao_deixa_arquivo(db: Path, tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    def quebra(*a: Any, **k: Any) -> str:
        raise TypeError("x")
    monkeypatch.setattr(montar.json, "dumps", quebra)
    with pytest.raises(ErroRelatorio):
        gerar_relatorio(db, _espec(), tmp_path / "r", AGORA)
    assert not list((tmp_path / "r").glob("*"))


def _tab(db: Path, tmp_path: Path, sql: str, formatos: tuple[str, ...] | None) -> dict[str, Any]:
    pasta = tmp_path / "r"
    r = gerar_relatorio(db, _espec(tabela=Tabela("T", sql, formatos)), pasta, AGORA)
    return json.loads((pasta / f"{r['id']}.json").read_text(encoding="utf-8"))["tabela"]


def test_tabela_formatos_padrao_texto_por_coluna(db: Path, tmp_path: Path) -> None:
    assert _tab(db, tmp_path, "SELECT 1 AS a, 2 AS b", None)["formatos"] == ["texto", "texto"]


def test_tabela_formatos_explicitos(db: Path, tmp_path: Path) -> None:
    assert _tab(db, tmp_path, "SELECT 'x' AS a, 2 AS b", ("texto", "moeda"))["formatos"] == ["texto", "moeda"]


def test_tabela_formatos_tamanho_errado(db: Path, tmp_path: Path) -> None:
    with pytest.raises(ErroRelatorio, match="uma entrada por coluna"):
        _tab(db, tmp_path, "SELECT 1 AS a, 2 AS b", ("moeda",))


def test_tabela_moeda_com_celula_nao_numerica(db: Path, tmp_path: Path) -> None:
    t = _tab(db, tmp_path, "SELECT 'abc' AS a", ("moeda",))
    assert t["linhas"] == [["abc"]]


def _tab_detalhe(db: Path, tmp_path: Path, sql: str, detalhe: str) -> dict[str, Any]:
    pasta = tmp_path / "r"
    r = gerar_relatorio(db, _espec(tabela=Tabela("T", sql, None, detalhe)), pasta, AGORA)
    return json.loads((pasta / f"{r['id']}.json").read_text(encoding="utf-8"))["tabela"]


def test_tabela_detalhes_agrupados_por_pai(db: Path, tmp_path: Path) -> None:
    t = _tab_detalhe(db, tmp_path, "SELECT 'A' AS c, 30 AS total",
                     "SELECT 'A' AS c, 'a1' AS s, 10 AS t UNION ALL SELECT 'A', 'a2', 20")
    assert t["detalhes"] == {"A": [["a1", 10], ["a2", 20]]}


def test_tabela_sem_detalhe_nao_tem_chave(db: Path, tmp_path: Path) -> None:
    assert "detalhes" not in _tab(db, tmp_path, "SELECT 'A' AS c, 30 AS total", None)


def test_tabela_detalhe_com_colunas_erradas(db: Path, tmp_path: Path) -> None:
    with pytest.raises(ErroRelatorio, match="uma coluna a mais"):
        _tab_detalhe(db, tmp_path, "SELECT 'A' AS c, 30 AS total", "SELECT 'A' AS c, 10 AS t")
