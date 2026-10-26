"""Agregações da tela Início: resumo do mês, gastos por categoria, comparativo e gráficos."""
import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.dominio.categorias import despesas_do_mes, entradas_do_mes
from app.dominio.dinheiro import formatar_brl, somar_meses
from app.dominio.lancamentos import ErroLancamento
from app.dominio.projecao import Vencimentos, projetar, vencimentos
from app.dominio.recorrentes import gerar_recorrentes
from app.dominio.saldo import saldo_atual
from app.repo import contas, painel

MSG_MES = "Mês inválido. Use o formato AAAA-MM."
SEM_CATEGORIA = "Sem categoria"
TOP_ROSCA = 5
TOP_BARRAS = 5
LIMITE_LANCAMENTOS = 50
_MES = re.compile(r"[0-9]{4}-[0-9]{2}")
_MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto",
          "setembro", "outubro", "novembro", "dezembro")
_ABREV = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def interpretar_mes(texto: str | None, hoje: date) -> tuple[int, int]:
    """Query param 'mes' ('AAAA-MM', ASCII, 2000-2100); None -> mês de `hoje`."""
    if texto is None:
        return hoje.year, hoje.month
    if not _MES.fullmatch(texto):
        raise ErroLancamento(MSG_MES)
    ano, mes = int(texto[:4]), int(texto[5:])
    if not 1 <= mes <= 12 or not 2000 <= ano <= 2100:
        raise ErroLancamento(MSG_MES)
    return ano, mes


def rotulo_mes(mes_iso: str) -> str:
    """'2027-01' -> 'jan/27'."""
    return f"{_ABREV[int(mes_iso[5:7]) - 1]}/{mes_iso[2:4]}"


def nome_mes(ano: int, mes: int) -> str:
    """(2026, 10) -> 'outubro de 2026'."""
    return f"{_MESES[mes - 1]} de {ano}"


def _limites(ano: int, mes: int) -> tuple[str, str, str]:
    """(primeiro dia, primeiro dia do mês seguinte, último dia), em ISO."""
    ini = date(ano, mes, 1)
    fim = somar_meses(ini, 1)
    ultimo = date(ano, mes, calendar.monthrange(ano, mes)[1])
    return ini.isoformat(), fim.isoformat(), ultimo.isoformat()


@dataclass(frozen=True)
class LinhaLanc:
    data: str
    descricao: str
    valor_centavos: int


@dataclass
class Sub:
    id: int | None
    nome: str
    total_centavos: int = 0
    n: int = 0
    lancamentos: list[LinhaLanc] = field(default_factory=list)


@dataclass
class Categoria:
    id: int | None
    nome: str
    total_centavos: int = 0
    subs: list[Sub] = field(default_factory=list)


def gastos_por_categoria(con: Any, ano: int, mes: int) -> list[Categoria]:
    """Despesas do mês agrupadas Categoria > Subcategoria > lançamentos (até 50 por
    subcategoria), maiores primeiro. Subcategorias somam na categoria-pai; lançamento direto
    na categoria vai para a linha '(sem subcategoria)'. A soma dos totais = despesas_do_mes."""
    ini, fim, _ = _limites(ano, mes)
    raizes: dict[int | None, Categoria] = {}
    for r in painel.despesas_detalhadas(con, ini, fim):
        if r["cat_id"] is None:
            raiz_id, raiz_nome, sub_id, sub_nome = None, SEM_CATEGORIA, None, ""
        elif r["pai_id"] is None:
            raiz_id, raiz_nome, sub_id, sub_nome = int(r["cat_id"]), str(r["cat_nome"]), None, ""
        else:
            raiz_id, raiz_nome = int(r["pai_id"]), str(r["pai_nome"])
            sub_id, sub_nome = int(r["cat_id"]), str(r["cat_nome"])
        cat = raizes.setdefault(raiz_id, Categoria(raiz_id, raiz_nome))
        sub = next((s for s in cat.subs if s.id == sub_id), None)
        if sub is None:
            sub = Sub(sub_id, sub_nome or "(sem subcategoria)")
            cat.subs.append(sub)
        v = int(r["valor"])
        cat.total_centavos += v
        sub.total_centavos += v
        sub.n += 1
        if len(sub.lancamentos) < LIMITE_LANCAMENTOS:
            sub.lancamentos.append(LinhaLanc(str(r["data"]), str(r["descricao"]), v))
    saida = sorted(raizes.values(), key=lambda c: (-c.total_centavos, c.nome))
    for c in saida:
        c.subs.sort(key=lambda s: (-s.total_centavos, s.nome))
    return saida


