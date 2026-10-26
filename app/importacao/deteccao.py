"""Detecção do formato do arquivo de extrato."""

from app.importacao.base import FormatoNaoSuportado, Parser
from app.importacao.itau_csv import ParserItauCsv, tem_cabecalho_itau
from app.importacao.ofx import MSG_CARTAO, ParserOfx, tem_cabecalho_ofc, tem_cabecalho_ofx, tem_cartao

MSG_NAO_OFX = "O arquivo não parece ser um OFX. No Itaú, baixe o extrato em OFX."
MSG_OFC = "O formato OFC ainda não é suportado. No Itaú, baixe o extrato em OFX."


def detectar(conteudo: bytes, nome: str) -> Parser | None:
    """Retorna o parser adequado ao arquivo, ou None se o formato não for reconhecido.
    Levanta FormatoNaoSuportado para OFC e OFX de cartão."""
    if tem_cabecalho_ofx(conteudo):
        if tem_cartao(conteudo):
            raise FormatoNaoSuportado(MSG_CARTAO)
        return ParserOfx()
    if nome.lower().endswith(".ofc") or tem_cabecalho_ofc(conteudo):
        raise FormatoNaoSuportado(MSG_OFC)
    if nome.lower().endswith(".csv") and tem_cabecalho_itau(conteudo):
        return ParserItauCsv()
    return None


def exigir_ofx_valido(nome: str, conteudo: bytes) -> None:
    """Um arquivo .ofx precisa ter cabeçalho OFX; senão FormatoNaoSuportado."""
    if nome.lower().endswith(".ofx") and not tem_cabecalho_ofx(conteudo):
        raise FormatoNaoSuportado(MSG_NAO_OFX)
