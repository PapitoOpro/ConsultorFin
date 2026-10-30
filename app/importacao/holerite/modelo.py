"""Modelo de leitura de holerite."""

from dataclasses import dataclass, field


class HoleriteIlegivel(ValueError):
    """Holerite não pôde ser lido (mensagem em português, sem conteúdo do arquivo)."""


class PdfInvalido(HoleriteIlegivel):
    pass


class PdfSemTexto(HoleriteIlegivel):
    pass


class PdfComSenha(HoleriteIlegivel):
    """PDF criptografado: o holerite não aceita senha."""


class LayoutNaoSuportado(HoleriteIlegivel):
    pass


@dataclass(frozen=True)
class ItemHolerite:
    codigo: str | None
    descricao: str
    referencia: str | None
    natureza: str  # 'provento' | 'desconto'
    valor_centavos: int  # sempre positivo


@dataclass(frozen=True)
class Conferencia:
    soma_proventos: int
    soma_descontos: int
    total_vencimentos: int
    total_descontos: int
    liquido: int

    @property
    def confere(self) -> bool:
        return (
            self.soma_proventos == self.total_vencimentos
            and self.soma_descontos == self.total_descontos
            and self.total_vencimentos - self.total_descontos == self.liquido
        )


@dataclass
class HoleriteLido:
    tipo: str  # 'adiantamento' | 'mensal'
    competencia: str  # 'YYYY-MM'
    empresa: str | None
    total_vencimentos_centavos: int
    total_descontos_centavos: int
    liquido_centavos: int
    itens: list[ItemHolerite] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    def conferencia(self) -> Conferencia:
        return Conferencia(
            soma_proventos=sum(i.valor_centavos for i in self.itens if i.natureza == "provento"),
            soma_descontos=sum(i.valor_centavos for i in self.itens if i.natureza == "desconto"),
            total_vencimentos=self.total_vencimentos_centavos,
            total_descontos=self.total_descontos_centavos,
            liquido=self.liquido_centavos,
        )
