"""Tipos de lançamento, categorias, regras e totais mensais."""
from typing import Any

from app.db import transacao
from app.repo import categorias as repo

PADROES_FATURA_PADRAO: list[str] = ["PAG FATURA", "FATURA CARTAO", "PAGAMENTO CARTAO"]
_MEIOS_TRANSFERENCIA = ("PIX", "TED", "TRANSF")


def classificar_tipo(descricao: str, valor_centavos: int, nome_titular: str | None,
                     padroes_fatura: list[str]) -> str:
    desc = descricao.casefold()
    titular = (nome_titular or "").strip().casefold()
    if titular and titular in desc and any(m.casefold() in desc for m in _MEIOS_TRANSFERENCIA):
        return "transferencia"
    if any(p.casefold() in desc for p in padroes_fatura if p.strip()):
        return "pagamento_fatura"
    return "entrada" if valor_centavos > 0 else "saida"


def _casar(regras: list[tuple[str, int]], descricao: str) -> int | None:
    desc = descricao.casefold()
    for padrao, categoria_id in regras:
        if padrao.strip() and padrao.casefold() in desc:
            return categoria_id
    return None


def categorizar(con: Any, descricao: str) -> int | None:
    return _casar(repo.listar_regras_ordenadas(con), descricao)


def criar_regra(con: Any, padrao: str, categoria_id: int, prioridade: int = 0) -> int:
    if not padrao.strip():
        raise ValueError("O padrão da regra não pode ser vazio.")
    if not repo.existe(con, categoria_id):
        raise ValueError(f"Categoria {categoria_id} não encontrada.")
    return repo.inserir_regra(con, padrao.strip(), categoria_id, prioridade)


def aplicar_regras(con: Any, importacao_id: int | None = None) -> int:
    regras = repo.listar_regras_ordenadas(con)
    if not regras:
        return 0
    if con.in_transaction:  # chamador (ex.: importar) é dono da transação
        return _aplicar(con, regras, importacao_id)
    with transacao(con):
        return _aplicar(con, regras, importacao_id)


def _aplicar(con: Any, regras: list[tuple[str, int]], importacao_id: int | None) -> int:
    atualizados = 0
    for lid, descricao in repo.lancamentos_sem_categoria(con, importacao_id):
        cat = _casar(regras, descricao)
        if cat is not None:
            repo.definir_categoria_do_lancamento(con, lid, cat)
            atualizados += 1
    return atualizados


def criar_categoria(con: Any, nome: str) -> int:
    if not nome.strip():
        raise ValueError("O nome da categoria não pode ser vazio.")
    return repo.criar(con, nome.strip())


def criar_subcategoria(con: Any, pai_id: int, nome: str) -> int:
    if not nome.strip():
        raise ValueError("O nome da subcategoria não pode ser vazio.")
    if not repo.existe(con, pai_id):
        raise ValueError(f"Categoria {pai_id} não encontrada.")
    return repo.criar(con, nome.strip(), pai_id)


def listar_categorias(con: Any) -> list[Any]:
    return repo.listar_raiz(con)


def listar_subcategorias(con: Any, pai_id: int) -> list[Any]:
    return repo.listar_filhas(con, pai_id)


def renomear_categoria(con: Any, categoria_id: int, nome: str) -> None:
    if not nome.strip():
        raise ValueError("O nome da categoria não pode ser vazio.")
    if not repo.renomear(con, categoria_id, nome.strip()):
        raise ValueError(f"Categoria {categoria_id} não encontrada.")


def remover_categoria(con: Any, categoria_id: int) -> None:
    if repo.tem_filhas(con, categoria_id):
        raise ValueError("Remova as subcategorias antes de remover a categoria.")
    if not repo.remover(con, categoria_id):
        raise ValueError(f"Categoria {categoria_id} não encontrada.")


def _limites(ano: int, mes: int) -> tuple[str, str]:
    if not 1 <= mes <= 12:
        raise ValueError(f"Mês inválido: {mes}.")
    prox = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return f"{ano:04d}-{mes:02d}-01", f"{prox[0]:04d}-{prox[1]:02d}-01"


def despesas_do_mes(con: Any, ano: int, mes: int) -> int:
    ini, fim = _limites(ano, mes)
    return -repo.total_compensado_do_mes(con, ("saida", "pagamento_fatura"), ini, fim)


def entradas_do_mes(con: Any, ano: int, mes: int) -> int:
    ini, fim = _limites(ano, mes)
    return repo.total_compensado_do_mes(con, ("entrada",), ini, fim)
