import hashlib
import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from app.consultor import cli, consultas
from app.db import MIGRACOES, conectar, migrar
from app.repo import contas as repo_contas, lancamentos as lanc
from app.dominio.recorrentes import criar_recorrente
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


@pytest.mark.parametrize("cmd", [["saldos"], ["gastos"], ["contas"], ["categorias"],
                                 ["lancamentos", "--texto", "pix"]])
def test_leituras_puras_nao_escrevem(db_cheio: Path, capsys: pytest.CaptureFixture[str],
                                     cmd: list[str]) -> None:
    antes = hash_db(db_cheio)
    codigo, r = rodar(capsys, db_cheio, *cmd)
    assert codigo == 0 and "erro" not in r
    assert hash_db(db_cheio) == antes


def _com_aluguel(db: Path, dia: int = 25) -> None:
    con = conectar(db)
    try:
        criar_recorrente(con, nome="Aluguel", tipo="saida", dia=dia,
                         valor_estimado_centavos=150000, conta_id=1, categoria_id=None)
    finally:
        con.close()


@pytest.mark.parametrize("cmd", [["resumo", "--mes", "2026-10"], ["vencimentos"],
                                 ["projecao", "--meses", "6"]])
def test_geradores_sao_idempotentes(db_cheio: Path, capsys: pytest.CaptureFixture[str],
                                    cmd: list[str]) -> None:
    _com_aluguel(db_cheio)
    codigo, r = rodar(capsys, db_cheio, *cmd)
    assert codigo == 0 and r["recorrentes_geradas"] >= 1
    depois = hash_db(db_cheio)
    codigo, r = rodar(capsys, db_cheio, *cmd)
    assert codigo == 0 and r["recorrentes_geradas"] == 0
    assert hash_db(db_cheio) == depois


def test_recorrente_vira_id_pagavel_e_propaga(db_cheio: Path,
                                              capsys: pytest.CaptureFixture[str]) -> None:
    _com_aluguel(db_cheio, dia=20)
    _, r = rodar(capsys, db_cheio, "vencimentos", "--dias", "7")
    item = r["vencidas"][0]
    assert item["descricao"] == "Aluguel" and item["recorrente"] is True
    codigo, r = rodar(capsys, db_cheio, "pagar", "--lancamento", str(item["id"]),
                      "--valor", "1.600,00", "--data", "2026-10-22")
    assert codigo == 0
    futuros = consulta(db_cheio, "SELECT valor_centavos FROM lancamentos "
                                 "WHERE recorrente_id IS NOT NULL AND compensado = 0")
    assert futuros and all(v == (-160000,) for v in futuros)


def _banco_antigo(caminho: Path) -> Path:
    con = conectar(caminho)
    try:
        for i, m in enumerate(MIGRACOES[:2]):
            m(con)
            con.execute(f"PRAGMA user_version = {i + 1}")
        con.commit()
    finally:
        con.close()
    return caminho


@pytest.mark.parametrize("cmd", [["saldos"], ["gastos"], ["contas"], ["categorias"],
                                 ["lancamentos"], ["importar", "--arquivo", str(EXTRATO),
                                                   "--conta", "1"]])
def test_banco_antigo_nao_e_alterado(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                     cmd: list[str]) -> None:
    banco = _banco_antigo(tmp_path / "antigo.db")
    antes = hash_db(banco)
    codigo, r = rodar(capsys, banco, *cmd)
    assert codigo == 1 and "painel web" in r["erro"]
    assert hash_db(banco) == antes


def test_stdout_utf8_com_codepage_legado(db_cheio: Path) -> None:
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
    raiz = Path(__file__).resolve().parent.parent
    proc = subprocess.run([sys.executable, "-m", "app.consultor", "saldos", "--db", str(db_cheio)],
                          cwd=raiz, env=env, capture_output=True, timeout=60)
    assert proc.returncode == 0
    assert "Itaú".encode("utf-8") in proc.stdout
    assert json.loads(proc.stdout.decode("utf-8"))["contas"][0]["nome"] == "Itaú"


