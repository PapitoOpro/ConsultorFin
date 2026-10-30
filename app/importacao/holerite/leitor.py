"""Interpretação do holerite a partir das palavras posicionadas do PDF.

Colunas: Código | Descrição | Referência | Vencimentos | Descontos. A natureza do valor vem
da posição horizontal (x) frente ao cabeçalho das colunas, nunca da ordem do texto.
"""

import re
import unicodedata

from app.importacao.fatura.modelo import valor_br_para_centavos
from app.importacao.holerite.modelo import (
    HoleriteIlegivel, HoleriteLido, ItemHolerite, LayoutNaoSuportado,
)
from app.importacao.holerite.texto import Palavra, abrir_pdf

_MESES = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
    "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}
_DINHEIRO = re.compile(r"^\d{1,3}(?:\.\d{3})*,\d{2}$|^\d+,\d{2}$")
_X_RUIDO = 520.0  # rótulos invertidos do rodapé ficam à direita da tabela
_X_CODIGO = 40.0  # a coluna Código termina antes disso
_TOL_LINHA = 4.0
_GAP_TITULO = 20.0  # na linha do "CC:", o título do holerite vem depois de um vão largo
_MSG_LAYOUT = "Layout de holerite não suportado (somente Adiantamento e Folha Mensal)."
_MSG_TIPO = ("Holerite de 13º salário, férias, rescisão, complementar ou PLR não é suportado "
             "(somente Adiantamento e Folha Mensal).")
_MSG_PAGINAS = ("O PDF tem mais de um holerite (várias páginas com a tabela de itens); envie "
                "um holerite por arquivo.")
_TIPOS = {"adiantamento": "adiantamento", "folha mensal": "mensal"}
_PROIBIDOS = re.compile(
    r"\b(?:13o|13a|decimo|ferias|rescisao|complementar|plr|participacao)\b|\b13\s+salario\b")
_CORTE_EMPRESA = re.compile(r"\b(?:CNPJ|CPF)\b.*$", re.IGNORECASE)
_COM_DIGITO = re.compile(r"\S*\d\S*")


def _sem_acento(txt: str) -> str:
    return unicodedata.normalize("NFD", txt).encode("ascii", "ignore").decode().lower()


def _normalizar(txt: str) -> str:
    """Sem acento, minúsculas; ordinais (º, ª, °) viram letra (13º -> 13o)."""
    txt = txt.replace("º", "o").replace("°", "o").replace("ª", "a")
    return unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode().lower()


def _centro(p: Palavra) -> float:
    return (p.x0 + p.x1) / 2


def _linhas(palavras: list[Palavra]) -> list[list[Palavra]]:
    linhas: list[list[Palavra]] = []
    inicio = 0.0
    for p in sorted(palavras, key=lambda w: (w.topo, w.x0)):
        if linhas and p.topo - inicio <= _TOL_LINHA:
            linhas[-1].append(p)
        else:
            linhas.append([p])
            inicio = p.topo
    return [sorted(linha, key=lambda w: w.x0) for linha in linhas]


def _texto(linha: list[Palavra]) -> str:
    return " ".join(p.texto for p in linha)


def _tem(linha: list[Palavra], *termos: str) -> bool:
    textos = {p.texto for p in linha}
    return all(t in textos for t in termos)


def _cabecalho(linha: list[Palavra]) -> bool:
    return _tem(linha, "Descrição", "Referência", "Vencimentos", "Descontos")


def _titulo(linhas: list[list[Palavra]]) -> str | None:
    """Título do holerite: o último bloco de palavras da linha do "CC:" (depois do centro de
    custo, separado por um vão largo)."""
    for linha in linhas:
        idx = next((i for i, p in enumerate(linha) if p.texto == "CC:"), None)
        if idx is None:
            continue
        blocos: list[list[Palavra]] = []
        anterior = linha[idx]
        for p in linha[idx + 1:]:
            if not blocos or p.x0 - anterior.x1 > _GAP_TITULO:
                blocos.append([])
            blocos[-1].append(p)
            anterior = p
        return _texto(blocos[-1]) if blocos else None
    return None


def _linha_do_mes(linha: list[Palavra]) -> bool:
    return any(_MESES.get(_sem_acento(m.group(1)))
               for m in re.finditer(r"([A-Za-zÀ-ÿ]+) de (\d{4})\b", _texto(linha)))


def _tipo_e_competencia(linhas: list[list[Palavra]]) -> tuple[str, str]:
    # 13º, férias etc. só contam na linha do título (CC:) e na do mês: empresa, endereço ou
    # cargo podem conter essas palavras sem mudar o tipo do holerite.
    titulo_e_mes = " ".join(_texto(linha) for linha in linhas
                            if any(p.texto == "CC:" for p in linha) or _linha_do_mes(linha))
    if _PROIBIDOS.search(_normalizar(titulo_e_mes)):
        raise LayoutNaoSuportado(_MSG_TIPO)
    texto = " ".join(_texto(linha) for linha in linhas)
    titulo = _titulo(linhas)
    tipo = _TIPOS.get(" ".join(_normalizar(titulo).split())) if titulo else None
    if tipo is None:
        raise LayoutNaoSuportado(_MSG_LAYOUT)
    for m in re.finditer(r"([A-Za-zÀ-ÿ]+) de (\d{4})\b", texto):
        mes = _MESES.get(_sem_acento(m.group(1)))
        if mes:
            return tipo, f"{int(m.group(2)):04d}-{mes:02d}"
    raise LayoutNaoSuportado(_MSG_LAYOUT)


