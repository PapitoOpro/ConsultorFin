"""Leitura da Nota de Negociação da Clear (layout BM&F: mini-índice/dólar, PDF com senha).

Só lê. Nunca guarda nem registra nome, CPF, endereço, código do cliente ou a senha: a nota lida
tem apenas número, data, negócios e totais. Layout de ações (Bovespa) não é suportado.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import datetime

import pdfplumber

from app.dominio.dinheiro import para_centavos, verificar_magnitude

_MSG_LAYOUT = "Nota de negociação não reconhecida (somente o layout BM&F da Clear é suportado)."
_MSG_SENHA = "Não foi possível abrir a nota: senha ausente ou incorreta."
_DATA = r"\d{2}/\d{2}/\d{4}"
_DIN = r"\d{1,3}(?:\.\d{3})*,\d{2}"
_NEGOCIO = re.compile(
    rf"^(C|V)\s+(.+?)\s+({_DATA})\s+(\d+)\s+({_DIN})\s+(DAY TRADE|NORMAL)\s+({_DIN})\s+([DC])\s+({_DIN})$")
_NUMERO_DATA = re.compile(rf"^(\d{{1,3}}(?:\.\d{{3}})*)\s+(\d+)\s+({_DATA})$")
_VALOR = re.compile(_DIN)
_SINAL = re.compile(r"\b([DC])\b")


class NotaIlegivel(ValueError):
    """PDF que não é uma nota suportada (mensagem em português)."""


class SenhaDaNota(ValueError):
    """Senha ausente ou incorreta."""


@dataclass(frozen=True)
class Negocio:
    lado: str  # 'C' compra, 'V' venda
    mercadoria: str
    vencimento: str  # ISO
    quantidade: int
    preco_centavos: int
    tipo: str  # 'DAY TRADE' | 'NORMAL'
    valor_centavos: int  # com sinal: crédito +, débito -
    taxa_centavos: int


@dataclass
class NotaLida:
    numero: str
    data: str  # ISO (data do pregão)
    negocios: list[Negocio] = field(default_factory=list)
    ajuste_day_trade_centavos: int = 0  # com sinal
    custos_centavos: int = 0  # total de custos operacionais, negativo
    irrf_day_trade_centavos: int = 0  # projetado, negativo
    liquido_centavos: int = 0  # total líquido da nota, com sinal
    avisos: list[str] = field(default_factory=list)


def _sem_acento(txt: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode().lower()


def _iso(br: str) -> str:
    return datetime.strptime(br, "%d/%m/%Y").date().isoformat()


def _cents(txt: str) -> int:
    return verificar_magnitude(para_centavos(txt))


def _com_sinal(valor: str, dc: str) -> int:
    c = _cents(valor)
    return c if dc == "C" else -c


def _linha_depois(linhas: list[str], inicio: str) -> str:
    for i, l in enumerate(linhas):
        if _sem_acento(l).startswith(inicio) and i + 1 < len(linhas):
            return linhas[i + 1]
    raise NotaIlegivel(_MSG_LAYOUT)


def interpretar_texto(texto: str) -> NotaLida:
    """Função pura: texto extraído da 1ª página -> NotaLida."""
    linhas = [l.strip() for l in texto.splitlines() if l.strip()]
    if not linhas or "nota de negocia" not in _sem_acento(linhas[0]):
        raise NotaIlegivel(_MSG_LAYOUT)
    cab = next((m for l in linhas[:8] if (m := _NUMERO_DATA.match(l))), None)
    if cab is None or not any("mercadoria" in _sem_acento(l) for l in linhas):
        raise NotaIlegivel(_MSG_LAYOUT)
    try:
        nota = NotaLida(numero=cab.group(1), data=_iso(cab.group(3)))
        for l in linhas:
            m = _NEGOCIO.match(l)
            if m is None:
                continue
            lado, merc, venc, qtd, preco, tipo, valor, dc, taxa = m.groups()
            nota.negocios.append(Negocio(lado, merc, _iso(venc), int(qtd), _cents(preco), tipo,
                                         _com_sinal(valor, dc), _cents(taxa)))
        if not nota.negocios:
            raise NotaIlegivel(_MSG_LAYOUT)
        # Blocos de totais: rótulos numa linha, valores na seguinte.
        l_irrf = _VALOR.findall(_linha_depois(linhas, "irrf irrf day trade"))
        l_ajuste = _linha_depois(linhas, "+outros custos")
        l_liq = _linha_depois(linhas, "outros irrf operacional")
        nota.irrf_day_trade_centavos = -_cents(l_irrf[1])
        v3, s3 = _VALOR.findall(l_ajuste), _SINAL.findall(l_ajuste)
        nota.ajuste_day_trade_centavos = _com_sinal(v3[3], s3[0])
        nota.custos_centavos = _com_sinal(v3[4], s3[1])
        v4, s4 = _VALOR.findall(l_liq), _SINAL.findall(l_liq)
        nota.liquido_centavos = _com_sinal(v4[-1], s4[-1])
    except (IndexError, ValueError) as exc:
        if isinstance(exc, NotaIlegivel):
            raise
        raise NotaIlegivel(_MSG_LAYOUT) from None
    esperado = (nota.ajuste_day_trade_centavos + nota.custos_centavos + nota.irrf_day_trade_centavos)
    if esperado != nota.liquido_centavos:
        nota.avisos.append("A soma dos totais não confere com o total líquido da nota.")
    return nota


def ler_nota(conteudo: bytes, senhas: list[str]) -> NotaLida:
    """PDF (com senha) -> NotaLida. Tenta cada senha; sem nenhuma que abra: SenhaDaNota."""
    if not conteudo.startswith(b"%PDF"):
        raise NotaIlegivel(_MSG_LAYOUT)
    criptografado = b"/Encrypt" in conteudo
    tentativas = senhas if criptografado else [""]
    if not tentativas:
        raise SenhaDaNota(_MSG_SENHA)
    for senha in tentativas:
        try:
            with pdfplumber.open(io.BytesIO(conteudo), password=senha) as pdf:
                texto = pdf.pages[0].extract_text() or ""
        except Exception:  # noqa: BLE001 - senha errada ou PDF inválido: tenta a próxima
            continue
        return interpretar_texto(texto)
    raise SenhaDaNota(_MSG_SENHA)


__all__ = ["NotaIlegivel", "NotaLida", "Negocio", "SenhaDaNota", "interpretar_texto", "ler_nota"]
