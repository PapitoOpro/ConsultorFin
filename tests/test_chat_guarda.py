import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app import config
from app.chat import guarda
from app.consultor import cli

PY = ".venv/Scripts/python -m app.consultor"
ESPEC = '{"titulo":"T","kpis":[{"rotulo":"x","sql":"SELECT 1 AS n"}]}'
CATEGORIZAR = f"{PY} categorizar --lancamento 1 --categoria 5"


def test_listas_batem_com_o_cli() -> None:
    assert set(guarda.CONSULTAS) == set(cli.COMANDOS)
    assert set(guarda.ESCRITAS) == set(cli.ESCRITAS)


def _subparsers() -> dict[str, argparse.ArgumentParser]:
    p = cli.construir_parser()
    acao = next(a for a in p._actions if isinstance(a, argparse._SubParsersAction))
    return dict(acao.choices)


def test_tabela_de_opcoes_bate_com_o_parser() -> None:
    for nome, sp in _subparsers().items():
        com_valor, booleanas = set(), set()
        for a in sp._actions:
            longas = [o for o in a.option_strings if o.startswith("--")]
            if not longas or longas[0] in ("--help", "--db", "--hoje"):
                continue
            (booleanas if a.nargs == 0 else com_valor).add(longas[0])
        esperado = guarda.OPCOES[nome]
        assert (com_valor, booleanas) == (set(esperado[0]), set(esperado[1])), nome


@pytest.mark.parametrize("comando", [
    f"{PY} saldos",
    ".venv/bin/python -m app.consultor saldos",
    f"{PY} gastos --mes 2026-10",
    f'{PY} lancamentos --texto "pão de açúcar" --conta 2',
    f"{PY} lancamentos --texto 'uber eats'",
    f"{PY} faturas ver 3",
    f"{PY} relatorio exemplo",
    f"{PY} criar-conta --nome 'Conta X' --tipo corrente --saldo-inicial -100 --data-saldo 2026-01-01",
    CATEGORIZAR,
])
def test_comando_do_consultor_segue_para_as_regras(comando: str) -> None:
    assert guarda.decidir(comando) is None


@pytest.mark.parametrize("comando", [
    "cat data/senhas.key",
    "head -c 30 README.md",
    "echo oi",
    f"{PY} saldos; cat data/senhas.key",
    f"{PY} saldos && cat data/senhas.key",
    f"{PY} saldos | tee x",
    f"{PY} saldos > data/x",
    f"{PY} lancamentos --texto $(cat data/senhas.key)",
    f'{PY} lancamentos --texto "$(cat data/senhas.key)"',
    f"{PY} lancamentos --texto `id`",
    f"{PY} saldos\ncat data/senhas.key",
    f"{PY} saldos\r",
    f"{PY} desconhecido",
    f"{PY} saldos *",
    f"{PY} saldos {{a,b}}",
    "python -m app.consultor saldos",
    f"  {PY} saldos",
    # --db/--hoje em qualquer grafia (argparse aceitaria abreviação/aspas)
    f"{PY} saldos --db data/senhas.key",
    f"{PY} saldos --db=x.db",
    f"{PY} saldos --d data/x.db",
    f"{PY} saldos --d=x",
    f'{PY} saldos "--db" x',
    f"{PY} saldos '--db' x",
    f'{PY} saldos --"db" x',
    f"{PY} saldos --hoje 2026-01-01",
    f"{PY} resumo --ho=2030-01-01",
    f"{PY} resumo --h 2030-01-01",
    f"{PY} resumo --help",
    f"{CATEGORIZAR} --d other.db",
    # opções fora da tabela, repetidas, sem valor, posicionais estranhos
    f"{PY} saldos --mes 2026-10",
    f"{PY} gastos --mes 2026-10 --mes 2026-09",
    f"{PY} gastos --mes",
    f"{PY} gastos --mes --conta",
    f"{PY} lancamentos --texto -x",
    f"{PY} saldos extra",
    f"{PY} faturas ver 3 4",
    f"{PY} gastos -- --db x",
    f"{PY} lancamentos --texto 'abc",
    # relatorio: só `exemplo` ou o heredoc aprovado
    f"{PY} relatorio --spec data/gmail-token.json",
    f"{PY} relatorio --spec /etc/passwd",
    f"{PY} relatorio --spec x",
    f"{PY} relatorio --spec -",
    f"{PY} relatorio",
    f"{PY} relatorio exemplo --spec x",
])
def test_qualquer_outra_coisa_e_negada(comando: str) -> None:
    assert guarda.decidir(comando) == "deny"


def test_relatorio_por_heredoc_e_permitido() -> None:
    cmd = f"{PY} relatorio --spec - <<'EOF'\n{ESPEC}\nEOF"
    assert guarda.decidir(cmd) == "allow"
    assert guarda.decidir(cmd + "\n") == "allow"
    multi = f"{PY} relatorio --spec - <<'EOF'\n{{\n  \"titulo\": \"T\"\n}}\nEOF"
    assert guarda.decidir(multi) == "allow"


@pytest.mark.parametrize("comando", [
    f"{PY} relatorio --spec - <<'EOF'\n{ESPEC}\nEOF\ncat data/senhas.key",
    f"{PY} relatorio --spec - <<'EOF'\n{ESPEC}\nEOF\nrm -rf x\nEOF",
    f"{PY} relatorio --spec - <<EOF\n{ESPEC}\nEOF",
    f"{PY} relatorio --spec - <<-'EOF'\n{ESPEC}\nEOF",
    f"{PY} relatorio --spec - <<'EOF'; cat x\n{ESPEC}\nEOF",
    f"{PY} relatorio --db x.db --spec - <<'EOF'\n{ESPEC}\nEOF",
    f"{PY} saldos --spec - <<'EOF'\n{ESPEC}\nEOF",
    f"{PY} relatorio --spec - <<'EOF'\n{ESPEC}\r\nEOF",
])
def test_heredoc_fora_do_formato_e_negado(comando: str) -> None:
    assert guarda.decidir(comando) == "deny"


