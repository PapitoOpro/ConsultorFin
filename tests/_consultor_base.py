"""Apoio dos testes do consultor: banco semeado com o extrato de exemplo."""
from datetime import date
from pathlib import Path
from typing import Any

from app.db import conectar, migrar
from app.importacao.deteccao import detectar
from app.importacao.servico import importar
from app.repo import contas

EXTRATO = Path(__file__).parent / "fixtures" / "extrato_itau_exemplo.csv"
HOJE = date(2026, 10, 23)


def nova_conta(con: Any, data_saldo: str = "2026-07-24") -> int:
    return contas.criar_conta(con, "Itaú", "corrente", saldo_inicial_centavos=214808,
                              data_saldo_inicial=data_saldo)


def importar_extrato(con: Any, conta_id: int) -> None:
    conteudo = EXTRATO.read_bytes()
    importar(con, conta_id, EXTRATO.name, conteudo, detectar(conteudo, EXTRATO.name), HOJE)


def criar_banco(caminho: Path, *, com_extrato: bool, data_saldo: str = "2026-07-24") -> Path:
    con = conectar(caminho)
    try:
        migrar(con)
        conta = nova_conta(con, data_saldo)
        if com_extrato:
            importar_extrato(con, conta)
    finally:
        con.close()
    return caminho
