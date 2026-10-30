"""Ações da tela Contas a pagar: validam o texto bruto dos formulários e delegam ao domínio."""
import re
from collections.abc import Callable
from datetime import date
from typing import Any, TypeVar

from app.dominio.casamento import MSG_LINHA_NO_EXTRATO, confirmar_casamento, linha_do_extrato_para
from app.dominio.dinheiro import ValorMuitoAlto, para_centavos
from app.dominio.lancamentos import (
    MSG_DATA_FUTURA, ErroLancamento, NaoEncontrado, interpretar_data, interpretar_id,
)
from app.dominio.parcelas import (
    MSG_FATURA_PAGAR,
    MSG_TOTAL_MENOR_QUE_PARCELAS,
    TIPO_FATURA,
    criar_parcelamento,
    editar_grupo_parcelas,
    excluir_grupo_parcelas,
    marcar_pago,
)
from app.dominio.recorrentes import (
    confirmar_recorrente,
    criar_recorrente,
    desativar_recorrente,
    pular_mes,
)
from app.repo import categorias as repo_categorias
from app.repo import contas as repo_contas
from app.repo import lancamentos as repo_lanc
from app.repo import recorrentes as repo_rec

TIPOS_CONTA = {"corrente": "Conta corrente", "poupanca": "Poupança", "cartao": "Cartão",
               "carteira": "Carteira"}
MAX_PARCELAS = 120
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_FINAL = re.compile(r"[A-Za-z0-9-]{1,20}")
_T = TypeVar("_T")


def _traduzir(chamada: Callable[[], _T]) -> _T:
    """ValueError do domínio (já em português) -> ErroLancamento."""
    try:
        return chamada()
    except ErroLancamento:
        raise
    except ValueError as exc:
        raise ErroLancamento(str(exc)) from exc


def _valor_positivo(texto: str) -> int:
    try:
        v = para_centavos(texto)
    except ValorMuitoAlto as exc:
        raise ErroLancamento("Valor muito alto. O máximo é R$ 10.000.000.000,00.") from exc
    except ValueError as exc:
        raise ErroLancamento("Valor inválido. Use o formato 1.234,56.") from exc
    if v <= 0:
        raise ErroLancamento("O valor deve ser maior que zero.")
    return v


def _inteiro(texto: str, minimo: int, maximo: int, mensagem: str) -> int:
    t = texto.strip()
    if not (t.isascii() and t.isdigit() and len(t) <= 6) or not minimo <= int(t) <= maximo:
        raise ErroLancamento(mensagem)
    return int(t)


def _id_feminino(texto: str, rotulo: str) -> int:
    try:
        return interpretar_id(texto)
    except ErroLancamento as exc:
        raise ErroLancamento(f"{rotulo} inválida.") from exc


def _conta(con: Any, texto: str) -> int:
    cid = _id_feminino(texto, "Conta")
    if repo_contas.obter(con, cid) is None:
        raise ErroLancamento("Conta não encontrada.")
    return cid


def _categoria(con: Any, texto: str) -> int | None:
    if not texto.strip():
        return None
    cid = _id_feminino(texto, "Categoria")
    if not repo_categorias.existe(con, cid):
        raise ErroLancamento("Categoria não encontrada.")
    return cid


def _grupo(con: Any, texto: str) -> str:
    g = texto.strip()
    if not _UUID.fullmatch(g):
        raise ErroLancamento("Parcelamento inválido.")
    if not repo_lanc.parcelas_do_grupo(con, g):
        raise NaoEncontrado("Parcelamento não encontrado.")
    return g


def criar_parcelamento_de_formulario(
    con: Any, *, descricao: str, valor_total: str, n_parcelas: str, primeiro_vencimento: str,
    conta_id: str, categoria_id: str,
) -> int:
    descricao = descricao.strip()
    if not descricao:
        raise ErroLancamento("Informe a descrição.")
    if len(descricao) > 200:
        raise ErroLancamento("A descrição pode ter no máximo 200 caracteres.")
    total = _valor_positivo(valor_total)
    n = _inteiro(n_parcelas, 1, MAX_PARCELAS,
                 f"O número de parcelas deve estar entre 1 e {MAX_PARCELAS}.")
    if total < n:
        raise ErroLancamento(MSG_TOTAL_MENOR_QUE_PARCELAS)
    venc = date.fromisoformat(interpretar_data(primeiro_vencimento))
    if not 2000 <= venc.year <= 2100:
        raise ErroLancamento("Data inválida.")
    conta, categoria = _conta(con, conta_id), _categoria(con, categoria_id)
    ids = _traduzir(lambda: criar_parcelamento(
        con, descricao=descricao, valor_total_centavos=total, n_parcelas=n,
        primeiro_vencimento=venc, conta_id=conta, categoria_id=categoria))
    return len(ids)


def criar_recorrente_de_formulario(
    con: Any, *, nome: str, tipo: str, dia: str, valor_estimado: str, conta_id: str,
    categoria_id: str,
) -> str:
    nome = nome.strip()
    if not nome:
        raise ErroLancamento("Informe o nome.")
    if len(nome) > 80:
        raise ErroLancamento("O nome pode ter no máximo 80 caracteres.")
    if tipo not in ("entrada", "saida"):
        raise ErroLancamento("Tipo inválido.")
    d = _inteiro(dia, 1, 31, "O dia deve estar entre 1 e 31.")
    valor = _valor_positivo(valor_estimado)
    conta, categoria = _conta(con, conta_id), _categoria(con, categoria_id)
    _traduzir(lambda: criar_recorrente(
        con, nome=nome, tipo=tipo, dia=d, valor_estimado_centavos=valor,
        conta_id=conta, categoria_id=categoria))
    return "Receita recorrente cadastrada." if tipo == "entrada" else "Conta fixa cadastrada."


