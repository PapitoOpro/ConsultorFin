"""Contas fixas e receitas recorrentes: geração mensal idempotente e confirmação."""
from datetime import date
from typing import Any

from app.db import transacao
from app.dominio.dinheiro import somar_meses
from app.repo import lancamentos as repo_lanc
from app.repo import recorrentes as repo


def criar_recorrente(
    con: Any, *, nome: str, tipo: str, dia: int, valor_estimado_centavos: int,
    conta_id: int, categoria_id: int | None,
) -> int:
    if not nome.strip():
        raise ValueError("O nome não pode ser vazio.")
    if tipo not in ("entrada", "saida"):
        raise ValueError("O tipo deve ser 'entrada' ou 'saida'.")
    if not 1 <= dia <= 31:
        raise ValueError("O dia deve estar entre 1 e 31.")
    if valor_estimado_centavos <= 0:
        raise ValueError("O valor estimado deve ser maior que zero.")
    if not repo.conta_existe(con, conta_id):
        raise ValueError("Conta não encontrada.")
    if categoria_id is not None and not repo.categoria_existe(con, categoria_id):
        raise ValueError("Categoria não encontrada.")
    with transacao(con):
        return repo.inserir(
            con, nome=nome.strip(), tipo=tipo, dia=dia,
            valor_estimado_centavos=valor_estimado_centavos,
            conta_id=conta_id, categoria_id=categoria_id)


def gerar_recorrentes(con: Any, hoje: date, meses: int) -> int:
    """Gera pendentes estimados para `meses` meses a partir do mês de `hoje`
    (janela: [mês de hoje, mês de hoje + meses - 1]). Idempotente por (recorrente, competência
    AAAA-MM), independente da data atual do lançamento (editável pelo usuário);
    ignora meses pulados e recorrentes inativas; dia ajustado ao fim do mês.
    Devolve o total criado."""
    if meses < 1:
        raise ValueError("O número de meses deve ser pelo menos 1.")
    criados = 0
    with transacao(con):
        for r in repo.listar_ativas(con):
            rid = int(r["id"])
            real = repo.ultimo_valor_real(con, rid)
            magnitude = int(r["valor_estimado_centavos"]) if real is None else real
            sinal = -1 if r["tipo"] == "saida" else 1
            for k in range(meses):
                venc = somar_meses(hoje.replace(day=1), k, int(r["dia"]))
                mes = venc.isoformat()[:7]
                if repo.mes_pulado(con, rid, mes) or repo.mes_ja_gerado(con, rid, mes):
                    continue
                repo.inserir_lancamento_estimado(
                    con, recorrente_id=rid, conta_id=int(r["conta_id"]), data=venc.isoformat(),
                    descricao=r["nome"], valor_centavos=sinal * magnitude, tipo=r["tipo"],
                    categoria_id=r["categoria_id"], competencia=mes)
                criados += 1
    return criados


def confirmar_recorrente(
    con: Any, lancamento_id: int, valor_real_centavos: int, data_pagamento: date
) -> None:
    """Marca o lançamento como pago com o valor real (estimado=0) e propaga o valor aos
    pendentes futuros ainda estimados do mesmo recorrente."""
    if valor_real_centavos <= 0:
        raise ValueError("O valor real deve ser maior que zero.")
    row = repo_lanc.obter(con, lancamento_id)
    if row is None:
        raise ValueError("Lançamento não encontrado.")
    if row["recorrente_id"] is None:
        raise ValueError("O lançamento não pertence a um recorrente.")
    sinal = -1 if int(row["valor_centavos"]) < 0 else 1
    with transacao(con):
        repo_lanc.marcar_compensado(
            con, lancamento_id, data_pagamento.isoformat(), sinal * valor_real_centavos)
        repo.atualizar_futuros_estimados(
            con, int(row["recorrente_id"]), str(row["data"]), sinal * valor_real_centavos)


def pular_mes(con: Any, lancamento_id: int, hoje: date | None = None) -> None:
    """Remove o pendente do mês e impede que a geração o recrie. Holerite ligado a ele é
    solto antes (`hoje` decide se volta a ter entrada prevista; padrão: data do sistema)."""
    row = repo_lanc.obter(con, lancamento_id)
    if row is None:
        raise ValueError("Lançamento não encontrado.")
    if row["recorrente_id"] is None:
        raise ValueError("O lançamento não pertence a um recorrente.")
    if row["compensado"]:
        raise ValueError("Lançamento já pago não pode ser pulado.")
    from app.dominio import holerites as dom_hol  # evita import circular
    with transacao(con):
        dom_hol.liberar_vinculo_do_lancamento(con, lancamento_id, hoje)
        repo.excluir_lancamento(con, lancamento_id)
        repo.marcar_pulado(con, int(row["recorrente_id"]), str(row["competencia"] or str(row["data"])[:7]))


def desativar_recorrente(con: Any, recorrente_id: int, hoje: date) -> None:
    """Desativa e apaga só os pendentes futuros (data > hoje); vencidos e pagos permanecem.
    Holerite ligado a um pendente apagado é solto antes."""
    if repo.obter(con, recorrente_id) is None:
        raise ValueError("Recorrente não encontrada.")
    from app.dominio import holerites as dom_hol  # evita import circular
    with transacao(con):
        repo.desativar(con, recorrente_id)
        for lid in repo.pendentes_futuros(con, recorrente_id, hoje.isoformat()):
            dom_hol.liberar_vinculo_do_lancamento(con, lid, hoje)
        repo.excluir_pendentes_futuros(con, recorrente_id, hoje.isoformat())
