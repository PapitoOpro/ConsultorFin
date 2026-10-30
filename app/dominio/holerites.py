"""Importação de holerite: prévia, gravação (holerite + itens), vínculo com o extrato."""
import calendar
import uuid
from collections import defaultdict, deque
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from app.db import transacao
from app.dominio.categorias import categorizar
from app.dominio.faturas import TOLERANCIA_MINIMA_CENTAVOS, TOLERANCIA_PERCENTUAL
from app.dominio.normalizacao import normalizar_descricao
from app.importacao.holerite.modelo import HoleriteLido
from app.repo import categorias as repo_categorias
from app.repo import configuracoes as repo_config
from app.repo import contas as repo_contas
from app.repo import holerites as repo
from app.repo import lancamentos as repo_lanc

JANELA_DIAS = 45
PROXIMIDADE_DIAS = 10  # pendente sugerido: data a até 10 dias da data prevista
CHAVE_DIA = {"adiantamento": "holerite_dia_adiantamento", "mensal": "holerite_dia_mensal"}
DIA_PADRAO = {"adiantamento": 20, "mensal": 2}
ROTULO = {"adiantamento": "Adiantamento", "mensal": "Salário"}


class ErroHolerite(ValueError):
    """Regra de holerite violada (mensagem em português, própria para o usuário)."""


class HoleriteJaImportado(ValueError):
    def __init__(self) -> None:
        super().__init__("Este holerite já foi importado.")


class HoleriteDiferenteExistente(ValueError):
    def __init__(self) -> None:
        super().__init__("Já existe um holerite deste tipo nesta competência, com conteúdo "
                         "diferente. Confirme para substituir.")


@dataclass
class Credito:
    lancamento_id: int
    data: str
    descricao: str
    valor_centavos: int
    pendente: bool = False  # entrada prevista já cadastrada (ex.: salário recorrente)


@dataclass
class PreviaHolerite:
    holerite: HoleriteLido
    arquivo_hash: str
    situacao: Literal["novo", "identico", "substitui"]
    creditos_candidatos: list[Credito]
    data_prevista: str
    categorias_sugeridas: list[int | None]
    vinculo_atual: int | None = None  # crédito já vinculado (substituição) ainda válido
    sem_prevista: bool = False  # sem crédito escolhido, não se cria entrada prevista
    pendente_sugerido: int | None = None  # única entrada pendente perto da data prevista


@dataclass
class ResumoHolerite:
    holerite_id: int
    lancamento_id: int | None  # None: sem crédito no extrato e sem entrada prevista
    itens: int
    vinculado: bool


@contextmanager
def _escrita(con: Any) -> Iterator[None]:
    """Transação própria; com uma já aberta pelo chamador, usa SAVEPOINT."""
    if not con.in_transaction:
        with transacao(con):
            yield
        return
    ponto = f"hol_{uuid.uuid4().hex}"
    con.execute(f"SAVEPOINT {ponto}")
    try:
        yield
    except BaseException:
        con.execute(f"ROLLBACK TO {ponto}")
        con.execute(f"RELEASE {ponto}")
        raise
    else:
        con.execute(f"RELEASE {ponto}")


def _dia_config(con: Any, tipo: str) -> int:
    bruto = repo_config.obter(con, CHAVE_DIA[tipo])
    try:
        dia = int(bruto) if bruto is not None else DIA_PADRAO[tipo]
    except ValueError:
        return DIA_PADRAO[tipo]
    return dia if 1 <= dia <= 31 else DIA_PADRAO[tipo]


def data_prevista(con: Any, tipo: str, competencia: str) -> str:
    """Adiantamento: dia configurado do mês; mensal: dia configurado do mês seguinte."""
    if tipo not in CHAVE_DIA:
        raise ErroHolerite("Tipo de holerite inválido.")
    ano, mes = int(competencia[:4]), int(competencia[5:7])
    if tipo == "mensal":
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    dia = min(_dia_config(con, tipo), calendar.monthrange(ano, mes)[1])
    return date(ano, mes, dia).isoformat()


