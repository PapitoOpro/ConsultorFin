"""Regras de lançamentos: chave de deduplicação, tipo inicial e a tela Lançamentos."""
import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.db import transacao
from app.dominio.categorias import aplicar_regras, criar_regra
from app.dominio.dinheiro import data_br_para_iso, para_centavos
from app.dominio.normalizacao import normalizar_descricao
from app.dominio.parcelas import marcar_pago
from app.dominio.recorrentes import confirmar_recorrente
from app.repo import categorias as repo_categorias
from app.repo import contas as repo_contas
from app.repo import lancamentos as repo


def chave_deduplicacao(conta_uuid: str, data: str, descricao: str, valor_centavos: int,
                       ocorrencia: int) -> str:
    """Chave v2: independe do formato (a descrição é normalizada; FITID não entra)."""
    base = "|".join((conta_uuid, data, normalizar_descricao(descricao), str(valor_centavos),
                     str(ocorrencia)))
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def chave_deduplicacao_legada(conta_uuid: str, data: str, descricao: str, valor_centavos: int,
                              ocorrencia: int) -> str:
    """Chave anterior à v7 (descrição literal); só para reconhecer linhas já importadas."""
    base = "|".join((conta_uuid, data, descricao, str(valor_centavos), str(ocorrencia)))
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def tipo_inicial(valor_centavos: int) -> str:
    return "entrada" if valor_centavos > 0 else "saida"


def criar_lancamento_manual(
    con: Any, *, conta_id: int, data: date, descricao: str, valor_centavos: int,
    categoria_id: int | None = None, compensado: bool = False,
    observacao: str | None = None,
) -> int:
    """Cria lançamento manual (entrada se valor > 0, saída se < 0). Se compensado, usa `data`."""
    if not descricao.strip():
        raise ValueError("A descrição não pode ser vazia.")
    if valor_centavos == 0:
        raise ValueError("O valor não pode ser zero.")
    with transacao(con):
        return repo.inserir_manual(
            con, conta_id=conta_id, data=data.isoformat(), descricao=descricao.strip(),
            valor_centavos=valor_centavos, tipo=tipo_inicial(valor_centavos),
            categoria_id=categoria_id, compensado=compensado, observacao=observacao)


# --- Tela Lançamentos ------------------------------------------------------------------------

POR_PAGINA = 50
MAX_LOTE = 500
TIPOS = ("entrada", "saida", "transferencia", "pagamento_fatura")
STATUS: dict[str, bool | None] = {"todos": None, "pendentes": False, "compensados": True}
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
MSG_DATA_FUTURA = "A data do pagamento não pode ser no futuro."


class ErroLancamento(ValueError):
    """Erro de regra/validação com mensagem em português para o usuário."""


class NaoEncontrado(ErroLancamento):
    """Registro inexistente (a rota responde 404)."""


_ROTULOS_FEMININOS = frozenset({"Conta", "Categoria", "Página", "Linha"})


def interpretar_id(texto: str | None, rotulo: str = "Identificador") -> int:
    """Id vindo de formulário/URL: somente dígitos ASCII."""
    t = (texto or "").strip()
    if not (t.isascii() and t.isdigit() and len(t) <= 18):
        feminino = rotulo.split(" ", 1)[0] in _ROTULOS_FEMININOS
        raise ErroLancamento(f"{rotulo} {'inválida' if feminino else 'inválido'}.")
    return int(t)


def interpretar_data(texto: str | None) -> str:
    """ISO (AAAA-MM-DD) ou DD/MM/AAAA -> ISO; senão ErroLancamento."""
    t = (texto or "").strip()
    try:
        if _ISO.fullmatch(t):
            return date.fromisoformat(t).isoformat()
        return data_br_para_iso(t)
    except ValueError as exc:
        raise ErroLancamento("Data inválida.") from exc


def _ultimo_dia_do_mes(hoje: date) -> date:
    primeiro_prox = date(hoje.year + (hoje.month == 12), hoje.month % 12 + 1, 1)
    return date.fromordinal(primeiro_prox.toordinal() - 1)


