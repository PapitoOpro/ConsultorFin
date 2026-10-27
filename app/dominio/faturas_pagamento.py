"""Casamento do pagamento bancário (tipo 'pagamento_fatura') com a fatura importada."""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.dominio.faturas import _escrita
from app.repo import contas as repo_contas
from app.repo import faturas as repo
from app.repo import lancamentos as repo_lanc

JANELA_DIAS = 7


class PagamentoInvalido(ValueError):
    """Casamento recusado (mensagem em português, própria para o usuário)."""


@dataclass(frozen=True)
class PropostaPagamento:
    fatura_id: int
    lancamento_id: int
    data: str
    valor_centavos: int
    pontuacao: float


def _fatura(con: Any, fatura_id: int) -> Any:
    fat = repo.obter(con, fatura_id)
    if fat is None:
        raise PagamentoInvalido("Fatura não encontrada.")
    return fat


def _janela(fat: Any) -> tuple[str, str]:
    venc = date.fromisoformat(str(fat["data_vencimento"]))
    delta = timedelta(days=JANELA_DIAS)
    return (venc - delta).isoformat(), (venc + delta).isoformat()


def _dias(fat: Any, data_iso: str) -> int:
    return abs((date.fromisoformat(data_iso) - date.fromisoformat(str(fat["data_vencimento"]))).days)


def propor_pagamentos(con: Any, fatura_id: int) -> list[PropostaPagamento]:
    """Pagamentos livres com o valor exato do total impresso e data no vencimento ± 7 dias,
    mais próximos primeiro. Fatura já paga: nenhuma proposta."""
    fat = _fatura(con, fatura_id)
    if fat["pagamento_lancamento_id"] is not None:
        return []
    ini, fim = _janela(fat)
    rows = repo_lanc.pagamentos_fatura_livres(con, -int(fat["total_impresso_centavos"]), ini, fim)
    props = [PropostaPagamento(fatura_id, int(r["id"]), str(r["data"]), int(r["valor_centavos"]),
                               round(1 - _dias(fat, str(r["data"])) / (JANELA_DIAS + 1), 3))
             for r in rows]
    return sorted(props, key=lambda p: (-p.pontuacao, p.lancamento_id))


def _validar(con: Any, fat: Any, lancamento_id: int) -> Any:
    if fat["pagamento_lancamento_id"] is not None:
        raise PagamentoInvalido("Esta fatura já está paga.")
    if fat["lancamento_id"] is None:
        raise PagamentoInvalido("Fatura sem lançamento.")
    lanc = repo_lanc.obter(con, lancamento_id)
    if lanc is None:
        raise PagamentoInvalido("Lançamento não encontrado.")
    if lanc["tipo"] != "pagamento_fatura" or not lanc["compensado"]:
        raise PagamentoInvalido("O lançamento não é um pagamento de fatura compensado.")
    conta = repo_contas.obter(con, int(lanc["conta_id"]))
    if conta is None or conta["tipo"] == "cartao":
        raise PagamentoInvalido("O pagamento precisa sair de uma conta bancária.")
    if repo.por_pagamento(con, lancamento_id) is not None:
        raise PagamentoInvalido("Este pagamento já está casado com outra fatura.")
    if int(lanc["valor_centavos"]) != -int(fat["total_impresso_centavos"]):
        raise PagamentoInvalido("O valor do pagamento difere do total da fatura.")
    if _dias(fat, str(lanc["data"])) > JANELA_DIAS:
        raise PagamentoInvalido(f"O pagamento está a mais de {JANELA_DIAS} dias do vencimento.")
    return lanc


def confirmar_pagamento(con: Any, fatura_id: int, lancamento_id: int) -> None:
    """Fatura paga na data do banco; a linha do banco deixa de contar como gasto."""
    with _escrita(con):
        fat = _fatura(con, fatura_id)
        lanc = _validar(con, fat, lancamento_id)
        data = str(lanc["data_compensacao"] or lanc["data"])
        repo_lanc.definir_compensacao_fatura(con, int(fat["lancamento_id"]), data)
        repo.marcar_pagamento(con, fatura_id, lancamento_id)


def desfazer_pagamento(con: Any, fatura_id: int) -> None:
    """Fatura volta a pendente; o pagamento do banco volta a contar como gasto."""
    with _escrita(con):
        fat = _fatura(con, fatura_id)
        if fat["pagamento_lancamento_id"] is None:
            raise PagamentoInvalido("Esta fatura não está paga.")
        if fat["lancamento_id"] is not None:
            repo_lanc.definir_compensacao_fatura(con, int(fat["lancamento_id"]), None)
        repo.marcar_pagamento(con, fatura_id, None)