def top_com_outros(itens: list[tuple[str, int]], n: int) -> list[tuple[str, int]]:
    """Top `n` por valor; o restante (e a categoria 'Outros', se houver) vira 'Outros'."""
    outros = sum(v for nome, v in itens if nome.casefold() == "outros")
    resto = sorted((i for i in itens if i[0].casefold() != "outros"), key=lambda i: -i[1])
    top = resto[:n]
    outros += sum(v for _, v in resto[n:])
    return top + ([("Outros", outros)] if outros > 0 else [])


@dataclass(frozen=True)
class SaldoConta:
    id: int
    nome: str
    tipo: str
    saldo_centavos: int


@dataclass(frozen=True)
class Resumo:
    vazio: bool
    contas: list[SaldoConta]
    saldo_total_centavos: int
    entradas_centavos: int
    saidas_centavos: int
    venc: Vencimentos
    categorias: list[Categoria]
    ano: int
    mes: int

    @property
    def sobra_centavos(self) -> int:
        return self.entradas_centavos - self.saidas_centavos

    @property
    def mes_iso(self) -> str:
        return f"{self.ano:04d}-{self.mes:02d}"

    @property
    def nome_mes(self) -> str:
        return nome_mes(self.ano, self.mes)

    @property
    def de_iso(self) -> str:
        return _limites(self.ano, self.mes)[0]

    @property
    def ate_iso(self) -> str:
        return _limites(self.ano, self.mes)[2]


def resumo_inicio(con: Any, hoje: date, ano: int, mes: int, horizonte: int,
                  janela_dias: int = 7) -> Resumo:
    """Dados da página Início. Gera as recorrentes do horizonte (idempotente)."""
    gerar_recorrentes(con, hoje, horizonte)
    lista = [SaldoConta(int(c["id"]), str(c["nome"]), str(c["tipo"]),
                        saldo_atual(con, int(c["id"]), hoje)) for c in contas.listar(con)]
    total = sum(c.saldo_centavos for c in lista if c.tipo != "cartao")
    return Resumo(
        vazio=not lista or painel.total_lancamentos(con) == 0, contas=lista,
        saldo_total_centavos=total, entradas_centavos=entradas_do_mes(con, ano, mes),
        saidas_centavos=despesas_do_mes(con, ano, mes), venc=vencimentos(con, hoje, janela_dias),
        categorias=gastos_por_categoria(con, ano, mes), ano=ano, mes=mes)


def _ponto(chave: str, rotulo: str, centavos: int) -> dict[str, Any]:
    return {"mes": chave, "rotulo": rotulo, "valor_centavos": centavos,
            "valor_fmt": formatar_brl(centavos)}


def comparativo_mensal(con: Any, ano: int, mes: int) -> list[dict[str, Any]]:
    """[mês anterior, mês selecionado] com entradas e saídas (centavos + formatado)."""
    anterior = somar_meses(date(ano, mes, 1), -1)
    saida: list[dict[str, Any]] = []
    for a, m in ((anterior.year, anterior.month), (ano, mes)):
        chave = f"{a:04d}-{m:02d}"
        e, s = entradas_do_mes(con, a, m), despesas_do_mes(con, a, m)
        saida.append({
            "mes": chave, "rotulo": rotulo_mes(chave), "entradas_centavos": e,
            "saidas_centavos": s, "entradas_fmt": formatar_brl(e), "saidas_fmt": formatar_brl(s)})
    return saida


