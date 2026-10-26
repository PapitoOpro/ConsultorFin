"""Parser de extrato OFX (1.x SGML e 2.x XML) com tokenizador linear tolerante.

Nada do arquivo é executado; não usa xml nem regex com retrocesso."""

import html
import re
from datetime import date, timedelta
from decimal import Decimal

from app.dominio.dinheiro import ValorMuitoAlto, verificar_magnitude
from app.importacao.base import ErroLeitura, LinhaImportada, ResultadoLeitura, eco

_BOM_UTF8 = b"\xef\xbb\xbf"
_INICIO = 4096
_MAX_NUM = 40
_NUM = re.compile(r"[+-]?[0-9]+(\.[0-9]+)?")
_MAX_ERROS = 50
_ANO_MIN, _ANO_MAX = 1900, 2100
_TAGS_CARTAO = (b"CCSTMTRS", b"CREDITCARDMSGSRSV1", b"CCACCTFROM")
MSG_CARTAO = "Extrato de cartão de crédito (fatura) ainda não é suportado. Isso fica para a v0.2."
_Evento = tuple[str, str]  # (TAG ou /TAG, valor)


def _sem_bom(conteudo: bytes) -> bytes:
    return conteudo[len(_BOM_UTF8):] if conteudo.startswith(_BOM_UTF8) else conteudo


def tem_cabecalho_ofx(conteudo: bytes) -> bool:
    ini = _sem_bom(conteudo)[:_INICIO].upper()
    return b"OFXHEADER" in ini or b"<OFX>" in ini


def tem_cabecalho_ofc(conteudo: bytes) -> bool:
    return b"<OFC>" in _sem_bom(conteudo)[:_INICIO].upper()


def tem_cartao(conteudo: bytes) -> bool:
    """OFX de cartão de crédito (CCSTMTRS etc.)."""
    up = conteudo.upper()
    return any(t in up for t in _TAGS_CARTAO)


def _decodificar(conteudo: bytes) -> str:
    utf8_bom = conteudo.startswith(_BOM_UTF8)
    conteudo = _sem_bom(conteudo)
    cab = conteudo[:_INICIO].decode("latin-1").upper()
    if utf8_bom or "CHARSET:UTF-8" in cab or "CHARSET:UTF8" in cab or "ENCODING:UTF-8" in cab:
        try:
            return conteudo.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return conteudo.decode("cp1252", errors="replace")


def _tokenizar(texto: str) -> list[_Evento]:
    """Lista linear de (tag, valor): valor é o texto entre a tag e a próxima '<'."""
    eventos: list[_Evento] = []
    n = len(texto)
    i = texto.find("<")
    while 0 <= i < n:
        fim = texto.find(">", i + 1)
        if fim < 0:
            break
        nome = texto[i + 1:fim].strip().upper()
        prox = texto.find("<", fim + 1)
        if prox < 0:
            prox = n
        if nome and not nome.startswith(("?", "!")) and len(nome) <= 64 and "<" not in nome:
            eventos.append((nome, " ".join(html.unescape(texto[fim + 1:prox]).split())))
        i = prox if prox < n else -1
    return eventos


def _data(txt: str) -> str:
    """AAAAMMDD[hhmmss...] -> ISO; ValueError se inválida."""
    t = txt.strip()
    if len(t) < 8 or not t[:8].isascii() or not t[:8].isdigit():
        raise ValueError("data inválida")
    ano = int(t[:4])
    if not _ANO_MIN <= ano <= _ANO_MAX:
        raise ValueError("ano implausível")
    return date(ano, int(t[4:6]), int(t[6:8])).isoformat()


def _centavos(txt: str) -> int:
    """Decimal (ponto ou vírgula) -> centavos; ValueError / ValorMuitoAlto."""
    t = txt.replace(" ", "").replace("R$", "")
    if not t or len(t) > _MAX_NUM:
        raise ValueError("valor inválido")
    if "," in t:
        if "." in t and t.rfind(".") > t.rfind(","):
            t = t.replace(",", "")
        else:
            t = t.replace(".", "").replace(",", ".")
    if not _NUM.fullmatch(t):
        raise ValueError("valor inválido")
    return verificar_magnitude(int((Decimal(t) * 100).to_integral_value()))


class _Item:
    __slots__ = ("campos",)

    def __init__(self) -> None:
        self.campos: dict[str, str] = {}