@dataclass(frozen=True)
class Filtros:
    de: str | None = None
    ate: str | None = None
    conta_id: int | None = None
    categoria_id: int | None = None
    sem_categoria: bool = False
    tipo: str | None = None
    texto: str = ""
    status: str = "todos"
    pagina: int = 1
    recentes_primeiro: bool = False  # ordem do extrato (a tela usa a ordem crescente)

    @classmethod
    def de_parametros(cls, params: Mapping[str, str], hoje: date) -> "Filtros":
        """Interpreta a query string. `de`/`ate` ausentes = mês de `hoje`; vazios = sem limite."""
        if "de" in params:
            de = interpretar_data(params["de"]) if params["de"].strip() else None
        else:
            de = hoje.replace(day=1).isoformat()
        if "ate" in params:
            ate = interpretar_data(params["ate"]) if params["ate"].strip() else None
        else:
            ate = _ultimo_dia_do_mes(hoje).isoformat()
        conta = params.get("conta_id", "").strip()
        cat = params.get("categoria_id", "").strip()
        tipo = params.get("tipo", "").strip()
        status = params.get("status", "").strip() or "todos"
        if tipo and tipo not in TIPOS:
            raise ErroLancamento("Tipo inválido.")
        if status not in STATUS:
            raise ErroLancamento("Status inválido.")
        pagina_txt = params.get("pagina", "").strip()
        pagina = interpretar_id(pagina_txt, "Página") if pagina_txt else 1
        if pagina < 1:
            raise ErroLancamento("Página inválida.")
        return cls(
            de=de, ate=ate,
            conta_id=interpretar_id(conta, "Conta") if conta else None,
            categoria_id=None if not cat or cat == "sem" else interpretar_id(cat, "Categoria"),
            sem_categoria=cat == "sem", tipo=tipo or None,
            texto=params.get("texto", "").strip()[:100], status=status, pagina=pagina)

    def como_parametros(self, pagina: int | None = None) -> dict[str, str]:
        """Query string equivalente (de/ate sempre explícitos)."""
        return {
            "de": self.de or "", "ate": self.ate or "",
            "conta_id": "" if self.conta_id is None else str(self.conta_id),
            "categoria_id": "sem" if self.sem_categoria else (
                "" if self.categoria_id is None else str(self.categoria_id)),
            "tipo": self.tipo or "", "texto": self.texto, "status": self.status,
            "pagina": str(pagina or self.pagina)}


@dataclass(frozen=True)
class LinhaExtrato:
    id: int
    data: str
    descricao: str
    conta_nome: str
    categoria_id: int | None
    categoria_nome: str | None
    valor_centavos: int
    saldo_acumulado_centavos: int | None
    tipo: str
    origem: str
    compensado: bool
    estimado: bool
    observacao: str | None


@dataclass(frozen=True)
class PaginaLancamentos:
    linhas: list[LinhaExtrato]
    total: int
    pagina: int
    paginas: int
    entradas_centavos: int
    saidas_centavos: int  # magnitude positiva
    liquido_centavos: int
    pendentes_centavos: int  # soma com sinal dos pendentes no filtro
    conta_cartao: bool = False  # filtrado por uma conta de cartão (fora dos totais do painel)


def _criterios(f: Filtros) -> dict[str, Any]:
    return {"de": f.de, "ate": f.ate, "conta_id": f.conta_id, "categoria_id": f.categoria_id,
            "sem_categoria": f.sem_categoria, "tipo": f.tipo, "texto": f.texto or None,
            "compensado": STATUS[f.status]}


