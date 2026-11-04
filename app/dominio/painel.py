"""Agregações da tela Início: resumo do mês, gastos por categoria, comparativo e gráficos."""
import calendar
import re
from urllib.parse import urlencode
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.dominio import insights
from app.dominio.categorias import despesas_do_mes, entradas_do_mes
from app.dominio.dinheiro import formatar_brl, iso_para_br, somar_meses
from app.dominio.lancamentos import ErroLancamento
from app.dominio.projecao import Vencimentos, pendentes_por_mes, projetar, vencimentos
from app.dominio.recorrentes import gerar_recorrentes
from app.dominio.saldo import saldo_atual
from app.repo import contas, faturas as repo_faturas, lancamentos as repo_lanc, painel

MSG_MES = "Mês inválido. Use o formato AAAA-MM."
SEM_CATEGORIA = "Sem categoria"
TOP_ROSCA = 5
TOP_BARRAS = 5
LIMITE_LANCAMENTOS = 50
LIMITE_DETALHE = 15
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
    conta: str = ""
    origem: str = ""  # 'lancamento' | 'fatura_item'


@dataclass
class Sub:
    id: int | None
    nome: str
    total_centavos: int = 0
    n: int = 0
    lancamentos: list[LinhaLanc] = field(default_factory=list)
    maiores: list[LinhaLanc] = field(default_factory=list)  # até LIMITE_DETALHE, maior valor primeiro


@dataclass
class Categoria:
    id: int | None
    nome: str
    total_centavos: int = 0
    subs: list[Sub] = field(default_factory=list)


