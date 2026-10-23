import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.consultor import cli, consultas
from app.db import conectar
from app.dominio.lancamentos import criar_lancamento_manual
from tests._consultor_base import EXTRATO, criar_banco


def rodar(capsys: pytest.CaptureFixture[str], db: Path, *args: str) -> tuple[int, dict[str, Any]]:
    capsys.readouterr()
    codigo = cli.main([*args, "--db", str(db), "--hoje", "2026-10-23"])
    saida = capsys.readouterr()
    assert "Traceback" not in saida.out + saida.err
    return codigo, json.loads(saida.out)


def hash_db(db: Path) -> str:
    return hashlib.sha256(db.read_bytes()).hexdigest()


def consulta(db: Path, sql: str) -> list[Any]:
    con = conectar(db)
    try:
        return [tuple(r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()


def pendente(db: Path, desc: str, valor: int, dia: str = "2026-10-05") -> int:
    con = conectar(db)
    try:
        return criar_lancamento_manual(con, conta_id=1, data=date.fromisoformat(dia),
                                       descricao=desc, valor_centavos=valor)
    finally:
        con.close()


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return criar_banco(tmp_path / "t.db", com_extrato=False)


@pytest.fixture
def db_cheio(tmp_path: Path) -> Path:
    return criar_banco(tmp_path / "c.db", com_extrato=True)


def test_banco_inexistente_nao_cria_nada(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    alvo = tmp_path / "data" / "x.db"
    codigo, r = rodar(capsys, alvo, "saldos")
    assert codigo == 1 and "criar-conta" in r["erro"] and "extrato" in r["erro"]
    assert not alvo.parent.exists()


@pytest.mark.parametrize("cmd", [["saldos"], ["resumo", "--mes", "2026-10"], ["vencimentos"],
                                 ["projecao", "--meses", "6"], ["gastos"], ["contas"],
                                 ["categorias"], ["lancamentos", "--texto", "pix"]])
def test_consultas_nao_escrevem(db_cheio: Path, capsys: pytest.CaptureFixture[str],
                                cmd: list[str]) -> None:
    antes = hash_db(db_cheio)
    codigo, r = rodar(capsys, db_cheio, *cmd)
    assert codigo == 0 and "erro" not in r
    assert hash_db(db_cheio) == antes


def test_saldos_json_utf8(db_cheio: Path, capsys: pytest.CaptureFixture[str]) -> None:
    codigo, r = rodar(capsys, db_cheio, "saldos")
    assert codigo == 0 and r["contas"][0]["nome"] == "Itaú"
    assert r["contas"][0]["saldo"] == "R$ 1.409,14"


def test_parametros_invalidos(db_cheio: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for args in (["resumo", "--mes", "2026-13"], ["projecao", "--meses", "5"],
                 ["lancamentos", "--limite", "999"], ["lancamentos", "--conta", "abc"],
                 ["lancamentos", "--de", "ontem"], ["lancamentos", "--tipo", "x"],
                 ["vencimentos", "--dias", "-1"], ["nada"], []):
        codigo, r = rodar(capsys, db_cheio, *args)
        assert codigo == 1 and r["erro"], args


def test_lancamentos_filtros(db_cheio: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, r = rodar(capsys, db_cheio, "lancamentos", "--texto", "padaria", "--limite", "1")
    assert r["total"] == 2 and len(r["lancamentos"]) == 1
    _, r = rodar(capsys, db_cheio, "lancamentos", "--categoria", "sem", "--status", "compensados")
    assert r["total"] > 0


def test_importar_simulacao_nao_grava(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    antes = hash_db(db)
    codigo, r = rodar(capsys, db, "importar", "--arquivo", str(EXTRATO), "--conta", "1")
    assert codigo == 0 and r["novos"] == 18 and r["gravado"] is False
    assert hash_db(db) == antes


def test_importar_confirmar(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pend = pendente(db, "PADARIA CENTRAL", -2490)
    codigo, r = rodar(capsys, db, "importar", "--arquivo", str(EXTRATO), "--conta", "1",
                      "--confirmar")
    assert codigo == 0 and r["novos"] == 18 and r["gravado"] is True
    assert r["conciliacao"]["status"] == "confere"
    assert any(p["pendente_id"] == pend for p in r["propostas_casamento"])
    # nunca confirma sozinho
    assert consulta(db, f"SELECT compensado FROM lancamentos WHERE id={pend}") == [(0,)]
    codigo, r = rodar(capsys, db, "importar", "--arquivo", str(EXTRATO), "--conta", "1",
                      "--confirmar")
    assert codigo == 1 and "já foi importado" in r["erro"]


def test_importar_anterior_ao_saldo_inicial(tmp_path: Path,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    banco = criar_banco(tmp_path / "a.db", com_extrato=False, data_saldo="2026-09-01")
    antes = hash_db(banco)
    codigo, r = rodar(capsys, banco, "importar", "--arquivo", str(EXTRATO), "--conta", "1",
                      "--confirmar")
    assert codigo == 1 and "anterior à data do saldo inicial" in r["erro"]
    assert hash_db(banco) == antes


def test_importar_entradas_invalidas(db: Path, tmp_path: Path,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    grande = tmp_path / "g.csv"
    grande.write_bytes(b"a" * (5 * 1024 * 1024 + 1))
    estranho = tmp_path / "e.csv"
    estranho.write_text("a;b\n1;2\n", encoding="utf-8")
    casos = [
        (["--arquivo", str(tmp_path / "nao.csv"), "--conta", "1"], "não encontrado"),
        (["--arquivo", str(pdf), "--conta", "1"], ".csv"),
        (["--arquivo", str(grande), "--conta", "1"], "5 MB"),
        (["--arquivo", str(estranho), "--conta", "1"], "painel"),
        (["--arquivo", str(EXTRATO), "--conta", "abc"], "Conta inválido"),
        (["--arquivo", str(EXTRATO), "--conta", "99"], "Conta não encontrada"),
    ]
    for args, trecho in casos:
        codigo, r = rodar(capsys, db, "importar", *args)
        assert codigo == 1 and trecho in r["erro"], (args, r)


def test_categorizar_e_regra(db_cheio: Path, capsys: pytest.CaptureFixture[str]) -> None:
    codigo, r = rodar(capsys, db_cheio, "categorizar", "--lancamento", "1", "--categoria", "2")
    assert codigo == 0 and r["ok"] is True
    assert consulta(db_cheio, "SELECT categoria_id FROM lancamentos WHERE id=1") == [(2,)]
    for args in (["--lancamento", "9999", "--categoria", "2"],
                 ["--lancamento", "1", "--categoria", "9999"],
                 ["--lancamento", "x", "--categoria", "2"]):
        codigo, r = rodar(capsys, db_cheio, "categorizar", *args)
        assert codigo == 1 and r["erro"]
    codigo, r = rodar(capsys, db_cheio, "regra", "--padrao", "padaria", "--categoria", "2")
    assert codigo == 0 and r["aplicados"] >= 1
    codigo, r = rodar(capsys, db_cheio, "regra", "--padrao", "x", "--categoria", "9999")
    assert codigo == 1 and "Categoria" in r["erro"]
    codigo, r = rodar(capsys, db_cheio, "regra", "--padrao", " ", "--categoria", "2")
    assert codigo == 1


def test_casar(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pend = pendente(db, "PADARIA CENTRAL", -2490)
    _, r = rodar(capsys, db, "importar", "--arquivo", str(EXTRATO), "--conta", "1", "--confirmar")
    p = next(x for x in r["propostas_casamento"] if x["pendente_id"] == pend)
    codigo, r = rodar(capsys, db, "casar", "--pendente", str(pend), "--linha", str(p["linha_id"]))
    assert codigo == 0 and r["ok"] is True
    assert consulta(db, f"SELECT compensado FROM lancamentos WHERE id={pend}") == [(1,)]
    codigo, r = rodar(capsys, db, "casar", "--pendente", "9999", "--linha", "9998")
    assert codigo == 1 and r["erro"]


def test_pagar(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    pend = pendente(db, "Luz", -10000, "2026-10-20")
    codigo, r = rodar(capsys, db, "pagar", "--lancamento", str(pend), "--valor", "R$ 95,50",
                      "--data", "2026-10-22")
    assert codigo == 0 and r["ok"] is True
    assert consulta(db, f"SELECT valor_centavos, compensado FROM lancamentos WHERE id={pend}") \
        == [(-9550, 1)]
    codigo, r = rodar(capsys, db, "pagar", "--lancamento", str(pend))
    assert codigo == 1 and "já está pago" in r["erro"]
    outro = pendente(db, "Agua", -5000)
    codigo, r = rodar(capsys, db, "pagar", "--lancamento", str(outro), "--data", "2026-10-24")
    assert codigo == 1 and "futuro" in r["erro"]
    codigo, r = rodar(capsys, db, "pagar", "--lancamento", "9999")
    assert codigo == 1 and "não encontrado" in r["erro"]
    codigo, r = rodar(capsys, db, "pagar", "--lancamento", str(outro), "--valor", "abc")
    assert codigo == 1 and "Valor inválido" in r["erro"]
    codigo, r = rodar(capsys, db, "pagar", "--lancamento", str(outro))
    assert codigo == 0


def test_criar_conta_cria_banco(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    alvo = tmp_path / "novo" / "f.db"
    codigo, r = rodar(capsys, alvo, "criar-conta", "--nome", "Itaú", "--tipo", "corrente",
                      "--saldo-inicial", "1.000,00", "--data-saldo", "2026-10-01")
    assert codigo == 0 and r["conta"]["id"] == 1 and r["conta"]["saldo_inicial_centavos"] == 100000
    _, r = rodar(capsys, alvo, "contas")
    assert r["contas"][0]["nome"] == "Itaú"
    codigo, r = rodar(capsys, alvo, "criar-conta", "--nome", "", "--tipo", "corrente",
                      "--saldo-inicial", "0", "--data-saldo", "2026-10-01")
    assert codigo == 1
    codigo, r = rodar(capsys, alvo, "criar-conta", "--nome", "X", "--tipo", "corrente",
                      "--saldo-inicial", "0", "--data-saldo", "31/02/2026")
    assert codigo == 1


def test_excecao_inesperada_vira_json(db_cheio: Path, capsys: pytest.CaptureFixture[str],
                                      monkeypatch: pytest.MonkeyPatch) -> None:
    def quebra(*_: Any, **__: Any) -> dict[str, Any]:
        raise RuntimeError("segredo interno")

    monkeypatch.setattr(consultas, "saldos", quebra)
    capsys.readouterr()
    codigo = cli.main(["saldos", "--db", str(db_cheio)])
    saida = capsys.readouterr()
    assert codigo == 1 and "erro" in json.loads(saida.out)
    assert "segredo interno" not in saida.out + saida.err
    assert "RuntimeError" in saida.err and "Traceback" not in saida.err


def test_parser_expoe_subcomandos() -> None:
    sub = next(a for a in cli.construir_parser()._actions if a.dest == "comando")
    assert set(sub.choices) == {"saldos", "resumo", "vencimentos", "projecao", "gastos",
                                "lancamentos", "contas", "categorias", "importar", "casar",
                                "categorizar", "regra", "pagar", "criar-conta"}
