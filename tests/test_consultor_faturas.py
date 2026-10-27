"""Consultor: comandos somente-leitura de faturas e recusa de PDF (dados inventados)."""
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.consultor import cli
from app.db import conectar, migrar
from app.dominio.faturas import importar_fatura
from app.importacao.fatura.modelo import FaturaLida, ItemFatura
from app.repo import contas

HOJE = date(2026, 10, 27)


def _fatura() -> FaturaLida:
    f = FaturaLida(
        emissor="Banco Teste", bandeira="Visa", produto="Gold", final_cartao="1234",
        data_vencimento="2026-10-10", data_fechamento="2026-10-01",
        total_impresso_centavos=0, ajustes_resumo_centavos=0,
        itens=[
            ItemFatura("2026-09-05", "LOJA FICTICIA", -10000, "compra", None, None, "1234", None),
            ItemFatura("2026-09-10", "MERCADO INVENTADO", -6000, "compra", 2, 3, "5678", None),
            ItemFatura("2026-09-12", "ESTORNO TESTE", 1000, "credito", None, None, "1234", None)])
    f.total_impresso_centavos = f.calculado_centavos  # 15000
    return f


@pytest.fixture
def db(tmp_path: Path) -> Path:
    caminho = tmp_path / "t.db"
    con = conectar(caminho)
    try:
        migrar(con)
        contas.criar_conta(con, "Corrente Teste", "corrente")
        cartao = contas.criar_conta(con, "Cartao Teste", "cartao", final_numero="1234")
        importar_fatura(con, _fatura(), "h1", "f.pdf", cartao, HOJE, [None, None, None])
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


def test_faturas_lista(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    codigo, r = rodar(capsys, db, "faturas")
    assert codigo == 0
    assert len(r["faturas"]) == 1
    f = r["faturas"][0]
    assert f["conta"] == "Cartao Teste" and f["conta_id"] == 2
    assert f["competencia"] == "2026-10"
    assert f["vencimento"] == "2026-10-10" and f["vencimento_br"] == "10/10/2026"
    assert f["total_centavos"] == 15000 and f["total"] == "R$ 150,00"
    assert f["status"] == "a_pagar"
    assert f["n_itens"] == 3
    assert f["divergencia_centavos"] is None


def test_faturas_filtra_por_conta(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rodar(capsys, db, "faturas", "--conta", "2")[1]["faturas"][0]["id"] == 1
    assert rodar(capsys, db, "faturas", "--conta", "1")[1]["faturas"] == []


def test_faturas_divergencia_e_paga(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    con = conectar(db)
    try:
        con.execute("UPDATE faturas SET total_impresso_centavos = 15500")
        con.execute("INSERT INTO lancamentos (uuid, conta_id, data, descricao, valor_centavos,"
                    " tipo, chave_deduplicacao, origem) VALUES ('u', 1, '2026-10-09', 'PAG',"
                    " -15500, 'pagamento_fatura', 'k', 'manual')")
        con.execute("UPDATE faturas SET pagamento_lancamento_id = last_insert_rowid()")
        con.commit()
    finally:
        con.close()
    f = rodar(capsys, db, "faturas")[1]["faturas"][0]
    assert f["status"] == "paga"
    assert f["divergencia_centavos"] == 500 and f["divergencia"] == "R$ 5,00"


def test_faturas_ver(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    codigo, r = rodar(capsys, db, "faturas", "ver", "1")
    assert codigo == 0
    assert r["fatura"]["id"] == 1 and r["fatura"]["total_centavos"] == 15000
    it = r["itens"]
    assert [i["descricao"] for i in it] == ["LOJA FICTICIA", "MERCADO INVENTADO", "ESTORNO TESTE"]
    assert it[0]["valor_centavos"] == -10000 and it[0]["valor"] == "- R$ 100,00"
    assert it[0]["categoria_id"] is None and it[0]["categoria"] is None
    assert it[0]["data"] == "2026-09-05" and it[0]["data_br"] == "05/09/2026"
    assert it[1]["parcela"] == "2/3" and it[1]["portador"] == "5678"
    assert it[0]["parcela"] is None and it[0]["portador"] == "1234"
    assert it[2]["credito"] is True and it[0]["credito"] is False
    assert it[2]["valor_centavos"] == 1000


def test_faturas_ver_inexistente_e_invalido(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    codigo, r = rodar(capsys, db, "faturas", "ver", "99")
    assert codigo == 1 and r["erro"] == "Fatura não encontrada."
    assert rodar(capsys, db, "faturas", "ver")[0] == 1
    assert rodar(capsys, db, "faturas", "ver", "abc")[0] == 1
    assert rodar(capsys, db, "faturas", "7")[0] == 1


def test_faturas_nao_grava(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    antes = hashlib.sha256(db.read_bytes()).hexdigest()
    rodar(capsys, db, "faturas")
    rodar(capsys, db, "faturas", "ver", "1")
    assert hashlib.sha256(db.read_bytes()).hexdigest() == antes


@pytest.mark.parametrize("nome", ["fatura.pdf", "FATURA.PDF", "inexistente.pdf"])
def test_importar_recusa_pdf(db: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
                             nome: str) -> None:
    pdf = tmp_path / nome
    if nome != "inexistente.pdf":
        pdf.write_bytes(b"%PDF-1.4 teste")
    codigo, r = rodar(capsys, db, "importar", "--arquivo", str(pdf), "--conta", "2",
                      "--confirmar")
    assert codigo == 1
    assert "painel" in r["erro"] and "Importar" in r["erro"] and "senha" in r["erro"]
