"""Consultas do consultor: finas camadas sobre o domínio, devolvendo dicts serializáveis.

Somente `resumo`, `vencimentos` e `projecao` escrevem: materializam as contas fixas e receitas
recorrentes (`gerar_recorrentes`, idempotente, como a tela Início) e informam quantas criaram em
`recorrentes_geradas`. As demais são puramente leitura.
"""
from dataclasses import replace
from datetime import date, timedelta
from typing import Any

from app import config
from app.dominio import categorias as dom_categorias
from app.dominio import lancamentos as dom_lanc
from app.dominio import painel, projecao as dom_proj
from app.dominio.dinheiro import formatar_brl, iso_para_br
from app.dominio.recorrentes import gerar_recorrentes
from app.dominio.saldo import saldo_atual
from app.repo import contas as repo_contas

MAX_LIMITE = 200
LIMITE_PADRAO = 50
DIAS_PADRAO = 30


def dinheiro(campo: str, centavos: int) -> dict[str, Any]:
    return {f"{campo}_centavos": centavos, campo: formatar_brl(centavos)}


def data(campo: str, iso: str | None) -> dict[str, Any]:
    return {campo: iso, f"{campo}_br": None if iso is None else iso_para_br(iso)}


def _linha_saldo(con: Any, hoje: date, c: Any) -> dict[str, Any]:
    return {"id": int(c["id"]), "nome": c["nome"], "tipo": c["tipo"],
            **dinheiro("saldo", saldo_atual(con, int(c["id"]), hoje))}


def saldos(con: Any, hoje: date) -> dict[str, Any]:
    linhas = [_linha_saldo(con, hoje, c) for c in repo_contas.listar(con)]
    total = sum(l["saldo_centavos"] for l in linhas if l["tipo"] != "cartao")
    return {"contas": linhas, **dinheiro("saldo_total", total),
            "observacao": "O total não inclui contas de cartão.", **data("hoje", hoje.isoformat())}


def _mes(hoje: date, mes: str | None) -> tuple[int, int]:
    return painel.interpretar_mes(mes, hoje)


def _meses_ate(hoje: date, fim: date, minimo: int = 1) -> int:
    """Quantos meses (a partir do mês de `hoje`) cobrem até `fim`."""
    return max(minimo, (fim.year * 12 + fim.month) - (hoje.year * 12 + hoje.month) + 1)


def _lanc_pendente(r: Any) -> dict[str, Any]:
    return {"id": int(r["id"]), "descricao": r["descricao"], "conta_id": int(r["conta_id"]),
            **data("data", r["data"]), **dinheiro("valor", int(r["valor_centavos"])),
            "estimado": bool(r["estimado"]), "recorrente": r["recorrente_id"] is not None,
            "parcela": (None if not r["grupo_parcela_uuid"]
                        else f"{r['parcela_atual']}/{r['parcela_total']}")}


def _totais_venc(v: dom_proj.Vencimentos) -> dict[str, Any]:
    """Totais como magnitudes positivas 'a pagar' (os itens mantêm valor negativo)."""
    return {**dinheiro("vencidas_total_a_pagar", -sum(int(r["valor_centavos"]) for r in v.vencidas)),
            **dinheiro("a_vencer_total_a_pagar", -sum(int(r["valor_centavos"]) for r in v.a_vencer))}


def resumo(con: Any, hoje: date, mes: str | None = None) -> dict[str, Any]:
    """Mesmos números da tela Início. Gera as recorrentes da janela de vencimentos (escreve,
    idempotente)."""
    ano, m = _mes(hoje, mes)
    geradas = gerar_recorrentes(
        con, hoje, _meses_ate(hoje, hoje + timedelta(days=config.JANELA_VENCER_DIAS)))
    entradas = dom_categorias.entradas_do_mes(con, ano, m)
    saidas = dom_categorias.despesas_do_mes(con, ano, m)
    lista = saldos(con, hoje)
    venc = dom_proj.vencimentos(con, hoje, config.JANELA_VENCER_DIAS)
    cats = painel.gastos_por_categoria(con, ano, m)
    return {
        **data("hoje", hoje.isoformat()), "mes": f"{ano:04d}-{m:02d}",
        "recorrentes_geradas": geradas, **dinheiro("entradas", entradas),
        **dinheiro("saidas", saidas), **dinheiro("sobra", entradas - saidas),
        **dinheiro("saldo_total", lista["saldo_total_centavos"]), "contas": lista["contas"],
        "vencidas": len(venc.vencidas), "a_vencer": len(venc.a_vencer), **_totais_venc(venc),
        "janela_dias": config.JANELA_VENCER_DIAS,
        "maiores_categorias": [
            {"nome": n, **dinheiro("total", v)}
            for n, v in painel.top_com_outros([(c.nome, c.total_centavos) for c in cats],
                                              painel.TOP_ROSCA)],
        "comparativo": [
            {"mes": c["mes"], **dinheiro("entradas", c["entradas_centavos"]),
             **dinheiro("saidas", c["saidas_centavos"])}
            for c in painel.comparativo_mensal(con, ano, m)],
    }


def vencimentos(con: Any, hoje: date, dias: int = config.JANELA_VENCER_DIAS) -> dict[str, Any]:
    """Saídas pendentes vencidas e a vencer em `dias`. Gera as recorrentes dos meses cobertos
    (escreve, idempotente). Itens têm valor negativo; os totais `*_total_a_pagar` são
    magnitudes positivas."""
    fim = hoje + timedelta(days=dias)
    geradas = gerar_recorrentes(con, hoje, _meses_ate(hoje, fim, 2))
    v = dom_proj.vencimentos(con, hoje, dias)
    return {"dias": dias, **data("hoje", hoje.isoformat()), **data("ate", fim.isoformat()),
            "recorrentes_geradas": geradas,
            "vencidas": [_lanc_pendente(r) for r in v.vencidas],
            "a_vencer": [_lanc_pendente(r) for r in v.a_vencer], **_totais_venc(v)}


