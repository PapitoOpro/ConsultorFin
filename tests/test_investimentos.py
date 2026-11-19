"""Investimentos (extrato Clear): leitor, classificação, importação sem duplicar, tela e consultor.

O .xlsx é gerado aqui, com valores inventados e o mesmo layout do extrato real."""
import io
import json
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.consultor import cli
from app.db import conectar, migrar
from app.dominio import investimentos as dom
from app.importacao.clear import ClearIlegivel, ler_extrato_clear, parece_extrato_clear
from app.main import create_app

# (movimentação, liquidação, descrição, valor, saldo) do mais recente para o mais antigo
LINHAS: list[tuple[date, date, str, float, float]] = [
    (date(2026, 11, 18), date(2026, 11, 19), "AJUSTE DAY-TRADE PR. 18/11/2026 NC. 1 WIN", 32.0, 33.56),
    (date(2026, 11, 18), date(2026, 11, 19), "IRRF S/DAY-TRADE PR. 18/11/2026 NC. 1", -0.31, 1.56),
    (date(2026, 11, 18), date(2026, 11, 19), "LIQUIDO DAS OPERAÇÕES BM&F PR. 18/11/2026 NC. 1", -1.0, 1.87),
    (date(2026, 11, 16), date(2026, 11, 16), "RENDIMENTOS DE CLIENTES FIIX11 S/    4", 0.4, 2.87),
    (date(2026, 11, 9), date(2026, 11, 9), "ESTORNO DEBITO COMPRAS FIIX11   S/ 1,00   05/11/26", 7.16, 2.47),
    (date(2026, 11, 5), date(2026, 11, 9), "OPERAÇÕES EM BOLSA PR 05/11/2026 NOTA Nº 1", -7.16, -4.69),
    (date(2026, 10, 30), date(2026, 10, 30), "Transferência recebida da conta digital", 100.0, 2.47),
]
# saldo coerente: ajusta a coluna de saldo para a cadeia fechar a partir de 0
_saldo = 0.0
_ajustadas: list[tuple[date, date, str, float, float]] = []
for mov, liq, desc, valor, _ in reversed(LINHAS):
    _saldo = round(_saldo + valor, 2)
    _ajustadas.append((mov, liq, desc, valor, _saldo))
LINHAS = list(reversed(_ajustadas))


