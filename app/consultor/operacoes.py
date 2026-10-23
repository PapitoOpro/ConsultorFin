"""Operações de escrita do consultor. Mesmas validações das telas (reusam o domínio)."""
import logging
from datetime import date
from pathlib import Path
from typing import Any

from app.consultor.consultas import data, dinheiro
from app.dominio import contas_pagar_acoes as acoes
from app.dominio.casamento import confirmar_casamento, propostas_com_detalhes
from app.dominio.lancamentos import (
    ErroLancamento, alterar_categoria, criar_regra_e_aplicar,
)
from app.dominio.saldo import conciliar
from app.importacao.base import ResultadoLeitura
from app.importacao.deteccao import detectar
from app.importacao.servico import (
    ArquivoJaImportado, LancamentosAnterioresAoSaldoInicial, importar as importar_arquivo,
    simular_importacao,
)
from app.repo import contas as repo_contas

log = logging.getLogger(__name__)

LIMITE_BYTES = 5 * 1024 * 1024
EXTENSOES = {".csv", ".xlsx"}
MSG_FORMATO = ("Formato do arquivo não reconhecido. Use o painel web (Importar) para mapear as "
               "colunas manualmente.")
MSG_ILEGIVEL = "Não foi possível ler o arquivo. Verifique se é um CSV ou uma planilha Excel válida."
MSG_PADRAO_LONGO = "O padrão da regra pode ter no máximo 100 caracteres."


def _ler_arquivo(caminho: str) -> tuple[str, bytes]:
    arq = Path(caminho)
    if arq.suffix.lower() not in EXTENSOES:
        raise ErroLancamento("Formato não aceito. Envie um arquivo .csv ou .xlsx.")
    if not arq.is_file():
        raise ErroLancamento("Arquivo não encontrado.")
    if arq.stat().st_size > LIMITE_BYTES:
        raise ErroLancamento("O arquivo excede o limite de 5 MB.")
    conteudo = arq.read_bytes()
    if not conteudo.strip():
        raise ErroLancamento("O arquivo está vazio.")
    return arq.name[:200], conteudo


def _conciliacao(con: Any, conta_id: int, leitura: ResultadoLeitura) -> dict[str, Any]:
    c = conciliar(con, conta_id, leitura.saldos_dia)
    if c.ok:
        return {"status": "confere", "dias_comparados": c.dias_comparados}
    if c.primeira_divergencia is not None:
        return {"status": "diverge", "dias_comparados": c.dias_comparados,
                **data("primeira_divergencia", c.primeira_divergencia),
                **dinheiro("esperado", c.esperado_centavos or 0),
                **dinheiro("calculado", c.calculado_centavos or 0)}
    return {"status": "nao_comparavel", "dias_comparados": 0,
            "aviso": "Nenhum saldo do extrato pôde ser comparado (o arquivo não traz saldos ou "
                     "todos são anteriores ao saldo inicial); a conferência não foi feita."}


