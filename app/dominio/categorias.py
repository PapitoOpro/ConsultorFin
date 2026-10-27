"""Tipos de lançamento, categorias, regras e totais mensais."""
import unicodedata
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
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


class ErroCategoria(ValueError):
    """Violação de regra de categoria (mensagem em português, própria para o usuário)."""


_TIPOS = ("entrada", "saida")


@contextmanager
def _escrita(con: Any) -> Iterator[None]:
    """Transação própria; se o chamador já tem uma aberta, usa SAVEPOINT para reverter só
    o que esta operação fez (a transação externa continua sendo do chamador)."""
    if not con.in_transaction:
        with transacao(con):
            yield
        return
    ponto = f"cat_{uuid.uuid4().hex}"
    con.execute(f"SAVEPOINT {ponto}")
    try:
        yield
    except BaseException:
        con.execute(f"ROLLBACK TO {ponto}")
        con.execute(f"RELEASE {ponto}")
        raise
    else:
        con.execute(f"RELEASE {ponto}")


_NOME_MAX = 80


def _nome_valido(nome: str, rotulo: str) -> str:
    limpo = " ".join(nome.split())
    if not limpo:
        raise ErroCategoria(f"O nome da {rotulo} não pode ser vazio.")
    if len(limpo) > _NOME_MAX:
        raise ErroCategoria(f"O nome da {rotulo} pode ter até {_NOME_MAX} caracteres.")
    if any(unicodedata.category(c) in ("Cc", "Cf") for c in limpo):
        raise ErroCategoria(f"O nome da {rotulo} tem caracteres inválidos.")
    return limpo


def _tipo_valido(tipo: str) -> str:
    if tipo not in _TIPOS:
        raise ErroCategoria("Tipo inválido: use entrada ou saída.")
    return tipo


def _exigir(con: Any, categoria_id: int) -> Any:
    row = repo.obter(con, categoria_id)
    if row is None:
        raise ErroCategoria(f"Categoria {categoria_id} não encontrada.")
    return row


def _exigir_nome_livre(con: Any, nome: str, pai_id: int | None,
                       ignorar_id: int | None = None) -> None:
    if repo.nome_existe(con, nome, pai_id, ignorar_id):
        onde = "entre as categorias" if pai_id is None else "nesta categoria"
        raise ErroCategoria(f'Já existe uma categoria chamada "{nome}" {onde}.')


def criar_categoria(con: Any, nome: str, tipo: str = "saida") -> int:
    nome = _nome_valido(nome, "categoria")
    _tipo_valido(tipo)
    with _escrita(con):
        _exigir_nome_livre(con, nome, None)
        return repo.criar(con, nome, None, tipo)


def criar_subcategoria(con: Any, pai_id: int, nome: str) -> int:
    nome = _nome_valido(nome, "subcategoria")
    with _escrita(con):
        pai = _exigir(con, pai_id)
        if pai["pai_id"] is not None:
            raise ErroCategoria("Uma subcategoria não pode ter subcategorias.")
        _exigir_nome_livre(con, nome, pai_id)
        return repo.criar(con, nome, pai_id, str(pai["tipo"]))


def listar_categorias(con: Any) -> list[Any]:
    return repo.listar_raiz(con)


def listar_subcategorias(con: Any, pai_id: int) -> list[Any]:
    return repo.listar_filhas(con, pai_id)


def renomear_categoria(con: Any, categoria_id: int, nome: str) -> None:
    nome = _nome_valido(nome, "categoria")
    with _escrita(con):
        cat = _exigir(con, categoria_id)
        _exigir_nome_livre(con, nome, cat["pai_id"], categoria_id)
        repo.renomear(con, categoria_id, nome)


def alterar_tipo(con: Any, categoria_id: int, tipo: str) -> None:
    _tipo_valido(tipo)
    with _escrita(con):
        cat = _exigir(con, categoria_id)
        if cat["pai_id"] is not None:
            raise ErroCategoria("O tipo da subcategoria segue o da categoria.")
        repo.atualizar_tipo(con, categoria_id, tipo)


