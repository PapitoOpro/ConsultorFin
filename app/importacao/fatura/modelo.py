"""Modelo de leitura de fatura de cartão, valores BR e inferência de ano."""

import re
from dataclasses import dataclass, field
from datetime import date

from app.importacao.base import eco

TIPOS_ITEM = ("compra", "servico", "internacional", "encargo", "credito")


class FaturaIlegivel(ValueError):
    """Fatura não pôde ser lida (mensagem em português, sem conteúdo do arquivo)."""


class PdfInvalido(FaturaIlegivel):
    pass


class PdfProtegido(FaturaIlegivel):
    """PDF criptografado: é preciso informar a senha."""


class SenhaIncorreta(FaturaIlegivel):
    pass


class PdfSemTexto(FaturaIlegivel):
    pass


class EmissorNaoSuportado(FaturaIlegivel):
    pass


@dataclass(frozen=True)
class ItemFatura:
    data: str  # ISO AAAA-MM-DD
    descricao: str
    valor_centavos: int  # despesa negativa, crédito positivo
    tipo: str  # um de TIPOS_ITEM
    parcela_atual: int | None
    parcela_total: int | None
    final_cartao: str | None
    categoria_banco: str | None


@dataclass(frozen=True)
class Conferencia:
    calculado_centavos: int
    impresso_centavos: int
    ajustes_resumo_centavos: int

    @property
    def diferenca_centavos(self) -> int:
        return self.impresso_centavos - (self.calculado_centavos + self.ajustes_resumo_centavos)

    @property
    def confere(self) -> bool:
        return self.diferenca_centavos == 0


@dataclass
class FaturaLida:
    emissor: str
    bandeira: str | None
    produto: str | None
    final_cartao: str
    data_vencimento: str
    data_fechamento: str | None
    total_impresso_centavos: int
    ajustes_resumo_centavos: int
    itens: list[ItemFatura] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def calculado_centavos(self) -> int:
        return -sum(i.valor_centavos for i in self.itens)

    def conferencia(self) -> Conferencia:
        return Conferencia(self.calculado_centavos, self.total_impresso_centavos,
                           self.ajustes_resumo_centavos)


_VALOR_BR = re.compile(r"^(R\$)?\s*(-)?\s*(R\$)?\s*([0-9]{1,3}(?:\.[0-9]{3})+|[0-9]+),([0-9]{2})$")


def valor_br_para_centavos(txt: str) -> int:
    """'1.234,56', '-9,99' ou 'R$ 9,99' -> centavos. Inválido: ValueError."""
    m = _VALOR_BR.match(str(txt).strip())
    if not m:
        raise ValueError(f"Valor inválido: {eco(txt)}")
    centavos = int(m.group(4).replace(".", "")) * 100 + int(m.group(5))
    return -centavos if m.group(2) else centavos


def inferir_ano(dia_mes: str, vencimento_iso: str) -> str:
    """'DD/MM' -> ISO AAAA-MM-DD: a maior data ≤ vencimento (29/02 só em bissexto)."""
    try:
        dd, mm = (int(p) for p in dia_mes.strip().split("/"))
        venc = date.fromisoformat(vencimento_iso)
    except ValueError:
        raise ValueError(f"Data inválida: {eco(dia_mes)}") from None
    for ano in range(venc.year, venc.year - 9, -1):
        try:
            d = date(ano, mm, dd)
        except ValueError:
            continue
        if d <= venc:
            return d.isoformat()
    raise ValueError(f"Data inválida: {eco(dia_mes)}")