def dados_graficos(con: Any, hoje: date, resumo: Resumo, horizonte: int) -> dict[str, Any]:
    """Estrutura serializável para os gráficos (centavos inteiros + rótulos pt-BR)."""
    reais = [(c.nome, c.total_centavos) for c in resumo.categorias]
    rosca = [{"nome": n, "valor_centavos": v, "valor_fmt": formatar_brl(v)}
             for n, v in top_com_outros(reais, TOP_ROSCA)]
    maiores = [{"nome": n, "valor_centavos": v, "valor_fmt": formatar_brl(v)}
               for n, v in sorted(reais, key=lambda i: -i[1])[:TOP_BARRAS]]
    pontos = projetar(con, hoje, horizonte)
    comparativo = comparativo_mensal(con, resumo.ano, resumo.mes)
    return {
        "mes": resumo.mes_iso, "horizonte": horizonte, "rosca": rosca, "maiores": maiores,
        "parcelas": [_ponto(p.mes, rotulo_mes(p.mes), p.parcelas_centavos) for p in pontos],
        "saldo_projetado": [
            {**_ponto(p.mes, rotulo_mes(p.mes), p.saldo_final_centavos),
             "negativo": p.saldo_final_centavos < 0} for p in pontos],
        "comparativo": comparativo,
        "fluxo": fluxo_caixa(con, resumo.ano, resumo.mes),
    }


def fluxo_caixa(con: Any, ano: int, mes: int, meses: int = 6) -> list[dict[str, Any]]:
    """Entradas e saídas dos `meses` meses que terminam em ano/mes (ordem cronológica), com as
    mesmas funções do resumo do mês (`entradas_do_mes`/`despesas_do_mes`)."""
    fim = date(ano, mes, 1)
    saida: list[dict[str, Any]] = []
    for i in range(meses - 1, -1, -1):
        d = somar_meses(fim, -i)
        chave = f"{d.year:04d}-{d.month:02d}"
        e, s = entradas_do_mes(con, d.year, d.month), despesas_do_mes(con, d.year, d.month)
        saida.append({
            "mes": chave, "rotulo": rotulo_mes(chave), "entradas_centavos": e,
            "saidas_centavos": s, "entradas_fmt": formatar_brl(e), "saidas_fmt": formatar_brl(s)})
    return saida


def _rotulo_categoria(cat: Any, pai: Any) -> str:
    if cat is None:
        return SEM_CATEGORIA
    return f"{pai} › {cat}" if pai is not None else str(cat)


def ultimos_lancamentos(con: Any, limite: int = 5) -> list[dict[str, Any]]:
    """Últimos lançamentos efetivados (compensados), mais recentes primeiro; valor com sinal."""
    return [{
        "id": int(r["id"]), "data": str(r["data"]), "descricao": str(r["descricao"]),
        "conta": str(r["conta_nome"]), "conta_tipo": str(r["conta_tipo"]),
        "categoria": _rotulo_categoria(r["cat_nome"], r["pai_nome"]), "tipo": str(r["tipo"]),
        "valor_centavos": int(r["valor_centavos"]),
    } for r in painel.ultimos_efetivados(con, limite)]


def _situacao(data: str, hoje: date) -> str:
    dias = (date.fromisoformat(data) - hoje).days
    if dias < 0:
        return f"vencida há {-dias} dia{'s' if dias != -1 else ''}"
    if dias == 0:
        return "vence hoje"
    return f"em {dias} dia{'s' if dias != 1 else ''}"


def proximos_vencimentos(con: Any, hoje: date, limite: int = 6) -> list[dict[str, Any]]:
    """Saídas pendentes mais próximas (vencidas primeiro); `valor_centavos` é o valor a pagar."""
    return [{
        "id": int(r["id"]), "data": str(r["data"]), "descricao": str(r["descricao"]),
        "conta": str(r["conta_nome"]), "conta_tipo": str(r["conta_tipo"]),
        "valor_centavos": -int(r["valor_centavos"]),
        "estimado": bool(r["estimado"]), "dias": (date.fromisoformat(str(r["data"])) - hoje).days,
        "situacao": _situacao(str(r["data"]), hoje),
        "vencida": str(r["data"]) < hoje.isoformat(),
        "categoria": _rotulo_categoria(r["cat_nome"], r["pai_nome"]),
    } for r in painel.saidas_pendentes_proximas(con, limite)]


