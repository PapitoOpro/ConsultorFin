import io
import json
from pathlib import Path
from typing import Any

import pytest

from app import config
from app.consultor import cli
from app.relatorio.espec import ESPEC_EXEMPLO
from tests._consultor_base import criar_banco

MINIMA: dict[str, Any] = {"titulo": "Teste", "observacao": "Relatório mínimo."}


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return criar_banco(tmp_path / "t.db", com_extrato=True)


@pytest.fixture(autouse=True)
def pasta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    alvo = tmp_path / "r"
    monkeypatch.setattr(config, "DIR_RELATORIOS", alvo)
    return alvo


def rodar(capsys: pytest.CaptureFixture[str], db: Path, *args: str) -> tuple[int, dict[str, Any]]:
    capsys.readouterr()
    codigo = cli.main([*args, "--db", str(db), "--hoje", "2026-10-23"])
    saida = capsys.readouterr()
    assert "Traceback" not in saida.out + saida.err
    return codigo, json.loads(saida.out)


def test_relatorio_exemplo_imprime_json_valido(db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    codigo, r = rodar(capsys, db, "relatorio", "exemplo")
    assert codigo == 0 and r == ESPEC_EXEMPLO


def test_relatorio_gera_arquivo_e_devolve_id(db: Path, pasta: Path, tmp_path: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    arq = tmp_path / "s.json"
    arq.write_text(json.dumps(MINIMA), encoding="utf-8")
    codigo, r = rodar(capsys, db, "relatorio", "--spec", str(arq))
    assert codigo == 0 and set(r) == {"relatorio_id", "titulo", "url"}
    assert r["titulo"] == "Teste" and r["url"] == f"/relatorio/{r['relatorio_id']}"
    assert (pasta / f"{r['relatorio_id']}.json").is_file()


def test_spec_via_stdin(db: Path, pasta: Path, monkeypatch: pytest.MonkeyPatch,
                        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(MINIMA)))
    codigo, r = rodar(capsys, db, "relatorio", "--spec", "-")
    assert codigo == 0 and (pasta / f"{r['relatorio_id']}.json").is_file()


def test_exemplo_roda_no_banco_semeado(db: Path, pasta: Path, tmp_path: Path,
                                       capsys: pytest.CaptureFixture[str]) -> None:
    _, ex = rodar(capsys, db, "relatorio", "exemplo")
    arq = tmp_path / "ex.json"
    arq.write_text(json.dumps(ex), encoding="utf-8")
    codigo, r = rodar(capsys, db, "relatorio", "--spec", str(arq))
    assert codigo == 0, r
    retrato = json.loads((pasta / f"{r['relatorio_id']}.json").read_text(encoding="utf-8"))
    assert retrato["titulo"] == ex["titulo"]


@pytest.mark.parametrize("conteudo", ["{nao é json", json.dumps({"titulo": "x"}),
                                      json.dumps({**MINIMA, "kpis": [{"rotulo": "a", "formato": "moeda",
                                                                      "sql": "DELETE FROM lancamentos"}]})])
def test_espec_invalida_devolve_erro_em_portugues(db: Path, tmp_path: Path, conteudo: str,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    arq = tmp_path / "s.json"
    arq.write_text(conteudo, encoding="utf-8")
    codigo, r = rodar(capsys, db, "relatorio", "--spec", str(arq))
    assert codigo == 1 and r["erro"]


def test_sem_spec_e_arquivo_inexistente(db: Path, tmp_path: Path,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    assert rodar(capsys, db, "relatorio")[0] == 1
    codigo, r = rodar(capsys, db, "relatorio", "--spec", str(tmp_path / "nao.json"))
    assert codigo == 1 and "erro" in r