class ParserOfx:
    @staticmethod
    def final_numero(conteudo: bytes) -> str | None:
        for tag, valor in _tokenizar(_decodificar(conteudo)):
            if tag == "ACCTID":
                digitos = "".join(c for c in valor if c in "0123456789")
                return digitos or None
        return None

    def ler(self, conteudo: bytes) -> ResultadoLeitura:
        res = ResultadoLeitura(formato="ofx")
        if not conteudo.strip():
            res.erros.append(ErroLeitura(0, "Arquivo vazio."))
            return res
        if tem_cartao(conteudo):
            res.erros.append(ErroLeitura(0, MSG_CARTAO))
            return res
        eventos = _tokenizar(_decodificar(conteudo))
        if not any(t == "OFX" for t, _ in eventos):
            res.erros.append(ErroLeitura(0, "Arquivo não reconhecido como OFX. No Itaú, baixe o extrato em OFX."))
            return res
        itens, topo, aberto = self._agrupar(eventos)
        for num, item in enumerate(itens, start=1):
            self._processar(num, item, res)
        if aberto:
            n = len(itens) + 1
            res.erros.append(ErroLeitura(n, f"Item {n}: arquivo truncado no meio de uma transação; ela foi ignorada."))
        if not itens and not aberto:
            res.erros.append(ErroLeitura(0, "Nenhuma transação (STMTTRN) encontrada no arquivo."))
        self._cabecalho(topo, res)
        if len(res.erros) > _MAX_ERROS:
            extra = len(res.erros) - _MAX_ERROS
            del res.erros[_MAX_ERROS:]
            res.erros.append(ErroLeitura(0, f"... e mais {extra} erros."))
        return res

    @staticmethod
    def _agrupar(eventos: list[_Evento]) -> tuple[list[_Item], dict[str, str], bool]:
        itens: list[_Item] = []
        topo: dict[str, str] = {}
        atual: _Item | None = None
        no_ledger = False
        for tag, valor in eventos:
            if tag == "STMTTRN":
                if atual is not None:
                    itens.append(atual)
                atual = _Item()
            elif tag in ("/STMTTRN", "/BANKTRANLIST", "/STMTRS", "/OFX", "LEDGERBAL"):
                if atual is not None:
                    itens.append(atual)
                    atual = None
                no_ledger = tag == "LEDGERBAL"
            elif tag in ("/LEDGERBAL", "AVAILBAL", "/STMTRS"):
                no_ledger = False
            elif atual is not None:
                atual.campos.setdefault(tag, valor)
            elif no_ledger and tag in ("BALAMT", "DTASOF"):
                topo.setdefault(tag, valor)
            elif tag in ("DTSTART", "DTEND", "ACCTID"):
                topo.setdefault(tag, valor)
        return itens, topo, atual is not None

    @staticmethod
    def _processar(num: int, item: _Item, res: ResultadoLeitura) -> None:
        c = item.campos
        # O sinal do TRNAMT é confiável: não é corrigido pelo TRNTYPE (DEBIT com valor positivo continua positivo).
        bruto = c.get("TRNAMT", "")
        if not bruto:
            res.erros.append(ErroLeitura(num, f"Item {num}: valor (TRNAMT) ausente"))
            return
        try:
            valor = _centavos(bruto)
        except ValorMuitoAlto:
            res.erros.append(ErroLeitura(num, f"Item {num}: valor muito alto '{eco(bruto)}'"))
            return
        except ValueError:
            res.erros.append(ErroLeitura(num, f"Item {num}: valor inválido '{eco(bruto)}'"))
            return
        try:
            data = _data(c.get("DTPOSTED", ""))
        except ValueError:
            res.erros.append(ErroLeitura(num, f"Item {num}: data inválida '{eco(c.get('DTPOSTED', ''))}'"))
            return
        desc = c.get("MEMO") or c.get("NAME") or c.get("TRNTYPE") or "SEM DESCRIÇÃO"
        res.linhas.append(LinhaImportada(data=data, descricao=desc, valor_centavos=valor))

    @staticmethod
    def _cabecalho(topo: dict[str, str], res: ResultadoLeitura) -> None:
        digitos = "".join(ch for ch in topo.get("ACCTID", "") if ch in "0123456789")
        res.conta_numero = digitos or None
        fim: str | None = None
        try:
            inicio, fim = _data(topo["DTSTART"]), _data(topo["DTEND"])
            res.periodo = (inicio, fim)
        except (KeyError, ValueError):
            pass
        if "BALAMT" not in topo:
            return
        try:
            saldo = _centavos(topo["BALAMT"])
        except ValueError:  # inclui ValorMuitoAlto
            res.erros.append(ErroLeitura(0, f"Saldo (LEDGERBAL) inválido '{eco(topo['BALAMT'])}'"))
            return
        try:
            data_saldo = _data(topo.get("DTASOF", ""))
        except ValueError:
            res.erros.append(ErroLeitura(0, f"Data do saldo (DTASOF) inválida '{eco(topo.get('DTASOF', ''))}'"))
            return
        if fim is not None and data_saldo > fim:
            res.saldo_informado = (data_saldo, saldo)
        else:
            res.saldos_dia[data_saldo] = saldo
        if res.linhas:
            primeiro = min(l.data for l in res.linhas)
            try:
                anterior = (date.fromisoformat(primeiro) - timedelta(days=1)).isoformat()
            except OverflowError:
                return
            if primeiro > data_saldo:  # todas as linhas são posteriores ao saldo: sem estimativa válida
                return
            ate_saldo = sum(l.valor_centavos for l in res.linhas if l.data <= data_saldo)
            res.saldo_anterior_estimado = (anterior, saldo - ate_saldo)
