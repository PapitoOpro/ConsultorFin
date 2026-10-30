"""Orquestra a importação de extratos com deduplicação, numa única transação."""
import hashlib
import json
from typing import Any
from dataclasses import dataclass, field
from datetime import date, datetime, time

from app.db import transacao
from app.dominio.categorias import PADROES_FATURA_PADRAO, aplicar_regras, classificar_tipo
from app.dominio.lancamentos import chave_deduplicacao, chave_deduplicacao_legada
from app.dominio.normalizacao import normalizar_descricao, so_letras
from app.dominio.casamento import PropostaCasamento, propostas_com_detalhes  # noqa: F401 - reexport
from app.dominio.dinheiro import formatar_brl, iso_para_br
from app.importacao.base import ErroLeitura, LinhaImportada, Parser, ResultadoLeitura
from app.repo import configuracoes, contas, faturas, importacoes, lancamentos


class ArquivoJaImportado(Exception):
    """Mesmo arquivo (hash) já importado e não desfeito para a conta."""


class LancamentosAnterioresAoSaldoInicial(ValueError):
    """O arquivo tem lançamentos anteriores à data do saldo inicial da conta (nada é importado)."""

    def __init__(self, quantidade: int, data_saldo_inicial: str, primeira_data: str,
                 saldo_anterior: tuple[str, int] | None) -> None:
        self.quantidade = quantidade
        self.data_saldo_inicial = data_saldo_inicial
        self.primeira_data = primeira_data
        self.saldo_anterior = saldo_anterior
        qtd = "1 lançamento" if quantidade == 1 else f"{quantidade} lançamentos"
        msg = (f"O arquivo tem {qtd} com data anterior à data do saldo inicial da conta "
               f"({iso_para_br(data_saldo_inicial)}); o primeiro é de {iso_para_br(primeira_data)}. "
               "Nada foi importado.")
        if saldo_anterior is not None:
            msg += (f" O extrato informa saldo anterior de {formatar_brl(saldo_anterior[1])} "
                    f"em {iso_para_br(saldo_anterior[0])}.")
        msg += (" Use \"Ajustar saldo inicial da conta\" para informar o saldo do início do "
                "extrato e importe de novo.")
        super().__init__(msg)


def _exigir_datas_validas(con: Any, conta_id: int, leitura: ResultadoLeitura) -> None:
    """Recusa o arquivo se algum lançamento for anterior à data do saldo inicial da conta."""
    _, data_inicial = contas.saldo_inicial_da_conta(con, conta_id)
    if data_inicial is None:
        return
    anteriores = [l.data for l in leitura.linhas if l.data < data_inicial]
    if anteriores:
        raise LancamentosAnterioresAoSaldoInicial(len(anteriores), data_inicial, min(anteriores),
                                                  leitura.saldo_anterior)


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


def _com_chaves(conta_uuid: str, linhas: list[LinhaImportada]
                ) -> list[tuple[LinhaImportada, str, str]]:
    """Cada linha com a chave nova (ocorrência sobre a descrição normalizada) e a legada
    (ocorrência sobre a descrição literal, como antes da v7)."""
    oc_nova: dict[tuple[str, str, int], int] = {}
    oc_legada: dict[tuple[str, str, int], int] = {}
    out: list[tuple[LinhaImportada, str, str]] = []
    for l in linhas:
        trio_n = (l.data, normalizar_descricao(l.descricao), l.valor_centavos)
        trio_l = (l.data, l.descricao, l.valor_centavos)
        oc_nova[trio_n] = oc_nova.get(trio_n, 0) + 1
        oc_legada[trio_l] = oc_legada.get(trio_l, 0) + 1
        out.append((
            l,
            chave_deduplicacao(conta_uuid, l.data, l.descricao, l.valor_centavos, oc_nova[trio_n]),
            chave_deduplicacao_legada(conta_uuid, l.data, l.descricao, l.valor_centavos,
                                      oc_legada[trio_l])))
    return out


def _ja_existe(con: Any, nova: str, legada: str) -> bool:
    return lancamentos.chave_existe(con, nova) or lancamentos.chave_existe(con, legada)