def sem_prevista(data_prev: str, liquido_centavos: int, hoje: date) -> bool:
    """Não prever entrada: líquido zero, ou a data prevista já passou além da janela de
    vínculo (seria uma entrada atrasada fantasma na projeção)."""
    return (liquido_centavos <= 0
            or date.fromisoformat(data_prev) + timedelta(days=JANELA_DIAS) < hoje)


def _janela(competencia: str) -> tuple[str, str]:
    inicio = date(int(competencia[:4]), int(competencia[5:7]), 1)
    return inicio.isoformat(), (inicio + timedelta(days=JANELA_DIAS)).isoformat()


def _credito(row: Any) -> Credito:
    return Credito(int(row["id"]), str(row["data"]), str(row["descricao"]),
                   int(row["valor_centavos"]), pendente=row["compensado"] == 0)


def _valor_proximo(previsto: int, real: int) -> bool:
    """Mesma tolerância da fatura: max(R$ 2,00; 2%)."""
    tolerancia = max(TOLERANCIA_MINIMA_CENTAVOS, abs(previsto) * TOLERANCIA_PERCENTUAL // 100)
    return abs(previsto - real) <= tolerancia


def _elegivel(row: Any, h: HoleriteLido, conta_id: int, de: str, ate: str) -> bool:
    """Crédito compensado de valor exato, ou entrada pendente (não prevista por holerite) de
    valor próximo, da conta e na janela."""
    if row["tipo"] != "entrada" or row["conta_id"] != conta_id or not de <= row["data"] <= ate:
        return False
    if row["compensado"] == 1:
        return int(row["valor_centavos"]) == h.liquido_centavos
    return row["origem"] != "holerite" and _valor_proximo(h.liquido_centavos,
                                                          int(row["valor_centavos"]))


def _candidatos(con: Any, h: HoleriteLido, conta_id: int, existente: Any | None) -> list[Credito]:
    """Entradas compensadas de valor exato e entradas pendentes já cadastradas (ex.: salário
    recorrente) de valor próximo, na janela e ainda sem holerite; na substituição, o
    lançamento do próprio holerite substituído também é elegível (o repo o exclui)."""
    de, ate = _janela(h.competencia)
    rows = list(repo_lanc.entradas_candidatas(con, conta_id, h.liquido_centavos, de, ate))
    rows += [r for r in repo_lanc.entradas_pendentes_candidatas(con, conta_id, de, ate)
             if _elegivel(r, h, conta_id, de, ate)]
    if existente is not None and existente["lancamento_id"] is not None:
        proprio = repo_lanc.obter(con, int(existente["lancamento_id"]))
        if proprio is not None and _elegivel(proprio, h, conta_id, de, ate):
            rows.append(proprio)
    prevista = date.fromisoformat(data_prevista(con, h.tipo, h.competencia))

    def ordem(r: Any) -> tuple[bool, int, str, int]:
        """Créditos do extrato por data; pendentes pela distância à data prevista."""
        pendente = int(r["compensado"]) == 0
        dist = abs((date.fromisoformat(str(r["data"])) - prevista).days) if pendente else 0
        return pendente, dist, str(r["data"]), int(r["id"])

    rows.sort(key=ordem)
    return [_credito(r) for r in rows]


def _pendente_sugerido(candidatos: list[Credito], prevista: str) -> int | None:
    """A entrada pendente a até PROXIMIDADE_DIAS da data prevista, se for a única."""
    alvo = date.fromisoformat(prevista)
    perto = [c for c in candidatos if c.pendente
             and abs((date.fromisoformat(c.data) - alvo).days) <= PROXIMIDADE_DIAS]
    return perto[0].lancamento_id if len(perto) == 1 else None


def _vinculo_valido(existente: Any | None, candidatos: list[Credito]) -> int | None:
    """Lançamento já vinculado ao holerite substituído, se ainda for candidato válido."""
    if existente is None or existente["lancamento_id"] is None:
        return None
    atual = int(existente["lancamento_id"])
    return atual if any(c.lancamento_id == atual for c in candidatos) else None


def _situacao(existente: Any | None,
              arquivo_hash: str) -> Literal["novo", "identico", "substitui"]:
    if existente is None:
        return "novo"
    return "identico" if existente["arquivo_hash"] == arquivo_hash else "substitui"


def preparar_previa(con: Any, h: HoleriteLido, arquivo_hash: str, conta_id: int,
                    hoje: date) -> PreviaHolerite:
    """Nada é gravado: situação, créditos candidatos, data prevista e categorias sugeridas."""
    existente = repo.por_competencia_tipo(con, h.competencia, h.tipo)
    sugeridas = [categorizar(con, i.descricao) for i in h.itens]
    candidatos = _candidatos(con, h, conta_id, existente)
    prevista = data_prevista(con, h.tipo, h.competencia)
    return PreviaHolerite(h, arquivo_hash, _situacao(existente, arquivo_hash), candidatos,
                          prevista, sugeridas, _vinculo_valido(existente, candidatos),
                          sem_prevista(prevista, h.liquido_centavos, hoje),
                          _pendente_sugerido(candidatos, prevista))


def _validar(con: Any, h: HoleriteLido, conta_id: int, categorias: Sequence[int | None]) -> None:
    if h.tipo not in CHAVE_DIA:
        raise ErroHolerite("Tipo de holerite inválido.")
    try:
        date.fromisoformat(f"{h.competencia}-01")
    except ValueError:
        raise ErroHolerite("Competência do holerite inválida.") from None
    if len(categorias) != len(h.itens):
        raise ErroHolerite("A lista de categorias não corresponde aos itens do holerite.")
    if repo_contas.obter(con, conta_id) is None:
        raise ErroHolerite("Conta não encontrada.")
    for cat in {c for c in categorias if c is not None}:
        if not repo_categorias.existe(con, cat):
            raise ErroHolerite("Categoria não encontrada.")


def _desfazer_anterior(con: Any, holerite_id: int) -> dict[str, deque[int | None]]:
    """Apaga os itens do holerite anterior; devolve as categorias por descrição."""
    guardadas: dict[str, deque[int | None]] = defaultdict(deque)
    for it in repo.itens(con, holerite_id):
        guardadas[normalizar_descricao(str(it["descricao"]))].append(it["categoria_id"])
    repo.excluir_itens(con, holerite_id)
    return guardadas


def _ligar_lancamento(con: Any, h: HoleriteLido, conta_id: int, lancamento_id: int | None,
                      existente: Any | None, salario: int | None,
                      hoje: date) -> tuple[int | None, bool, int | None]:
    """Resolve o lançamento do holerite: crédito escolhido, ou entrada prevista (nova, ou a já
    criada pelo holerite substituído, atualizada), ou nenhum (`sem_prevista`). Devolve (id,
    vinculado ao extrato, prevista antiga a excluir depois de reapontar o holerite)."""
    antigo = None if existente is None or existente["lancamento_id"] is None \
        else repo_lanc.obter(con, int(existente["lancamento_id"]))
    prevista_antiga = (antigo is not None and antigo["origem"] == "holerite"
                       and antigo["compensado"] == 0)  # prevista ainda não casada
    data = data_prevista(con, h.tipo, h.competencia)
    if lancamento_id is not None:
        obsoleta = (int(antigo["id"])
                    if prevista_antiga and antigo["id"] != lancamento_id else None)
        credito = repo_lanc.obter(con, lancamento_id)
        if credito is not None and credito["categoria_id"] is None and salario is not None:
            repo_lanc.atualizar_categoria(con, [lancamento_id], salario)
        return lancamento_id, credito is not None and credito["compensado"] == 1, obsoleta
    if sem_prevista(data, h.liquido_centavos, hoje):
        return None, False, (int(antigo["id"]) if prevista_antiga else None)
    if prevista_antiga and antigo["conta_id"] == conta_id:
        repo_lanc.atualizar_fatura(con, int(antigo["id"]), data=data,
                                   valor_centavos=h.liquido_centavos)
        return int(antigo["id"]), False, None
    novo = repo_lanc.inserir_holerite_previsto(
        con, conta_id=conta_id, data=data,
        descricao=f"Holerite {ROTULO[h.tipo]} {h.competencia}",
        valor_centavos=h.liquido_centavos, categoria_id=salario)
    return novo, False, (int(antigo["id"]) if prevista_antiga else None)


def importar_holerite(con: Any, h: HoleriteLido, arquivo_hash: str, arquivo_nome: str | None,
                      conta_id: int, hoje: date, categorias: list[int | None],
                      lancamento_id: int | None, substituir: bool = False,
                      manter_vinculo: bool = True) -> ResumoHolerite:
    """Grava holerite, itens e vínculo/entrada prevista — tudo numa transação. Na substituição
    com `lancamento_id=None`, o vínculo anterior é mantido se ainda for válido (mesmo valor,
    janela e conta), salvo `manter_vinculo=False` (força "sem vínculo": cria prevista)."""
    _validar(con, h, conta_id, categorias)
    with _escrita(con):
        existente = repo.por_competencia_tipo(con, h.competencia, h.tipo)
        guardadas: dict[str, deque[int | None]] = {}
        if existente is not None:
            if existente["arquivo_hash"] == arquivo_hash:
                raise HoleriteJaImportado()
            if not substituir:
                raise HoleriteDiferenteExistente()
        candidatos = _candidatos(con, h, conta_id, existente)
        if lancamento_id is None and manter_vinculo:
            lancamento_id = _vinculo_valido(existente, candidatos)
        if lancamento_id is not None and lancamento_id not in {
                c.lancamento_id for c in candidatos}:
            raise ErroHolerite("O crédito escolhido não é um candidato válido.")
        if existente is not None:
            guardadas = _desfazer_anterior(con, int(existente["id"]))
        salario = repo_categorias.id_por_nome(con, "Salário")
        lid, vinculado, obsoleta = _ligar_lancamento(con, h, conta_id, lancamento_id, existente,
                                                     salario, hoje)
        if existente is None:
            hid = repo.criar(
                con, conta_id=conta_id, competencia=h.competencia, tipo=h.tipo,
                empresa=h.empresa, total_vencimentos=h.total_vencimentos_centavos,
                total_descontos=h.total_descontos_centavos, liquido=h.liquido_centavos,
                arquivo_hash=arquivo_hash, arquivo_nome=arquivo_nome, lancamento_id=lid,
                criado_em=hoje.isoformat())
        else:
            hid = int(existente["id"])
            repo.atualizar_cabecalho(
                con, hid, conta_id=conta_id, empresa=h.empresa,
                total_vencimentos=h.total_vencimentos_centavos,
                total_descontos=h.total_descontos_centavos, liquido=h.liquido_centavos,
                arquivo_hash=arquivo_hash, arquivo_nome=arquivo_nome, lancamento_id=lid)
        if obsoleta is not None:
            repo_lanc.excluir(con, obsoleta)
        for ordem, (item, cat) in enumerate(zip(h.itens, categorias), 1):
            fila = guardadas.get(normalizar_descricao(item.descricao))
            antiga = fila.popleft() if fila else None
            repo.inserir_item(con, hid, ordem=ordem, codigo=item.codigo,
                              descricao=item.descricao, referencia=item.referencia,
                              natureza=item.natureza, valor_centavos=item.valor_centavos,
                              categoria_id=antiga if antiga is not None else cat)
        return ResumoHolerite(hid, lid, len(h.itens), vinculado)


def alterar_categoria_item(con: Any, item_id: int, categoria_id: int | None) -> None:
    if repo.obter_item(con, item_id) is None:
        raise ErroHolerite("Item do holerite não encontrado.")
    if categoria_id is not None and not repo_categorias.existe(con, categoria_id):
        raise ErroHolerite("Categoria não encontrada.")
    with _escrita(con):
        repo.atualizar_categoria_item(con, item_id, categoria_id)


def detalhe(con: Any, holerite_id: int) -> tuple[Any, list[Any]]:
    """Holerite e seus itens (na ordem do documento)."""
    hol = repo.obter(con, holerite_id)
    if hol is None:
        raise ErroHolerite("Holerite não encontrado.")
    return hol, repo.itens(con, holerite_id)


def liberar_vinculos_da_importacao(con: Any, importacao_id: int, hoje: date | None = None) -> int:
    """Antes de remover as linhas de uma importação: cada holerite ligado a uma delas passa a
    uma entrada prevista nova (o líquido segue na projeção), ou fica sem lançamento quando a
    data prevista já passou além da janela (`sem_prevista`). Devolve quantos foram liberados."""
    vinculados = repo.vinculados_a_linhas_da_importacao(con, importacao_id)
    if not vinculados:
        return 0
    with _escrita(con):
        for hol in vinculados:
            _liberar(con, hol, hoje or date.today())
    return len(vinculados)


def _liberar(con: Any, hol: Any, hoje: date) -> None:
    """Solta o holerite do lançamento atual: nova entrada prevista, ou nenhum lançamento
    quando `sem_prevista` (data prevista já passou além da janela ou líquido zero)."""
    tipo, comp = str(hol["tipo"]), str(hol["competencia"])
    data, liquido = data_prevista(con, tipo, comp), int(hol["liquido_centavos"])
    novo: int | None = None
    if not sem_prevista(data, liquido, hoje):
        novo = repo_lanc.inserir_holerite_previsto(
            con, conta_id=int(hol["conta_id"]), data=data,
            descricao=f"Holerite {ROTULO[tipo]} {comp}", valor_centavos=liquido,
            categoria_id=repo_categorias.id_por_nome(con, "Salário"))
    repo.definir_lancamento(con, int(hol["id"]), novo)


def liberar_vinculo_do_lancamento(con: Any, lancamento_id: int, hoje: date | None = None) -> bool:
    """Antes de apagar um lançamento (ex.: pendente pulado, recorrente desativada, manual
    excluído): o holerite ligado a ele é solto pela mesma regra do desfazer. Devolve se
    havia holerite ligado."""
    hol = repo.por_lancamento(con, lancamento_id)
    if hol is None:
        return False
    with _escrita(con):
        _liberar(con, hol, hoje or date.today())
    return True


@dataclass
class LinhaHolerite:
    id: int
    competencia: str
    tipo: str
    rotulo: str
    empresa: str | None
    bruto_centavos: int
    descontos_centavos: int
    liquido_centavos: int
    credito: Literal["recebido", "previsto", "sem"]
    credito_data: str | None


def anos_com_holerite(con: Any) -> list[int]:
    """Anos que têm holerite importado, do mais recente ao mais antigo."""
    return sorted({int(str(h["competencia"])[:4]) for h in repo.listar(con)}, reverse=True)


def listar_por_ano(con: Any, ano: int) -> list[LinhaHolerite]:
    """Holerites da competência-ano com o estado do crédito (recebido no extrato ou previsto)."""
    linhas: list[LinhaHolerite] = []
    for h in repo.listar(con, ano):
        lanc = None if h["lancamento_id"] is None else repo_lanc.obter(con, int(h["lancamento_id"]))
        estado: Literal["recebido", "previsto", "sem"] = "sem"
        if lanc is not None:
            estado = "recebido" if lanc["compensado"] == 1 else "previsto"
        linhas.append(LinhaHolerite(
            int(h["id"]), str(h["competencia"]), str(h["tipo"]), ROTULO.get(str(h["tipo"]), "—"),
            h["empresa"], int(h["total_vencimentos_centavos"]), int(h["total_descontos_centavos"]),
            int(h["liquido_centavos"]), estado, None if lanc is None else str(lanc["data"])))
    return linhas
