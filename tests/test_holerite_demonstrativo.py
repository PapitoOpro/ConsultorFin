"""Layout 'Demonstrativo de Pagamento de Salário' (Ev | Descrição | Referência | Proventos |
Descontos), com palavras sintéticas anonimizadas."""

import pytest

from app.importacao.holerite.leitor import interpretar
from app.importacao.holerite.modelo import LayoutNaoSuportado
from app.importacao.holerite.texto import Palavra


def _p(x0: float, topo: float, texto: str, larg: float = 0) -> Palavra:
    return Palavra(x0, x0 + (larg or 5.0 * len(texto)), topo, texto)


def _via(rotulo: str = "Mensal", comp: str = "10/2026", titulo: str = "") -> list[Palavra]:
    w = [_p(9, 15, "1002-ACME"), _p(60, 15, "INVENTADA"), _p(305, 15, "Demonstrativo"),
         _p(9, 27, "ACME"), _p(394, 27, comp), _p(454, 27, rotulo), _p(500, 15, titulo)]
    w += [_p(9, 87, "Ev"), _p(39, 87, "Descrição"), _p(354, 87, "Referência"),
          _p(448, 87, "Proventos"), _p(547, 87, "Descontos")]
    w += [_p(26, 99, "1", 5), _p(36, 99, "Salário"), _p(360, 99, "13"), _p(372, 99, "Dias"),
          _p(460, 99, "880,65", 27)]
    w += [_p(11, 111, "1950", 20), _p(36, 111, "Inss"), _p(362, 111, "7,50"),
          _p(567, 111, "66,04", 23)]
    w += [_p(9, 280, "Parabens"), _p(338, 280, "Total"), _p(453, 280, "880,65", 35),
          _p(561, 280, "66,04", 28)]
    w += [_p(338, 304, "Total"), _p(362, 304, "Líquido"), _p(560, 303, "814,61", 27)]
    return w


def test_demonstrativo_mensal() -> None:
    lido = interpretar(_via())
    assert (lido.tipo, lido.competencia, lido.empresa) == ("mensal", "2026-10", "ACME INVENTADA")
    assert (lido.total_vencimentos_centavos, lido.total_descontos_centavos,
            lido.liquido_centavos) == (88065, 6604, 81461)
    assert [(i.natureza, i.valor_centavos) for i in lido.itens] == [
        ("provento", 88065), ("desconto", 6604)]
    assert lido.avisos == []


def test_demonstrativo_tipo_desconhecido_recusado() -> None:
    with pytest.raises(LayoutNaoSuportado):
        interpretar(_via(rotulo="Rescisão"))