def listar(con: Any, filtros: Filtros) -> PaginaLancamentos:
    """Extrato filtrado, ordenado por data. Totais: só compensados (entradas = tipo entrada;
    saídas = saida + pagamento_fatura; transferência não conta); pendentes à parte.
    Saldo acumulado = saldo inicial da conta + compensados até a linha (independe do filtro).
    Como no painel, contas de cartão ficam fora dos totais, salvo ao filtrar por uma delas."""
    criterios = _criterios(filtros)
    t = repo.totais_extrato(con, **criterios, excluir_cartao=filtros.conta_id is None)
    conta = None if filtros.conta_id is None else repo_contas.obter(con, filtros.conta_id)
    conta_cartao = conta is not None and conta["tipo"] == "cartao"
    total = int(t["total"])
    paginas = max(1, math.ceil(total / POR_PAGINA))
    pagina = min(filtros.pagina, paginas)
    rows = repo.listar_extrato(con, **criterios, limite=POR_PAGINA,
                               deslocamento=(pagina - 1) * POR_PAGINA,
                               decrescente=filtros.recentes_primeiro)
    linhas = [LinhaExtrato(
        id=int(r["id"]), data=r["data"], descricao=r["descricao"], conta_nome=r["conta_nome"],
        categoria_id=r["categoria_id"], categoria_nome=r["categoria_nome"],
        valor_centavos=int(r["valor_centavos"]),
        saldo_acumulado_centavos=None if r["saldo_acumulado"] is None else int(r["saldo_acumulado"]),
        tipo=r["tipo"], origem=r["origem"], compensado=bool(r["compensado"]),
        estimado=bool(r["estimado"]), observacao=r["observacao"]) for r in rows]
    entradas, saidas = int(t["entradas"]), int(t["saidas"])
    return PaginaLancamentos(
        linhas=linhas, total=total, pagina=pagina, paginas=paginas, entradas_centavos=entradas,
        saidas_centavos=-saidas, liquido_centavos=entradas + saidas,
        pendentes_centavos=int(t["pendentes"]), conta_cartao=conta_cartao)


def opcoes_categorias(con: Any, tipo: str | None = None) -> list[tuple[int, str, str]]:
    """(id, rótulo, tipo): 'Categoria' ou 'Categoria › Subcategoria', por tipo, categoria, sub."""
    linhas = repo_categorias.todas(con)
    filhas: dict[int, list[Any]] = {}
    for r in linhas:
        if r["pai_id"] is not None:
            filhas.setdefault(int(r["pai_id"]), []).append(r)
    saida: list[tuple[int, str, str]] = []
    for pai in linhas:
        if pai["pai_id"] is not None or (tipo is not None and pai["tipo"] != tipo):
            continue
        saida.append((int(pai["id"]), str(pai["nome"]), str(pai["tipo"])))
        for f in filhas.get(int(pai["id"]), []):
            saida.append((int(f["id"]), f"{pai['nome']} › {f['nome']}", str(pai["tipo"])))
    return sorted(saida, key=lambda o: o[2])  # estável: tipo, depois ordem de categoria/sub


def _exigir_categoria(con: Any, categoria_id: int | None) -> None:
    if categoria_id is not None and not repo_categorias.existe(con, categoria_id):
        raise ErroLancamento("Categoria não encontrada.")


def _exigir_lancamento(con: Any, lancamento_id: int) -> Any:
    row = repo.obter(con, lancamento_id)
    if row is None:
        raise NaoEncontrado("Lançamento não encontrado.")
    return row


def alterar_categoria(con: Any, lancamento_id: int, categoria_id: int | None) -> None:
    _exigir_lancamento(con, lancamento_id)
    _exigir_categoria(con, categoria_id)
    with transacao(con):
        repo.atualizar_categoria(con, [lancamento_id], categoria_id)


def categorizar_em_lote(con: Any, ids: Sequence[int], categoria_id: int) -> int:
    unicos = sorted(set(ids))
    if not unicos:
        raise ErroLancamento("Selecione ao menos um lançamento.")
    if len(unicos) > MAX_LOTE:
        raise ErroLancamento(f"Selecione no máximo {MAX_LOTE} lançamentos por vez.")
    _exigir_categoria(con, categoria_id)
    if repo.contar_existentes(con, unicos) != len(unicos):
        raise NaoEncontrado("Algum lançamento selecionado não foi encontrado.")
    with transacao(con):
        return repo.atualizar_categoria(con, unicos, categoria_id)