def _empresa(linha: list[Palavra]) -> str | None:
    """Nome da empresa (1ª linha) sem CNPJ/CPF: corta a partir do rótulo e tira toda palavra
    com dígito; vazio -> None."""
    texto = _CORTE_EMPRESA.sub("", _texto(linha))
    limpo = " ".join(_COM_DIGITO.sub("", texto).split()).strip(" -/.,:")
    return limpo or None


def _item_da_linha(linha: list[Palavra], lim_ref: float, lim_desc: float,
                   x_ref: float) -> list[ItemHolerite]:
    valores = [p for p in linha if _centro(p) >= lim_ref and _DINHEIRO.match(p.texto)
               and valor_br_para_centavos(p.texto) > 0]
    if not valores:
        return []
    cod = next((p for p in linha if p.x1 <= _X_CODIGO and p.texto.isdigit()), None)
    descricao = " ".join(p.texto for p in linha if p.x0 < x_ref and p is not cod)
    referencia = " ".join(p.texto for p in linha
                          if p.x0 >= x_ref and _centro(p) < lim_ref) or None
    return [
        ItemHolerite(
            codigo=cod.texto if cod else None,
            descricao=descricao,
            referencia=referencia,
            natureza="provento" if _centro(v) < lim_desc else "desconto",
            valor_centavos=valor_br_para_centavos(v.texto),
        )
        for v in valores
    ]


def interpretar(palavras: list[Palavra]) -> HoleriteLido:
    """Interpreta as palavras de uma via do holerite. Função pura."""
    validas = [p for p in palavras if p.texto.strip() and p.x0 < _X_RUIDO]
    linhas = _linhas(validas)

    idx_cab = next((i for i, linha in enumerate(linhas) if _cabecalho(linha)), None)
    if idx_cab is None:
        raise LayoutNaoSuportado(_MSG_LAYOUT)
    cab = {p.texto: p for p in linhas[idx_cab]}
    c_ref, c_venc, c_desc = (_centro(cab[n]) for n in ("Referência", "Vencimentos", "Descontos"))
    lim_ref = (c_ref + c_venc) / 2  # centro < lim_ref: descrição/referência
    lim_desc = (c_venc + c_desc) / 2  # lim_ref..lim_desc: Vencimentos; acima: Descontos
    x_ref = cab["Referência"].x0 - 10

    tipo, competencia = _tipo_e_competencia(linhas[:idx_cab])
    empresa = _empresa(linhas[0]) if idx_cab > 0 else None

    idx_total = next((i for i in range(idx_cab + 1, len(linhas))
                      if _tem(linhas[i], "Total", "de", "Vencimentos")), None)
    liq = next((p for p in validas if p.texto == "Líquido"), None)
    if idx_total is None or liq is None:
        raise LayoutNaoSuportado(_MSG_LAYOUT)

    itens = [it for linha in linhas[idx_cab + 1:idx_total]
             for it in _item_da_linha(linha, lim_ref, lim_desc, x_ref)]

    topo_total = next(p.topo for p in linhas[idx_total] if p.texto == "Total")
    dinheiro = [p for p in validas if _DINHEIRO.match(p.texto)]
    totais = [p for p in dinheiro if topo_total + _TOL_LINHA <= p.topo < liq.topo - 6]
    t_venc = [p for p in totais if lim_ref <= _centro(p) < lim_desc]
    t_desc = [p for p in totais if _centro(p) >= lim_desc]
    liquido = [p for p in dinheiro if abs(p.topo - liq.topo) <= 6 and _centro(p) >= lim_desc]
    if not (t_venc and t_desc and liquido):
        raise LayoutNaoSuportado(_MSG_LAYOUT)

    lido = HoleriteLido(
        tipo=tipo, competencia=competencia, empresa=empresa,
        total_vencimentos_centavos=valor_br_para_centavos(t_venc[0].texto),
        total_descontos_centavos=valor_br_para_centavos(t_desc[0].texto),
        liquido_centavos=valor_br_para_centavos(liquido[0].texto),
        itens=itens,
    )
    if not itens:
        lido.avisos.append("Nenhum item de provento ou desconto foi encontrado.")
    if not lido.conferencia().confere:
        lido.avisos.append("A soma dos itens não confere com os totais impressos no holerite.")
    return lido


def ler_holerite(conteudo: bytes) -> HoleriteLido:
    """PDF em memória -> HoleriteLido. Falhas de leitura viram HoleriteIlegivel. Só a 1ª
    página é lida: outra página com a tabela de itens (vários holerites) é recusada."""
    paginas = abrir_pdf(conteudo)
    if any(_cabecalho(linha) for pagina in paginas[1:] for linha in _linhas(pagina)):
        raise LayoutNaoSuportado(_MSG_PAGINAS)
    try:
        return interpretar(paginas[0])
    except HoleriteIlegivel:
        raise
    except Exception:
        raise LayoutNaoSuportado(_MSG_LAYOUT) from None
