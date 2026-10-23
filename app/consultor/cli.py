"""CLI do consultor: um objeto JSON em stdout; erros como {"erro": "..."} e código 1."""
import argparse
import json
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any, NoReturn

from app import config
from app.consultor import consultas, operacoes
from app.db import MIGRACOES, conectar, migrar
from app.dominio.lancamentos import ErroLancamento, interpretar_data, interpretar_id
from app.importacao.servico import ArquivoJaImportado

MSG_SEM_BANCO = ("Banco de dados não encontrado ({caminho}). Crie a conta com o comando "
                 "criar-conta e importe o primeiro extrato com o comando importar.")
MSG_DESATUALIZADO = ("O banco de dados é de uma versão antiga. Abra o painel web uma vez (ou rode "
                     "um comando de escrita) para atualizá-lo.")
MSG_INESPERADO = "Não foi possível concluir a operação. Nada foi alterado."
TIPOS_CONTA = ("corrente", "poupanca", "cartao", "carteira")

Comando = Callable[[Any, date, argparse.Namespace], dict[str, Any]]


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise ErroLancamento(f"Argumentos inválidos: {message}. Use --help para ver as opções.")


class _Subs:
    """Cria subparsers já com as opções globais."""

    def __init__(self, acao: Any, comum: argparse.ArgumentParser) -> None:
        self._acao, self._comum = acao, comum

    def add_parser(self, nome: str, **kw: Any) -> argparse.ArgumentParser:
        parser: argparse.ArgumentParser = self._acao.add_parser(nome, parents=[self._comum], **kw)
        return parser


def _int(texto: str, rotulo: str) -> int:
    t = texto.strip()
    sinal = t.lstrip("-")
    if not (sinal.isascii() and sinal.isdigit() and len(sinal) <= 6):
        raise ErroLancamento(f"{rotulo} inválido.")
    return int(t)


