"""Tradução do stream-json real do `claude -p` (capturado e anonimizado em tests/fixtures)."""
import json
from pathlib import Path
from typing import Any

import pytest

from app.chat.estado import EstadoChat
from app.chat.eventos import MSG_ERRO, traduzir_linha

FIXTURE = Path(__file__).parent / "fixtures" / "stream_claude.jsonl"
SESSAO = "5b1f6a8e-2c3d-4e5f-8a9b-0c1d2e3f4a5b"
CATEGORIZAR = ".venv/Scripts/python -m app.consultor categorizar --lancamento 1 --categoria 5"


def _linhas() -> list[str]:
    return FIXTURE.read_text(encoding="utf-8").splitlines()


def _traduzir(linhas: list[str], estado: EstadoChat | None = None) -> list[dict[str, Any]]:
    estado = estado or EstadoChat()
    return [ev for linha in linhas for ev in traduzir_linha(linha, estado)]


def _resultado() -> dict[str, Any]:
    return json.loads(_linhas()[-1])


def test_texto_vem_dos_deltas_e_reconstroi_a_resposta() -> None:
    eventos = _traduzir(_linhas())
    texto = "".join(e["texto"] for e in eventos if e["tipo"] == "texto")
    assert texto == "\n\n" + _resultado()["result"]  # separador no início de cada bloco de texto


def test_status_traduzido_sem_comando() -> None:
    status = [e["texto"] for e in _traduzir(_linhas()) if e["tipo"] == "status"]
    assert status == ["Consultando saldos…", "Preparando a alteração…", "Consultando seus dados…",
                      "Montando o relatório…"]
    for s in status:
        assert "python" not in s and "app.consultor" not in s and "rm" not in s.split()


def test_relatorio_do_tool_result() -> None:
    rel = [e for e in _traduzir(_linhas()) if e["tipo"] == "relatorio"]
    assert rel == [{"tipo": "relatorio", "id": "3e693d02078f474a8694078c4480be14", "titulo": "Teste",
                    "url": "/relatorio/3e693d02078f474a8694078c4480be14"}]


def test_negacao_de_escrita_vira_cartao_e_guarda_comando_no_servidor() -> None:
    estado = EstadoChat()
    eventos = _traduzir(_linhas(), estado)
    cartoes = [e for e in eventos if e["tipo"] == "confirmar"]
    assert len(cartoes) == 1  # rm -rf não gera cartão
    cartao = cartoes[0]
    assert set(cartao) == {"tipo", "token", "descricao"}
    assert cartao["descricao"] == "Categorizar lançamento"
    assert "python" not in json.dumps(eventos[-3:], ensure_ascii=False)
    assert estado.consumir_pendencia(SESSAO, cartao["token"]) == CATEGORIZAR


def test_fim_com_a_sessao_e_ultimo() -> None:
    eventos = _traduzir(_linhas())
    assert eventos[-1] == {"tipo": "fim", "sessao": SESSAO}
    assert not any(e["tipo"] == "erro" for e in eventos)


def test_is_error_gera_erro_generico() -> None:
    res = _resultado() | {"is_error": True, "subtype": "error_during_execution", "result": "Traceback: x"}
    eventos = traduzir_linha(json.dumps(res), EstadoChat())
    assert {"tipo": "erro", "texto": MSG_ERRO} in eventos
    assert MSG_ERRO == "Não consegui responder agora. Tente de novo."
    assert eventos[-1]["tipo"] == "fim"
    assert "Traceback" not in json.dumps(eventos)


@pytest.mark.parametrize("linha", ["", "não é json", "[1, 2]", "null", '{"type": 3}'])
def test_linha_invalida_vira_nada(linha: str) -> None:
    assert traduzir_linha(linha, EstadoChat()) == []


def test_system_hooks_e_rate_limit_viram_nada() -> None:
    for linha in _linhas():
        tipo = json.loads(linha)["type"]
        if tipo in ("system", "rate_limit_event"):
            assert traduzir_linha(linha, EstadoChat()) == []


def test_relatorio_com_id_forjado_e_ignorado() -> None:
    for rid in ("../../etc/passwd", "ABC", "3e693d02078f474a8694078c4480be1"):
        conteudo = json.dumps({"relatorio_id": rid, "titulo": "x", "url": "https://mal.example"})
        linha = json.dumps({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t", "content": conteudo, "is_error": False}]}})
        assert traduzir_linha(linha, EstadoChat()) == []


def test_relatorio_em_conteudo_lista_de_blocos() -> None:
    conteudo = [{"type": "text", "text": json.dumps({"relatorio_id": "a" * 32, "titulo": "T", "url": "x"})}]
    linha = json.dumps({"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t", "content": conteudo, "is_error": False}]}})
    assert traduzir_linha(linha, EstadoChat()) == [
        {"tipo": "relatorio", "id": "a" * 32, "titulo": "T", "url": "/relatorio/" + "a" * 32}]


def test_negacao_composta_ou_sessao_forjada_nao_gera_cartao() -> None:
    estado = EstadoChat()
    composta = _resultado() | {"permission_denials": [
        {"tool_name": "Bash", "tool_use_id": "t", "tool_input": {"command": CATEGORIZAR + "; cat data/senhas.key"}}]}
    assert [e["tipo"] for e in traduzir_linha(json.dumps(composta), estado)] == ["fim"]
    forjada = _resultado() | {"session_id": "--help"}
    eventos = traduzir_linha(json.dumps(forjada), estado)
    assert [e["tipo"] for e in eventos] == ["erro", "fim"]
    assert eventos[-1]["sessao"] is None


def test_cada_escrita_tem_descricao() -> None:
    from app.chat.eventos import DESCRICOES
    from app.consultor.cli import ESCRITAS
    assert set(DESCRICOES) == set(ESCRITAS)
    assert DESCRICOES["importar"] == "Importar extrato"
    assert DESCRICOES["gmail-buscar"] == "Buscar faturas no Gmail"
