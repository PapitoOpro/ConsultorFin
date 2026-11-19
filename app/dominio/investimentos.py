"""Investimentos (corretora): classificação, importação sem duplicar e resumo mensal.

Módulo independente: não toca em contas, lançamentos, saldo, projeção nem gastos. Valores em
centavos. A evolução é a custo (soma das notas de bolsa), nunca a preço de mercado."""
import hashlib
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.db import sem_acento
from app.dominio.dinheiro import formatar_brl
from app.importacao.clear import ExtratoClear, LinhaClear
from app.repo import investimentos as repo

CATEGORIAS: dict[str, str] = {
    "aporte": "Aporte", "resgate": "Resgate", "day_trade": "Resultado day-trade",
    "custos": "Custos e impostos", "ativos": "Compra/venda de ativos (a custo)",
    "proventos": "Proventos", "outros": "Outros",
}


def classificar(descricao: str, valor_centavos: int) -> str:
    d = sem_acento(descricao)
    if "transferencia" in d:
        return "aporte" if valor_centavos > 0 else "resgate"
    if "ajuste day-trade" in d:
        return "day_trade"
    if "irrf" in d or "liquido das operacoes" in d:
        return "custos"
    if "operacoes em bolsa" in d or "estorno debito compras" in d:
        return "ativos"
    if "rendimentos de clientes" in d or "dividendo" in d or "jscp" in d:
        return "proventos"
    return "outros"


def _chave(conta: str, l: LinhaClear, ocorrencia: int) -> str:
    bruto = "|".join((conta, l.data, l.data_liquidacao, l.descricao, str(l.valor_centavos),
                      str(l.saldo_centavos), str(ocorrencia)))
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()


def divergencia_de_saldo(extrato: ExtratoClear) -> str | None:
    """Data (AAAA-MM-DD) da primeira linha cujo saldo não bate com o anterior + valor."""
    ls = extrato.linhas
    for ant, atual in zip(ls, ls[1:]):
        if atual.saldo_centavos != ant.saldo_centavos + atual.valor_centavos:
            return atual.data
    return None


@dataclass(frozen=True)
class ResultadoImportacao:
    lidos: int
    novos: int
    ignorados: int
    periodo_inicio: str
    periodo_fim: str
    aviso: str | None


def importar_extrato(con: sqlite3.Connection, extrato: ExtratoClear, arquivo_nome: str,
                     agora: datetime) -> ResultadoImportacao:
    """Grava as linhas novas (reimportar período sobreposto não duplica). Sem commit."""
    ls = extrato.linhas
    ini, fim = ls[0].data, ls[-1].data
    imp = repo.criar_importacao(
        con, arquivo_nome=arquivo_nome[:200], conta_corretora=extrato.conta, periodo_inicio=ini,
        periodo_fim=fim, total_lidos=len(ls), total_novos=0, criado_em=agora.isoformat())
    vistas: Counter[tuple[str, ...]] = Counter()
    novos = 0
    for ordem, l in enumerate(ls):
        id_ = (l.data, l.data_liquidacao, l.descricao, str(l.valor_centavos), str(l.saldo_centavos))
        vistas[id_] += 1
        if repo.inserir_lancamento(
                con, conta_corretora=extrato.conta, data=l.data, data_liquidacao=l.data_liquidacao,
                descricao=l.descricao, valor_centavos=l.valor_centavos, saldo_centavos=l.saldo_centavos,
                categoria=classificar(l.descricao, l.valor_centavos), ordem=ordem,
                chave=_chave(extrato.conta, l, vistas[id_]), importacao_id=imp):
            novos += 1
    repo.atualizar_total_novos(con, imp, novos)
    aviso = None
    if (d := divergencia_de_saldo(extrato)) is not None:
        aviso = (f"Importado, mas o saldo do arquivo não bate com os valores a partir de "
                 f"{d[8:]}/{d[5:7]}/{d[:4]}.")
    elif (extrato.saldo_informado_centavos is not None
          and extrato.saldo_informado_centavos != ls[-1].saldo_centavos):
        aviso = "Importado, mas o saldo final não bate com o saldo total informado no arquivo."
    return ResultadoImportacao(len(ls), novos, len(ls) - novos, ini, fim, aviso)