def pagar(con: Any, lancamento_id: str, valor: str, data: str, hoje: date) -> str:
    """Confirma o valor real (recorrente) ou marca como pago; recusa pendente já pago
    e data de pagamento futura."""
    lid = interpretar_id(lancamento_id)
    row = repo_lanc.obter(con, lid)
    if row is None:
        raise NaoEncontrado("Lançamento não encontrado.")
    if row["tipo"] == TIPO_FATURA:
        raise ErroLancamento(MSG_FATURA_PAGAR)
    if row["compensado"]:
        raise ErroLancamento("Este lançamento já está pago. Nada foi alterado.")
    dia = date.fromisoformat(interpretar_data(data))
    if dia > hoje:
        raise ErroLancamento(MSG_DATA_FUTURA)
    real = _valor_positivo(valor) if valor.strip() else abs(int(row["valor_centavos"]))
    if row["recorrente_id"] is not None:
        _traduzir(lambda: confirmar_recorrente(con, lid, real, dia))
        msg = "Valor real confirmado e pagamento registrado."
    else:
        _traduzir(lambda: marcar_pago(con, lid, real, dia))
        msg = "Pagamento registrado."
    return f"{msg} {MSG_LINHA_NO_EXTRATO}" if linha_do_extrato_para(con, lid) else msg


def confirmar_correspondencia(con: Any, pendente_id: str, lancamento_id: str) -> str:
    """Confirma uma "Possível correspondência no extrato" (mesma regra do casamento)."""
    pid, lid = interpretar_id(pendente_id), interpretar_id(lancamento_id)
    _traduzir(lambda: confirmar_casamento(con, pid, lid))
    return "Correspondência confirmada."


def pular(con: Any, lancamento_id: str, hoje: date | None = None) -> str:
    lid = interpretar_id(lancamento_id)
    if repo_lanc.obter(con, lid) is None:
        raise NaoEncontrado("Lançamento não encontrado.")
    _traduzir(lambda: pular_mes(con, lid, hoje))
    return "Mês pulado."


def desativar(con: Any, recorrente_id: str, hoje: date) -> str:
    rid = interpretar_id(recorrente_id)
    if repo_rec.obter(con, rid) is None:
        raise NaoEncontrado("Conta fixa não encontrada.")
    _traduzir(lambda: desativar_recorrente(con, rid, hoje))
    return "Conta fixa desativada."


def editar_grupo(con: Any, grupo: str, valor_total: str, n_parcelas: str) -> str:
    g = _grupo(con, grupo)
    total = _valor_positivo(valor_total) if valor_total.strip() else None
    n = (_inteiro(n_parcelas, 1, MAX_PARCELAS,
                  f"O número de parcelas deve estar entre 1 e {MAX_PARCELAS}.")
         if n_parcelas.strip() else None)
    if total is not None and n is not None and total < n:
        raise ErroLancamento(MSG_TOTAL_MENOR_QUE_PARCELAS)
    # Se só um dos dois foi informado, dividir_em_parcelas recusa total < n (mesma mensagem).
    _traduzir(lambda: editar_grupo_parcelas(con, g, valor_total_centavos=total, n_parcelas=n))
    return "Parcelamento atualizado."


def excluir_grupo(con: Any, grupo: str) -> str:
    g = _grupo(con, grupo)
    if not repo_lanc.parcelas_do_grupo(con, g, so_pendentes=True):
        raise ErroLancamento("O parcelamento não tem parcelas pendentes para excluir.")
    _traduzir(lambda: excluir_grupo_parcelas(con, g))
    return "Parcelas pendentes excluídas."


def criar_conta_de_formulario(
    con: Any, *, nome: str, tipo: str, instituicao: str, final_numero: str,
    saldo_inicial: str, data_saldo_inicial: str,
) -> str:
    nome = nome.strip()
    if not nome:
        raise ErroLancamento("Informe o nome da conta.")
    if len(nome) > 80:
        raise ErroLancamento("O nome pode ter no máximo 80 caracteres.")
    if tipo not in TIPOS_CONTA:
        raise ErroLancamento("Tipo de conta inválido.")
    instituicao = instituicao.strip()
    if len(instituicao) > 80:
        raise ErroLancamento("A instituição pode ter no máximo 80 caracteres.")
    final = final_numero.strip()
    if final and not _FINAL.fullmatch(final):
        raise ErroLancamento("Final inválido. Use só letras, números e hífen (até 20).")
    try:
        saldo = para_centavos(saldo_inicial)
    except ValorMuitoAlto as exc:
        raise ErroLancamento("Valor muito alto. O máximo é R$ 10.000.000.000,00.") from exc
    except ValueError as exc:
        raise ErroLancamento("Valor inválido. Use o formato 1.234,56.") from exc
    dia = interpretar_data(data_saldo_inicial)
    repo_contas.criar_conta(
        con, nome, tipo, instituicao=instituicao or None, final_numero=final or None,
        saldo_inicial_centavos=saldo, data_saldo_inicial=dia)
    return "Conta cadastrada."