def projecao(con: Any, hoje: date, meses: int = config.MESES_PADRAO) -> dict[str, Any]:
    """Projeção de saldo. Gera as recorrentes dos `meses` meses (escreve, idempotente)."""
    if meses not in dom_proj.HORIZONTES:
        raise ValueError(f"Horizonte inválido: {meses} (use 3, 6 ou 12 meses).")
    geradas = gerar_recorrentes(con, hoje, meses)
    pontos = dom_proj.projetar(con, hoje, meses)
    return {"meses": meses, **data("hoje", hoje.isoformat()), "recorrentes_geradas": geradas,
            "primeiro_mes": pontos[0].mes, "ultimo_mes": pontos[-1].mes, "pontos": [
                {"mes": p.mes, **dinheiro("parcelas", p.parcelas_centavos),
                 **dinheiro("saida_pendente", p.saida_pendente_centavos),
                 **dinheiro("entrada_pendente", p.entrada_pendente_centavos),
                 **dinheiro("saldo_final", p.saldo_final_centavos),
                 "negativo": p.saldo_final_centavos < 0} for p in pontos]}


def gastos(con: Any, hoje: date, mes: str | None = None) -> dict[str, Any]:
    ano, m = _mes(hoje, mes)
    return {
        **data("hoje", hoje.isoformat()), "mes": f"{ano:04d}-{m:02d}",
        **dinheiro("saidas", dom_categorias.despesas_do_mes(con, ano, m)),
        "categorias": [
            {"id": c.id, "nome": c.nome, **dinheiro("total", c.total_centavos),
             "subcategorias": [{"id": s.id, "nome": s.nome, **dinheiro("total", s.total_centavos),
                                "n_lancamentos": s.n} for s in c.subs]}
            for c in painel.gastos_por_categoria(con, ano, m)]}


def lancamentos(con: Any, hoje: date, *, de: str | None = None, ate: str | None = None,
                conta_id: int | None = None, categoria_id: int | None = None,
                sem_categoria: bool = False, tipo: str | None = None, status: str = "todos",
                texto: str = "", limite: int = LIMITE_PADRAO) -> dict[str, Any]:
    """Extrato filtrado, MAIS RECENTES PRIMEIRO. Sem `de`: últimos 30 dias; sem `ate`: aberto
    (pendentes futuros aparecem)."""
    if not 1 <= limite <= MAX_LIMITE:
        raise dom_lanc.ErroLancamento(f"O limite deve estar entre 1 e {MAX_LIMITE}.")
    if tipo is not None and tipo not in dom_lanc.TIPOS:
        raise dom_lanc.ErroLancamento("Tipo inválido.")
    if status not in dom_lanc.STATUS:
        raise dom_lanc.ErroLancamento("Status inválido.")
    de = de or (hoje - timedelta(days=DIAS_PADRAO)).isoformat()
    base = dom_lanc.Filtros(de=de, ate=ate, conta_id=conta_id, categoria_id=categoria_id,
                            sem_categoria=sem_categoria, tipo=tipo, texto=texto.strip()[:100],
                            status=status, recentes_primeiro=True)
    linhas: list[Any] = []
    primeira = dom_lanc.listar(con, base)
    pagina = primeira
    while True:
        linhas.extend(pagina.linhas)
        if len(linhas) >= limite or pagina.pagina >= pagina.paginas:
            break
        pagina = dom_lanc.listar(con, replace(base, pagina=pagina.pagina + 1))
    return {
        **data("hoje", hoje.isoformat()), "periodo": {**data("de", de), **data("ate", ate)},
        "limite": limite, "total": primeira.total, "truncado": primeira.total > limite,
        **dinheiro("entradas", primeira.entradas_centavos),
        **dinheiro("saidas", primeira.saidas_centavos),
        **dinheiro("liquido", primeira.liquido_centavos),
        **dinheiro("pendentes", primeira.pendentes_centavos),
        "lancamentos": [
            {"id": l.id, **data("data", l.data), "descricao": l.descricao, "conta": l.conta_nome,
             "categoria_id": l.categoria_id, "categoria": l.categoria_nome,
             **dinheiro("valor", l.valor_centavos), "tipo": l.tipo, "origem": l.origem,
             "compensado": l.compensado, "estimado": l.estimado, "observacao": l.observacao}
            for l in linhas[:limite]]}


def contas(con: Any, hoje: date) -> dict[str, Any]:
    return {"contas": [
        {"id": int(c["id"]), "nome": c["nome"], "tipo": c["tipo"], "instituicao": c["instituicao"],
         "final_numero": c["final_numero"],
         **dinheiro("saldo_inicial", int(c["saldo_inicial_centavos"])),
         **data("data_saldo_inicial", c["data_saldo_inicial"]),
         **dinheiro("saldo", saldo_atual(con, int(c["id"]), hoje))}
        for c in repo_contas.listar(con)]}


def categorias(con: Any, hoje: date) -> dict[str, Any]:
    return {"categorias": [
        {"id": int(c["id"]), "nome": c["nome"], "subcategorias": [
            {"id": int(s["id"]), "nome": s["nome"]}
            for s in dom_categorias.listar_subcategorias(con, int(c["id"]))]}
        for c in dom_categorias.listar_categorias(con)]}
