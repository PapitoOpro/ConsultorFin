"""Notas de negociação da Clear: busca no Gmail e importação automática (PDF com senha do cofre).

Nunca registra assunto, remetente, nome de anexo, conteúdo da nota nem senha.
A senha vem do cofre (cadastrada pelo painel); o Claude nunca a vê.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

from app import config
from app.dominio import cofre_senhas
from app.db import sem_acento
from app.gmail.cliente import Mensagem
from app.importacao.nota_negociacao import NotaIlegivel, NotaLida, SenhaDaNota, ler_notas
from app.repo import inv_notas as repo

log = logging.getLogger(__name__)

EMISSOR_COFRE = "Clear"
FINAL_COFRE = "notas"
_TERMO_BUSCA = "clear.com.br"


class ClienteBusca(Protocol):
    def buscar(self, de: date, ate: date, limite: int = ...,
               remetentes_termos: tuple[str, ...] = ...) -> list[Mensagem]: ...
    def baixar_anexo(self, mensagem_id: str, anexo_id: str) -> bytes: ...


@dataclass(frozen=True)
class ResumoNotas:
    encontradas: int = 0
    novas: int = 0
    ja_importadas: int = 0
    ilegiveis: int = 0
    sem_senha: int = 0
    limite_atingido: bool = False


def eh_nota_clear(msg: Mensagem) -> bool:
    """Remetente da Clear e assunto 'Nota de Negociação' (só regex/contém; dado não confiável)."""
    return ("@clear.com.br" in msg.de.lower()
            and "nota de negociacao" in sem_acento(msg.assunto))


def importar_nota(con: sqlite3.Connection, nota: NotaLida, agora: datetime) -> bool:
    """Grava a nota; False se (número, data) já existe. Faz commit."""
    if repo.existe(con, nota.numero, nota.data):
        return False
    repo.inserir(con, nota, agora.isoformat(timespec="seconds"))
    con.commit()
    return True


def buscar_e_importar(con: sqlite3.Connection, cliente: ClienteBusca, de: date, ate: date,
                      agora: datetime) -> ResumoNotas:
    mensagens = cliente.buscar(de, ate, config.LIMITE_MENSAGENS_GMAIL,
                               remetentes_termos=(_TERMO_BUSCA,))
    truncada = bool(getattr(cliente, "ultima_busca_truncada", False))
    senhas = cofre_senhas.candidatas(con, EMISSOR_COFRE, FINAL_COFRE)
    encontradas = novas = ja = ilegiveis = sem_senha = 0
    for msg in mensagens:
        if not eh_nota_clear(msg):
            continue
        for anexo in msg.anexos:
            if not anexo.nome.lower().endswith(".pdf") or anexo.tamanho > config.LIMITE_PDF_BYTES:
                continue
            encontradas += 1
            try:
                lidas, falhas = ler_notas(cliente.baixar_anexo(msg.id, anexo.id), senhas)
            except SenhaDaNota:
                sem_senha += 1
                continue
            except NotaIlegivel:
                ilegiveis += 1
                continue
            ilegiveis += falhas
            for nota in lidas:  # um PDF pode trazer mais de uma nota (uma por página)
                if importar_nota(con, nota, agora):
                    novas += 1
                else:
                    ja += 1
    log.info("event=notas_busca encontradas=%d novas=%d sem_senha=%d", encontradas, novas, sem_senha)
    return ResumoNotas(encontradas, novas, ja, ilegiveis, sem_senha, truncada)


def notas_json(con: Any, de: str | None = None, ate: str | None = None) -> list[dict[str, Any]]:
    from app.consultor.consultas import dinheiro
    saida: list[dict[str, Any]] = []
    for r in repo.listar(con, de, ate):
        saida.append({"numero": r["numero"], "data": r["data"], "tipo": r["tipo"],
                      "negocios": int(r["negocios"]),
                      **dinheiro("ajuste_day_trade", int(r["ajuste_day_trade_centavos"])),
                      **dinheiro("custos", int(r["custos_centavos"])),
                      **dinheiro("irrf", int(r["irrf_centavos"])),
                      **dinheiro("liquido", int(r["liquido_centavos"]))})
    return saida