def test_saldos_json_utf8(db_cheio: Path, capsys: pytest.CaptureFixture[str]) -> None:
    codigo, r = rodar(capsys, db_cheio, "saldos")
    assert codigo == 0 and r["contas"][0]["nome"] == "Itaú"
    assert r["contas"][0]["saldo"] == "R$ 1.409,14"


def test_parametros_invalidos(db_cheio: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for args in (["resumo", "--mes", "2026-13"], ["projecao", "--meses", "5"],
                 ["lancamentos", "--limite", "999"], ["lancamentos", "--limite", "0"],
                 ["lancamentos", "--limite", "-1"], ["lancamentos", "--limite", "--5"], ["lancamentos", "--conta", "abc"],
                 ["lancamentos", "--de", "ontem"], ["lancamentos", "--tipo", "x"],
                 ["vencimentos", "--dias", "-1"], ["nada"], []):
        codigo, r = rodar(capsys, db_cheio, *args)
        assert codigo == 1 and r["erro"], args


def test_lancamentos_filtros(db_cheio: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, r = rodar(capsys, db_cheio, "lancamentos", "--texto", "padaria", "--limite", "1")
    assert r["total"] == 2 and len(r["lancamentos"]) == 1 and r["periodo"]["ate"] == "2026-10-23"
    _, r = rodar(capsys, db_cheio, "lancamentos", "--categoria", "sem", "--status", "compensados",
                 "--de", "2026-01-01")
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
        (["--arquivo", str(pdf), "--conta", "1"], "tela Importar"),
        (["--arquivo", str(grande), "--conta", "1"], "5 MB"),
        (["--arquivo", str(estranho), "--conta", "1"], "painel"),
        (["--arquivo", str(EXTRATO), "--conta", "abc"], "Conta inválida"),
        (["--arquivo", str(EXTRATO), "--conta", "99"], "Conta não encontrada"),
    ]
    for args, trecho in casos:
        codigo, r = rodar(capsys, db, "importar", *args)
        assert codigo == 1 and trecho in r["erro"], (args, r)


OFX = Path(__file__).parent / "fixtures" / "extrato_itau_exemplo.ofx"


def _banco_ofx(tmp_path: Path, saldo: int = 47111) -> Path:
    banco = tmp_path / "o.db"
    con = conectar(banco)
    try:
        migrar(con)
        repo_contas.criar_conta(con, "Itaú", "corrente", saldo_inicial_centavos=saldo,
                                data_saldo_inicial="2026-10-12")
    finally:
        con.close()
    return banco


def test_importar_ofx_simulacao_e_confirmar(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco = _banco_ofx(tmp_path)
    antes = hash_db(banco)
    codigo, r = rodar(capsys, banco, "importar", "--arquivo", str(OFX), "--conta", "1")
    assert codigo == 0 and r["novos"] == 12 and r["gravado"] is False and r["formato"] == "ofx"
    assert r["periodo"] == {"de": "2026-10-13", "de_br": "13/10/2026", "ate": "2026-10-22", "ate_br": "22/10/2026"}
    est = r["saldo_inicial_estimado"]
    assert (est["data"], est["data_br"], est["centavos"], est["valor"]) == (
        "2026-10-12", "12/10/2026", 47112, "R$ 471,12")
    assert est["prefill"] is False and "movimentos entre 22/10 e 26/10" in est["observacao"]
    assert hash_db(banco) == antes
    codigo, r = rodar(capsys, banco, "importar", "--arquivo", str(OFX), "--conta", "1", "--confirmar")
    assert codigo == 0 and r["gravado"] is True and r["formato"] == "ofx"
    si = r["saldo_informado"]
    assert {k: si[k] for k in ("data", "data_br", "banco_centavos", "banco", "calculado_centavos",
                               "calculado", "diferenca_centavos", "diferenca")} == {
        "data": "2026-10-26", "data_br": "26/10/2026", "banco_centavos": 200000, "banco": "R$ 2.000,00",
        "calculado_centavos": 199999, "calculado": "R$ 1.999,99",
        "diferenca_centavos": 1, "diferenca": "R$ 0,01"}
    assert "R$ 0,01 abaixo do saldo do banco" in si["mensagem"]
    assert r["conciliacao"]["status"] == "saldo_informado" and "aviso" not in r["conciliacao"]
    assert r["conciliacao"]["diferenca_centavos"] == 1


def test_importar_ofx_prefill_true_quando_dtasof_no_fim(tmp_path: Path,
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    banco = _banco_ofx(tmp_path)
    arq = tmp_path / "d.ofx"
    arq.write_bytes(OFX.read_bytes().replace(b"<DTASOF>20261026", b"<DTASOF>20261022"))
    codigo, r = rodar(capsys, banco, "importar", "--arquivo", str(arq), "--conta", "1")
    assert codigo == 0 and r["saldo_inicial_estimado"]["prefill"] is True


def test_importar_ofx_saldo_inicial_igual_estimativa_e_neutro(tmp_path: Path,
                                                             capsys: pytest.CaptureFixture[str]) -> None:
    banco = _banco_ofx(tmp_path, saldo=47112)
    codigo, r = rodar(capsys, banco, "importar", "--arquivo", str(OFX), "--conta", "1", "--confirmar")
    assert codigo == 0 and r["conciliacao"]["status"] == "saldo_inicial_estimado"
    assert "saldo_informado" not in r and "confere" not in json.dumps(r["conciliacao"])


def test_importar_ofx_caixa_ignora_manual_compensado(tmp_path: Path,
                                                    capsys: pytest.CaptureFixture[str]) -> None:
    banco = _banco_ofx(tmp_path, saldo=47112)
    con = conectar(banco)
    try:
        con.execute("UPDATE contas SET data_saldo_inicial='2026-10-11'")
        lanc.inserir_manual(con, conta_id=1, data="2026-10-14", descricao="PAGO NA MAO",
                            valor_centavos=-5000, tipo="saida", categoria_id=None,
                            compensado=True, observacao=None)
        con.commit()
    finally:
        con.close()
    codigo, r = rodar(capsys, banco, "importar", "--arquivo", str(OFX), "--conta", "1", "--confirmar")
    assert codigo == 0 and r["saldo_informado"]["diferenca_centavos"] == 0
    assert r["conciliacao"]["status"] == "saldo_informado"


def test_importar_ofx_que_nao_e_ofx(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco = _banco_ofx(tmp_path)
    falso = tmp_path / "f.ofx"
    falso.write_text("a;b\n1;2\n", encoding="utf-8")
    codigo, r = rodar(capsys, banco, "importar", "--arquivo", str(falso), "--conta", "1")
    assert codigo == 1 and "O arquivo não parece ser um OFX." in r["erro"]


def test_importar_ofc_e_cartao_dao_erro(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco = _banco_ofx(tmp_path)
    ofc = tmp_path / "x.ofc"
    ofc.write_bytes(b"<OFC>\n</OFC>")
    cartao = tmp_path / "fatura.ofx"
    cartao.write_bytes(OFX.read_bytes().replace(b"<STMTRS>", b"<CCSTMTRS>"))
    antes = hash_db(banco)
    for arq, trecho in ((ofc, "OFC"), (cartao, "cartão")):
        codigo, r = rodar(capsys, banco, "importar", "--arquivo", str(arq), "--conta", "1", "--confirmar")
        assert codigo == 1 and trecho in r["erro"], r
    assert hash_db(banco) == antes


def test_importar_extensao_continua_restrita(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    banco = _banco_ofx(tmp_path)
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF")
    codigo, r = rodar(capsys, banco, "importar", "--arquivo", str(pdf), "--conta", "1")
    assert codigo == 1 and "tela Importar" in r["erro"]


def test_categorizar_e_regra(db_cheio: Path, capsys: pytest.CaptureFixture[str]) -> None:
    codigo, r = rodar(capsys, db_cheio, "categorizar", "--lancamento", "1", "--categoria", "1")
    assert codigo == 0 and r["ok"] is True
    assert consulta(db_cheio, "SELECT categoria_id FROM lancamentos WHERE id=1") == [(1,)]
    for args in (["--lancamento", "9999", "--categoria", "1"],
                 ["--lancamento", "1", "--categoria", "9999"],
                 ["--lancamento", "x", "--categoria", "1"]):
        codigo, r = rodar(capsys, db_cheio, "categorizar", *args)
        assert codigo == 1 and r["erro"]
    codigo, r = rodar(capsys, db_cheio, "regra", "--padrao", "padaria", "--categoria", "1")
    assert codigo == 0 and r["aplicados"] >= 1
    codigo, r = rodar(capsys, db_cheio, "regra", "--padrao", "x", "--categoria", "9999")
    assert codigo == 1 and "Categoria" in r["erro"]
    codigo, r = rodar(capsys, db_cheio, "regra", "--padrao", " ", "--categoria", "1")
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
                                "lancamentos", "faturas", "contas", "categorias", "importar", "casar",
                                "categorizar", "regra", "pagar", "criar-conta"}


def test_erros_de_argparse_em_portugues(db_cheio: Path, capsys: pytest.CaptureFixture[str]) -> None:
    casos = [(["importar"], "obrigatório"), (["nada"], "Comando desconhecido"),
             (["saldos", "--xpto"], "Opção desconhecida"),
             (["resumo", "--mes"], "precisa de um valor"),
             (["criar-conta", "--nome", "x"], "obrigatório")]
    for args, trecho in casos:
        codigo, r = rodar(capsys, db_cheio, *args)
        assert codigo == 1 and trecho in r["erro"], (args, r)
        assert "argument" not in r["erro"] and "required" not in r["erro"]


def test_criar_conta_invalida_nao_deixa_rastro(tmp_path: Path,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    alvo = tmp_path / "novo" / "f.db"
    codigo, _ = rodar(capsys, alvo, "criar-conta", "--nome", "X", "--tipo", "corrente",
                      "--saldo-inicial", "abc", "--data-saldo", "2026-10-01")
    assert codigo == 1 and not alvo.parent.exists()


def test_estimativas_futuras_fora_do_padrao(db_cheio: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _com_aluguel(db_cheio)
    rodar(capsys, db_cheio, "projecao", "--meses", "6")
    _, r = rodar(capsys, db_cheio, "lancamentos", "--limite", "200")
    assert r["periodo"]["ate"] == "2026-10-23"
    assert not any(x["descricao"] == "Aluguel" and x["data"] > "2026-10-23" for x in r["lancamentos"])
    _, r = rodar(capsys, db_cheio, "lancamentos", "--ate", "2027-12-31", "--texto", "aluguel")
    assert r["total"] >= 4 and r["lancamentos"][0]["data"] > "2026-12-31"
    for status in ("compensados", "pendentes", "todos"):
        codigo, r = rodar(capsys, db_cheio, "lancamentos", "--categoria", "sem", "--status", status,
                          "--ate", "2027-12-31")
        assert codigo == 0
        if status == "compensados":
            assert r["total"] > 0 and all(x["compensado"] for x in r["lancamentos"])
        if status == "pendentes":
            assert r["total"] > 0 and not any(x["compensado"] for x in r["lancamentos"])


def test_banco_travado_tem_mensagem_propria(db_cheio: Path, capsys: pytest.CaptureFixture[str],
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    class OperationalError(Exception):
        pass

    def trava(*_: Any, **__: Any) -> dict[str, Any]:
        raise OperationalError("database is locked")

    monkeypatch.setattr(consultas, "saldos", trava)
    capsys.readouterr()
    assert cli.main(["saldos", "--db", str(db_cheio)]) == 1
    assert "gravando no banco" in json.loads(capsys.readouterr().out)["erro"]
    assert cli.mensagem_inesperada(RuntimeError("x")) == cli.MSG_INESPERADO


def test_importar_ofx_todas_as_linhas_depois_do_dtasof_sem_estimativa(tmp_path: Path,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    banco = _banco_ofx(tmp_path)
    arq = tmp_path / "e.ofx"
    arq.write_bytes(OFX.read_bytes().replace(b"<DTASOF>20261026", b"<DTASOF>20261012"))
    codigo, r = rodar(capsys, banco, "importar", "--arquivo", str(arq), "--conta", "1")
    assert codigo == 0 and r["novos"] == 12
    assert r.get("saldo_inicial_estimado") is None
