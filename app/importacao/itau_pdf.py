"""Parser do extrato de conta corrente do Itaú em PDF (texto, sem OCR)."""

import io
import re

import pdfplumber

from app.dominio.dinheiro import data_br_para_iso, para_centavos
from app.importacao.base import ErroLeitura, LinhaImportada, ResultadoLeitura, eco

_ASSINATURA_PDF = b"%PDF"
_MARCADOR = re.compile(r"extrato\s+conta\s*/\s*lan[çc]amentos", re.IGNORECASE)
_LINHA = re.compile(r"^(\d{2}/\d{2}/\d{4})\s+(.+?)\s+(-?\d{1,3}(?:\.\d{3})*,\d{2})$")
_PERIODO = re.compile(r"(\d{2}/\d{2}/\d{4})\s+at[ée]\s+(\d{2}/\d{2}/\d{4})", re.IGNORECASE)
_CONTA = re.compile(r"conta:\s*([0-9][0-9\-]*)", re.IGNORECASE)
_MSG_INVALIDO = "O PDF não é um extrato de conta do Itaú legível (precisa ter texto, não ser imagem)."


def extrair_texto(conteudo: bytes) -> str:
    """Texto de todas as páginas; levanta ValueError (mensagem em português) se ilegível."""
    try:
        with pdfplumber.open(io.BytesIO(conteudo)) as pdf:
            return "\n".join(p.extract_text() or "" for p in pdf.pages)
    except Exception as exc:  # noqa: BLE001 - mensagem original pode ter conteúdo
        raise ValueError(_MSG_INVALIDO) from exc


def tem_extrato_itau_pdf(conteudo: bytes) -> bool:
    if not conteudo.startswith(_ASSINATURA_PDF):
        return False
    try:
        return bool(_MARCADOR.search(extrair_texto(conteudo)))
    except ValueError:
        return False


class ParserItauPdf:
    @staticmethod
    def final_numero(conteudo: bytes) -> str | None:
        try:
            m = _CONTA.search(extrair_texto(conteudo)[:2000])
        except ValueError:
            return None
        return m.group(1) if m else None

    def ler(self, conteudo: bytes) -> ResultadoLeitura:
        res = ResultadoLeitura(formato="pdf")
        try:
            texto = extrair_texto(conteudo)
        except ValueError as exc:
            res.erros.append(ErroLeitura(0, str(exc)))
            return res
        return self.ler_texto(texto)

    @staticmethod
    def ler_texto(texto: str) -> ResultadoLeitura:
        res = ResultadoLeitura(formato="pdf")
        if not _MARCADOR.search(texto):
            res.erros.append(ErroLeitura(0, "Extrato do Itaú não reconhecido no PDF."))
            return res
        if (m := _CONTA.search(texto[:2000])):
            res.conta_numero = "".join(ch for ch in m.group(1) if ch.isdigit()) or None
        if (m := _PERIODO.search(texto)):
            try:
                res.periodo = (data_br_para_iso(m.group(1)), data_br_para_iso(m.group(2)))
            except ValueError:
                pass
        for num, bruta in enumerate(texto.splitlines(), 1):
            m = _LINHA.match(bruta.strip())
            if m is None:
                continue
            data_txt, desc, valor_txt = m.groups()
            try:
                data = data_br_para_iso(data_txt)
                valor = para_centavos(valor_txt)
            except ValueError:
                res.erros.append(ErroLeitura(num, f"Linha {num}: valor ou data inválidos '{eco(bruta)}'"))
                continue
            if desc.upper().startswith("SALDO"):
                if desc.upper().startswith("SALDO ANTERIOR"):
                    res.saldo_anterior = (data, valor)
                else:
                    res.saldos_dia[data] = valor
                continue
            res.linhas.append(LinhaImportada(data=data, descricao=desc, valor_centavos=valor))
        return res
