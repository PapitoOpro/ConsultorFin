"""Leitura do extrato da conta Clear (corretora) em .xlsx. Só lê; nunca grava nem registra conteúdo."""
import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from openpyxl import load_workbook

from app.dominio.dinheiro import verificar_magnitude

_CONTA = re.compile(r"Conta Clear:\s*(\d{1,20})", re.IGNORECASE)
_COLUNAS = {"movimentacao": "mov", "liquidacao": "liq", "lancamento": "desc",
            "valor (r$)": "valor", "saldo (r$)": "saldo"}
_MAX_LINHAS = 20000


class ClearIlegivel(ValueError):
    """Arquivo que não é um extrato Clear legível (mensagem em português)."""


@dataclass(frozen=True)
class LinhaClear:
    data: str
    data_liquidacao: str
    descricao: str
    valor_centavos: int
    saldo_centavos: int


@dataclass(frozen=True)
class ExtratoClear:
    conta: str
    linhas: list[LinhaClear]  # ordem cronológica (a mais antiga primeiro)
    saldo_informado_centavos: int | None


def _sem_acento(texto: str) -> str:
    import unicodedata
    base = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in base if not unicodedata.combining(c))


def _centavos(valor: Any) -> int:
    if isinstance(valor, bool) or not isinstance(valor, (int, float, Decimal)):
        raise ClearIlegivel("Valor inválido no extrato.")
    return verificar_magnitude(int((Decimal(str(valor)) * 100).to_integral_value(ROUND_HALF_UP)))


def _dia(valor: Any) -> str:
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    raise ClearIlegivel("Data inválida no extrato.")


def _abrir(conteudo: bytes) -> list[tuple[Any, ...]]:
    if conteudo[:2] != b"PK":
        raise ClearIlegivel("Envie o extrato da Clear em .xlsx.")
    try:
        wb = load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        return [tuple(r) for r in ws.iter_rows(values_only=True, max_row=_MAX_LINHAS)]
    except Exception as exc:  # noqa: BLE001 - qualquer falha vira mensagem amigável
        raise ClearIlegivel("Não foi possível ler a planilha.") from exc


def _achar_conta(linhas: list[tuple[Any, ...]]) -> str | None:
    for linha in linhas[:15]:
        for c in linha:
            if isinstance(c, str) and (m := _CONTA.search(c)):
                return m.group(1)
    return None


def parece_extrato_clear(conteudo: bytes) -> bool:
    try:
        return _achar_conta(_abrir(conteudo)) is not None
    except ClearIlegivel:
        return False


def ler_extrato_clear(conteudo: bytes) -> ExtratoClear:
    linhas = _abrir(conteudo)
    conta = _achar_conta(linhas)
    if conta is None:
        raise ClearIlegivel("Este arquivo não parece o extrato da conta Clear.")
    saldo_inf: int | None = None
    cab: dict[str, int] = {}
    inicio = -1
    for i, linha in enumerate(linhas):
        rotulos = [_sem_acento(str(c)).strip() if c is not None else "" for c in linha]
        if not cab and rotulos[:1] == [""] and "saldo total projetado" in rotulos:
            k = rotulos.index("saldo total projetado")
            achado = [v for v in linha[k + 1:] if isinstance(v, (int, float))]
            saldo_inf = _centavos(achado[0]) if achado else None
        achados = {_COLUNAS[r]: j for j, r in enumerate(rotulos) if r in _COLUNAS}
        if len(achados) == len(_COLUNAS):
            cab, inicio = achados, i
            break
    if inicio < 0:
        raise ClearIlegivel("Não encontrei a tabela de movimentação do extrato.")
    lidas: list[LinhaClear] = []
    for linha in linhas[inicio + 1:]:
        if not isinstance(linha[cab["mov"]], (datetime, date)):
            break
        lidas.append(LinhaClear(
            data=_dia(linha[cab["mov"]]), data_liquidacao=_dia(linha[cab["liq"]]),
            descricao=" ".join(str(linha[cab["desc"]] or "").split())[:200],
            valor_centavos=_centavos(linha[cab["valor"]]),
            saldo_centavos=_centavos(linha[cab["saldo"]])))
    if not lidas:
        raise ClearIlegivel("O extrato não tem movimentações.")
    lidas.reverse()  # o arquivo vem do mais recente para o mais antigo
    return ExtratoClear(conta=conta, linhas=lidas, saldo_informado_centavos=saldo_inf)
