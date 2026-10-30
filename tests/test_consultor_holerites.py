"""Consultor: comandos somente-leitura de holerites e recusa de PDF (dados inventados)."""
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.consultor import cli
from app.db import conectar, migrar
from app.dominio.holerites import importar_holerite
from app.importacao.holerite.modelo import HoleriteLido, ItemHolerite
from app.repo import contas

HOJE = date(2026, 10, 27)


def _hol(tipo: str, comp: str, pensao: int, imposto: int) -> HoleriteLido:
    bruto = 500000
    its = [ItemHolerite("1", "SALARIO FICTICIO", "21,00", "provento", bruto),
           ItemHolerite("2", "PENSÃO INVENTADA", None, "desconto", pensao),
           ItemHolerite("3", "IMPOSTO TESTE", "7,50", "desconto", imposto)]
    desc = pensao + imposto
    return HoleriteLido(tipo, comp, "ACME", bruto, desc, bruto - desc, its)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    caminho = tmp_path / "t.db"
    con = conectar(caminho)
    try:
        migrar(con)
        conta = contas.criar_conta(con, "Corrente Teste", "corrente")
        importar_holerite(con, _hol("adiantamento", "2026-10", 30000, 10000), "h1", "x", conta,
                          HOJE, [None] * 3, None)
        importar_holerite(con, _hol("mensal", "2026-09", 60000, 20000), "h2", "x", conta,
                          HOJE, [None] * 3, None)
        con.execute("UPDATE lancamentos SET compensado = 1, data_compensacao = data"
                    " WHERE descricao LIKE '%2026-09'")
        con.commit()
    finally:
        con.close()
    return caminho


def rodar(capsys: pytest.CaptureFixture[str], db: Path, *args: str) -> tuple[int, dict[str, Any]]:
    capsys.readouterr()
    codigo = cli.main([*args, "--db", str(db), "--hoje", "2026-10-27"])
    saida = capsys.readouterr()
    assert "Traceback" not in saida.out + saida.err
    return codigo, json.loads(saida.out)


def test_holerites_lista_e_totais_do_ano(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    codigo, r = rodar(capsys, db, "holerites", "--ano", "2026")
    assert codigo == 0 and r["ano"] == 2026
    h = r["holerites"]
    assert [x["competencia"] for x in h] == ["2026-10", "2026-09"]
    assert h[0]["tipo"] == "adiantamento" and h[0]["bruto_centavos"] == 500000
    assert h[0]["descontos"] == "R$ 400,00" and h[0]["liquido"] == "R$ 4.600,00"
    assert h[0]["credito"]["status"] == "previsto" and h[0]["credito"]["data_br"] == "20/10/2026"
    assert h[1]["credito"]["status"] == "recebido" and h[1]["credito"]["data_br"]
    assert "empresa" not in h[0]
    t = r["totais_ano"]
    assert t["bruto_centavos"] == 1000000 and t["descontos_centavos"] == 120000
    assert t["liquido_centavos"] == 880000 and t["liquido"] == "R$ 8.800,00"
    assert "total_item" not in r


def _hl(tipo: str, comp: str, bruto: int, desc: int) -> HoleriteLido:
    its = [ItemHolerite("1", "SALARIO FICTICIO", None, "provento", bruto),
           ItemHolerite("2", "DESCONTO TESTE", None, "desconto", desc)]
    return HoleriteLido(tipo, comp, "ACME", bruto, desc, bruto - desc, its)


def test_totais_do_ano_nao_contam_o_adiantamento_duas_vezes(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    caminho = tmp_path / "f4.db"
    con = conectar(caminho)
    try:
        migrar(con)
        conta = contas.criar_conta(con, "Corrente Teste", "corrente")
        # agosto: adiantamento (líquido 1.400,00) e folha (desconta o adiantamento)
        for n, (tipo, comp, bruto, desc) in enumerate([
                ("adiantamento", "2026-08", 150000, 10000),
                ("mensal", "2026-08", 500000, 210000),
                ("adiantamento", "2026-10", 150000, 10000)]):  # folha de outubro ainda não veio
            importar_holerite(con, _hl(tipo, comp, bruto, desc), f"f{n}", None, conta, HOJE,
                              [None, None], None)
        con.commit()
    finally:
        con.close()
    _, r = rodar(capsys, caminho, "holerites", "--ano", "2026")
    t = r["totais_ano"]
    assert t["bruto_centavos"] == 500000 + 150000  # folha de ago + adiantamento avulso de out
    assert t["liquido_centavos"] == 140000 + 290000 + 140000
    assert t["descontos_centavos"] == 650000 - 570000
    status = {(h["competencia"], h["tipo"]): h["credito"]["status"] for h in r["holerites"]}
    assert status[("2026-08", "mensal")] == "sem" and status[("2026-08", "adiantamento")] == "sem"
    assert status[("2026-10", "adiantamento")] == "previsto"


def test_holerites_item_soma_descontos_por_termo(db: Path,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    _, r = rodar(capsys, db, "holerites", "--ano", "2026", "--item", "pensao")
    assert r["total_item"]["termo"] == "pensao"
    assert r["total_item"]["total_centavos"] == 90000
    _, r = rodar(capsys, db, "holerites", "--item", "PENSÃO")
    assert r["total_item"]["total_centavos"] == 90000


def test_holerites_ver_lista_itens(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    codigo, r = rodar(capsys, db, "holerites", "ver", "1")
    assert codigo == 0 and r["holerite"]["competencia"] == "2026-10"
    it = r["itens"]
    assert [i["descricao"] for i in it] == ["SALARIO FICTICIO", "PENSÃO INVENTADA", "IMPOSTO TESTE"]
    assert it[1]["natureza"] == "desconto" and it[1]["valor"] == "R$ 300,00"
    assert it[0]["referencia"] == "21,00" and it[0]["categoria"] is None
    assert rodar(capsys, db, "holerites", "ver", "99")[0] == 1
    assert rodar(capsys, db, "holerites", "ver")[0] == 1
    assert rodar(capsys, db, "holerites", "7")[0] == 1


def test_holerites_sem_dados_retorna_lista_vazia(tmp_path: Path,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    caminho = tmp_path / "v.db"
    con = conectar(caminho)
    migrar(con)
    con.commit()
    con.close()
    codigo, r = rodar(capsys, caminho, "holerites")
    assert codigo == 0 and r["holerites"] == []
    assert r["totais_ano"]["liquido_centavos"] == 0


def test_holerites_nao_grava(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    antes = hashlib.sha256(db.read_bytes()).hexdigest()
    rodar(capsys, db, "holerites")
    rodar(capsys, db, "holerites", "ver", "1")
    assert hashlib.sha256(db.read_bytes()).hexdigest() == antes


def test_importar_do_consultor_continua_recusando_pdf(
        db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pdf = tmp_path / "holerite.pdf"
    pdf.write_bytes(b"%PDF-1.4 teste")
    codigo, r = rodar(capsys, db, "importar", "--arquivo", str(pdf), "--conta", "1",
                      "--confirmar")
    assert codigo == 1 and "painel" in r["erro"]
