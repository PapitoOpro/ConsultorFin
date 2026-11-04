"""Página de relatório (retrato guardado em disco): /relatorio/<id>."""
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from app.dominio.dinheiro import formatar_brl
from app.relatorio.montar import ler_relatorio

router = APIRouter()

MSG_NAO_ENCONTRADO = "Relatório não encontrado ou expirado."
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
CABECALHOS: dict[str, str] = {
    "Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
}


def _numero_br(valor: float, casas: int = 0) -> str:
    texto = f"{valor:,.{casas}f}"
    return texto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def formatar_valor(valor: Any, formato: str) -> str:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return "—"
    if formato == "moeda":
        return formatar_brl(int(valor))
    if formato == "pct":
        return _numero_br(valor, 1) + "%"
    return _numero_br(valor)


def _kpis(retrato: dict[str, Any]) -> list[dict[str, Any]]:
    saida: list[dict[str, Any]] = []
    for k in retrato.get("kpis") or []:
        delta = k.get("delta_pct")
        tem = isinstance(delta, (int, float)) and not isinstance(delta, bool)
        saida.append({
            "rotulo": k.get("rotulo", ""), "texto": formatar_valor(k.get("valor"), str(k.get("formato"))),
            "sem_valor": k.get("valor") is None,
            "delta": (f"{_numero_br(abs(delta), 1)}%" if tem else None),
            "sobe": bool(tem and delta >= 0),
        })
    return saida


def _geracao(retrato: dict[str, Any]) -> str:
    try:
        return datetime.fromisoformat(str(retrato.get("gerado_em"))).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return "data desconhecida"


def _periodo(retrato: dict[str, Any]) -> str | None:
    p = retrato.get("periodo")
    if not isinstance(p, dict):
        return None
    try:
        de = datetime.fromisoformat(str(p["de"])).strftime("%d/%m/%Y")
        ate = datetime.fromisoformat(str(p["ate"])).strftime("%d/%m/%Y")
    except (KeyError, ValueError):
        return None
    return f"{de} a {ate}"


@router.get("/relatorio/{rid}", response_class=HTMLResponse)
def relatorio(request: Request, rid: str) -> Response:
    pasta: Path = request.app.state.dir_relatorios
    retrato = ler_relatorio(pasta, rid)
    if retrato is None:
        return PlainTextResponse(MSG_NAO_ENCONTRADO, status_code=404, headers=CABECALHOS)
    graficos = retrato.get("graficos") or []
    tabela = retrato.get("tabela")
    ctx = {"dados": retrato, "kpis": _kpis(retrato), "gerado_em": _geracao(retrato),
           "periodo": _periodo(retrato), "tem_graficos": bool(graficos), "tem_tabela": bool(tabela)}
    resp = request.app.state.templates.TemplateResponse(request, "relatorio.html", ctx)
    resp.headers.update(CABECALHOS)
    return resp
