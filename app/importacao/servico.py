"""Orquestra a importação de extratos com deduplicação, numa única transação."""
import hashlib
import json
from typing import Any
from dataclasses import dataclass, field
from datetime import date, datetime, time

from app.db import transacao
from app.dominio.categorias import PADROES_FATURA_PADRAO, aplicar_regras, classificar_tipo
from app.dominio.lancamentos import chave_deduplicacao
from app.dominio.casamento import propor_casamentos
from app.dominio.dinheiro import iso_para_br
from app.importacao.base import ErroLeitura, LinhaImportada, Parser
from app.repo import configuracoes, contas, importacoes, lancamentos


class ArquivoJaImportado(Exception):
    """Mesmo arquivo (hash) já importado e não desfeito para a conta."""


@dataclass
class ResumoImportacao:
    importacao_id: int
    total_lidos: int
    novos: int
    ignorados: int
    erros: list[ErroLeitura] = field(default_factory=list)


def _padroes_fatura(con: Any) -> list[str]:
    bruto = configuracoes.obter(con, "padroes_fatura")
    if bruto:
        try:
            lista = json.loads(bruto)
            if isinstance(lista, list) and all(isinstance(p, str) for p in lista):
                return lista
        except ValueError:
            pass
    return list(PADROES_FATURA_PADRAO)


@dataclass
class SimulacaoImportacao:
    total_lidos: int
    novos: int
    ignorados: int
    erros: list[ErroLeitura] = field(default_factory=list)


@dataclass(frozen=True)
class PropostaCasamento:
    pendente_id: int
    lancamento_id: int
    pendente_descricao: str
    pendente_data: str
    pendente_valor: int
    linha_descricao: str
    linha_data: str
    linha_valor: int


def _com_chaves(conta_uuid: str, linhas: list[LinhaImportada]) -> list[tuple[LinhaImportada, str]]:
    """Cada linha com sua chave de deduplicação (n-ésima ocorrência do mesmo trio)."""
    ocorrencias: dict[tuple[str, str, int], int] = {}
    out: list[tuple[LinhaImportada, str]] = []
    for l in linhas:
        trio = (l.data, l.descricao, l.valor_centavos)
        ocorrencias[trio] = ocorrencias.get(trio, 0) + 1
        out.append((l, chave_deduplicacao(conta_uuid, l.data, l.descricao, l.valor_centavos,
                                          ocorrencias[trio])))
    return out


def simular_importacao(con: Any, conta_id: int, nome_arquivo: str, conteudo: bytes,
                       parser: Parser) -> SimulacaoImportacao:
    """Dry-run de `importar`: conta novos/ignorados/erros sem escrever nada."""
    arquivo_hash = hashlib.sha256(conteudo).hexdigest()
    conta_uuid = contas.uuid_da_conta(con, conta_id)
    criada = importacoes.criada_em_da_ativa_com_hash(con, conta_id, arquivo_hash)
    if criada is not None:
        raise ArquivoJaImportado(
            f"Este arquivo já foi importado em {iso_para_br(criada[:10])}. "
            "Desfaça a importação anterior para importá-lo de novo."
        )
    leitura = parser.ler(conteudo)
    novos = ignorados = 0
    for _, chave in _com_chaves(conta_uuid, leitura.linhas):
        if lancamentos.chave_existe(con, chave):
            ignorados += 1
        else:
            novos += 1
    return SimulacaoImportacao(len(leitura.linhas), novos, ignorados, leitura.erros)


def conta_detectada(con: Any, conteudo: bytes, parser: Parser) -> int | None:
    """Conta cujo final_numero corresponde ao informado no arquivo (se o parser souber ler)."""
    extrator = getattr(parser, "final_numero", None)
    if extrator is None:
        return None
    final = extrator(conteudo)
    digitos = "".join(ch for ch in (final or "") if ch.isdigit())
    if not digitos:
        return None
    for conta_id, cadastrado in contas.ids_por_final_numero(con):
        d = "".join(ch for ch in cadastrado if ch.isdigit())
        if d and (digitos == d or digitos.endswith(d)):
            return conta_id
    return None


def propostas_com_detalhes(con: Any, conta_id: int, importacao_id: int) -> list[PropostaCasamento]:
    """Propostas de casamento (Task 9) com os dados de cada lado, para exibição."""
    out: list[PropostaCasamento] = []
    for c in propor_casamentos(con, conta_id, importacao_id):
        p = lancamentos.obter(con, c.pendente_id)
        l = lancamentos.obter(con, c.lancamento_id)
        if p is None or l is None:
            continue
        out.append(PropostaCasamento(
            c.pendente_id, c.lancamento_id, str(p["descricao"]), str(p["data"]),
            int(p["valor_centavos"]), str(l["descricao"]), str(l["data"]),
            int(l["valor_centavos"])))
    return out


def importar(con: Any, conta_id: int, nome_arquivo: str, conteudo: bytes,
             parser: Parser, hoje: date) -> ResumoImportacao:
    arquivo_hash = hashlib.sha256(conteudo).hexdigest()
    conta_uuid = contas.uuid_da_conta(con, conta_id)
    if importacoes.existe_ativa_com_hash(con, conta_id, arquivo_hash):
        raise ArquivoJaImportado(
            f"O arquivo '{nome_arquivo}' já foi importado para esta conta. "
            "Desfaça a importação anterior para importá-lo de novo."
        )
    leitura = parser.ler(conteudo)
    titular = configuracoes.obter(con, "nome_titular")
    padroes = _padroes_fatura(con)
    novos = ignorados = 0
    with transacao(con):
        imp_id = importacoes.criar(con, conta_id, nome_arquivo, arquivo_hash,
                                   datetime.combine(hoje, time()))
        for l, chave in _com_chaves(conta_uuid, leitura.linhas):
            if lancamentos.chave_existe(con, chave):
                ignorados += 1
                continue
            lancamentos.inserir_importado(con, conta_id, imp_id, l.data, l.descricao,
                                          l.valor_centavos, classificar_tipo(l.descricao, l.valor_centavos,
                                                             titular, padroes),
                                          chave)
            novos += 1
        aplicar_regras(con, imp_id)
        datas = [l.data for l in leitura.linhas]
        importacoes.finalizar(con, imp_id, min(datas, default=None), max(datas, default=None),
                              len(leitura.linhas), novos, ignorados)
    return ResumoImportacao(imp_id, len(leitura.linhas), novos, ignorados, leitura.erros)


def desfazer_importacao(con: Any, importacao_id: int) -> None:
    with transacao(con):
        # Reabre pendentes casados (valor real mantido, compensado=0, sem importação)
        # e libera as chaves, para que reimportar volte a criar as linhas.
        lancamentos.reabrir_pendentes_quitados_da_importacao(con, importacao_id)
        lancamentos.apagar_chaves_quitadas_da_importacao(con, importacao_id)
        lancamentos.remover_importados_da_importacao(con, importacao_id)
        if not importacoes.marcar_desfeita(con, importacao_id):
            raise ValueError(f"Importação {importacao_id} não encontrada.")
