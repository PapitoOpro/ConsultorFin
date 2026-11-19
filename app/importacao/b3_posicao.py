"""Leitura da posição de custódia da B3 (posicao-AAAA-MM-DD-HH-MM-SS.xlsx). Só lê; nada é registrado."""
import io
import re
import unicodedata
import warnings
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from openpyxl import load_workbook

from app.dominio.dinheiro import verificar_magnitude

_NOME = re.compile(r"posicao-(\d{4})-(\d{2})-(\d{2})", re.IGNORECASE)
_COLUNAS = {"produto": "produto", "conta": "conta", "codigo de negociacao": "codigo",
            "quantidade": "quantidade", "preco de fechamento": "preco", "valor atualizado": "valor"}
_MAX_LINHAS = 5000


class PosicaoIlegivel(ValueError):
    """Arquivo que não é uma posição B3 legível (mensagem em português)."""


@dataclass(frozen=True)
class AtivoB3:
    codigo: str
    produto: str
    classe: str  # nome da aba: Acoes, ETF, Fundo de Investimento...
    quantidade: float
    preco_centavos: int
    valor_centavos: int


@dataclass(frozen=True)
class PosicaoB3:
    data: str | None  # AAAA-MM-DD tirada do nome do arquivo; None se o nome não trouxer
    conta: str
    ativos: list[AtivoB3]


def _sem_acento(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in base if not unicodedata.combining(c)).strip()


def _centavos(valor: Any) -> int:
    if isinstance(valor, bool) or not isinstance(valor, (int, float, Decimal)):
        raise PosicaoIlegivel("Valor inválido na posição.")
    return verificar_magnitude(int((Decimal(str(valor)) * 100).to_integral_value(ROUND_HALF_UP)))


def data_do_nome(nome: str) -> str | None:
    m = _NOME.search(nome)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def _abas(conteudo: bytes) -> list[tuple[str, list[tuple[Any, ...]]]]:
    if conteudo[:2] != b"PK":
        raise PosicaoIlegivel("Envie a posição da B3 em .xlsx.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            wb = load_workbook(io.BytesIO(conteudo), data_only=True)
            return [(ws.title, [tuple(r) for r in ws.iter_rows(values_only=True, max_row=_MAX_LINHAS)])
                    for ws in wb.worksheets]
    except Exception as exc:  # noqa: BLE001 - qualquer falha vira mensagem amigável
        raise PosicaoIlegivel("Não foi possível ler a planilha.") from exc


def _cabecalho(linhas: list[tuple[Any, ...]]) -> tuple[int, dict[str, int]] | None:
    for i, linha in enumerate(linhas[:10]):
        achados = {_COLUNAS[r]: j for j, c in enumerate(linha)
                   if isinstance(c, str) and (r := _sem_acento(c)) in _COLUNAS}
        if len(achados) == len(_COLUNAS):
            return i, achados
    return None


def parece_posicao_b3(conteudo: bytes) -> bool:
    try:
        return any(_cabecalho(linhas) for _, linhas in _abas(conteudo))
    except PosicaoIlegivel:
        return False


def ler_posicao_b3(conteudo: bytes, nome_arquivo: str = "") -> PosicaoB3:
    ativos: list[AtivoB3] = []
    contas: set[str] = set()
    for titulo, linhas in _abas(conteudo):
        cab = _cabecalho(linhas)
        if cab is None:
            continue
        inicio, col = cab
        for linha in linhas[inicio + 1:]:
            codigo = linha[col["codigo"]]
            if not isinstance(codigo, str) or not codigo.strip():
                break
            qtd = linha[col["quantidade"]]
            if isinstance(qtd, bool) or not isinstance(qtd, (int, float)) or qtd < 0:
                raise PosicaoIlegivel("Quantidade inválida na posição.")
            contas.add(str(linha[col["conta"]] or "").strip())
            ativos.append(AtivoB3(
                codigo=codigo.strip().upper()[:20], produto=" ".join(str(linha[col["produto"]] or "").split())[:200],
                classe=titulo[:60], quantidade=float(qtd), preco_centavos=_centavos(linha[col["preco"]]),
                valor_centavos=_centavos(linha[col["valor"]])))
    if not ativos:
        raise PosicaoIlegivel("Não encontrei ativos na posição.")
    conta = next(iter(contas)) if len(contas) == 1 else ""
    if not conta.isdigit():
        raise PosicaoIlegivel("Não consegui identificar a conta da posição.")
    return PosicaoB3(data=data_do_nome(nome_arquivo), conta=conta, ativos=ativos)
