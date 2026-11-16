"""Layout "Demonstrativo de Pagamento de Salário" (colunas Ev | Descrição | Referência |
Proventos | Descontos). Competência e tipo vêm de "MM/AAAA Mensal" no topo; a natureza do
valor vem da posição horizontal frente ao cabeçalho."""

import re

from app.importacao.fatura.modelo import valor_br_para_centavos
from app.importacao.holerite.leitor import (
    _DINHEIRO, _MSG_LAYOUT, _MSG_TIPO, _PROIBIDOS, _TOL_LINHA, _centro, _linhas, _normalizar,
    _tem, _texto,
)
from app.importacao.holerite.modelo import HoleriteLido, ItemHolerite, LayoutNaoSuportado
from app.importacao.holerite.texto import Palavra

_X_CODIGO = 40.0
_COMPETENCIA = re.compile(r"^(\d{2})/(\d{4})$")
_TIPOS = {"mensal": "mensal", "adiantamento": "adiantamento"}
_TITULO = "demonstrativo"


def eh_demonstrativo(linhas: list[list[Palavra]]) -> bool:
    return any(_cabecalho(linha) for linha in linhas)


def _cabecalho(linha: list[Palavra]) -> bool:
    return _tem(linha, "Ev", "Descrição", "Referência", "Proventos", "Descontos")


def _tipo_e_competencia(linhas: list[list[Palavra]]) -> tuple[str, str]:
    topo = " ".join(_texto(linha) for linha in linhas)
    if _PROIBIDOS.search(_normalizar(topo)):
        raise LayoutNaoSuportado(_MSG_TIPO)
    for linha in linhas:
        for i, p in enumerate(linha):
            m = _COMPETENCIA.match(p.texto)
            if not m or not 1 <= int(m.group(1)) <= 12:
                continue
            tipo = _TIPOS.get(_normalizar(linha[i + 1].texto)) if i + 1 < len(linha) else None
            if tipo is None:
                raise LayoutNaoSuportado(_MSG_LAYOUT)
            return tipo, f"{m.group(2)}-{m.group(1)}"
    raise LayoutNaoSuportado(_MSG_LAYOUT)


def _empresa(linha: list[Palavra]) -> str | None:
    """1ª linha: 'CÓDIGO-EMPRESA ... Demonstrativo ...'; fica só o que vem antes do título."""
    antes = []
    for p in linha:
        if _normalizar(p.texto) == _TITULO:
            break
        antes.append(p.texto)
    nome = re.sub(r"^\d+\s*-\s*", "", " ".join(antes)).strip()
    return nome or None


def _itens(linha: list[Palavra], lim_ref: float, lim_desc: float, x_ref: float
           ) -> list[ItemHolerite]:
    valores = [p for p in linha if _centro(p) >= lim_ref and _DINHEIRO.match(p.texto)
               and valor_br_para_centavos(p.texto) > 0]
    cod = next((p for p in linha if p.x1 <= _X_CODIGO and p.texto.isdigit()), None)
    ref = " ".join(p.texto for p in linha if p.x0 >= x_ref and _centro(p) < lim_ref) or None
    desc = " ".join(p.texto for p in linha if p.x0 < x_ref and p is not cod)
    return [
        ItemHolerite(codigo=cod.texto if cod else None, descricao=desc, referencia=ref,
                     natureza="provento" if _centro(v) < lim_desc else "desconto",
                     valor_centavos=valor_br_para_centavos(v.texto))
        for v in valores
    ]


def interpretar_demonstrativo(palavras: list[Palavra]) -> HoleriteLido:
    linhas = _linhas([p for p in palavras if p.texto.strip()])
    idx_cab = next(i for i, linha in enumerate(linhas) if _cabecalho(linha))
    cab = {p.texto: p for p in linhas[idx_cab]}
    c_ref, c_venc, c_desc = (_centro(cab[n]) for n in ("Referência", "Proventos", "Descontos"))
    lim_ref, lim_desc = (c_ref + c_venc) / 2, (c_venc + c_desc) / 2
    x_ref = cab["Referência"].x0 - 10

    tipo, competencia = _tipo_e_competencia(linhas[:idx_cab])
    empresa = _empresa(linhas[0]) if idx_cab > 0 else None

    idx_total = next((i for i in range(idx_cab + 1, len(linhas))
                      if _tem(linhas[i], "Total") and not _tem(linhas[i], "Líquido")
                      and any(_DINHEIRO.match(p.texto) for p in linhas[i])), None)
    idx_liq = next((i for i in range(idx_cab + 1, len(linhas))
                    if _tem(linhas[i], "Total", "Líquido")), None)
    if idx_total is None or idx_liq is None:
        raise LayoutNaoSuportado(_MSG_LAYOUT)

    itens = [it for linha in linhas[idx_cab + 1:idx_total]
             for it in _itens(linha, lim_ref, lim_desc, x_ref)]
    din = [p for p in linhas[idx_total] if _DINHEIRO.match(p.texto)]
    t_venc = [p for p in din if lim_ref <= _centro(p) < lim_desc]
    t_desc = [p for p in din if _centro(p) >= lim_desc]
    liquido = [p for p in linhas[idx_liq] if _DINHEIRO.match(p.texto) and _centro(p) >= lim_ref]
    if not (t_venc and t_desc and liquido):
        raise LayoutNaoSuportado(_MSG_LAYOUT)

    lido = HoleriteLido(
        tipo=tipo, competencia=competencia, empresa=empresa,
        total_vencimentos_centavos=valor_br_para_centavos(t_venc[0].texto),
        total_descontos_centavos=valor_br_para_centavos(t_desc[0].texto),
        liquido_centavos=valor_br_para_centavos(liquido[0].texto), itens=itens,
    )
    if not itens:
        lido.avisos.append("Nenhum item de provento ou desconto foi encontrado.")
    if not lido.conferencia().confere:
        lido.avisos.append("A soma dos itens não confere com os totais impressos no holerite.")
    return lido
