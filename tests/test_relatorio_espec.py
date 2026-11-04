import copy
from typing import Any

import pytest

from app.relatorio.espec import ESPEC_EXEMPLO, ErroRelatorio, Espec, validar_espec


def _minima() -> dict[str, Any]:
    return {"titulo": "Gastos", "observacao": "Resumo do mês."}


def test_espec_minima_valida() -> None:
    e = validar_espec(_minima())
    assert isinstance(e, Espec)
    assert e.titulo == "Gastos"
    assert e.periodo is None
    assert e.kpis == () and e.graficos == () and e.tabela is None


def test_exemplo_e_valido() -> None:
    e = validar_espec(ESPEC_EXEMPLO)
    assert e.kpis and e.graficos and e.tabela is not None


def test_largura_padrao_meia() -> None:
    b = _minima()
    b["graficos"] = [{"tipo": "linha", "titulo": "t", "formato": "moeda", "sql": "select 1"}]
    assert validar_espec(b).graficos[0].largura == "meia"


def test_sem_observacao() -> None:
    with pytest.raises(ErroRelatorio, match="observacao"):
        validar_espec({"titulo": "x"})


def _kpi() -> dict[str, Any]:
    return {"rotulo": "r", "formato": "moeda", "sql": "select 1"}


def test_seis_kpis() -> None:
    b = _minima()
    b["kpis"] = [copy.deepcopy(_kpi()) for _ in range(6)]
    with pytest.raises(ErroRelatorio, match="kpis"):
        validar_espec(b)


def test_tipo_invalido() -> None:
    b = _minima()
    b["graficos"] = [{"tipo": "pizza", "titulo": "t", "formato": "moeda", "sql": "select 1"}]
    with pytest.raises(ErroRelatorio, match="tipo"):
        validar_espec(b)


def test_formato_invalido() -> None:
    b = _minima()
    b["kpis"] = [{**_kpi(), "formato": "euro"}]
    with pytest.raises(ErroRelatorio, match="formato"):
        validar_espec(b)


def test_sql_vazio() -> None:
    b = _minima()
    b["kpis"] = [{**_kpi(), "sql": ""}]
    with pytest.raises(ErroRelatorio, match="sql"):
        validar_espec(b)


def test_chave_extra() -> None:
    with pytest.raises(ErroRelatorio, match="html"):
        validar_espec({**_minima(), "html": "<b>"})


def test_periodo_invertido() -> None:
    with pytest.raises(ErroRelatorio, match="periodo"):
        validar_espec({**_minima(), "periodo": {"de": "2026-02-01", "ate": "2026-01-01"}})


def test_periodo_valido() -> None:
    e = validar_espec({**_minima(), "periodo": {"de": "2026-01-01", "ate": "2026-01-31"}})
    assert e.periodo == ("2026-01-01", "2026-01-31")


def test_bruto_nao_dict() -> None:
    with pytest.raises(ErroRelatorio, match="objeto"):
        validar_espec([1, 2])


def test_titulo_longo() -> None:
    with pytest.raises(ErroRelatorio, match="titulo"):
        validar_espec({"titulo": "a" * 121, "observacao": "o"})


def _com_tabela(**extra: Any) -> dict[str, Any]:
    b = _minima()
    b["tabela"] = {"titulo": "T", "sql": "select 1 as a", **extra}
    return b


def test_tabela_formatos_validos_e_padrao_none() -> None:
    assert validar_espec(_com_tabela()).tabela.formatos is None  # type: ignore[union-attr]
    e = validar_espec(_com_tabela(formatos=["texto", "moeda", "inteiro", "pct"]))
    assert e.tabela.formatos == ("texto", "moeda", "inteiro", "pct")  # type: ignore[union-attr]


@pytest.mark.parametrize("ruim", [["dinheiro"], "moeda", [1], ["texto"] * 31, None])
def test_tabela_formatos_invalidos(ruim: Any) -> None:
    b = _com_tabela(formatos=ruim)
    if ruim is None:
        validar_espec(b)  # null equivale a ausente
        return
    with pytest.raises(ErroRelatorio):
        validar_espec(b)


def test_exemplo_declara_moeda_na_tabela() -> None:
    assert "moeda" in (validar_espec(ESPEC_EXEMPLO).tabela.formatos or ())  # type: ignore[union-attr]