def xlsx(linhas: list[tuple[date, date, str, float, float]] = LINHAS, conta: str = "13712591",
         saldo_total: float | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws["G2"] = "Extrato da conta"
    ws["B5"] = "NOME INVENTADO"
    ws["G5"] = f"Conta Clear: {conta}"
    ws["B12"] = "Saldo total projetado"
    ws["G12"] = linhas[0][4] if saldo_total is None else saldo_total
    for j, t in enumerate(("Movimentação", "Liquidação", "Lançamento", None, "Valor (R$)", "Saldo (R$)")):
        ws.cell(row=14, column=2 + j, value=t)
    for i, (mov, liq, desc, valor, saldo) in enumerate(linhas):
        for j, v in enumerate((datetime.combine(mov, datetime.min.time()),
                               datetime.combine(liq, datetime.min.time()), desc, None, valor, saldo)):
            ws.cell(row=15 + i, column=2 + j, value=v)
    ws.cell(row=15 + len(linhas) + 1, column=2, value="Lançamentos futuros")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def con() -> Iterator[Any]:
    c = conectar(":memory:")
    migrar(c)
    yield c
    c.close()


def test_leitor_extrai_conta_linhas_cronologicas_e_saldo_informado() -> None:
    e = ler_extrato_clear(xlsx())
    assert e.conta == "13712591" and len(e.linhas) == len(LINHAS)
    assert e.linhas[0].data == "2026-10-30" and e.linhas[0].valor_centavos == 10000
    assert e.linhas[-1].saldo_centavos == e.saldo_informado_centavos == 13109
    assert e.linhas[3].descricao == "RENDIMENTOS DE CLIENTES FIIX11 S/ 4"  # espaços normalizados


def test_leitor_recusa_arquivos_que_nao_sao_extrato_clear() -> None:
    assert parece_extrato_clear(xlsx()) is True
    assert parece_extrato_clear(b"nao e xlsx") is False
    with pytest.raises(ClearIlegivel):
        ler_extrato_clear(b"a;b\n1;2")
    with pytest.raises(ClearIlegivel):
        ler_extrato_clear(xlsx(conta="sem-numero"))


@pytest.mark.parametrize("descricao,valor,esperada", [
    ("Transferência recebida da conta digital", 10000, "aporte"),
    ("Transferência enviada para conta digital", -5000, "resgate"),
    ("AJUSTE DAY-TRADE PR. 16/11/2026 NC. 1 WDO", -2000, "day_trade"),
    ("IRRF S/DAY-TRADE PR. 17/11/2026 NC. 1", -21, "custos"),
    ("LIQUIDO DAS OPERAÇÕES BMF PR. 16/11/2026 NC. 1", -222, "custos"),
    ("OPERAÇÕES EM BOLSA PR 11/11/2026 NOTA Nº 9", -15960, "ativos"),
    ("ESTORNO DEBITO COMPRAS FIIX11 S/ 1,00 05/11/26", 716, "ativos"),
    ("RENDIMENTOS DE CLIENTES DIVD11 S/ 1", 16, "proventos"),
    ("DIFERENÇA DE RECOMPRA - COMPRADOR D7 OUTRO", 285, "outros"),
    ("ALGO DESCONHECIDO", 1, "outros"),
])
def test_classificacao(descricao: str, valor: int, esperada: str) -> None:
    assert dom.classificar(descricao, valor) == esperada


def test_importacao_nao_duplica_e_resume_por_mes(con: Any) -> None:
    e = ler_extrato_clear(xlsx())
    r1 = dom.importar_extrato(con, e, "a.xlsx", datetime(2026, 11, 19))
    assert (r1.novos, r1.ignorados, r1.aviso) == (len(LINHAS), 0, None)
    r2 = dom.importar_extrato(con, e, "a.xlsx", datetime(2026, 11, 19))
    assert (r2.novos, r2.ignorados) == (0, len(LINHAS))
    assert con.execute("SELECT COUNT(*) FROM inv_lancamentos").fetchone()[0] == len(LINHAS)

    res = dom.resumo(con)
    assert [m.mes for m in res.meses] == ["2026-10", "2026-11"]
    nov = res.meses[1]
    assert nov.day_trade_bruto == 3200 and nov.custos == -131
    assert nov.day_trade_liquido == 3069 and nov.proventos == 40
    assert nov.ativos == 0  # compra de 7,16 e o estorno dela
    assert nov.aportes_liquidos_acum == 10000 and nov.resultado_acum == 3109
    assert res.saldo_caixa == 13109 and res.ultima_data == "2026-11-18"


def test_importacao_sobreposta_so_acrescenta_linhas_novas(con: Any) -> None:
    dom.importar_extrato(con, ler_extrato_clear(xlsx(LINHAS[2:])), "a.xlsx", datetime(2026, 11, 10))
    r = dom.importar_extrato(con, ler_extrato_clear(xlsx()), "b.xlsx", datetime(2026, 11, 19))
    assert r.novos == 2 and r.ignorados == len(LINHAS) - 2


def test_aviso_quando_saldo_do_arquivo_nao_confere(con: Any) -> None:
    ruim = list(LINHAS)
    mov, liq, desc, valor, _ = ruim[1]
    ruim[1] = (mov, liq, desc, valor, 999.0)
    r = dom.importar_extrato(con, ler_extrato_clear(xlsx(ruim)), "r.xlsx", datetime(2026, 11, 19))
    assert r.aviso is not None and "não bate" in r.aviso


def test_modulo_nao_toca_nas_contas_nem_lancamentos(con: Any) -> None:
    dom.importar_extrato(con, ler_extrato_clear(xlsx()), "a.xlsx", datetime(2026, 11, 19))
    assert con.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM contas").fetchone()[0] == 0


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "t.db"


@pytest.fixture
def cliente(db: Path) -> Iterator[TestClient]:
    with TestClient(create_app(db)) as c:
        yield c


def _enviar(cliente: TestClient, conteudo: bytes, nome: str = "extrato.xlsx") -> Any:
    return cliente.post("/investimentos/importar", files={"arquivo": (nome, conteudo)})


def test_tela_vazia_e_importacao_pela_web(cliente: TestClient) -> None:
    r = cliente.get("/investimentos")
    assert r.status_code == 200 and "Nenhum extrato importado" in r.text
    r = _enviar(cliente, xlsx())
    assert r.status_code == 200
    assert "Extrato importado" in r.text and "Day-trade líquido" in r.text
    assert "a custo" in r.text and "Resultado day-trade" in r.text
    assert "NOME INVENTADO" not in r.text
    r = _enviar(cliente, xlsx())
    assert f"0 novos, {len(LINHAS)} já existentes" in r.text


def test_filtro_por_categoria_e_mes(cliente: TestClient) -> None:
    _enviar(cliente, xlsx())
    r = cliente.get("/investimentos?mes=2026-11&categoria=proventos")
    assert "RENDIMENTOS DE CLIENTES" in r.text and "AJUSTE DAY-TRADE" not in r.text
    assert cliente.get("/investimentos?mes=lixo&categoria=xx").status_code == 200


@pytest.mark.parametrize("nome,conteudo", [("e.csv", b"a;b"), ("e.xlsx", b"nao e planilha"),
                                           ("e.xlsx", xlsx(conta="sem-numero"))])
def test_envio_invalido_nao_grava(cliente: TestClient, db: Path, nome: str, conteudo: bytes) -> None:
    r = _enviar(cliente, conteudo, nome)
    assert r.status_code == 400 and "Traceback" not in r.text
    con = conectar(db)
    try:
        assert con.execute("SELECT COUNT(*) FROM inv_lancamentos").fetchone()[0] == 0
    finally:
        con.close()


def test_importar_exibe_aba_investimentos(cliente: TestClient) -> None:
    assert 'action="/investimentos/importar"' in cliente.get("/importar").text


def test_consultor_investimentos(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    con = conectar(db)
    try:
        migrar(con)
        dom.importar_extrato(con, ler_extrato_clear(xlsx()), "a.xlsx", datetime(2026, 11, 19))
        con.commit()
    finally:
        con.close()

    def rodar(*args: str) -> tuple[int, dict[str, Any]]:
        capsys.readouterr()
        codigo = cli.main([*args, "--db", str(db), "--hoje", "2026-11-19"])
        return codigo, json.loads(capsys.readouterr().out)

    codigo, r = rodar("investimentos")
    assert codigo == 0 and r["mes"] == "2026-11" and "a custo" in r["nota"]
    assert r["mes_atual"]["day_trade_liquido_centavos"] == 3069 and len(r["meses"]) == 2
    codigo, r = rodar("investimentos", "--mes", "2026-10")
    assert r["mes"] == "2026-10" and r["mes_atual"]["aportes_centavos"] == 10000
    codigo, r = rodar("investimentos", "--mes", "10/2026")
    assert codigo != 0 and "erro" in r


# ---- posição de custódia da B3 --------------------------------------------------------------
from app.importacao.b3_posicao import (  # noqa: E402
    PosicaoIlegivel, data_do_nome, ler_posicao_b3, parece_posicao_b3)

ABAS_B3 = {
    "Acoes": (["Produto", "Instituição", "Conta", "Código de Negociação", "Tipo", "Quantidade",
               "Preço de Fechamento", "Valor Atualizado"],
              [("ACME3 - ACME INVENTADA", "X", "13712591", "ACME3", "ON", 3, 13.15, 39.45)]),
    "ETF": (["Produto", "Instituição", "Conta", "Código de Negociação", "Tipo", "Quantidade",
             "Preço de Fechamento", "Valor Atualizado"],
            [("IDXX11 - INDICE INVENTADO", "X", "13712591", "IDXX11", "Ações", 3, 57.8, 173.4)]),
}


def xlsx_b3(abas: dict[str, Any] = ABAS_B3, conta: str | None = None) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for titulo, (cab, linhas) in abas.items():
        ws = wb.create_sheet(titulo)
        ws.append(cab)
        for l in linhas:
            ws.append([conta if (conta is not None and i == 2) else v for i, v in enumerate(l)])
        ws.append([])
        ws.append([""] * (len(cab) - 1) + ["Total"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_leitor_b3_le_todas_as_abas_e_data_do_nome() -> None:
    p = ler_posicao_b3(xlsx_b3(), "posicao-2026-11-19-16-25-15.xlsx")
    assert p.data == "2026-11-19" and p.conta == "13712591"
    assert [(a.codigo, a.quantidade, a.valor_centavos) for a in p.ativos] == [
        ("ACME3", 3.0, 3945), ("IDXX11", 3.0, 17340)]
    assert p.ativos[0].classe == "Acoes" and p.ativos[1].preco_centavos == 5780
    assert data_do_nome("qualquer.xlsx") is None and data_do_nome("posicao-2026-13-40.xlsx") is None


def test_b3_nao_confunde_com_extrato_clear() -> None:
    assert parece_posicao_b3(xlsx_b3()) and not parece_extrato_clear(xlsx_b3())
    assert parece_extrato_clear(xlsx()) and not parece_posicao_b3(xlsx())
    with pytest.raises(PosicaoIlegivel):
        ler_posicao_b3(xlsx_b3(conta="abc"), "posicao-2026-11-19.xlsx")


def test_importar_posicao_substitui_a_mesma_data_e_guarda_historico(con: Any) -> None:
    p = ler_posicao_b3(xlsx_b3(), "posicao-2026-11-19-1.xlsx")
    assert dom.importar_posicao(con, p, datetime(2026, 11, 19)).total_centavos == 21285
    dom.importar_posicao(con, p, datetime(2026, 11, 19, 18))
    assert con.execute("SELECT COUNT(*) FROM inv_posicoes").fetchone()[0] == 2
    outra = dict(ABAS_B3)
    outra["ETF"] = (ABAS_B3["ETF"][0], [("IDXX11 - X", "X", "13712591", "IDXX11", "A", 4, 60.0, 240.0)])
    dom.importar_posicao(con, ler_posicao_b3(xlsx_b3(outra), "posicao-2026-11-20-1.xlsx"),
                         datetime(2026, 11, 20))
    c = dom.carteira_atual(con)
    assert c is not None and c.data == "2026-11-20" and c.total_centavos == 3945 + 24000
    assert c.ativos[0]["codigo"] == "IDXX11"  # maior valor primeiro
    assert con.execute("SELECT COUNT(DISTINCT data) FROM inv_posicoes").fetchone()[0] == 2


def test_web_importa_posicao_e_mostra_carteira(cliente: TestClient) -> None:
    r = cliente.post("/investimentos/importar",
                     files={"arquivo": ("posicao-2026-11-19-16-25-15.xlsx", xlsx_b3())})
    assert r.status_code == 200 and "Posição de 19/11/2026 importada" in r.text
    assert "Carteira atual" in r.text and "ACME3" in r.text and "R$ 212,85" in r.text
    _enviar(cliente, xlsx())
    assert "Aplicado em ativos, a custo" in cliente.get("/investimentos").text


def test_web_arquivo_xlsx_desconhecido(cliente: TestClient) -> None:
    wb = Workbook()
    wb.active["A1"] = "qualquer"
    buf = io.BytesIO()
    wb.save(buf)
    r = cliente.post("/investimentos/importar", files={"arquivo": ("x.xlsx", buf.getvalue())})
    assert r.status_code == 400 and "Não reconheci" in r.text


def test_consultor_traz_carteira(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    con = conectar(db)
    try:
        migrar(con)
        dom.importar_posicao(con, ler_posicao_b3(xlsx_b3(), "posicao-2026-11-19-1.xlsx"),
                             datetime(2026, 11, 19))
        con.commit()
    finally:
        con.close()
    capsys.readouterr()
    assert cli.main(["investimentos", "--db", str(db), "--hoje", "2026-11-19"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["sem_extrato"] is True and r["carteira"]["total_centavos"] == 21285
    assert r["carteira"]["ativos"][0]["codigo"] == "IDXX11"
