"""Importação de fatura de cartão: prévia, gravação (fatura + itens), previstas e substituição."""
import uuid
from collections import defaultdict, deque
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from typing import Any, Literal

from app.db import transacao
from app.dominio.categorias import categorizar
from app.dominio.dinheiro import somar_meses
from app.dominio.lancamentos import MAX_LOTE
from app.dominio.normalizacao import normalizar_descricao
from app.importacao.fatura.modelo import FaturaLida, ItemFatura
from app.repo import categorias as repo_categorias
from app.repo import contas as repo_contas
from app.repo import faturas as repo
from app.repo import lancamentos as repo_lanc

TOLERANCIA_MINIMA_CENTAVOS = 200  # R$ 2,00
TOLERANCIA_PERCENTUAL = 2  # %
SIMILARIDADE_MINIMA = 0.8


class ErroFatura(ValueError):
    """Regra de fatura/itens violada (mensagem em português, própria para o usuário)."""


class RegistroNaoEncontrado(ErroFatura):
    """Fatura ou item inexistente (mensagem em português; a web responde 404)."""


class FaturaJaImportada(ValueError):
    def __init__(self) -> None:
        super().__init__("Esta fatura já foi importada.")


class FaturaDiferenteExistente(ValueError):
    def __init__(self) -> None:
        super().__init__("Já existe uma fatura deste cartão neste mês, com conteúdo diferente. "
                         "Confirme para substituir.")


@dataclass
class PreviaFatura:
    fatura: FaturaLida
    arquivo_hash: str
    conta_id: int | None
    situacao: Literal["nova", "identica", "substitui"]
    categorias_sugeridas: list[int | None]
    previstas_a_baixar: int
    previstas_a_criar: int


@dataclass
class ResumoFatura:
    fatura_id: int
    lancamento_id: int
    itens: int
    previstas_criadas: int
    previstas_baixadas: int
    conta_id: int


@contextmanager
def _escrita(con: Any) -> Iterator[None]:
    """Transação própria; com uma já aberta pelo chamador, usa SAVEPOINT."""
    if not con.in_transaction:
        with transacao(con):
            yield
        return
    ponto = f"fat_{uuid.uuid4().hex}"
    con.execute(f"SAVEPOINT {ponto}")
    try:
        yield
    except BaseException:
        con.execute(f"ROLLBACK TO {ponto}")
        con.execute(f"RELEASE {ponto}")
        raise
    else:
        con.execute(f"RELEASE {ponto}")


def _competencia(fatura: FaturaLida) -> str:
    return fatura.data_vencimento[:7]


def nome_cartao(fatura: FaturaLida) -> str:
    partes = [p for p in (fatura.emissor, fatura.bandeira, fatura.produto) if p]
    return " ".join([*partes, "final", fatura.final_cartao])


def _conta_do_cartao(con: Any, final: str) -> int | None:
    """Só contas do tipo cartão (sem cartão: a prévia oferece criar a conta)."""
    for cid, fin in repo_contas.ids_por_final_numero(con):
        if fin != final:
            continue
        conta = repo_contas.obter(con, cid)
        if conta is not None and conta["tipo"] == "cartao":
            return cid
    return None


def _situacao(con: Any, conta_id: int | None, fatura: FaturaLida,
              arquivo_hash: str) -> Literal["nova", "identica", "substitui"]:
    if conta_id is None:
        return "nova"
    existente = repo.por_conta_competencia(con, conta_id, _competencia(fatura))
    if existente is None:
        return "nova"
    return "identica" if existente["arquivo_hash"] == arquivo_hash else "substitui"


def _historico_categorias(con: Any, conta_id: int | None) -> dict[str, int]:
    """Descrição normalizada -> categoria usada na fatura mais recente da conta."""
    if conta_id is None:
        return {}
    usadas: dict[str, int] = {}
    for r in repo.categorias_usadas(con, conta_id):
        usadas.setdefault(normalizar_descricao(str(r["descricao"])), int(r["categoria_id"]))
    return usadas


