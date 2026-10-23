"""Orquestra a importação de extratos com deduplicação, numa única transação."""
import hashlib
from typing import Any
from dataclasses import dataclass, field
from datetime import date, datetime, time

from app.db import transacao
from app.dominio.lancamentos import chave_deduplicacao, tipo_inicial
from app.importacao.base import ErroLeitura, Parser
from app.repo import contas, importacoes, lancamentos


class ArquivoJaImportado(Exception):
    """Mesmo arquivo (hash) já importado e não desfeito para a conta."""


@dataclass
class ResumoImportacao:
    importacao_id: int
    total_lidos: int
    novos: int
    ignorados: int
    erros: list[ErroLeitura] = field(default_factory=list)


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
    novos = ignorados = 0
    with transacao(con):
        imp_id = importacoes.criar(con, conta_id, nome_arquivo, arquivo_hash,
                                   datetime.combine(hoje, time()))
        ocorrencias: dict[tuple[str, str, int], int] = {}
        for l in leitura.linhas:
            trio = (l.data, l.descricao, l.valor_centavos)
            ocorrencias[trio] = ocorrencias.get(trio, 0) + 1
            chave = chave_deduplicacao(conta_uuid, l.data, l.descricao, l.valor_centavos,
                                       ocorrencias[trio])
            if lancamentos.chave_existe(con, chave):
                ignorados += 1
                continue
            lancamentos.inserir_importado(con, conta_id, imp_id, l.data, l.descricao,
                                          l.valor_centavos, tipo_inicial(l.valor_centavos),
                                          chave)
            novos += 1
        datas = [l.data for l in leitura.linhas]
        importacoes.finalizar(con, imp_id, min(datas, default=None), max(datas, default=None),
                              len(leitura.linhas), novos, ignorados)
    return ResumoImportacao(imp_id, len(leitura.linhas), novos, ignorados, leitura.erros)


def desfazer_importacao(con: Any, importacao_id: int) -> None:
    with transacao(con):
        lancamentos.remover_importados_da_importacao(con, importacao_id)
        if not importacoes.marcar_desfeita(con, importacao_id):
            raise ValueError(f"Importação {importacao_id} não encontrada.")