def gastos_por_categoria(con: Any, ano: int, mes: int, por_pagamento: bool = True) -> list[Categoria]:
    """Despesas do mês agrupadas Categoria > Subcategoria > lançamentos (até 50 por
    subcategoria), maiores primeiro. Subcategorias somam na categoria-pai; lançamento direto
    na categoria vai para a linha '(sem subcategoria)'. Critério de caixa: itens de fatura
    entram no mês do pagamento da fatura (a soma dos totais ≈ `despesas_do_mes`);
    `por_pagamento=False` usa a data da compra (= `consumo_do_mes`)."""
    ini, fim, _ = _limites(ano, mes)
    raizes: dict[int | None, Categoria] = {}
    for r in painel.despesas_detalhadas(con, ini, fim, por_pagamento):
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
        linha = LinhaLanc(str(r["data"]), str(r["descricao"]), v, str(r["conta_nome"]), str(r["origem"]))
        if len(sub.lancamentos) < LIMITE_LANCAMENTOS:
            sub.lancamentos.append(linha)
        sub.maiores.append(linha)
        if len(sub.maiores) > 4 * LIMITE_DETALHE:
            sub.maiores = sorted(sub.maiores, key=lambda x: -x.valor_centavos)[:LIMITE_DETALHE]
    saida = sorted(raizes.values(), key=lambda c: (-c.total_centavos, c.nome))
    for c in saida:
        c.subs.sort(key=lambda s: (-s.total_centavos, s.nome))
        for s in c.subs:
            s.maiores = sorted(s.maiores, key=lambda x: -x.valor_centavos)[:LIMITE_DETALHE]
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
    a_pagar_centavos: int = 0  # saídas pendentes vencidas + a vencer na janela (magnitude)
    a_pagar_vencidas_centavos: int = 0  # parte do total acima que já venceu
    a_pagar_qtd: int = 0

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
    pend = repo_lanc.saidas_pendentes_entre(
        con, None, (hoje + timedelta(days=JANELA_A_PAGAR_DIAS)).isoformat())
    return Resumo(
        a_pagar_centavos=-sum(int(r["valor_centavos"]) for r in pend),
        a_pagar_vencidas_centavos=-sum(int(r["valor_centavos"]) for r in pend
                                       if str(r["data"]) < hoje.isoformat()),
        a_pagar_qtd=len(pend),
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
    top = sorted(reais, key=lambda i: -i[1])[:TOP_BARRAS]
    total_saidas = sum(v for _, v in reais)
    maiores = [{"nome": n, "valor_centavos": v, "valor_fmt": formatar_brl(v),
                "percentual": f"{_pct_texto(round(v * 1000 / total_saidas) / 10)}%" if total_saidas > 0 else "0,0%"}
               for n, v in top]
    pontos = projetar(con, hoje, horizonte)
    comparativo = comparativo_mensal(con, resumo.ano, resumo.mes)
    importadas = _parcelas_importadas_por_mes(con, hoje, horizonte)
    parcelas = [_ponto(p.mes, rotulo_mes(p.mes),
                       p.parcelas_centavos + sum(-int(r["valor_centavos"]) for r in importadas[p.mes]))
                for p in pontos]
    saldo = [{**_ponto(p.mes, rotulo_mes(p.mes), p.saldo_final_centavos),
              "negativo": p.saldo_final_centavos < 0} for p in pontos]
    cartoes = cartoes_em_aberto(con)
    anterior = comparativo[0]
    por_nome = {c.nome: c for c in resumo.categorias}
    top_rosca = {n for n, _ in top_com_outros(reais, TOP_ROSCA) if n.casefold() != "outros"}
    detalhes = {
        "maiores": {n: detalhe_categorias([por_nome[n]], n, resumo, _link_categoria(por_nome[n], resumo))
                    for n, _ in top},
        "rosca": {f["nome"]: _detalhe_rosca(f["nome"], por_nome, top_rosca, resumo) for f in rosca},
        "cartoes": detalhe_cartoes(con),
        **detalhes_projecao(con, hoje, horizonte, pontos),
    }
    return {
        "mes": resumo.mes_iso, "horizonte": horizonte, "rosca": rosca, "maiores": maiores,
        "parcelas": parcelas, "saldo_projetado": saldo, "cartoes": cartoes,
        "comparativo": comparativo,
        "fluxo": fluxo_caixa(con, resumo.ano, resumo.mes),
        "detalhes": detalhes,
        "insights": {
            "saldo": insights.saldo_projetado(saldo),
            "parcelas": insights.parcelas_futuras(parcelas),
            "maiores": insights.maiores_categorias(top, total_saidas),
            "cartoes": insights.cartoes_em_aberto(cartoes, resumo.saldo_total_centavos),
            "gastos": insights.gastos_do_mes(total_saidas, anterior["saidas_centavos"], anterior["rotulo"],
                                             top[0][0] if top else None),
        },
    }


def _parcelas_importadas_por_mes(con: Any, hoje: date, horizonte: int) -> dict[str, list[Any]]:
    """Parcelas já lançadas em faturas importadas, por mês de vencimento da fatura ('AAAA-MM')."""
    primeiro = hoje.replace(day=1)
    por_mes: dict[str, list[Any]] = {somar_meses(primeiro, i).isoformat()[:7]: [] for i in range(horizonte)}
    for r in repo_faturas.parcelas_importadas(con, primeiro.isoformat(), somar_meses(primeiro, horizonte).isoformat()):
        por_mes[str(r["data_vencimento"])[:7]].append(r)
    return por_mes


def detalhes_projecao(con: Any, hoje: date, horizonte: int, pontos: list[Any]) -> dict[str, dict[str, Any]]:
    """Origem de cada mês dos gráficos Saldo projetado e Parcelas futuras (chave = rótulo do mês):
    os maiores pendentes que o compõem, com a mesma base de `projetar`."""
    nomes = {int(c["id"]): str(c["nome"]) for c in contas.listar(con)}
    por_mes = pendentes_por_mes(con, hoje, horizonte)
    importadas = _parcelas_importadas_por_mes(con, hoje, horizonte)

    def linha(r: Any) -> dict[str, str]:
        v = int(r["valor_centavos"])
        return {"data": iso_para_br(str(r["data"])), "descricao": str(r["descricao"]),
                "valor_fmt": formatar_brl(v), "conta": nomes.get(int(r["conta_id"]), ""),
                "origem": "Estimado" if r["estimado"] else "Pendente", "categoria": ""}

    def montar(titulo: str, total: int, rows: list[Any], nota: str) -> dict[str, Any]:
        top = sorted(rows, key=lambda r: -abs(int(r["valor_centavos"])))[:LIMITE_DETALHE]
        return {"titulo": titulo, "total_fmt": formatar_brl(total), "n": len(rows),
                "restantes": len(rows) - len(top), "link": "/contas-a-pagar", "link_rotulo": "Ver em Contas a pagar",
                "nota": nota, "linhas": [linha(r) for r in top]}

    saldo: dict[str, Any] = {}
    parcelas: dict[str, Any] = {}
    for p in pontos:
        rot, rows = rotulo_mes(p.mes), por_mes[p.mes]
        inicio = p.saldo_final_centavos - p.entrada_pendente_centavos + p.saida_pendente_centavos
        nota = (f"Saldo no início do mês {formatar_brl(inicio)} + entradas pendentes "
                f"{formatar_brl(p.entrada_pendente_centavos)} − saídas pendentes {formatar_brl(p.saida_pendente_centavos)}.")
        if p.mes == next(iter(por_mes)):
            nota += " Inclui pendências vencidas."
        saldo[rot] = montar(f"Saldo ao fim de {rot}", p.saldo_final_centavos, rows, nota)
        par = [r for r in rows if int(r["valor_centavos"]) < 0 and r["grupo_parcela_uuid"]]
        imp = importadas[p.mes]
        total_imp = sum(-int(r["valor_centavos"]) for r in imp)
        det = montar(f"Parcelas de {rot}", p.parcelas_centavos + total_imp, par,
                     "Parcelas pendentes + parcelas de faturas já importadas neste mês.")
        linhas_imp = [{"data": iso_para_br(str(r["data"])), "descricao": f"{r['descricao']} ({r['parcela_atual']}/{r['parcela_total']})",
                       "valor_fmt": formatar_brl(-int(r["valor_centavos"])), "conta": nomes.get(int(r["conta_id"]), ""),
                       "origem": "Fatura importada", "categoria": ""} for r in imp]
        det["linhas"] = (det["linhas"] + linhas_imp)[:LIMITE_DETALHE]
        det["n"] += len(imp)
        det["restantes"] = det["n"] - len(det["linhas"])
        parcelas[rot] = det
    return {"saldo": saldo, "parcelas": parcelas}


def _link_categoria(cat: Categoria, resumo: Resumo) -> str:
    """Extrato filtrado por período e categoria (aproximado: o painel usa a data da compra)."""
    return "/lancamentos?" + urlencode({
        "de": resumo.de_iso, "ate": resumo.ate_iso, "categoria_id": "sem" if cat.id is None else cat.id,
        "tipo": "saida", "status": "compensados"})


def _detalhe_rosca(nome: str, por_nome: dict[str, Categoria], top_rosca: set[str],
                   resumo: Resumo) -> dict[str, Any]:
    if nome in por_nome and nome in top_rosca:
        return detalhe_categorias([por_nome[nome]], nome, resumo, _link_categoria(por_nome[nome], resumo))
    resto = [c for n, c in por_nome.items() if n not in top_rosca]  # fatia "Outros"
    return detalhe_categorias(resto, "Outros (categorias menores)", resumo, None)


def detalhe_categorias(cats: list[Categoria], titulo: str, resumo: Resumo,
                       link: str | None) -> dict[str, Any]:
    """Origem do valor de uma categoria: os maiores lançamentos/itens de fatura que a compõem,
    com conta, tipo de origem e subcategoria (mesma base de `gastos_por_categoria`)."""
    linhas = sorted(((c.nome, s.nome, l) for c in cats for s in c.subs for l in s.maiores),
                    key=lambda t: -t[2].valor_centavos)[:LIMITE_DETALHE]
    n = sum(s.n for c in cats for s in c.subs)
    return {
        "titulo": titulo, "total_fmt": formatar_brl(sum(c.total_centavos for c in cats)),
        "n": n, "restantes": n - len(linhas), "link": link, "link_rotulo": "Ver no extrato", "nota": "",
        "linhas": [{
            "data": iso_para_br(l.data), "descricao": l.descricao, "valor_fmt": formatar_brl(l.valor_centavos),
            "conta": l.conta,
            "origem": "Compra no cartão" if l.origem == "fatura_item" else "Lançamento na conta",
            "categoria": cat if sub_.startswith("(") else f"{cat} › {sub_}"} for cat, sub_, l in linhas],
    }


def detalhe_cartoes(con: Any) -> dict[str, Any]:
    """Origem do valor em aberto de cada cartão: vencimento, total impresso e maiores compras."""
    saida: dict[str, Any] = {}
    for c in contas.listar(con):
        if c["tipo"] != "cartao":
            continue
        faturas = repo_faturas.abertas_da_conta(con, int(c["id"]))
        if not faturas:
            continue
        itens = [(f, i) for f in faturas for i in repo_faturas.itens(con, int(f["id"]))]
        top = sorted(itens, key=lambda t: int(t[1]["valor_centavos"]))[:LIMITE_DETALHE]  # compras são negativas
        total = sum(int(f["total_impresso_centavos"]) for f in faturas)
        nota = "; ".join(f"vence em {iso_para_br(str(f['data_vencimento']))} · total impresso "
                         f"{formatar_brl(int(f['total_impresso_centavos']))}" for f in faturas)
        saida[str(c["nome"])] = {
            "titulo": str(c["nome"]), "total_fmt": formatar_brl(total), "n": len(itens),
            "restantes": len(itens) - len(top), "link": "/contas-a-pagar", "link_rotulo": "Ver em Contas a pagar",
            "nota": f"Fatura em aberto: {nota}. O total impresso pode incluir saldo anterior e encargos.",
            "linhas": [{
                "data": iso_para_br(str(i["data"])), "descricao": str(i["descricao"]) + (
                    f" ({i['parcela_atual']}/{i['parcela_total']})" if i["parcela_atual"] else ""),
                "valor_fmt": formatar_brl(-int(i["valor_centavos"])), "conta": str(c["nome"]),
                "origem": "Compra no cartão", "categoria": ""} for _, i in top],
        }
    return saida


def cartoes_em_aberto(con: Any) -> list[dict[str, Any]]:
    """Fatura em aberto por cartão (maior primeiro); cartões sem fatura em aberto ficam de fora."""
    itens = [(str(c["nome"]), repo_faturas.total_em_aberto(con, int(c["id"])))
             for c in contas.listar(con) if c["tipo"] == "cartao"]
    return [{"nome": n, "valor_centavos": v, "valor_fmt": formatar_brl(v)}
            for n, v in sorted(itens, key=lambda i: -i[1]) if v > 0]


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


MAX_VENCIDAS_INICIO = 3
MAX_A_VENCER_INICIO = 5
TOTAL_VENCIMENTOS_INICIO = 6
JANELA_A_VENCER_DIAS = 7
JANELA_A_PAGAR_DIAS = 30


def selecionar_vencimentos(
    itens: list[dict[str, Any]], total: int = TOTAL_VENCIMENTOS_INICIO,
) -> tuple[list[dict[str, Any]], int, int]:
    """Escolhe o que a lista do Início mostra. `itens` vem por data, com `dias` e `vencida`.

    Vencidas primeiro (no máximo 3), depois TODAS as que vencem em até 7 dias (hoje..+7, no
    máximo 5); itens além de 7 dias só entram se houver espaço (até `total` no conjunto) e
    nenhuma conta a vencer ficou de fora. Devolve (lista, vencidas omitidas, a vencer omitidas).
    """
    vencidas = [i for i in itens if i["vencida"]]
    a_vencer = [i for i in itens if not i["vencida"] and i["dias"] <= JANELA_A_VENCER_DIAS]
    depois = [i for i in itens if not i["vencida"] and i["dias"] > JANELA_A_VENCER_DIAS]
    mostrar = vencidas[:MAX_VENCIDAS_INICIO] + a_vencer[:MAX_A_VENCER_INICIO]
    mais_venc = max(0, len(vencidas) - MAX_VENCIDAS_INICIO)
    mais_prox = max(0, len(a_vencer) - MAX_A_VENCER_INICIO)
    if mais_prox == 0:
        mostrar += depois[:max(0, total - len(mostrar))]
    return mostrar, mais_venc, mais_prox


def _vencimento(r: Any, hoje: date) -> dict[str, Any]:
    return {
        "id": int(r["id"]), "data": str(r["data"]), "descricao": str(r["descricao"]),
        "conta": str(r["conta_nome"]), "conta_tipo": str(r["conta_tipo"]),
        "conta_id": int(r["conta_id"]), "origem": str(r["origem"]),
        "valor_centavos": -int(r["valor_centavos"]),
        "estimado": bool(r["estimado"]), "dias": (date.fromisoformat(str(r["data"])) - hoje).days,
        "situacao": _situacao(str(r["data"]), hoje),
        "vencida": str(r["data"]) < hoje.isoformat(),
        "categoria": _rotulo_categoria(r["cat_nome"], r["pai_nome"]),
    }


def proximos_vencimentos_com_resto(con: Any, hoje: date) -> tuple[list[dict[str, Any]], int, int]:
    """Saídas pendentes para a lista do Início (regra de `selecionar_vencimentos`) e quantas
    vencidas / a vencer ficaram de fora."""
    return selecionar_vencimentos(_agrupar_faturas_previstas(
        [_vencimento(r, hoje) for r in painel.saidas_pendentes_proximas(con, 1000)]))


def _agrupar_faturas_previstas(itens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Junta os itens da fatura prevista (origem 'fatura_prevista') de mesma conta e data em
    uma linha única; lançamentos avulsos e faturas importadas passam como estão (ordem por data)."""
    grupos: dict[tuple[str, str], dict[str, Any]] = {}
    saida: list[dict[str, Any]] = []
    for i in itens:
        if i["origem"] != "fatura_prevista":
            saida.append(i)
            continue
        g = grupos.get((i["conta"], i["data"]))
        if g is None:
            g = grupos[(i["conta"], i["data"])] = {
                **i, "id": f"fatura-{i['conta_id']}-{i['data']}",
                "descricao": f"Fatura prevista · {i['conta']}", "valor_centavos": 0, "estimado": True}
            saida.append(g)
        g["valor_centavos"] += i["valor_centavos"]
    return sorted(saida, key=lambda x: x["data"])


def proximos_vencimentos(con: Any, hoje: date, limite: int = 6) -> list[dict[str, Any]]:
    """Saídas pendentes mais próximas (vencidas primeiro); `valor_centavos` é o valor a pagar."""
    return [_vencimento(r, hoje) for r in painel.saidas_pendentes_proximas(con, limite)]


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
    venc, mais_venc, mais_prox = proximos_vencimentos_com_resto(con, hoje)
    return {
        "mes_anterior": rotulo_mes(f"{ant.year:04d}-{ant.month:02d}"),
        "mes_rotulo": rotulo_mes(resumo.mes_iso),
        "var_entradas": v_e, "aviso_entradas": aviso_e,
        "var_saidas": v_s, "aviso_saidas": aviso_s,
        "legenda_rosca": legenda_rosca(resumo),
        "recentes": ultimos_lancamentos(con),
        "vencimentos": venc, "vencimentos_mais_vencidas": mais_venc, "vencimentos_mais_a_vencer": mais_prox,
    }
