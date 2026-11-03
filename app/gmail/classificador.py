"""Classifica e-mails de fatura de cartão pelo remetente (função pura, sem rede/DB).

A lista padrão de remetentes é um PALPITE, a validar contra a caixa real;
pode ser sobrescrita por data/gmail-remetentes.json
(lista de {"emissor": str, "contem": [str, ...]}).
Assunto/trecho são não confiáveis: só regexes os leem e nunca vão para logs.
"""
from __future__ import annotations

import json
import logging
import re
from email.utils import parseaddr
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app import config

log = logging.getLogger(__name__)

_MAX_TEXTO = 2000
_RE_FINAL = re.compile(r"final\s{0,5}(\d{4})", re.IGNORECASE)
_RE_VENC = re.compile(
    r"venc\w{0,15}\s{0,5}(?:em|dia|:)?\s{0,5}(\d{2})/(\d{2})/(\d{4})", re.IGNORECASE
)


@dataclass(frozen=True)
class Remetente:
    emissor: str
    contem: tuple[str, ...]


@dataclass(frozen=True)
class Candidata:
    emissor: str
    cartao_final: str | None
    vencimento: str | None


REMETENTES_PADRAO: tuple[Remetente, ...] = (
    Remetente("Itaú", ("itau",)),
    Remetente("Nubank", ("nubank",)),
    Remetente("Porto", ("portoseguro", "portobank")),
    Remetente("Mercado Pago", ("mercadopago",)),
)


def _caminho_padrao() -> Path:
    return config.RAIZ / "data" / "gmail-remetentes.json"


def _termos(bruto: object) -> tuple[str, ...]:
    if not isinstance(bruto, list) or not all(isinstance(t, str) and t.strip() for t in bruto):
        raise ValueError("contem deve ser lista de textos não vazios")
    return tuple(t.strip().lower() for t in bruto)


def carregar_remetentes(caminho: Path | None = None) -> tuple[Remetente, ...]:
    """Lê o override JSON; ausente ou inválido -> REMETENTES_PADRAO."""
    alvo = caminho if caminho is not None else _caminho_padrao()
    if not alvo.exists():
        return REMETENTES_PADRAO
    try:
        dados = json.loads(alvo.read_text(encoding="utf-8"))
        if not isinstance(dados, list) or not dados:
            raise ValueError("lista vazia ou formato inesperado")
        lidos = tuple(
            Remetente(str(item["emissor"]), _termos(item["contem"])) for item in dados
        )
        if any(not r.emissor or not r.contem for r in lidos):
            raise ValueError("emissor/contem vazio")
        return lidos
    except (OSError, ValueError, KeyError, TypeError) as exc:
        log.warning("event=gmail_remetentes_invalido erro=%s", type(exc).__name__)
        return REMETENTES_PADRAO


def _vencimento_iso(texto: str) -> str | None:
    m = _RE_VENC.search(texto)
    if m is None:
        return None
    dia, mes, ano = (int(g) for g in m.groups())
    try:
        return date(ano, mes, dia).isoformat()
    except ValueError:
        return None


def classificar(
    de: str, assunto: str, trecho: str, remetentes: tuple[Remetente, ...]
) -> Candidata | None:
    """Candidata se o From casa com algum remetente; senão None."""
    origem = parseaddr(de[:_MAX_TEXTO])[1].lower()  # só o endereço, nunca o nome de exibição
    emissor = next(
        (r.emissor for r in remetentes if any(t in origem for t in r.contem)), None
    )
    if emissor is None:
        return None
    texto = f"{assunto[:_MAX_TEXTO]}\n{trecho[:_MAX_TEXTO]}"
    m = _RE_FINAL.search(texto)
    return Candidata(emissor, m.group(1) if m else None, _vencimento_iso(texto))