def _ja_existe_parecida(con: Any, conta_id: int, l: LinhaImportada, vistas: dict[tuple[str, int, str], int],
                        imp_id: int | None) -> bool:
    """Mesma transação vinda de outro formato (descrição escrita diferente): mesmo dia, valor e
    letras da descrição; conta ocorrências para não esconder duas transações iguais do arquivo."""
    letras = so_letras(l.descricao)
    trio = (l.data, l.valor_centavos, letras)
    vistas[trio] = vistas.get(trio, 0) + 1
    return bool(letras) and lancamentos.contar_parecidos(
        con, conta_id, l.data, l.valor_centavos, letras, imp_id) >= vistas[trio]


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
    _exigir_datas_validas(con, conta_id, leitura)
    novos = ignorados = 0
    vistas: dict[tuple[str, int, str], int] = {}
    for l, nova, legada in _com_chaves(conta_uuid, leitura.linhas):
        parecida = _ja_existe_parecida(con, conta_id, l, vistas, None)  # sempre: conta ocorrências
        if _ja_existe(con, nova, legada) or parecida:
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
    _exigir_datas_validas(con, conta_id, leitura)
    titular = configuracoes.obter(con, "nome_titular")
    padroes = _padroes_fatura(con)
    novos = ignorados = 0
    with transacao(con):
        imp_id = importacoes.criar(con, conta_id, nome_arquivo, arquivo_hash,
                                   datetime.combine(hoje, time()))
        vistas: dict[tuple[str, int, str], int] = {}
        for l, chave, legada in _com_chaves(conta_uuid, leitura.linhas):
            parecida = _ja_existe_parecida(con, conta_id, l, vistas, imp_id)  # sempre: conta ocorrências
            if _ja_existe(con, chave, legada) or parecida:
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
    from app.dominio.faturas_pagamento import casar_automaticamente
    casar_automaticamente(con)
    return ResumoImportacao(imp_id, len(leitura.linhas), novos, ignorados, leitura.erros)


class ImportacaoNaoEncontrada(ValueError):
    """Importação inexistente."""


def desfazer_importacao(con: Any, importacao_id: int, hoje: date | None = None) -> None:
    """Desfaz só a importação ativa mais recente da conta (uma anterior pode ter linhas
    das quais uma posterior, sobreposta, depende). `hoje` decide se um holerite liberado
    volta a ter entrada prevista (padrão: data do sistema)."""
    with transacao(con):
        imp = importacoes.obter(con, importacao_id)
        if imp is None:
            raise ImportacaoNaoEncontrada(f"Importação {importacao_id} não encontrada.")
        if imp["desfeita"]:
            raise ValueError(f"A importação #{importacao_id} já foi desfeita.")
        ultima = importacoes.ultima_ativa_da_conta(con, imp["conta_id"])
        if ultima is not None and int(ultima["id"]) != importacao_id:
            raise ValueError(
                f"Desfaça primeiro a importação mais recente (#{ultima['id']}, "
                f"{iso_para_br(str(ultima['criado_em'])[:10])}) desta conta; só a última "
                "importação ativa pode ser desfeita.")
        # Desfaz os casamentos (pendente volta a pendente; pago à mão volta a pago à mão,
        # com valor/estimado/data originais) e libera as chaves, para que reimportar volte
        # a criar as linhas.
        # Faturas pagas por linhas desta importação voltam a 'a pagar' (antes de remover as
        # linhas, que a fatura referencia).
        lancamentos.reabrir_faturas_pagas_pela_importacao(con, importacao_id)
        faturas.descasar_pagamentos_da_importacao(con, importacao_id)
        lancamentos.reabrir_pendentes_quitados_da_importacao(con, importacao_id)
        lancamentos.apagar_chaves_quitadas_da_importacao(con, importacao_id)
        # Holerites ligados a linhas desta importação voltam a uma entrada prevista (FK).
        from app.dominio.holerites import liberar_vinculos_da_importacao
        liberar_vinculos_da_importacao(con, importacao_id, hoje)
        lancamentos.remover_importados_da_importacao(con, importacao_id)
        importacoes.marcar_desfeita(con, importacao_id)