def variacao_percentual(atual: int, anterior: int) -> float | None:
    """Variação (%) de `atual` sobre `anterior`, 1 casa; None se `anterior` <= 0."""
    if anterior <= 0:
        return None
    return round((atual - anterior) / anterior * 100, 1)


def _pct_texto(valor: float) -> str:
    return f"{abs(valor):,.1f}".replace(",", "_").replace(".", ",").replace("_", ".")


def formatar_variacao(valor: float) -> str:
    """8.2 -> '+8,2%'; -12.0 -> '-12,0%'; 0 -> '0,0%'; milhar com ponto."""
    texto = _pct_texto(valor)
    sinal = "+" if valor > 0 else "-" if valor < 0 else ""
    return f"{sinal}{texto}%"


def legenda_rosca(resumo: Resumo) -> list[dict[str, Any]]:
    """Itens da legenda da rosca (mesma ordem/agrupamento do gráfico): nome, valor e % do total."""
    itens = top_com_outros([(c.nome, c.total_centavos) for c in resumo.categorias], TOP_ROSCA)
    total = sum(v for _, v in itens)
    return [{"nome": n, "valor_centavos": v, "valor_fmt": formatar_brl(v),
             "percentual": f"{_pct_texto(round(v * 1000 / total) / 10)}%" if total > 0 else "0,0%"}
            for n, v in itens]


@dataclass(frozen=True)
class Variacao:
    texto: str          # '+8,2%'
    sentido: str        # 'subiu' | 'caiu' | 'estavel'
    bom: bool | None    # True = favorável, False = desfavorável, None = neutro


def _variacao(atual: int, anterior: int, mais_e_bom: bool) -> Variacao | None:
    v = variacao_percentual(atual, anterior)
    if v is None:
        return None
    if v == 0:
        return Variacao(formatar_variacao(v), "estavel", None)
    return Variacao(formatar_variacao(v), "subiu" if v > 0 else "caiu", (v > 0) == mais_e_bom)


AVISO_SEM_HISTORICO = "sem mês anterior para comparar"
AVISO_ANTERIOR_ZERO = "mês anterior sem movimento"
AVISO_MES_FUTURO = "mês ainda sem movimento"


def _comparacao(atual: int, anterior: int, mais_e_bom: bool, tem_historico: bool,
                futuro: bool) -> tuple[Variacao | None, str]:
    """(variação, aviso): só um dos dois vem preenchido."""
    if futuro and atual == 0:
        return None, AVISO_MES_FUTURO
    v = _variacao(atual, anterior, mais_e_bom)
    if v is not None:
        return v, ""
    return None, AVISO_ANTERIOR_ZERO if tem_historico else AVISO_SEM_HISTORICO


def indicadores_inicio(con: Any, hoje: date, resumo: Resumo) -> dict[str, Any]:
    """Extras do dashboard: comparação com o mês anterior (só se calculável), lançamentos
    recentes e próximos vencimentos."""
    primeiro = date(resumo.ano, resumo.mes, 1)
    ant = somar_meses(primeiro, -1)
    e_ant, s_ant = entradas_do_mes(con, ant.year, ant.month), despesas_do_mes(con, ant.year, ant.month)
    historico = painel.existe_compensado_antes(con, primeiro.isoformat())
    futuro = (resumo.ano, resumo.mes) > (hoje.year, hoje.month)
    v_e, aviso_e = _comparacao(resumo.entradas_centavos, e_ant, True, historico, futuro)
    v_s, aviso_s = _comparacao(resumo.saidas_centavos, s_ant, False, historico, futuro)
    return {
        "mes_anterior": rotulo_mes(f"{ant.year:04d}-{ant.month:02d}"),
        "mes_rotulo": rotulo_mes(resumo.mes_iso),
        "var_entradas": v_e, "aviso_entradas": aviso_e,
        "var_saidas": v_s, "aviso_saidas": aviso_s,
        "legenda_rosca": legenda_rosca(resumo),
        "recentes": ultimos_lancamentos(con),
        "vencimentos": proximos_vencimentos(con, hoje),
    }
