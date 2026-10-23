"""Visões da tela Contas a pagar: painel de vencimentos, parcelamentos agrupados, recorrentes."""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.dominio.dinheiro import somar_meses
from app.dominio.lancamentos import ErroLancamento
from app.dominio.projecao import HORIZONTES, vencimentos
from app.dominio.recorrentes import gerar_recorrentes
from app.repo import lancamentos as repo_lanc
from app.repo import recorrentes as repo_rec

JANELA_VENCER_DIAS = 7
HORIZONTE_PADRAO = 6
MSG_HORIZONTE = "Horizonte inválido. Use 3, 6 ou 12 meses."


def interpretar_horizonte(texto: str | None) -> int:
    """Query param 'horizonte': None -> 6; senão exatamente '3', '6' ou '12' (ASCII)."""
    if texto is None:
        return HORIZONTE_PADRAO
    if texto in {str(h) for h in HORIZONTES}:
        return int(texto)
    raise ErroLancamento(MSG_HORIZONTE)


def _soma(linhas: list[Any]) -> int:
    return sum(int(r["valor_centavos"]) for r in linhas)


@dataclass(frozen=True)
class Painel:
    horizonte: int
    vencidas: list[Any]
    a_vencer: list[Any]
    proximos: list[Any]
    receitas: list[Any]

    @property
    def total_vencidas_centavos(self) -> int:
        return _soma(self.vencidas)

    @property
    def total_a_vencer_centavos(self) -> int:
        return _soma(self.a_vencer)

    @property
    def total_proximos_centavos(self) -> int:
        return _soma(self.proximos)

    @property
    def total_receitas_centavos(self) -> int:
        return _soma(self.receitas)


@dataclass(frozen=True)
class Parcelamento:
    grupo: str
    descricao: str
    n: int
    pagas: int
    pendentes: int
    total_centavos: int
    restante_centavos: int
    proxima_data: str | None


@dataclass(frozen=True)
class RecorrenteVista:
    id: int
    nome: str
    tipo: str
    dia: int
    valor_estimado_centavos: int
    ultimo_real_centavos: int | None
    ativa: bool
    conta_nome: str | None


def abrir_painel(con: Any, hoje: date, meses: int) -> Painel:
    """Gera as recorrentes do horizonte (idempotente) e monta as listas da tela.

    Saídas: vencidas (< hoje), a vencer (hoje..hoje+7) e próximos (depois disso, até o fim do
    horizonte). Entradas pendentes (receitas recorrentes etc.) vão em `receitas`.
    """
    gerar_recorrentes(con, hoje, meses)
    fim = somar_meses(hoje.replace(day=1), meses)  # exclusivo
    v = vencimentos(con, hoje, JANELA_VENCER_DIAS)
    inicio_proximos = hoje + timedelta(days=JANELA_VENCER_DIAS + 1)
    ultimo_dia = fim - timedelta(days=1)
    proximos = (repo_lanc.saidas_pendentes_entre(
        con, inicio_proximos.isoformat(), ultimo_dia.isoformat())
        if inicio_proximos <= ultimo_dia else [])
    return Painel(
        horizonte=meses, vencidas=list(v.vencidas), a_vencer=list(v.a_vencer),
        proximos=list(proximos),
        receitas=list(repo_lanc.entradas_pendentes_ate(con, fim.isoformat())))


def resumo_parcelamentos(con: Any) -> list[Parcelamento]:
    return [
        Parcelamento(
            grupo=str(r["grupo"]), descricao=str(r["descricao"]), n=int(r["n"]),
            pagas=int(r["pagas"]), pendentes=int(r["pendentes"]),
            total_centavos=int(r["total_centavos"]), restante_centavos=int(r["restante_centavos"]),
            proxima_data=r["proxima_data"])
        for r in repo_lanc.resumo_grupos_parcelas(con)
    ]


def listar_recorrentes(con: Any) -> list[RecorrenteVista]:
    return [
        RecorrenteVista(
            id=int(r["id"]), nome=str(r["nome"]), tipo=str(r["tipo"]), dia=int(r["dia"]),
            valor_estimado_centavos=int(r["valor_estimado_centavos"]),
            ultimo_real_centavos=None if r["ultimo_real_centavos"] is None
            else int(r["ultimo_real_centavos"]),
            ativa=bool(r["ativa"]), conta_nome=r["conta_nome"])
        for r in repo_rec.listar_com_ultimo_real(con)
    ]