def alterar_tipo(con: Any, lancamento_id: int, tipo: str) -> None:
    """tipo: 'transferencia', 'pagamento_fatura' ou 'auto' (volta a entrada/saída pelo sinal)."""
    row = _exigir_lancamento(con, lancamento_id)
    if tipo == "auto":
        novo = tipo_inicial(int(row["valor_centavos"]))
    elif tipo in ("transferencia", "pagamento_fatura"):
        novo = tipo
    else:
        raise ErroLancamento("Tipo inválido.")
    with transacao(con):
        repo.atualizar_tipo(con, lancamento_id, novo)


def excluir_manual(con: Any, lancamento_id: int) -> None:
    row = _exigir_lancamento(con, lancamento_id)
    if row["origem"] != "manual" or row["importacao_id"] is not None:
        raise ErroLancamento("Lançamentos importados não podem ser excluídos aqui. "
                             "Para removê-los, desfaça a importação.")
    if row["recorrente_id"] is not None:
        raise ErroLancamento("Este lançamento é de uma conta fixa ou receita recorrente e seria "
                             "recriado. Para não pagá-lo neste mês, use \"Pular este mês\" em "
                             "Contas a pagar.")
    with transacao(con):
        repo.excluir(con, lancamento_id)


def pagar_pendente(con: Any, lancamento_id: int, valor_real_centavos: int | None,
                   data_pagamento: date, *, hoje: date) -> None:
    row = _exigir_lancamento(con, lancamento_id)
    if row["compensado"]:
        raise ErroLancamento("Este lançamento já está compensado; não é possível pagar de novo.")
    if data_pagamento > hoje:
        raise ErroLancamento(MSG_DATA_FUTURA)
    try:
        if row["recorrente_id"] is not None:
            real = (abs(int(row["valor_centavos"])) if valor_real_centavos is None
                    else valor_real_centavos)
            confirmar_recorrente(con, lancamento_id, real, data_pagamento)
        else:
            marcar_pago(con, lancamento_id, valor_real_centavos, data_pagamento)
    except ValueError as exc:
        raise ErroLancamento(str(exc)) from exc


def criar_regra_e_aplicar(con: Any, padrao: str, categoria_id: int) -> int:
    """Cria a regra 'descrição contém padrão' e aplica às linhas sem categoria; devolve quantas."""
    if not padrao.strip():
        raise ErroLancamento("O padrão da regra não pode ser vazio.")
    _exigir_categoria(con, categoria_id)
    criar_regra(con, padrao, categoria_id)
    return aplicar_regras(con)


def criar_manual_de_formulario(
    con: Any, *, data: str, descricao: str, valor: str, sinal: str, conta_id: str,
    categoria_id: str, observacao: str, compensado: bool,
) -> int:
    """Valida os campos do formulário (texto bruto) e cria o lançamento manual."""
    dia = date.fromisoformat(interpretar_data(data))
    descricao = descricao.strip()
    if not descricao:
        raise ErroLancamento("Informe a descrição.")
    if len(descricao) > 200:
        raise ErroLancamento("A descrição pode ter no máximo 200 caracteres.")
    try:
        magnitude = para_centavos(valor)
    except ValueError as exc:
        raise ErroLancamento("Valor inválido. Use o formato 1.234,56.") from exc
    if magnitude <= 0:
        raise ErroLancamento("O valor deve ser maior que zero; escolha Entrada ou Saída.")
    if sinal not in ("entrada", "saida"):
        raise ErroLancamento("Escolha Entrada ou Saída.")
    conta = interpretar_id(conta_id, "Conta")
    if repo_contas.obter(con, conta) is None:
        raise ErroLancamento("Conta não encontrada.")
    cat = interpretar_id(categoria_id, "Categoria") if categoria_id.strip() else None
    _exigir_categoria(con, cat)
    return criar_lancamento_manual(
        con, conta_id=conta, data=dia, descricao=descricao,
        valor_centavos=magnitude if sinal == "entrada" else -magnitude, categoria_id=cat,
        compensado=compensado, observacao=observacao.strip()[:500] or None)
