"""Leitura da Nota de Negociação da Clear (PDF com senha; uma nota por página).

Layouts: BM&F (mini-índice/dólar, day-trade) e Bovespa (ações/ETFs/FIIs à vista).

Só lê. Nunca guarda nem registra nome, CPF, endereço, código do cliente ou a senha: a nota lida
tem apenas número, data, negócios e totais.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import datetime

import pdfplumber

from app.dominio.dinheiro import para_centavos, verificar_magnitude

_MSG_LAYOUT = "Nota de negociação não reconhecida (layouts suportados: BM&F e Bovespa à vista, da Clear)."
_MSG_SENHA = "Não foi possível abrir a nota: senha ausente ou incorreta."
_DATA = r"\d{2}/\d{2}/\d{4}"
_DIN = r"\d{1,3}(?:\.\d{3})*,\d{2}"
_NEGOCIO = re.compile(
    rf"^(C|V)\s+(.+?)\s+({_DATA})\s+(\d+)\s+({_DIN})\s+(DAY TRADE|NORMAL)\s+({_DIN})\s+([DC])\s+({_DIN})$")
_NUMERO_DATA = re.compile(rf"^([\d.]+)\s+(\d+)\s+({_DATA})$")
_BOVESPA = re.compile(
    rf"^1-BOVESPA\s+(C|V)\s+(\S+)\s+(.+?)\s+([\d.]+)\s+({_DIN})\s+({_DIN})\s+([DC])$")
_TICKER = re.compile(r"\b([A-Z]{4}\d{1,2})\b")
_LIQ_PARA = re.compile(rf"liquido para ({_DATA})\s+({_DIN})\s+([DC])", re.IGNORECASE)
_VAL_OPER = re.compile(rf"valor liquido das operacoes\s+({_DIN})\s+([DC])", re.IGNORECASE)
_IRRF_BOV = re.compile(rf"i\.r\.r\.f\. s/ operacoes, base r\$[\d.,]+\s+({_DIN})")
_VALOR = re.compile(_DIN)
_IRRF_BOV_DISPENSA = 100  # até R$ 1,00 o IRRF de ações é só informado, não descontado do líquido
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
    tipo: str = "bmf"  # 'bmf' | 'bovespa'
    operacoes_centavos: int = 0  # bovespa: valor líquido das operações (compra = negativo)
    data_liquidacao: str | None = None  # bovespa: "Líquido para <data>"
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


def _cabecalho(linhas: list[str]) -> re.Match[str]:
    if not linhas or "nota de negocia" not in _sem_acento(linhas[0]):
        raise NotaIlegivel(_MSG_LAYOUT)
    cab = next((m for l in linhas[:8] if (m := _NUMERO_DATA.match(l))), None)
    if cab is None:
        raise NotaIlegivel(_MSG_LAYOUT)
    return cab


def interpretar_texto(texto: str) -> NotaLida:
    """Função pura: texto de uma página (uma nota) -> NotaLida, pelo layout detectado."""
    linhas = [l.strip() for l in texto.splitlines() if l.strip()]
    cab = _cabecalho(linhas)
    sem = [_sem_acento(l) for l in linhas]
    try:
        if any("1-bovespa" in l for l in sem):
            return _interpretar_bovespa(linhas, sem, cab)
        if any("mercadoria" in l for l in sem):
            return _interpretar_bmf(linhas, cab)
    except (IndexError, ValueError):
        raise NotaIlegivel(_MSG_LAYOUT) from None
    raise NotaIlegivel(_MSG_LAYOUT)


def _interpretar_bovespa(linhas: list[str], sem: list[str], cab: re.Match[str]) -> NotaLida:
    nota = NotaLida(numero=cab.group(1), data=_iso(cab.group(3)), tipo="bovespa")
    for l in linhas:
        m = _BOVESPA.match(l)
        if m is None:
            continue
        lado, prazo, spec, qtd, preco, valor, dc = m.groups()
        tk = _TICKER.search(spec)
        nota.negocios.append(Negocio(lado, tk.group(1) if tk else spec[:40], "", int(qtd.replace(".", "")),
                                     _cents(preco), prazo, _com_sinal(valor, dc), 0))
    if not nota.negocios:
        raise NotaIlegivel(_MSG_LAYOUT)
    texto = chr(10).join(sem)
    oper, liq = _VAL_OPER.search(texto), _LIQ_PARA.search(texto)
    if oper is None or liq is None:
        raise NotaIlegivel(_MSG_LAYOUT)
    nota.operacoes_centavos = _com_sinal(oper.group(1), oper.group(2).upper())
    nota.liquido_centavos = _com_sinal(liq.group(2), liq.group(3).upper())
    nota.data_liquidacao = _iso(liq.group(1))
    irrf = _IRRF_BOV.search(texto)
    retido = _cents(irrf.group(1)) if irrf else 0
    nota.irrf_day_trade_centavos = -retido if retido > _IRRF_BOV_DISPENSA else 0
    nota.custos_centavos = nota.liquido_centavos - nota.operacoes_centavos - nota.irrf_day_trade_centavos
    if sum(n.valor_centavos for n in nota.negocios) != nota.operacoes_centavos:
        nota.avisos.append("A soma dos negócios não confere com o valor das operações da nota.")
    return nota


def _interpretar_bmf(linhas: list[str], cab: re.Match[str]) -> NotaLida:
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
    esperado = nota.ajuste_day_trade_centavos + nota.custos_centavos + nota.irrf_day_trade_centavos
    if esperado != nota.liquido_centavos:
        nota.avisos.append("A soma dos totais não confere com o total líquido da nota.")
    return nota


def _juntar(notas: dict[tuple[str, str, str], NotaLida], nota: NotaLida) -> None:
    """Nota que continua em outra página (mesmo número): soma negócios e totais."""
    chave = (nota.numero, nota.data, nota.tipo)
    atual = notas.get(chave)
    if atual is None:
        notas[chave] = nota
        return
    atual.negocios.extend(nota.negocios)
    for campo in ("ajuste_day_trade_centavos", "custos_centavos", "irrf_day_trade_centavos",
                  "liquido_centavos", "operacoes_centavos"):
        setattr(atual, campo, getattr(atual, campo) + getattr(nota, campo))
    atual.avisos.extend(nota.avisos)


def ler_notas(conteudo: bytes, senhas: list[str]) -> tuple[list[NotaLida], int]:
    """PDF (com senha; cada página pode ser uma nota) -> (notas lidas, páginas ilegíveis).
    Sem nenhuma senha que abra: SenhaDaNota. PDF sem nenhuma nota reconhecível: NotaIlegivel."""
    if not conteudo.startswith(b"%PDF"):
        raise NotaIlegivel(_MSG_LAYOUT)
    criptografado = b"/Encrypt" in conteudo
    tentativas = senhas if criptografado else [""]
    if not tentativas:
        raise SenhaDaNota(_MSG_SENHA)
    for senha in tentativas:
        try:
            with pdfplumber.open(io.BytesIO(conteudo), password=senha) as pdf:
                textos = [p.extract_text() or "" for p in pdf.pages]
        except Exception:  # noqa: BLE001 - senha errada ou PDF inválido: tenta a próxima
            continue
        notas: dict[tuple[str, str, str], NotaLida] = {}
        falhas = 0
        for texto in textos:
            if "nota de negocia" not in _sem_acento(texto[:200]):
                continue  # página sem nota (avisos legais): ignorada
            try:
                _juntar(notas, interpretar_texto(texto))
            except NotaIlegivel:
                falhas += 1
        if not notas:
            raise NotaIlegivel(_MSG_LAYOUT)
        return list(notas.values()), falhas
    raise SenhaDaNota(_MSG_SENHA)


__all__ = ["NotaIlegivel", "NotaLida", "Negocio", "SenhaDaNota", "interpretar_texto", "ler_notas"]
