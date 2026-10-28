"""Utilitários puros de dinheiro (centavos inteiros) e datas."""

import calendar
import re
from datetime import date, datetime
from decimal import Decimal

_PADRAO_VALOR = re.compile(r"^-?(\d{1,3}(\.\d{3})+|\d+)(,\d{1,2})?$")


LIMITE_CENTAVOS = 10**12


class ValorMuitoAlto(ValueError):
    """Valor acima do teto aceito (R$ 10 bilhões)."""


def verificar_magnitude(centavos: int) -> int:
    if abs(centavos) > LIMITE_CENTAVOS:
        raise ValorMuitoAlto("valor muito alto")
    return centavos


def para_centavos(texto: str) -> int:
    """Converte texto BRL (ex.: '1.234,56', '-603,89', 'R$ 10,00') em centavos.
    |valor| acima de 10**12 centavos -> ValorMuitoAlto ('valor muito alto')."""
    limpo = texto.replace("R$", "").replace(" ", "").strip()
    if not _PADRAO_VALOR.match(limpo):
        raise ValueError(f"valor inválido: {texto!r}")
    numero = limpo.replace(".", "").replace(",", ".")
    return verificar_magnitude(int((Decimal(numero) * 100).to_integral_value()))


def formatar_brl(centavos: int) -> str:
    """Formata centavos como 'R$ 1.234,56'; negativos viram '- R$ 1.234,56'."""
    sinal = "- " if centavos < 0 else ""
    reais, cent = divmod(abs(centavos), 100)
    return f"{sinal}R$ {reais:,}".replace(",", ".") + f",{cent:02d}"


def data_br_para_iso(texto: str) -> str:
    """Converte 'DD/MM/AAAA' em 'AAAA-MM-DD'."""
    try:
        return datetime.strptime(texto, "%d/%m/%Y").date().isoformat()
    except ValueError as exc:
        raise ValueError(f"data inválida: {texto!r}") from exc


def iso_para_br(iso: str) -> str:
    """Converte 'AAAA-MM-DD' em 'DD/MM/AAAA'."""
    return date.fromisoformat(iso).strftime("%d/%m/%Y")


def fim_do_mes_iso(iso: str) -> str:
    """'AAAA-MM-DD' -> último dia do mesmo mês, em ISO."""
    d = date.fromisoformat(iso[:10])
    return d.replace(day=calendar.monthrange(d.year, d.month)[1]).isoformat()


def somar_meses(d: date, n: int, dia: int | None = None) -> date:
    """Soma n meses a d; usa `dia` (ou o dia de d) limitado ao último dia do mês."""
    if dia is not None and not 1 <= dia <= 31:
        raise ValueError(f"dia inválido: {dia}")
    indice = d.year * 12 + (d.month - 1) + n
    ano, mes_zero = divmod(indice, 12)
    mes = mes_zero + 1
    ultimo = calendar.monthrange(ano, mes)[1]
    return date(ano, mes, min(dia if dia is not None else d.day, ultimo))