def mover_subcategoria(con: Any, categoria_id: int, novo_pai_id: int) -> None:
    with _escrita(con):
        cat = _exigir(con, categoria_id)
        if cat["pai_id"] is None:
            raise ErroCategoria("Só subcategorias podem ser movidas.")
        novo = _exigir(con, novo_pai_id)
        if novo["pai_id"] is not None:
            raise ErroCategoria("Uma subcategoria não pode ter subcategorias.")
        if novo["tipo"] != cat["tipo"]:
            raise ErroCategoria("Só é possível mover para uma categoria do mesmo tipo.")
        if int(cat["pai_id"]) == novo_pai_id:
            return
        _exigir_nome_livre(con, str(cat["nome"]), novo_pai_id, categoria_id)
        repo.mover(con, categoria_id, novo_pai_id)


def _descrever_uso(u: dict[str, int]) -> str:
    return (f"{u['lancamentos']} lançamento(s), {u['recorrentes']} conta(s) fixa(s) e "
            f"{u['regras']} regra(s) automática(s)")


def remover_categoria(con: Any, categoria_id: int, substituir_por: int | None = None,
                      deixar_sem: bool = False) -> dict[str, int]:
    """Remove a categoria. Em uso, exige substituir_por (mesmo tipo) ou deixar_sem.

    Retorna quantos lançamentos, recorrentes e regras foram reapontados/zerados/apagados."""
    if substituir_por is not None and deixar_sem:
        raise ErroCategoria("Escolha só uma opção: substituir por outra categoria ou deixar sem categoria.")
    with _escrita(con):
        cat = _exigir(con, categoria_id)
        if repo.tem_filhas(con, categoria_id):
            raise ErroCategoria("Remova as subcategorias antes de remover a categoria.")
        if substituir_por is not None:
            if substituir_por == categoria_id:
                raise ErroCategoria("Escolha outra categoria para substituir.")
            destino = _exigir(con, substituir_por)
            if destino["tipo"] != cat["tipo"]:
                raise ErroCategoria("A categoria substituta precisa ter o mesmo tipo.")
        u = repo.uso(con, categoria_id)
        if sum(u.values()) and substituir_por is None and not deixar_sem:
            raise ErroCategoria(
                f"A categoria está em uso: {_descrever_uso(u)}. "
                "Escolha substituir por outra categoria ou deixar sem categoria.")
        if substituir_por is not None:
            resumo = repo.reapontar(con, categoria_id, substituir_por)
        else:
            resumo = repo.limpar(con, categoria_id)
        repo.remover(con, categoria_id)
        return resumo


def arvore_para_tela(con: Any) -> dict[str, list[dict[str, Any]]]:
    """Categorias agrupadas por tipo, com subcategorias e contagem de uso (para o template)."""
    tela: dict[str, list[dict[str, Any]]] = {"entrada": [], "saida": []}
    for raiz in repo.arvore(con):
        raiz["uso_total"] = (sum(raiz["uso"].values())
                             + sum(sum(f["uso"].values()) for f in raiz["filhas"]))
        tela[str(raiz["tipo"])].append(raiz)
    return tela


def _limites(ano: int, mes: int) -> tuple[str, str]:
    if not 1 <= mes <= 12:
        raise ValueError(f"Mês inválido: {mes}.")
    prox = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return f"{ano:04d}-{mes:02d}-01", f"{prox[0]:04d}-{prox[1]:02d}-01"


def despesas_do_mes(con: Any, ano: int, mes: int) -> int:
    """Caixa: saídas e pagamentos de fatura não casados (contas não-cartão) e linhas de fatura
    pagas, pela data de compensação. Itens de fatura não entram (ver `consumo_do_mes`)."""
    ini, fim = _limites(ano, mes)
    return -repo.total_compensado_do_mes(con, ("saida", "pagamento_fatura", "fatura"), ini, fim)


def consumo_do_mes(con: Any, ano: int, mes: int) -> int:
    """Consumo (relatório por categoria): saídas e pagamentos de fatura não casados (contas
    não-cartão, pela compensação) mais os itens de fatura pela data da compra (créditos abatem).
    A linha da fatura não entra."""
    ini, fim = _limites(ano, mes)
    return -(repo.total_compensado_do_mes(con, ("saida", "pagamento_fatura"), ini, fim)
             + repo.total_itens_fatura_do_mes(con, ini, fim))


def entradas_do_mes(con: Any, ano: int, mes: int) -> int:
    ini, fim = _limites(ano, mes)
    return repo.total_compensado_do_mes(con, ("entrada",), ini, fim)