@dataclass
class MesInv:
    mes: str  # AAAA-MM
    aportes: int = 0
    resgates: int = 0
    day_trade_bruto: int = 0
    irrf: int = 0     # imposto retido no day-trade (negativo)
    taxas: int = 0    # corretagem, emolumentos e demais custos das operações (negativo)
    proventos: int = 0
    ativos: int = 0   # soma com sinal (compra = negativo)
    outros: int = 0
    saldo_caixa: int = 0
    # acumulados até o fim do mês
    aportes_liquidos_acum: int = 0
    aplicado_acum: int = 0       # valor aplicado em ativos, a custo (positivo)
    resultado_acum: int = 0      # day-trade líquido + proventos + outros

    @property
    def custos(self) -> int:
        return self.irrf + self.taxas

    @property
    def day_trade_liquido(self) -> int:
        return self.day_trade_bruto + self.custos

    @property
    def aportes_liquidos(self) -> int:
        return self.aportes + self.resgates


@dataclass
class ResumoInvestimentos:
    meses: list[MesInv] = field(default_factory=list)  # cronológico
    saldo_caixa: int = 0
    ultima_data: str | None = None

    @property
    def vazio(self) -> bool:
        return not self.meses


def agregar(linhas: list[Any]) -> ResumoInvestimentos:
    """`linhas`: registros com data, categoria, valor_centavos e saldo_centavos, em ordem cronológica."""
    if not linhas:
        return ResumoInvestimentos()
    por_mes: dict[str, MesInv] = {}
    for l in linhas:
        m = por_mes.setdefault(l["data"][:7], MesInv(mes=l["data"][:7]))
        v, cat = int(l["valor_centavos"]), l["categoria"]
        if cat == "aporte":
            m.aportes += v
        elif cat == "resgate":
            m.resgates += v
        elif cat == "day_trade":
            m.day_trade_bruto += v
        elif cat == "custos":
            if "irrf" in sem_acento(l["descricao"] if "descricao" in l.keys() else ""):
                m.irrf += v
            else:
                m.taxas += v
        elif cat == "proventos":
            m.proventos += v
        elif cat == "ativos":
            m.ativos += v
        else:
            m.outros += v
        m.saldo_caixa = int(l["saldo_centavos"])
    meses = [por_mes[k] for k in sorted(por_mes)]
    aport = aplic = result = 0
    for m in meses:
        aport += m.aportes_liquidos
        aplic += -m.ativos
        result += m.day_trade_liquido + m.proventos + m.outros
        m.aportes_liquidos_acum, m.aplicado_acum, m.resultado_acum = aport, aplic, result
    return ResumoInvestimentos(meses=meses, saldo_caixa=int(linhas[-1]["saldo_centavos"]),
                               ultima_data=linhas[-1]["data"])


def resumo(con: sqlite3.Connection) -> ResumoInvestimentos:
    return agregar(repo.todos(con))


def mes_padrao(r: ResumoInvestimentos, pedido: str | None) -> str | None:
    if pedido in {m.mes for m in r.meses}:
        return pedido
    return r.meses[-1].mes if r.meses else None


def _din(campo: str, centavos: int) -> dict[str, Any]:
    return {f"{campo}_centavos": centavos, campo: formatar_brl(centavos)}


