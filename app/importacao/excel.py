"""Leitura de planilhas (xlsx/xls) e CSV genéricos com mapeamento manual de colunas."""
import io
import logging
import numbers
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import pandas as pd

from app.dominio.dinheiro import ValorMuitoAlto, data_br_para_iso, para_centavos, verificar_magnitude
from app.importacao.base import ErroLeitura, LinhaImportada, ResultadoLeitura

log = logging.getLogger(__name__)

CAMPOS = ("data", "descricao", "valor")
_ASSINATURA_XLS = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_MSG_ILEGIVEL = (
    "Não foi possível ler o arquivo. Verifique se é um CSV (com colunas separadas por ';', ',' "
    "ou tabulação) ou uma planilha Excel (.xlsx/.xls) válida."
)


class ArquivoIlegivel(ValueError):
    """Arquivo que não pôde ser lido como tabela (mensagem já em português)."""


def _decodificar_csv(conteudo: bytes) -> str:
    try:
        return conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        return conteudo.decode("latin-1")


def _carregar(conteudo: bytes) -> "pd.DataFrame":
    """Lê a primeira planilha/CSV como tabela de textos/objetos; 1ª linha = cabeçalho."""
    if not conteudo.strip():
        raise ArquivoIlegivel("Arquivo vazio.")
    try:
        if conteudo[:2] == b"PK" or conteudo[:8] == _ASSINATURA_XLS:
            motor = "openpyxl" if conteudo[:2] == b"PK" else "xlrd"
            df = pd.read_excel(io.BytesIO(conteudo), dtype=object, keep_default_na=False,
                               engine=motor)
        else:
            texto = _decodificar_csv(conteudo)
            primeira = next((l for l in texto.splitlines() if l.strip()), "")
            sep = max((";", ",", "\t"), key=primeira.count)
            if primeira.count(sep) == 0:
                raise ArquivoIlegivel(_MSG_ILEGIVEL)
            df = pd.read_csv(io.StringIO(texto), sep=sep, dtype=str, keep_default_na=False,
                             skip_blank_lines=True)
    except ArquivoIlegivel:
        raise
    except Exception as exc:  # noqa: BLE001 - qualquer falha de parsing vira mensagem amigável
        log.warning("event=leitura_tabela_falhou erro=%s", type(exc).__name__)
        raise ArquivoIlegivel(_MSG_ILEGIVEL) from exc
    df.columns = [str(c).strip() for c in df.columns]
    if len(df.columns) < 2:
        raise ArquivoIlegivel(_MSG_ILEGIVEL)
    return df


def listar_colunas(conteudo: bytes, amostra: int = 5) -> tuple[list[str], list[list[str]]]:
    """Nomes das colunas e as primeiras linhas (como texto), para a tela de mapeamento."""
    df = _carregar(conteudo)
    linhas = [["" if v is None else str(v) for v in row] for row in df.head(amostra).itertuples(index=False)]
    return list(df.columns), linhas


def _vazio(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _data_iso(v: Any) -> str:
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    texto = str(v).strip()
    try:
        return data_br_para_iso(texto)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(texto).date().isoformat()
    except ValueError as exc:
        raise ValueError(texto) from exc


def _centavos(v: Any) -> int:
    if isinstance(v, numbers.Real) and not isinstance(v, bool):
        bruto = Decimal(str(float(v)))
        if not bruto.is_finite():
            raise ValueError("valor inválido")
        return verificar_magnitude(int((bruto * 100).to_integral_value(ROUND_HALF_UP)))
    return para_centavos(str(v))


class ParserExcel:
    """Lê CSV/Excel genérico: `mapeamento` = {'data': col, 'descricao': col, 'valor': col}."""

    def __init__(self, mapeamento: dict[str, str]) -> None:
        self.mapeamento = mapeamento

    def ler(self, conteudo: bytes) -> ResultadoLeitura:
        res = ResultadoLeitura()
        try:
            df = _carregar(conteudo)
        except ArquivoIlegivel as exc:
            res.erros.append(ErroLeitura(0, str(exc)))
            return res
        for campo in CAMPOS:
            col = self.mapeamento.get(campo, "")
            if col not in df.columns:
                res.erros.append(ErroLeitura(0, f"Coluna '{col}' não encontrada no arquivo."))
        if res.erros:
            return res
        c_data, c_desc, c_valor = (self.mapeamento[c] for c in CAMPOS)
        for i, (d, desc, val) in enumerate(zip(df[c_data], df[c_desc], df[c_valor], strict=True)):
            num = i + 2  # cabeçalho = linha 1
            if _vazio(d) and _vazio(desc) and _vazio(val):
                continue
            try:
                data = _data_iso(d)
            except ValueError:
                res.erros.append(ErroLeitura(num, f"Linha {num}: data inválida '{str(d).strip()}'"))
                continue
            if _vazio(val):
                res.erros.append(ErroLeitura(num, f"Linha {num}: valor inválido ''"))
                continue
            try:
                valor = _centavos(val)
            except ValueError as exc:
                motivo = "muito alto" if isinstance(exc, ValorMuitoAlto) else "inválido"
                res.erros.append(ErroLeitura(num, f"Linha {num}: valor {motivo} '{str(val).strip()}'"))
                continue
            res.linhas.append(LinhaImportada(data=data, descricao=str(desc).strip(), valor_centavos=valor))
        return res
