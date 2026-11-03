import sqlite3

import pytest

from app.repo import faturas, fila_faturas as fila
from app.repo.contas import criar_conta


def _ins(con, msg="m1", anexo="a1", agora="2026-11-01T10:00:00", h="h1"):
    return fila.inserir(con, gmail_msg_id=msg, anexo_id=anexo, emissor="X", cartao_final="1111",
                        vencimento="2026-11-05", pdf_hash=h, agora=agora)


def test_inserir_duplicado_devolve_none(con) -> None:
    assert _ins(con) is not None
    assert _ins(con) is None
    assert _ins(con, anexo="a2") is not None


def test_estado_invalido_rejeitado(con) -> None:
    i = _ins(con)
    with pytest.raises(sqlite3.IntegrityError):
        fila.definir_estado(con, i, "xyz")


def test_listar_pendentes_ordem_e_filtro(con) -> None:
    b = _ins(con, msg="b", agora="2026-11-02T10:00:00")
    a = _ins(con, msg="a", agora="2026-11-01T10:00:00")
    c = _ins(con, msg="c", agora="2026-11-03T10:00:00")
    fila.definir_estado(con, c, "pulada")
    assert [r["id"] for r in fila.listar_pendentes(con)] == [a, b]
    assert fila.obter(con, c)["estado"] == "pulada"
    assert fila.obter(con, 999) is None


def test_expirados_so_pendentes_antigos(con) -> None:
    velho = _ins(con, msg="v", agora="2026-10-01T00:00:00")
    pulado = _ins(con, msg="p", agora="2026-10-01T00:00:00")
    _ins(con, msg="n", agora="2026-11-01T00:00:00")
    fila.definir_estado(con, pulado, "pulada")
    assert [r["id"] for r in fila.expirados(con, "2026-10-15T00:00:00")] == [velho]


def _fatura(con, conta):
    return faturas.criar(
        con, conta_id=conta, competencia="2026-10", data_vencimento="2026-11-05",
        data_fechamento=None, emissor=None, bandeira=None, produto=None,
        total_impresso_centavos=100, total_calculado_centavos=100, ajustes_resumo_centavos=0,
        arquivo_hash="hh", arquivo_nome=None, criado_em="2026-11-01T00:00:00")


def test_hash_ja_importado(con) -> None:
    conta = criar_conta(con, "C", "cartao", final_numero="1111")
    _fatura(con, conta)
    assert fila.hash_ja_importado(con, "hh") is True
    assert fila.hash_ja_importado(con, "outro") is False


def test_fatura_existente_por_final_e_vencimento(con) -> None:
    conta = criar_conta(con, "C", "cartao", final_numero="1111")
    _fatura(con, conta)
    assert fila.fatura_existente(con, "1111", "2026-11-05") is True
    assert fila.fatura_existente(con, "2222", "2026-11-05") is False
    assert fila.fatura_existente(con, "1111", "2026-12-05") is False