def construir_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="python -m app.consultor", description="Consulta e operações do ConsultorFin.")
    p.add_argument("--db", default=None, help="Caminho do banco (padrão: data/consultorfin.db).")
    p.add_argument("--hoje", default=None, help=argparse.SUPPRESS)
    comum = _Parser(add_help=False)  # aceita --db/--hoje também depois do subcomando
    comum.add_argument("--db", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    comum.add_argument("--hoje", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = _Subs(p.add_subparsers(dest="comando", required=True), comum)
    sub.add_parser("saldos", help="Saldo atual de cada conta.")
    s = sub.add_parser("resumo", help="Resumo do mês (como a tela Início).")
    s.add_argument("--mes", default=None)
    s = sub.add_parser("vencimentos", help="Contas vencidas e a vencer.")
    s.add_argument("--dias", default=str(config.JANELA_VENCER_DIAS))
    s = sub.add_parser("projecao", help="Projeção de saldo (3, 6 ou 12 meses).")
    s.add_argument("--meses", default=str(config.MESES_PADRAO))
    s = sub.add_parser("gastos", help="Gastos por categoria no mês.")
    s.add_argument("--mes", default=None)
    s = sub.add_parser("lancamentos", help="Extrato filtrado.")
    for flag in ("--de", "--ate", "--conta", "--categoria", "--tipo", "--status", "--texto"):
        s.add_argument(flag, default=None)
    s.add_argument("--limite", default=str(consultas.LIMITE_PADRAO))
    sub.add_parser("contas", help="Contas cadastradas.")
    sub.add_parser("categorias", help="Categorias e subcategorias.")
    s = sub.add_parser("importar", help="Importa um extrato (simula sem --confirmar).")
    s.add_argument("--arquivo", required=True)
    s.add_argument("--conta", required=True)
    s.add_argument("--confirmar", action="store_true")
    s = sub.add_parser("casar", help="Confirma o casamento de um pendente com uma linha do extrato.")
    s.add_argument("--pendente", required=True)
    s.add_argument("--linha", required=True)
    s = sub.add_parser("categorizar", help="Define a categoria de um lançamento.")
    s.add_argument("--lancamento", required=True)
    s.add_argument("--categoria", required=True)
    s = sub.add_parser("regra", help="Cria regra 'descrição contém' e aplica às sem categoria.")
    s.add_argument("--padrao", required=True)
    s.add_argument("--categoria", required=True)
    s = sub.add_parser("pagar", help="Registra o pagamento de um lançamento pendente.")
    s.add_argument("--lancamento", required=True)
    s.add_argument("--valor", default=None)
    s.add_argument("--data", default=None)
    s = sub.add_parser("criar-conta", help="Cria uma conta (e o banco, se não existir).")
    s.add_argument("--nome", required=True)
    s.add_argument("--tipo", required=True)
    s.add_argument("--saldo-inicial", required=True)
    s.add_argument("--data-saldo", required=True)
    return p


def _lancamentos(con: Any, hoje: date, a: argparse.Namespace) -> dict[str, Any]:
    cat = (a.categoria or "").strip()
    return consultas.lancamentos(
        con, hoje, de=interpretar_data(a.de) if a.de else None,
        ate=interpretar_data(a.ate) if a.ate else None,
        conta_id=interpretar_id(a.conta, "Conta") if a.conta else None,
        categoria_id=None if not cat or cat == "sem" else interpretar_id(cat, "Categoria"),
        sem_categoria=cat == "sem", tipo=a.tipo, status=a.status or "todos", texto=a.texto or "",
        limite=_int(a.limite, "Limite"))


COMANDOS: dict[str, Comando] = {
    "saldos": lambda con, hoje, a: consultas.saldos(con, hoje),
    "resumo": lambda con, hoje, a: consultas.resumo(con, hoje, a.mes),
    "vencimentos": lambda con, hoje, a: consultas.vencimentos(con, hoje, _dias(a.dias)),
    "projecao": lambda con, hoje, a: consultas.projecao(con, hoje, _int(a.meses, "Horizonte")),
    "gastos": lambda con, hoje, a: consultas.gastos(con, hoje, a.mes),
    "lancamentos": _lancamentos,
    "contas": lambda con, hoje, a: consultas.contas(con, hoje),
    "categorias": lambda con, hoje, a: consultas.categorias(con, hoje),
}
ESCRITAS: dict[str, Comando] = {
    "importar": lambda con, hoje, a: operacoes.importar(
        con, hoje, a.arquivo, interpretar_id(a.conta, "Conta"), a.confirmar),
    "casar": lambda con, hoje, a: operacoes.casar(
        con, interpretar_id(a.pendente, "Pendente"), interpretar_id(a.linha, "Linha")),
    "categorizar": lambda con, hoje, a: operacoes.categorizar(
        con, interpretar_id(a.lancamento, "Lançamento"), interpretar_id(a.categoria, "Categoria")),
    "regra": lambda con, hoje, a: operacoes.regra(
        con, a.padrao, interpretar_id(a.categoria, "Categoria")),
    "pagar": lambda con, hoje, a: operacoes.pagar(
        con, hoje, interpretar_id(a.lancamento, "Lançamento"), a.valor, a.data),
    "criar-conta": lambda con, hoje, a: operacoes.criar_conta(
        con, a.nome, a.tipo, a.saldo_inicial, a.data_saldo),
}


def _dias(texto: str) -> int:
    dias = _int(texto, "Prazo")
    if not 0 <= dias <= 366:
        raise ErroLancamento("O prazo deve estar entre 0 e 366 dias.")
    return dias


def _abrir(caminho: Path, comando: str) -> Any:
    """Consultas e escritas exigem banco existente; só criar-conta o cria."""
    if comando != "criar-conta" and not caminho.is_file():
        raise ErroLancamento(MSG_SEM_BANCO.format(caminho=caminho))
    con = conectar(caminho)
    try:
        if comando in ESCRITAS:
            migrar(con)
        elif con.execute("PRAGMA user_version").fetchone()[0] < len(MIGRACOES):
            raise ErroLancamento(MSG_DESATUALIZADO)
    except BaseException:
        con.close()
        raise
    return con


def _executar(args: argparse.Namespace) -> dict[str, Any]:
    hoje = date.fromisoformat(interpretar_data(args.hoje)) if args.hoje else date.today()
    caminho = Path(args.db) if args.db else config.DB_PATH
    acao = COMANDOS.get(args.comando) or ESCRITAS[args.comando]
    con = _abrir(caminho, args.comando)
    try:
        return acao(con, hoje, args)
    finally:
        con.close()


def _emitir(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    try:
        resultado = _executar(construir_parser().parse_args(argv))
    except (ErroLancamento, ArquivoJaImportado, ValueError) as exc:
        _emitir({"erro": str(exc)})
        return 1
    except Exception as exc:  # noqa: BLE001 - nunca expor stack trace nem dados internos
        sys.stderr.write(f"event=consultor_falhou erro={type(exc).__name__}\n")
        _emitir({"erro": MSG_INESPERADO})
        return 1
    _emitir(resultado)
    return 0
