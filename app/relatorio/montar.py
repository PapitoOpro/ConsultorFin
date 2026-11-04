"""Monta o retrato (JSON com valores crus) de um relatório e o guarda em disco.

Dinheiro fica em centavos inteiros; a página formata depois. Só os nomes
(1ª coluna dos gráficos) são transformados: datas ISO -> DD/MM/AAAA e
AAAA-MM -> "setembro de 2026". Nada financeiro vai para logs.
"""
import json
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app import config
from app.dominio.dinheiro import iso_para_br
from app.relatorio.consulta import Resultado, executar_select
from app.relatorio.espec import ErroRelatorio, Espec, Grafico, Kpi, Tabela

_RID = re.compile(r"^[0-9a-f]{32}$")
_MES = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_DATA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MESES: tuple[str, ...] = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
                           "agosto", "setembro", "outubro", "novembro", "dezembro")
_MAX_FATIAS_ROSCA = 6
_NOME_VAZIO = "—"


def _nome(valor: Any) -> str:
    if valor is None:
        return _NOME_VAZIO
    texto = str(valor)
    if _DATA.match(texto):
        try:
            return iso_para_br(texto)
        except ValueError:
            return texto
    m = _MES.match(texto)
    if m:
        return f"{_MESES[int(m.group(2)) - 1]} de {m.group(1)}"
    return texto


def _numero(valor: Any, onde: str) -> int:
    """Inteiro (centavos/contagem). Float é arredondado; qualquer outra coisa é erro."""
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ErroRelatorio(f"{onde}: o valor retornado pela consulta deve ser numérico.")
    return int(round(valor))


def _valor_unico(res: Resultado, onde: str) -> int | None:
    if not res.linhas or not res.linhas[0]:
        return None
    bruto = res.linhas[0][0]
    return None if bruto is None else _numero(bruto, onde)


def _delta_pct(valor: int | None, anterior: int | None) -> float | None:
    if valor is None or not anterior:
        return None
    return round((valor - anterior) / abs(anterior) * 100, 1)


def _montar_kpi(caminho_db: Path, kpi: Kpi) -> dict[str, Any]:
    onde = f"KPI '{kpi.rotulo}'"
    valor = _valor_unico(executar_select(caminho_db, kpi.sql), onde)
    anterior = (_valor_unico(executar_select(caminho_db, kpi.delta_sql), onde)
                if kpi.delta_sql else None)
    return {"rotulo": kpi.rotulo, "formato": kpi.formato, "valor": valor,
            "delta_pct": _delta_pct(valor, anterior)}


def _agrupar_rosca(linhas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(linhas) <= _MAX_FATIAS_ROSCA:
        return linhas
    ordenadas = sorted(linhas, key=lambda x: x["valor"], reverse=True)
    resto = sum(x["valor"] for x in ordenadas[_MAX_FATIAS_ROSCA - 1:])
    return ordenadas[:_MAX_FATIAS_ROSCA - 1] + [{"nome": "Outros", "valor": resto, "serie": None}]


def _montar_grafico(caminho_db: Path, g: Grafico) -> dict[str, Any]:
    onde = f"gráfico '{g.titulo}'"
    res = executar_select(caminho_db, g.sql)
    if len(res.colunas) < 2:
        raise ErroRelatorio(f"{onde}: a consulta deve retornar ao menos 2 colunas (nome e valor).")
    linhas: list[dict[str, Any]] = []
    for r in res.linhas:
        if r[1] is None:
            continue
        serie = r[2] if len(r) > 2 and r[2] is not None else None
        linhas.append({"nome": _nome(r[0]), "valor": _numero(r[1], onde),
                       "serie": None if serie is None else str(serie)})
    if g.tipo == "rosca":
        linhas = _agrupar_rosca(linhas)
    return {"tipo": g.tipo, "titulo": g.titulo, "formato": g.formato, "largura": g.largura,
            "linhas": linhas}


def _montar_tabela(caminho_db: Path, t: Tabela) -> dict[str, Any]:
    res = executar_select(caminho_db, t.sql)
    return {"titulo": t.titulo, "colunas": list(res.colunas),
            "linhas": [list(r) for r in res.linhas], "truncado": res.truncado}


def _escrever_atomico(pasta: Path, rid: str, retrato: dict[str, Any]) -> None:
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"{rid}.json"
    temporario = pasta / f"{rid}.json.tmp"
    try:
        temporario.write_text(json.dumps(retrato, ensure_ascii=False), encoding="utf-8")
        os.replace(temporario, destino)
    except OSError:
        temporario.unlink(missing_ok=True)
        raise ErroRelatorio("Não foi possível salvar o relatório.") from None


def gerar_relatorio(caminho_db: Path, espec: Espec, pasta: Path, agora: datetime) -> dict[str, str]:
    """Executa os SQLs, grava o retrato em <pasta>/<id>.json e devolve id, título e URL."""
    retrato: dict[str, Any] = {
        "titulo": espec.titulo,
        "periodo": ({"de": espec.periodo[0], "ate": espec.periodo[1]} if espec.periodo else None),
        "observacao": espec.observacao,
        "gerado_em": agora.isoformat(),
        "kpis": [_montar_kpi(caminho_db, k) for k in espec.kpis],
        "graficos": [_montar_grafico(caminho_db, g) for g in espec.graficos],
        "tabela": _montar_tabela(caminho_db, espec.tabela) if espec.tabela else None,
    }
    rid = uuid.uuid4().hex
    _escrever_atomico(pasta, rid, retrato)
    return {"id": rid, "titulo": espec.titulo, "url": f"/relatorio/{rid}"}


def ler_relatorio(pasta: Path, rid: str) -> dict[str, Any] | None:
    if not isinstance(rid, str) or not _RID.fullmatch(rid):
        return None
    try:
        dados = json.loads((pasta / f"{rid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dados if isinstance(dados, dict) else None


def limpar_expirados(pasta: Path, agora: datetime,
                     dias: int = config.DIAS_EXPIRACAO_RELATORIO) -> int:
    """Apaga retratos com mtime mais antigo que `dias`; devolve quantos apagou."""
    if not pasta.is_dir():
        return 0
    limite = agora.timestamp() - dias * 86400
    apagados = 0
    for arq in pasta.glob("*.json"):
        try:
            if arq.stat().st_mtime < limite:
                arq.unlink()
                apagados += 1
        except OSError:
            continue
    return apagados
