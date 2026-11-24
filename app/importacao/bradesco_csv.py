"""Parser do extrato CSV do Bradesco (';', colunas Data, Histórico, Docto., Crédito, Débito, Saldo)."""

import csv
import re
from datetime import date, timedelta

from app.dominio.dinheiro import data_br_para_iso, para_centavos
from app.importacao.base import ErroLeitura, LinhaImportada, ResultadoLeitura, eco

CABECALHO = "data;histórico;docto.;crédito (r$);débito (r$);saldo (r$)"
_BOM_UTF8 = b"\xef\xbb\xbf"
_CONTA = re.compile(r"conta:\s*([0-9][0-9\-]*)", re.IGNORECASE)


def decodificar(conteudo: bytes) -> str:
    """UTF-8 (com ou sem BOM); se falhar, Latin-1."""
    if conteudo.startswith(_BOM_UTF8):
        conteudo = conteudo[len(_BOM_UTF8):]
    try:
        return conteudo.decode("utf-8")
    except UnicodeDecodeError:
        return conteudo.decode("latin-1")


def _eh_cabecalho(linha: str) -> bool:
    return ";".join(c.strip() for c in linha.split(";")).lower().rstrip(";") == CABECALHO


def tem_cabecalho_bradesco(conteudo: bytes) -> bool:
    return any(_eh_cabecalho(l) for l in decodificar(conteudo).splitlines())


def extrair_final_conta(conteudo: bytes) -> str | None:
    m = _CONTA.search(decodificar(conteudo)[:2000])
    return m.group(1) if m else None


class ParserBradescoCsv:
    @staticmethod
    def final_numero(conteudo: bytes) -> str | None:
        return extrair_final_conta(conteudo)

    def ler(self, conteudo: bytes) -> ResultadoLeitura:
        res = ResultadoLeitura(formato="csv")
        if not conteudo.strip():
            res.erros.append(ErroLeitura(0, "Arquivo vazio."))
            return res
        linhas_txt = decodificar(conteudo).splitlines()
        inicio = next((i for i, l in enumerate(linhas_txt) if _eh_cabecalho(l)), None)
        if inicio is None:
            res.erros.append(ErroLeitura(0, "Cabeçalho da tabela do Bradesco não encontrado."))
            return res
        res.conta_numero = "".join(ch for ch in (extrair_final_conta(conteudo) or "")
                                   if ch.isdigit()) or None
        saldos: list[tuple[str, int | None]] = []
        for i in range(inicio + 1, len(linhas_txt)):
            if _eh_cabecalho(linhas_txt[i]):
                break  # 2ª tabela (lançamentos futuros): fora do extrato
            if not linhas_txt[i].strip(";\t "):
                continue
            campos = next(csv.reader([linhas_txt[i]], delimiter=";"))
            self._processar(i + 1, [c.strip() for c in campos] + [""] * 6, res, saldos)
        self._saldos(res, saldos)
        return res

    @staticmethod
    def _processar(num: int, campos: list[str], res: ResultadoLeitura,
                   saldos: list[tuple[str, int | None]]) -> None:
        data_txt, desc, _doc, cred, deb, saldo_txt = campos[:6]
        try:
            data = data_br_para_iso(data_txt)
        except ValueError:
            return  # texto livre entre as tabelas (rótulos, avisos): ignorado
        try:
            valor = (para_centavos(cred) if cred else 0) - (abs(para_centavos(deb)) if deb else 0)
            saldo = para_centavos(saldo_txt) if saldo_txt else None
        except ValueError:
            res.erros.append(ErroLeitura(num, f"Linha {num}: valor inválido '{eco(cred or deb or saldo_txt)}'"))
            return
        if not cred and not deb:
            res.erros.append(ErroLeitura(num, f"Linha {num}: sem valor de crédito nem de débito"))
            return
        res.linhas.append(LinhaImportada(data=data, descricao=desc, valor_centavos=valor))
        saldos.append((data, saldo))

    @staticmethod
    def _saldos(res: ResultadoLeitura, saldos: list[tuple[str, int | None]]) -> None:
        """Saldo do fim de cada dia e saldo anterior estimado, na ordem cronológica das linhas."""
        if not res.linhas:
            return
        if res.linhas[0].data > res.linhas[-1].data:  # arquivo em ordem decrescente
            res.linhas.reverse()
            saldos.reverse()
        for (data, saldo) in saldos:
            if saldo is not None:
                res.saldos_dia[data] = saldo  # a última linha do dia vence
        primeiro_saldo = saldos[0][1]
        if primeiro_saldo is not None:
            anterior = (date.fromisoformat(res.linhas[0].data) - timedelta(days=1)).isoformat()
            res.saldo_anterior_estimado = (anterior, primeiro_saldo - res.linhas[0].valor_centavos)

