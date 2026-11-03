"""Frases de insight dos gráficos da Início: regras determinísticas sobre os mesmos números do painel."""
from typing import Any

from app.dominio.dinheiro import formatar_brl

Insight = dict[str, str]  # destaque (número grande), texto (frase) e tom ("bom" | "ruim" | "neutro")


def _pct(parte: int, total: int) -> int:
    return round(parte * 100 / total) if total > 0 else 0


def _insight(destaque: str, texto: str, tom: str = "neutro") -> Insight:
    return {"destaque": destaque, "texto": texto, "tom": tom}


def saldo_projetado(pontos: list[dict[str, Any]]) -> Insight:
    ultimo = pontos[-1]
    negativos = [p for p in pontos if p["negativo"]]
    if negativos:
        pior = min(pontos, key=lambda p: p["valor_centavos"])
        return _insight(
            ultimo["valor_fmt"],
            f"Fica negativo a partir de {negativos[0]['rotulo']}; o pior ponto é {pior['valor_fmt']} em {pior['rotulo']}.",
            "ruim")
    menor = min(pontos, key=lambda p: p["valor_centavos"])
    return _insight(ultimo["valor_fmt"],
                    f"Continua positivo em todo o período; o menor saldo é {menor['valor_fmt']} em {menor['rotulo']}.",
                    "bom")


def parcelas_futuras(pontos: list[dict[str, Any]]) -> Insight:
    total = sum(p["valor_centavos"] for p in pontos)
    if total == 0:
        return _insight("R$ 0,00", "Nenhuma parcela futura no período.", "bom")
    primeiro, ultimo = pontos[0], pontos[-1]
    if primeiro["valor_centavos"] > 0 and ultimo["valor_centavos"] != primeiro["valor_centavos"]:
        queda = _pct(abs(primeiro["valor_centavos"] - ultimo["valor_centavos"]), primeiro["valor_centavos"])
        sentido = "cai" if ultimo["valor_centavos"] < primeiro["valor_centavos"] else "sobe"
        texto = (f"Total comprometido no período. A parcela mensal {sentido} {queda}% "
                 f"({primeiro['valor_fmt']} em {primeiro['rotulo']} → {ultimo['valor_fmt']} em {ultimo['rotulo']}).")
        return _insight(formatar_brl(total), texto, "bom" if sentido == "cai" else "ruim")
    return _insight(formatar_brl(total), "Total comprometido no período, em valor estável por mês.")


def maiores_categorias(itens: list[tuple[str, int]], total_saidas: int) -> Insight:
    if not itens or total_saidas <= 0:
        return _insight("—", "Sem gastos neste mês.")
    topo = itens[0]
    soma_top = sum(v for _, v in itens)
    if len(itens) == 1:
        return _insight(f"{_pct(topo[1], total_saidas)}% em {topo[0]}",
                        f"Todas as saídas do mês estão nessa categoria ({formatar_brl(total_saidas)}).")
    return _insight(f"{_pct(topo[1], total_saidas)}% em {topo[0]}",
                    f"As {len(itens)} maiores categorias somam {_pct(soma_top, total_saidas)}% das saídas "
                    f"({formatar_brl(soma_top)} de {formatar_brl(total_saidas)}).")


def cartoes_em_aberto(cartoes: list[dict[str, Any]], saldo_total: int) -> Insight:
    total = sum(c["valor_centavos"] for c in cartoes)
    if total == 0:
        return _insight("R$ 0,00", "Nenhuma fatura de cartão em aberto.", "bom")
    maior = cartoes[0]
    if total > saldo_total:
        return _insight(formatar_brl(total),
                        f"Maior que o saldo em conta ({formatar_brl(saldo_total)}); a maior é {maior['nome']}.", "ruim")
    return _insight(formatar_brl(total),
                    f"Equivale a {_pct(total, saldo_total)}% do saldo em conta; a maior é {maior['nome']}.")


def gastos_do_mes(total_saidas: int, saidas_mes_anterior: int, rotulo_anterior: str,
                  categoria_topo: str | None) -> Insight:
    if total_saidas <= 0:
        return _insight("R$ 0,00", "Sem gastos neste mês.", "bom")
    partes = []
    if saidas_mes_anterior > 0:
        delta = _pct(abs(total_saidas - saidas_mes_anterior), saidas_mes_anterior)
        if total_saidas > saidas_mes_anterior:
            partes.append(f"{delta}% acima de {rotulo_anterior}")
        elif total_saidas < saidas_mes_anterior:
            partes.append(f"{delta}% abaixo de {rotulo_anterior}")
        else:
            partes.append(f"igual a {rotulo_anterior}")
    if categoria_topo:
        partes.append(f"maior fatia: {categoria_topo}")
    tom = "neutro"
    if saidas_mes_anterior > 0 and total_saidas != saidas_mes_anterior:
        tom = "ruim" if total_saidas > saidas_mes_anterior else "bom"
    return _insight(formatar_brl(total_saidas), "; ".join(partes).capitalize() + "." if partes else "", tom)