def _sugerir_categoria(con: Any, item: ItemFatura, historico: dict[str, int]) -> int | None:
    """Regras do usuário; depois a categoria dada ao mesmo item em fatura anterior; por fim a
    categoria do banco."""
    cat = categorizar(con, item.descricao)
    if cat is None:
        cat = historico.get(normalizar_descricao(item.descricao))
    if cat is None and item.categoria_banco:
        cat = repo_categorias.id_por_nome(con, item.categoria_banco)
    return cat


def _similaridade(a: str, b: str) -> float:
    na, nb = normalizar_descricao(a), normalizar_descricao(b)
    if not na or not nb:
        return 0.0
    if na in nb or nb in na:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _valor_proximo(previsto: int, real: int) -> bool:
    tolerancia = max(TOLERANCIA_MINIMA_CENTAVOS, abs(previsto) * TOLERANCIA_PERCENTUAL // 100)
    return abs(previsto - real) <= tolerancia


def _compativel(row: Any, item: ItemFatura) -> bool:
    """Mesmo total de parcelas, valor dentro da tolerância e descrição parecida."""
    return (row["parcela_total"] == item.parcela_total
            and _valor_proximo(int(row["valor_centavos"]), item.valor_centavos)
            and _similaridade(str(row["descricao"]), item.descricao) >= SIMILARIDADE_MINIMA)


def _melhor(candidatos: list[Any], item: ItemFatura) -> Any | None:
    """Valor mais próximo; depois descrição mais parecida; depois menor id."""
    if not candidatos:
        return None
    return min(candidatos, key=lambda r: (abs(int(r["valor_centavos"]) - item.valor_centavos),
                                          -_similaridade(str(r["descricao"]), item.descricao),
                                          int(r["id"])))


@dataclass
class _Plano:
    prevista: Any | None  # prevista pendente a baixar
    grupo: str | None  # grupo da prevista ou inferido de item de outra fatura


def _planejar(con: Any, conta_id: int, itens: list[ItemFatura],
              excluir_fatura_id: int | None) -> list[_Plano]:
    """Casa cada item parcelado com uma prevista (cada uma usada uma vez) ou, sem prevista,
    com o grupo de um item parcelado de outra fatura da conta (importação fora de ordem)."""
    livres = list(repo_lanc.previstas_pendentes_da_conta(con, conta_id))
    existentes = repo.itens_parcelados_da_conta(con, conta_id, excluir_fatura_id)
    ocupados: set[tuple[str, int]] = set()
    planos: list[_Plano] = []
    for item in itens:
        n = item.parcela_atual
        if n is None or item.parcela_total is None:
            planos.append(_Plano(None, None))
            continue
        prevista = _melhor([p for p in livres if p["parcela_atual"] == n
                            and (str(p["grupo_parcela_uuid"]), n) not in ocupados
                            and _compativel(p, item)], item)
        grupo: str | None
        if prevista is not None:
            livres.remove(prevista)
            grupo = str(prevista["grupo_parcela_uuid"])
        else:
            irmao = _melhor([e for e in existentes if e["parcela_atual"] != n
                             and (str(e["grupo_parcela_uuid"]), n) not in ocupados
                             and _compativel(e, item)
                             and not repo_lanc.parcela_existe_no_grupo(
                                 con, str(e["grupo_parcela_uuid"]), n, excluir_fatura_id)], item)
            grupo = None if irmao is None else str(irmao["grupo_parcela_uuid"])
        if grupo is not None:
            ocupados.add((grupo, n))
        planos.append(_Plano(prevista, grupo))
    return planos


def _parcelas_futuras(item: ItemFatura) -> range:
    if item.parcela_atual is None or item.parcela_total is None:
        return range(0)
    return range(item.parcela_atual + 1, item.parcela_total + 1)


def _a_criar(con: Any, item: ItemFatura, grupo: str | None,
             excluir_fatura_id: int | None) -> list[int]:
    """Parcelas futuras que ainda não existem no grupo (nem prevista nem item de outra fatura)."""
    return [k for k in _parcelas_futuras(item)
            if grupo is None
            or not repo_lanc.parcela_existe_no_grupo(con, grupo, k, excluir_fatura_id)]


def preparar_previa(con: Any, fatura: FaturaLida, arquivo_hash: str) -> PreviaFatura:
    """Nada é gravado: conta-cartão, situação, categorias sugeridas e efeito nas previstas."""
    conta_id = _conta_do_cartao(con, fatura.final_cartao)
    situacao = _situacao(con, conta_id, fatura, arquivo_hash)
    historico = _historico_categorias(con, conta_id)
    sugeridas = [_sugerir_categoria(con, i, historico) for i in fatura.itens]
    if conta_id is None:
        criar = sum(len(_parcelas_futuras(i)) for i in fatura.itens)
        return PreviaFatura(fatura, arquivo_hash, None, situacao, sugeridas, 0, criar)
    existente = repo.por_conta_competencia(con, conta_id, _competencia(fatura))
    excluir = None if existente is None else int(existente["id"])
    planos = _planejar(con, conta_id, fatura.itens, excluir)
    baixar = sum(p.prevista is not None for p in planos)
    criar = sum(len(_a_criar(con, i, p.grupo, excluir)) for i, p in zip(fatura.itens, planos))
    return PreviaFatura(fatura, arquivo_hash, conta_id, situacao, sugeridas, baixar, criar)


def criar_conta_cartao(con: Any, fatura: FaturaLida) -> int:
    """Cria a conta-cartão sem commit próprio: transação própria, ou SAVEPOINT dentro da
    transação do chamador (que decide confirmar ou reverter)."""
    with _escrita(con):
        return repo_contas.criar_conta(con, nome_cartao(fatura), "cartao",
                                       instituicao=fatura.emissor,
                                       final_numero=fatura.final_cartao, commit=False)


def _validar(con: Any, fatura: FaturaLida, conta_id: int | None,
             categorias: Sequence[int | None]) -> None:
    if len(categorias) != len(fatura.itens):
        raise ErroFatura("A lista de categorias não corresponde aos itens da fatura.")
    if conta_id is not None and repo_contas.obter(con, conta_id) is None:
        raise ErroFatura("Conta do cartão não encontrada.")
    for cat in {c for c in categorias if c is not None}:
        if not repo_categorias.existe(con, cat):
            raise ErroFatura("Categoria não encontrada.")


Chave = tuple[str, str, int]
Guardados = dict[Chave, deque[tuple[int | None, str | None]]]


def _chave(data: str, descricao: str, valor: int) -> Chave:
    return data, normalizar_descricao(descricao), valor


def _desfazer_anterior(con: Any, fatura_id: int) -> Guardados:
    """Apaga itens e previstas da fatura anterior; devolve categoria/grupo por item."""
    guardados: Guardados = defaultdict(deque)
    antigos = repo.itens(con, fatura_id)
    for it in antigos:
        guardados[_chave(str(it["data"]), str(it["descricao"]), int(it["valor_centavos"]))
                  ].append((it["categoria_id"], it["grupo_parcela_uuid"]))
    grupos = sorted({str(it["grupo_parcela_uuid"]) for it in antigos if it["grupo_parcela_uuid"]})
    repo_lanc.excluir_previstas_dos_grupos(con, grupos)
    repo.excluir_itens(con, fatura_id)
    return guardados


def _gravar_cabecalho(con: Any, fatura: FaturaLida, arquivo_hash: str, arquivo_nome: str,
                      conta_id: int, hoje: date, existente: Any | None) -> tuple[int, int]:
    valor = -fatura.calculado_centavos
    if existente is None:
        fid = repo.criar(
            con, conta_id=conta_id, competencia=_competencia(fatura),
            data_vencimento=fatura.data_vencimento, data_fechamento=fatura.data_fechamento,
            emissor=fatura.emissor, bandeira=fatura.bandeira, produto=fatura.produto,
            total_impresso_centavos=fatura.total_impresso_centavos,
            total_calculado_centavos=fatura.calculado_centavos,
            ajustes_resumo_centavos=fatura.ajustes_resumo_centavos,
            arquivo_hash=arquivo_hash, arquivo_nome=arquivo_nome, criado_em=hoje.isoformat())
        lid = repo_lanc.inserir_fatura(con, conta_id=conta_id, data=fatura.data_vencimento,
                                       descricao=f"Fatura {nome_cartao(fatura)}",
                                       valor_centavos=valor)
        repo.definir_lancamento(con, fid, lid)
        return fid, lid
    fid = int(existente["id"])
    repo.atualizar_totais(con, fid, fatura.total_impresso_centavos, fatura.calculado_centavos,
                          fatura.ajustes_resumo_centavos, arquivo_hash, arquivo_nome)
    repo.atualizar_vencimento(con, fid, fatura.data_vencimento, fatura.data_fechamento)
    lid = existente["lancamento_id"]
    if lid is None:
        lid = repo_lanc.inserir_fatura(con, conta_id=conta_id, data=fatura.data_vencimento,
                                       descricao=f"Fatura {nome_cartao(fatura)}",
                                       valor_centavos=valor)
        repo.definir_lancamento(con, fid, lid)
    else:
        repo_lanc.atualizar_fatura(con, int(lid), data=fatura.data_vencimento,
                                   valor_centavos=valor)
    return fid, int(lid)


def importar_fatura(con: Any, fatura: FaturaLida, arquivo_hash: str, arquivo_nome: str,
                    conta_id: int | None, hoje: date, categorias: list[int | None],
                    substituir: bool = False) -> ResumoFatura:
    """Grava fatura, itens, baixa/cria previstas — tudo numa transação. conta_id None:
    cria a conta-cartão (dados da fatura) na mesma transação."""
    _validar(con, fatura, conta_id, categorias)
    with _escrita(con):
        if conta_id is None:
            conta_id = criar_conta_cartao(con, fatura)
        existente = repo.por_conta_competencia(con, conta_id, _competencia(fatura))
        guardados: Guardados = {}
        if existente is not None:
            if existente["arquivo_hash"] == arquivo_hash:
                raise FaturaJaImportada()
            if not substituir:
                raise FaturaDiferenteExistente()
            guardados = _desfazer_anterior(con, int(existente["id"]))
        fid, lid = _gravar_cabecalho(con, fatura, arquivo_hash, arquivo_nome, conta_id, hoje,
                                     existente)
        planos = _planejar(con, conta_id, fatura.itens, fid)
        vencimento = date.fromisoformat(fatura.data_vencimento)
        criadas = baixadas = 0
        for ordem, (item, cat, plano) in enumerate(zip(fatura.itens, categorias, planos), 1):
            fila = guardados.get(_chave(item.data, item.descricao, item.valor_centavos))
            cat_antiga, grupo_antigo = fila.popleft() if fila else (None, None)
            categoria = cat_antiga if cat_antiga is not None else cat
            if plano.prevista is not None:
                if categoria is None:
                    categoria = plano.prevista["categoria_id"]
                repo_lanc.excluir(con, int(plano.prevista["id"]))
                baixadas += 1
            grupo: str | None = None
            if item.parcela_atual is not None and item.parcela_total is not None:
                grupo = plano.grupo or grupo_antigo or str(uuid.uuid4())
            repo.inserir_item(
                con, fid, ordem=ordem, data=item.data, descricao=item.descricao,
                valor_centavos=item.valor_centavos, tipo=item.tipo, categoria_id=categoria,
                categoria_banco=item.categoria_banco, parcela_atual=item.parcela_atual,
                parcela_total=item.parcela_total, grupo_parcela_uuid=grupo,
                final_cartao=item.final_cartao)
            if grupo is None:
                continue
            for k in _a_criar(con, item, grupo, fid):
                repo_lanc.inserir_prevista(
                    con, conta_id=conta_id,
                    data=somar_meses(vencimento, k - int(item.parcela_atual), vencimento.day
                                     ).isoformat(),
                    descricao=item.descricao, valor_centavos=item.valor_centavos,
                    tipo="saida" if item.valor_centavos < 0 else "entrada",
                    categoria_id=categoria, grupo_parcela_uuid=grupo, parcela_atual=k,
                    parcela_total=int(item.parcela_total))
                criadas += 1
        resumo = ResumoFatura(fid, lid, len(fatura.itens), criadas, baixadas, conta_id)
    from app.dominio.faturas_pagamento import casar_automaticamente
    casar_automaticamente(con)
    return resumo


def alterar_categoria_item(con: Any, item_id: int, categoria_id: int | None) -> None:
    if repo.obter_item(con, item_id) is None:
        raise RegistroNaoEncontrado("Item da fatura não encontrado.")
    if categoria_id is not None and not repo_categorias.existe(con, categoria_id):
        raise ErroFatura("Categoria não encontrada.")
    with _escrita(con):
        repo.atualizar_categoria_item(con, [item_id], categoria_id)


def categorizar_itens_em_lote(con: Any, ids: Sequence[int], categoria_id: int) -> int:
    unicos = sorted(set(ids))
    if not unicos:
        raise ErroFatura("Selecione ao menos um item.")
    if len(unicos) > MAX_LOTE:
        raise ErroFatura(f"Selecione no máximo {MAX_LOTE} itens por vez.")
    if not repo_categorias.existe(con, categoria_id):
        raise ErroFatura("Categoria não encontrada.")
    if repo.contar_itens(con, unicos) != len(unicos):
        raise ErroFatura("Algum item selecionado não foi encontrado.")
    with _escrita(con):
        return repo.atualizar_categoria_item(con, unicos, categoria_id)


def detalhe(con: Any, fatura_id: int) -> tuple[Any, list[Any]]:
    """Fatura e seus itens (na ordem da fatura)."""
    fat = repo.obter(con, fatura_id)
    if fat is None:
        raise RegistroNaoEncontrado("Fatura não encontrada.")
    return fat, repo.itens(con, fatura_id)


def categorizar_itens_da_fatura(con: Any, fatura_id: int, ids: Sequence[int],
                                categoria_id: int) -> int:
    """Lote restrito aos itens da fatura `fatura_id`."""
    _, itens = detalhe(con, fatura_id)
    if not set(ids) <= {int(it["id"]) for it in itens}:
        raise RegistroNaoEncontrado("Algum item selecionado não pertence a esta fatura.")
    return categorizar_itens_em_lote(con, ids, categoria_id)


def aplicar_regras_itens(con: Any, fatura_id: int) -> int:
    """Aplica as regras de categoria aos itens sem categoria da fatura; devolve quantos."""
    regras = repo_categorias.listar_regras_ordenadas(con)
    if not regras:
        return 0
    atualizados = 0
    with _escrita(con):
        for it in repo.itens(con, fatura_id):
            if it["categoria_id"] is not None:
                continue
            cat = categorizar(con, str(it["descricao"]))
            if cat is not None:
                repo.atualizar_categoria_item(con, [int(it["id"])], cat)
                atualizados += 1
    return atualizados


def previstas_orfas(con: Any) -> list[Any]:
    """Previstas já cobertas por uma fatura importada sem item correspondente (descartáveis)."""
    return repo_lanc.previstas_orfas(con)


def ids_previstas_orfas(con: Any) -> set[int]:
    """Ids das previstas órfãs (uma consulta só, para marcar a página do extrato)."""
    return {int(r["id"]) for r in repo_lanc.previstas_orfas(con)}