def importar(con: Any, hoje: date, caminho: str, conta_id: int, confirmar: bool) -> dict[str, Any]:
    nome, conteudo = _ler_arquivo(caminho)
    if repo_contas.obter(con, conta_id) is None:
        raise ErroLancamento("Conta não encontrada.")
    parser = detectar(conteudo, nome)
    if parser is None:
        raise ErroLancamento(MSG_FORMATO)
    try:
        leitura = parser.ler(conteudo)
    except Exception as exc:  # noqa: BLE001 - nunca expor stack trace
        log.warning("event=leitura_falhou erro=%s", type(exc).__name__)
        raise ErroLancamento(MSG_ILEGIVEL) from exc
    if not leitura.linhas:
        raise ErroLancamento("Nenhuma linha válida encontrada no arquivo; nada foi importado.")
    erros = [{"linha": e.linha, "mensagem": e.mensagem} for e in leitura.erros]
    try:
        if not confirmar:
            sim = simular_importacao(con, conta_id, nome, conteudo, parser)
            return {"gravado": False, "arquivo": nome, "conta_id": conta_id,
                    "total_lidos": sim.total_lidos, "novos": sim.novos,
                    "ignorados": sim.ignorados, "erros": erros,
                    "proximo_passo": "Rode de novo com --confirmar para gravar."}
        resumo = importar_arquivo(con, conta_id, nome, conteudo, parser, hoje)
    except (ArquivoJaImportado, LancamentosAnterioresAoSaldoInicial) as exc:
        raise ErroLancamento(str(exc)) from exc
    saida: dict[str, Any] = {
        "gravado": True, "arquivo": nome, "conta_id": conta_id, "importacao_id": resumo.importacao_id,
        "total_lidos": resumo.total_lidos, "novos": resumo.novos, "ignorados": resumo.ignorados,
        "erros": erros, "conciliacao": None, "propostas_casamento": []}
    try:  # a importação já foi gravada: falha aqui não pode virar erro
        saida["conciliacao"] = _conciliacao(con, conta_id, leitura)
        saida["propostas_casamento"] = [
            {"pendente_id": p.pendente_id, "linha_id": p.lancamento_id,
             "pendente_descricao": p.pendente_descricao, **data("pendente_data", p.pendente_data),
             **dinheiro("pendente_valor", p.pendente_valor), "linha_descricao": p.linha_descricao,
             **data("linha_data", p.linha_data), **dinheiro("linha_valor", p.linha_valor)}
            for p in propostas_com_detalhes(con, conta_id, resumo.importacao_id)]
    except Exception as exc:  # noqa: BLE001
        log.error("event=conferencia_falhou erro=%s", type(exc).__name__)
        saida["aviso"] = "Importado, mas a conferência de saldos falhou. Nada foi desfeito."
    return saida


def casar(con: Any, pendente_id: int, linha_id: int) -> dict[str, Any]:
    try:
        confirmar_casamento(con, pendente_id, linha_id)
    except ValueError as exc:
        raise ErroLancamento(str(exc)) from exc
    return {"ok": True, "pendente_id": pendente_id, "linha_id": linha_id,
            "mensagem": "Correspondência confirmada."}


def categorizar(con: Any, lancamento_id: int, categoria_id: int) -> dict[str, Any]:
    alterar_categoria(con, lancamento_id, categoria_id)
    return {"ok": True, "lancamento_id": lancamento_id, "categoria_id": categoria_id}


def regra(con: Any, padrao: str, categoria_id: int) -> dict[str, Any]:
    padrao = padrao.strip()
    if len(padrao) > 100:
        raise ErroLancamento(MSG_PADRAO_LONGO)
    aplicados = criar_regra_e_aplicar(con, padrao, categoria_id)
    return {"ok": True, "padrao": padrao, "categoria_id": categoria_id, "aplicados": aplicados}


def pagar(con: Any, hoje: date, lancamento_id: int, valor: str | None,
          data_pagamento: str | None) -> dict[str, Any]:
    """Mesmas guardas da tela: sem pagamento duplo, sem data futura, propaga recorrente."""
    mensagem = acoes.pagar(con, str(lancamento_id), valor or "",
                           data_pagamento or hoje.isoformat(), hoje)
    return {"ok": True, "lancamento_id": lancamento_id, "mensagem": mensagem}


def criar_conta(con: Any, nome: str, tipo: str, saldo_inicial: str,
                data_saldo: str) -> dict[str, Any]:
    antes = {int(c["id"]) for c in repo_contas.listar(con)}
    acoes.criar_conta_de_formulario(
        con, nome=nome, tipo=tipo, instituicao="", final_numero="",
        saldo_inicial=saldo_inicial, data_saldo_inicial=data_saldo)
    nova = next(c for c in repo_contas.listar(con) if int(c["id"]) not in antes)
    return {"ok": True, "conta": {
        "id": int(nova["id"]), "nome": nova["nome"], "tipo": nova["tipo"],
        **dinheiro("saldo_inicial", int(nova["saldo_inicial_centavos"])),
        **data("data_saldo_inicial", nova["data_saldo_inicial"])}}
