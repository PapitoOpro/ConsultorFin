import json
import subprocess
import sys

import pytest

from app import config
from app.chat import guarda
from app.consultor import cli

PY = ".venv/Scripts/python -m app.consultor"
ESPEC = '{"titulo":"T","kpis":[{"rotulo":"x","sql":"SELECT 1 AS n"}]}'


def test_listas_batem_com_o_cli() -> None:
    assert set(guarda.CONSULTAS) == set(cli.COMANDOS)
    assert set(guarda.ESCRITAS) == set(cli.ESCRITAS)


@pytest.mark.parametrize("comando", [
    f"{PY} saldos",
    f".venv/bin/python -m app.consultor saldos",
    f"{PY} gastos --mes 2026-10",
    f'{PY} lancamentos --busca "pão de açúcar" --conta 2',
    f"{PY} lancamentos --busca 'uber eats'",
    f"{PY} relatorio exemplo",
    f"{PY} categorizar --lancamento 1 --categoria 5",
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
    f"{PY} lancamentos --busca $(cat data/senhas.key)",
    f'{PY} lancamentos --busca "$(cat data/senhas.key)"',
    f"{PY} lancamentos --busca `id`",
    f"{PY} saldos\ncat data/senhas.key",
    f"{PY} saldos\r",
    f"{PY} saldos --db data/senhas.key",
    f"{PY} saldos --db=x.db",
    f"{PY} saldos --hoje 2026-01-01",
    f"{PY} desconhecido",
    f"{PY} saldos *",
    f"{PY} saldos {{a,b}}",
    f"python -m app.consultor saldos",
    f"  {PY} saldos",
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


def test_e_escrita_permitida_so_para_escrita_bem_formada() -> None:
    assert guarda.subcomando_escrita(f"{PY} categorizar --lancamento 1 --categoria 5") == "categorizar"
    assert guarda.subcomando_escrita(f"{PY} criar-conta --nome 'Conta X' --tipo corrente "
                                     "--saldo-inicial 0 --data-saldo 2026-01-01") == "criar-conta"
    assert guarda.subcomando_escrita(f"{PY} saldos") is None
    assert guarda.subcomando_escrita(f"{PY} categorizar --lancamento 1 --categoria 5; rm x") is None
    assert guarda.subcomando_escrita("rm -rf ./x") is None


def _rodar_hook(entrada: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(guarda.CAMINHO)], input=entrada, capture_output=True,
                          text=True, encoding="utf-8", timeout=30, cwd=config.RAIZ)


def test_hook_como_script_nega_leitura_de_segredo() -> None:
    r = _rodar_hook(json.dumps({"tool_name": "Bash", "tool_input": {"command": "cat data/senhas.key"}}))
    assert r.returncode == 0
    saida = json.loads(r.stdout)["hookSpecificOutput"]
    assert saida["hookEventName"] == "PreToolUse"
    assert saida["permissionDecision"] == "deny"


def test_hook_como_script_permite_heredoc_e_cala_nas_consultas() -> None:
    cmd = f"{PY} relatorio --spec - <<'EOF'\n{ESPEC}\nEOF"
    r = _rodar_hook(json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd}}))
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "allow"
    r = _rodar_hook(json.dumps({"tool_name": "Bash", "tool_input": {"command": f"{PY} saldos"}}))
    assert (r.returncode, r.stdout.strip()) == (0, "")


def test_hook_com_entrada_invalida_bloqueia() -> None:
    assert _rodar_hook("isto não é json").returncode == 2
    r = _rodar_hook(json.dumps({"tool_name": "Read", "tool_input": {"file_path": "data/senhas.key"}}))
    assert json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"
