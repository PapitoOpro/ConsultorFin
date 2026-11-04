"""Especificação declarativa de relatório (JSON do consultor) e sua validação."""
from dataclasses import dataclass
from datetime import date
from typing import Any

FORMATOS: tuple[str, ...] = ("moeda", "inteiro", "pct")
TIPOS_GRAFICO: tuple[str, ...] = ("linha", "barras", "barras_h", "rosca")
LARGURAS: tuple[str, ...] = ("cheia", "meia")
MAX_TITULO = 120
MAX_OBSERVACAO = 300
MAX_KPIS = 5
MAX_GRAFICOS = 6
MAX_SQL = 4000


class ErroRelatorio(ValueError):
    """Especificação ou execução de relatório inválida."""


@dataclass(frozen=True)
class Kpi:
    rotulo: str
    formato: str
    sql: str
    delta_sql: str | None


@dataclass(frozen=True)
class Grafico:
    tipo: str
    titulo: str
    formato: str
    largura: str
    sql: str


@dataclass(frozen=True)
class Tabela:
    titulo: str
    sql: str


@dataclass(frozen=True)
class Espec:
    titulo: str
    periodo: tuple[str, str] | None
    observacao: str
    kpis: tuple[Kpi, ...]
    graficos: tuple[Grafico, ...]
    tabela: Tabela | None


def _objeto(bruto: object, onde: str, permitidas: set[str], obrigatorias: set[str]) -> dict[str, Any]:
    if not isinstance(bruto, dict):
        raise ErroRelatorio(f"{onde}: deve ser um objeto JSON.")
    extras = sorted(str(k) for k in bruto if k not in permitidas)
    if extras:
        raise ErroRelatorio(f"{onde}: chave(s) desconhecida(s): {', '.join(extras)}.")
    faltam = sorted(obrigatorias - set(bruto))
    if faltam:
        raise ErroRelatorio(f"{onde}: campo(s) obrigatório(s) ausente(s): {', '.join(faltam)}.")
    return bruto


def _texto(valor: object, onde: str, campo: str, maximo: int) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise ErroRelatorio(f"{onde}: {campo} deve ser um texto não vazio.")
    if len(valor) > maximo:
        raise ErroRelatorio(f"{onde}: {campo} excede {maximo} caracteres.")
    return valor


def _escolha(valor: object, onde: str, campo: str, opcoes: tuple[str, ...]) -> str:
    if valor not in opcoes:
        raise ErroRelatorio(f"{onde}: {campo} deve ser um de {', '.join(opcoes)}.")
    return str(valor)


def _lista(valor: object, onde: str, campo: str, maximo: int) -> list[Any]:
    if not isinstance(valor, list):
        raise ErroRelatorio(f"{onde}: {campo} deve ser uma lista.")
    if len(valor) > maximo:
        raise ErroRelatorio(f"{onde}: {campo} aceita no máximo {maximo} itens.")
    return valor


def _kpi(bruto: object, i: int) -> Kpi:
    onde = f"kpis[{i}]"
    d = _objeto(bruto, onde, {"rotulo", "formato", "sql", "delta_sql"}, {"rotulo", "formato", "sql"})
    delta = d.get("delta_sql")
    return Kpi(
        rotulo=_texto(d["rotulo"], onde, "rotulo", MAX_TITULO),
        formato=_escolha(d["formato"], onde, "formato", FORMATOS),
        sql=_texto(d["sql"], onde, "sql", MAX_SQL),
        delta_sql=None if delta is None else _texto(delta, onde, "delta_sql", MAX_SQL),
    )


def _grafico(bruto: object, i: int) -> Grafico:
    onde = f"graficos[{i}]"
    d = _objeto(bruto, onde, {"tipo", "titulo", "formato", "largura", "sql"},
                {"tipo", "titulo", "formato", "sql"})
    return Grafico(
        tipo=_escolha(d["tipo"], onde, "tipo", TIPOS_GRAFICO),
        titulo=_texto(d["titulo"], onde, "titulo", MAX_TITULO),
        formato=_escolha(d["formato"], onde, "formato", FORMATOS),
        largura=_escolha(d.get("largura", "meia"), onde, "largura", LARGURAS),
        sql=_texto(d["sql"], onde, "sql", MAX_SQL),
    )


def _tabela(bruto: object) -> Tabela:
    d = _objeto(bruto, "tabela", {"titulo", "sql"}, {"titulo", "sql"})
    return Tabela(titulo=_texto(d["titulo"], "tabela", "titulo", MAX_TITULO),
                  sql=_texto(d["sql"], "tabela", "sql", MAX_SQL))


def _data_iso(valor: object, campo: str) -> str:
    try:
        if not isinstance(valor, str):
            raise ValueError
        return date.fromisoformat(valor).isoformat()
    except ValueError:
        raise ErroRelatorio(f"periodo: {campo} deve ser uma data ISO (AAAA-MM-DD).") from None


def _periodo(bruto: object) -> tuple[str, str]:
    d = _objeto(bruto, "periodo", {"de", "ate"}, {"de", "ate"})
    de, ate = _data_iso(d["de"], "de"), _data_iso(d["ate"], "ate")
    if de > ate:
        raise ErroRelatorio("periodo: 'de' não pode ser posterior a 'ate'.")
    return de, ate


def validar_espec(bruto: object) -> Espec:
    """Valida o JSON do relatório e devolve a Espec imutável; erros viram ErroRelatorio."""
    d = _objeto(bruto, "espec", {"titulo", "periodo", "observacao", "kpis", "graficos", "tabela"},
                {"titulo", "observacao"})
    kpis = _lista(d.get("kpis", []), "espec", "kpis", MAX_KPIS)
    graficos = _lista(d.get("graficos", []), "espec", "graficos", MAX_GRAFICOS)
    return Espec(
        titulo=_texto(d["titulo"], "espec", "titulo", MAX_TITULO),
        periodo=_periodo(d["periodo"]) if d.get("periodo") is not None else None,
        observacao=_texto(d["observacao"], "espec", "observacao", MAX_OBSERVACAO),
        kpis=tuple(_kpi(k, i) for i, k in enumerate(kpis)),
        graficos=tuple(_grafico(g, i) for i, g in enumerate(graficos)),
        tabela=_tabela(d["tabela"]) if d.get("tabela") is not None else None,
    )


ESPEC_EXEMPLO: dict[str, Any] = {
    "titulo": "Gastos por categoria",
    "periodo": {"de": "2026-01-01", "ate": "2026-01-31"},
    "observacao": "Resumo dos gastos do mês e das maiores categorias.",
    "kpis": [
        {
            "rotulo": "Total gasto",
            "formato": "moeda",
            "sql": "SELECT -SUM(valor_centavos) FROM lancamentos WHERE valor_centavos < 0",
        },
    ],
    "graficos": [
        {
            "tipo": "barras_h",
            "titulo": "Top categorias",
            "formato": "moeda",
            "largura": "cheia",
            "sql": "SELECT categoria, -SUM(valor_centavos) AS valor FROM lancamentos "
                   "WHERE valor_centavos < 0 GROUP BY categoria ORDER BY valor DESC LIMIT 10",
        },
    ],
    "tabela": {
        "titulo": "Maiores gastos",
        "sql": "SELECT data, descricao, valor_centavos FROM lancamentos "
               "WHERE valor_centavos < 0 ORDER BY valor_centavos LIMIT 20",
    },
}