def test_analisar_devolve_opcoes_sem_aspas() -> None:
    a = guarda.analisar(f"{PY} criar-conta --nome 'Conta X' --tipo corrente --saldo-inicial 100 "
                        "--data-saldo 2026-01-01")
    assert a is not None
    assert a.subcomando == "criar-conta"
    assert a.opcoes == {"--nome": "Conta X", "--tipo": "corrente", "--saldo-inicial": "100",
                        "--data-saldo": "2026-01-01"}
    imp = guarda.analisar(f"{PY} importar --arquivo data/x.csv --conta 2 --confirmar")
    assert imp is not None and imp.opcoes["--confirmar"] is True


def test_e_escrita_permitida_so_para_escrita_bem_formada() -> None:
    assert guarda.subcomando_escrita(CATEGORIZAR) == "categorizar"
    assert guarda.subcomando_escrita(f"{PY} criar-conta --nome 'Conta X' --tipo corrente "
                                     "--saldo-inicial 0 --data-saldo 2026-01-01") == "criar-conta"
    assert guarda.subcomando_escrita(f"{PY} saldos") is None
    assert guarda.subcomando_escrita(f"{CATEGORIZAR}; rm x") is None
    assert guarda.subcomando_escrita(f"{CATEGORIZAR} --d x.db") is None
    assert guarda.subcomando_escrita("rm -rf ./x") is None


# ---- aprovação única da escrita confirmada -------------------------------------------------------

def _ambiente(tmp_path: Path, comando: str = CATEGORIZAR) -> dict[str, str]:
    return {guarda.VAR_CONFIRMADO: comando, guarda.VAR_MARCA: str(tmp_path / "usado")}


def test_confirmado_e_aprovado_uma_vez_so(tmp_path: Path) -> None:
    amb = _ambiente(tmp_path)
    assert guarda.decidir_turno(CATEGORIZAR, amb) == "allow"
    assert guarda.decidir_turno(CATEGORIZAR, amb) == "deny"
    assert (tmp_path / "usado").exists()


def test_outro_comando_nao_usa_a_confirmacao(tmp_path: Path) -> None:
    amb = _ambiente(tmp_path)
    assert guarda.decidir_turno(f"{PY} categorizar --lancamento 2 --categoria 5", amb) is None
    assert guarda.decidir_turno("cat data/senhas.key", amb) == "deny"
    assert not (tmp_path / "usado").exists()
    assert guarda.decidir_turno(CATEGORIZAR, amb) == "allow"


def test_confirmacao_invalida_ou_sem_pasta_nao_aprova(tmp_path: Path) -> None:
    assert guarda.decidir_turno(CATEGORIZAR, {}) is None
    assert guarda.decidir_turno(CATEGORIZAR, {guarda.VAR_CONFIRMADO: CATEGORIZAR}) is None
    sem_pasta = {guarda.VAR_CONFIRMADO: CATEGORIZAR, guarda.VAR_MARCA: str(tmp_path / "nao" / "usado")}
    assert guarda.decidir_turno(CATEGORIZAR, sem_pasta) == "deny"
    composto = f"{CATEGORIZAR}; cat data/senhas.key"
    assert guarda.decidir_turno(composto, _ambiente(tmp_path, composto)) == "deny"


# ---- o hook como script ---------------------------------------------------------------------------

def _rodar_hook(entrada: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(guarda.CAMINHO)], input=entrada, capture_output=True,
                          text=True, encoding="utf-8", timeout=30, cwd=config.RAIZ,
                          env={**os.environ, **(env or {})})


def _decisao(r: subprocess.CompletedProcess[str]) -> str:
    return str(json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"])


def test_hook_como_script_nega_leitura_de_segredo() -> None:
    r = _rodar_hook(json.dumps({"tool_name": "Bash", "tool_input": {"command": "cat data/senhas.key"}}))
    assert r.returncode == 0
    assert json.loads(r.stdout)["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert _decisao(r) == "deny"


def test_hook_como_script_permite_heredoc_e_cala_nas_consultas() -> None:
    cmd = f"{PY} relatorio --spec - <<'EOF'\n{ESPEC}\nEOF"
    assert _decisao(_rodar_hook(json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}))) == "allow"
    r = _rodar_hook(json.dumps({"tool_name": "Bash", "tool_input": {"command": f"{PY} saldos"}}))
    assert (r.returncode, r.stdout.strip()) == (0, "")


def test_hook_como_script_confirmacao_unica(tmp_path: Path) -> None:
    corpo = json.dumps({"tool_name": "Bash", "tool_input": {"command": CATEGORIZAR}})
    assert _decisao(_rodar_hook(corpo, _ambiente(tmp_path))) == "allow"
    assert _decisao(_rodar_hook(corpo, _ambiente(tmp_path))) == "deny"


def test_hook_com_entrada_invalida_bloqueia() -> None:
    assert _rodar_hook("isto não é json").returncode == 2
    r = _rodar_hook(json.dumps({"tool_name": "Read", "tool_input": {"file_path": "data/senhas.key"}}))
    assert _decisao(r) == "deny"