def mes_json(m: MesInv) -> dict[str, Any]:
    return {"mes": m.mes, **_din("aportes", m.aportes), **_din("resgates", m.resgates),
            **_din("day_trade_bruto", m.day_trade_bruto), **_din("custos", m.custos),
            **_din("irrf", m.irrf), **_din("taxas", m.taxas),
            **_din("day_trade_liquido", m.day_trade_liquido), **_din("proventos", m.proventos),
            **_din("aplicado_acum", m.aplicado_acum),
            **_din("aportes_liquidos_acum", m.aportes_liquidos_acum),
            **_din("resultado_acum", m.resultado_acum), **_din("saldo_caixa", m.saldo_caixa)}


@dataclass(frozen=True)
class ResultadoPosicao:
    data: str
    ativos: int
    total_centavos: int


def importar_posicao(con: sqlite3.Connection, posicao: Any, agora: datetime) -> ResultadoPosicao:
    """Grava o retrato da carteira (mesma data e conta: substitui). Sem commit."""
    data = posicao.data or agora.date().isoformat()
    repo.substituir_posicao(
        con, data=data, conta_corretora=posicao.conta, criado_em=agora.isoformat(),
        ativos=[(a.codigo, a.produto, a.classe, a.quantidade, a.preco_centavos, a.valor_centavos)
                for a in posicao.ativos])
    return ResultadoPosicao(data, len(posicao.ativos), sum(a.valor_centavos for a in posicao.ativos))


@dataclass(frozen=True)
class Carteira:
    data: str
    ativos: list[Any]
    total_centavos: int


def carteira_atual(con: sqlite3.Connection) -> Carteira | None:
    linhas = repo.ultima_posicao(con)
    if not linhas:
        return None
    return Carteira(linhas[0]["data"], linhas, sum(int(l["valor_centavos"]) for l in linhas))


def carteira_json(c: Carteira) -> dict[str, Any]:
    return {"data": c.data, "ativos": [
        {"codigo": l["codigo"], "produto": l["produto"], "classe": l["classe"],
         "quantidade": l["quantidade"], **_din("preco", int(l["preco_centavos"])),
         **_din("valor", int(l["valor_centavos"]))} for l in c.ativos],
        **_din("total", c.total_centavos)}


@dataclass(frozen=True)
class DiaDayTrade:
    data: str
    liquido_centavos: int


@dataclass(frozen=True)
class EstatisticaDayTrade:
    dias: list[DiaDayTrade]
    positivos: int
    negativos: int
    melhor: DiaDayTrade | None
    pior: DiaDayTrade | None
    total_centavos: int

    @property
    def taxa_acerto(self) -> int | None:
        operados = self.positivos + self.negativos
        return None if operados == 0 else round(100 * self.positivos / operados)


def resultado_diario(linhas: list[Any]) -> EstatisticaDayTrade:
    """Resultado líquido do day-trade por dia (ajuste + IRRF + custos BM&F) e estatísticas."""
    por_dia: dict[str, int] = {}
    for l in linhas:
        if l["categoria"] in ("day_trade", "custos"):
            por_dia[l["data"]] = por_dia.get(l["data"], 0) + int(l["valor_centavos"])
    # só dias com ajuste de day-trade contam como dia operado (custo de nota de bolsa não)
    operados = {l["data"] for l in linhas if l["categoria"] == "day_trade"}
    dias = [DiaDayTrade(d, v) for d, v in sorted(por_dia.items()) if d in operados]
    return EstatisticaDayTrade(
        dias=dias, positivos=sum(1 for d in dias if d.liquido_centavos > 0),
        negativos=sum(1 for d in dias if d.liquido_centavos < 0),
        melhor=max(dias, key=lambda d: d.liquido_centavos, default=None),
        pior=min(dias, key=lambda d: d.liquido_centavos, default=None),
        total_centavos=sum(d.liquido_centavos for d in dias))


def dias_json(e: EstatisticaDayTrade) -> list[dict[str, Any]]:
    return [{"data": d.data, "valor_centavos": d.liquido_centavos, "valor": formatar_brl(d.liquido_centavos)}
            for d in e.dias]
