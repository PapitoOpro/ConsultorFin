"""Parser do extrato CSV do Itaú (Latin-1, ';', ordem decrescente)."""

import csv
import re

from app.dominio.dinheiro import data_br_para_iso, para_centavos
from app.importacao.base import ErroLeitura, LinhaImportada, ResultadoLeitura, eco

CABECALHO = "data;lançamento;ag./origem;valor (R$);saldos (R$)"
_BOM_UTF8 = b"\xef\xbb\xbf"


def decodificar(conteudo: bytes) -> str:
    """Decodifica Latin-1 aceitando BOM UTF-8 no início."""
    if conteudo.startswith(_BOM_UTF8):
        conteudo = conteudo[len(_BOM_UTF8):]
    return conteudo.decode("latin-1")


def _eh_cabecalho(linha: str) -> bool:
    return ";".join(c.strip() for c in linha.split(";")).lower().rstrip(";") == CABECALHO.lower()


def tem_cabecalho_itau(conteudo: bytes) -> bool:
    return any(_eh_cabecalho(l) for l in decodificar(conteudo).splitlines())


class ParserItauCsv:
    @staticmethod
    def final_numero(conteudo: bytes) -> str | None:
        return extrair_final_conta(conteudo)

    def ler(self, conteudo: bytes) -> ResultadoLeitura:
        res = ResultadoLeitura()
        if not conteudo.strip():
            res.erros.append(ErroLeitura(0, "Arquivo vazio."))
            return res
        linhas_txt = decodificar(conteudo).splitlines()
        inicio = next((i for i, l in enumerate(linhas_txt) if _eh_cabecalho(l)), None)
        if inicio is None:
            res.erros.append(ErroLeitura(
                0,
                "Cabeçalho da tabela não encontrado. O extrato do Itaú deve ser um CSV "
                "em Latin-1 com as colunas 'data;lançamento;ag./origem;valor (R$);saldos (R$)'.",
            ))
            return res
        for i in range(inicio + 1, len(linhas_txt)):
            num = i + 1
            if not linhas_txt[i].strip(";\t "):
                continue
            self._processar(num, next(csv.reader([linhas_txt[i]], delimiter=";")), res)
        return res

    @staticmethod
    def _processar(num: int, campos: list[str], res: ResultadoLeitura) -> None:
        campos = [c.strip() for c in campos] + [""] * 5
        data_txt, desc, _origem, valor_txt, saldo_txt = campos[:5]
        try:
            data = data_br_para_iso(data_txt)
        except ValueError:
            res.erros.append(ErroLeitura(num, f"Linha {num}: data inválida '{eco(data_txt)}'"))
            return
        if desc.upper().startswith("SALDO"):
            try:
                saldo = para_centavos(saldo_txt or valor_txt)
            except ValueError:
                res.erros.append(ErroLeitura(num, f"Linha {num}: saldo inválido '{eco(saldo_txt or valor_txt)}'"))
                return
            if desc.upper().startswith("SALDO ANTERIOR"):
                res.saldo_anterior = (data, saldo)
            else:
                res.saldos_dia[data] = saldo
            return
        try:
            valor = para_centavos(valor_txt)
        except ValueError:
            res.erros.append(ErroLeitura(num, f"Linha {num}: valor inválido '{eco(valor_txt)}'"))
            return
        res.linhas.append(LinhaImportada(data=data, descricao=desc, valor_centavos=valor))


def extrair_final_conta(conteudo: bytes) -> str | None:
    """Número da conta no cabeçalho do extrato ('conta: 00000-0'), se houver."""
    m = re.search(r"conta:\s*([0-9][0-9\-]*)", decodificar(conteudo)[:2000], re.IGNORECASE)
    return m.group(1) if m else None
