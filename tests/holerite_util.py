"""Auxiliar de testes: palavras sintéticas posicionadas como as colunas do holerite real."""

from app.importacao.holerite.texto import Palavra

X1_REF, X1_VENC, X1_DESC = 347.0, 427.0, 507.0
_LARG = 5.5

# (codigo, descricao, referencia, natureza, valor)
Linha = tuple[str, str, str, str, str]

PADRAO: dict[str, dict] = {
    "adiantamento": {
        "rotulo": "Adiantamento", "mes": "Outubro", "ano": "2026",
        "itens": [("101", "ADIANTAMENTO TESTE", "15,00", "provento", "1.500,00"),
                  ("202", "DESCONTO FICTICIO UM", "7,50", "desconto", "100,00")],
        "total_venc": "1.500,00", "total_desc": "100,00", "liquido": "1.400,00",
    },
    "mensal": {
        "rotulo": "Folha Mensal", "mes": "Setembro", "ano": "2026",
        "itens": [("111", "SALARIO FICTICIO", "22,00", "provento", "3.000,00"),
                  ("112", "AJUDA INVENTADA", "4,00", "provento", "800,00"),
                  ("113", "ADICIONAL ALFA", "0,00", "provento", "55,00"),
                  ("114", "ESTORNO BETA", "250,00", "provento", "250,00"),
                  ("115", "HORAS GAMA 60%", "2:30", "provento", "115,00"),
                  ("116", "BONUS DELTA", "780,00", "provento", "780,00"),
                  ("211", "IMPOSTO TESTE", "7,50", "desconto", "300,00"),
                  ("212", "ADIANT. FICTICIO", "400,00", "desconto", "400,00"),
                  ("213", "PENSAO INVENTADA", "20,00", "desconto", "350,00"),
                  ("214", "EMPRESTIMO ZETA", "60,00", "desconto", "60,00"),
                  ("215", "EMPRESTIMO ETA", "90,00", "desconto", "90,00")],
        "total_venc": "5.000,00", "total_desc": "1.200,00", "liquido": "3.800,00",
    },
}

EMPRESA = "ACME INVENTADA LTDA"
NOME = "FULANO DE TESTE"
CPF = "123.456.789-09"
CNPJ = "00.000.000/0001-00"
CARGO = "COORDENADOR"


def _p(x0: float, topo: float, texto: str, x1: float | None = None) -> Palavra:
    return Palavra(x0, x1 if x1 is not None else x0 + _LARG * len(texto), topo, texto)


def _frases(x0: float, topo: float, frase: str) -> list[Palavra]:
    palavras, x = [], x0
    for w in frase.split():
        palavras.append(_p(x, topo, w))
        x += _LARG * len(w) + 5
    return palavras


def _via(topo0: float, d: dict, itens: list[Linha], dados: dict) -> list[Palavra]:
    t = topo0
    out = _frases(7, t + 4, dados["empresa"])
    out += _frases(7, t + 14, f"CNPJ: {CNPJ}") + _frases(250, t + 14, "CC: SETOR")
    out += _frases(443, t + 14, d["rotulo"])
    out += _frases(422, t + 26, f"{d['mes']} de {d['ano']}")
    out += _frases(19, t + 40, "Código Nome do Funcionário")
    out += _frases(23, t + 48, f"9 {NOME} CPF {CPF} {CARGO}")
    cab = t + 78
    out += [_p(6, cab, "Código", 29), _p(135, cab, "Descrição", 167),
            _p(291, cab, "Referência", 326), _p(370, cab, "Vencimentos", 411),
            _p(454, cab, "Descontos", 488)]
    topo = cab + 10
    for cod, desc, ref, nat, valor in itens:
        out.append(_p(10, topo, cod, 31))
        out += _frases(38, topo, desc)
        out.append(_p(X1_REF - _LARG * len(ref), topo, ref, X1_REF))
        x1 = X1_VENC if nat == "provento" else X1_DESC
        out.append(_p(x1 - _LARG * len(valor), topo, valor, x1))
        topo += 12
    out += [_p(525, t + 88, "etsen"), _p(552, t + 108, "oiránoicnuF")]  # ruído invertido
    tt = t + 280
    out += [_p(370, tt, "Total", 380), _p(381, tt, "de", 386), _p(387, tt, "Vencimentos", 413),
            _p(452, tt, "Total", 462), _p(463, tt, "de", 468), _p(470, tt, "Descontos", 491)]
    out += [_p(X1_VENC - 44, tt + 14, dados["total_venc"], X1_VENC - 1),
            _p(X1_DESC - 27, tt + 14, dados["total_desc"], X1_DESC)]
    out += [_p(354, tt + 36, "Valor", 365), _p(366, tt + 36, "Líquido", 381),
            _p(X1_DESC - 43, tt + 36, dados["liquido"], X1_DESC)]
    out += [_p(34, tt + 52, "Salário"), _p(50, tt + 52, "Base"),
            _p(26, tt + 60, "5.000,00", 69), _p(384, tt + 60, dados["total_venc"], 428)]
    return out


def palavras_sinteticas(tipo: str, *, itens: list[Linha] | None = None,
                        total_venc: str | None = None, total_desc: str | None = None,
                        liquido: str | None = None, rotulo: str | None = None,
                        duplicada: bool = True, empresa: str = EMPRESA) -> list[Palavra]:
    """Palavras de uma página de holerite sintético (via duplicada por padrão)."""
    d = PADRAO[tipo]
    dados = {
        "empresa": empresa,
        "total_venc": total_venc or d["total_venc"],
        "total_desc": total_desc or d["total_desc"],
        "liquido": liquido or d["liquido"],
    }
    dd = {**d, "rotulo": rotulo or d["rotulo"]}
    lista = itens if itens is not None else d["itens"]
    palavras = _via(0, dd, lista, dados)
    if duplicada:
        palavras += _via(412, dd, lista, dados)
    return palavras


def pdf_minimo(texto: str | None) -> bytes:
    """PDF mínimo válido; sem texto quando texto é None."""
    conteudo = f"BT /F1 12 Tf 50 700 Td ({texto}) Tj ET" if texto else ""
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(conteudo)} >>\nstream\n{conteudo}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offs:
        out += f"{o:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def _escala(p: Palavra) -> float:
    """Escala horizontal (Tz) para a palavra caber na largura sintética (evita sobreposição)."""
    return 100 * max(1.0, p.x1 - p.x0 - 3.5) / (3.0 * len(p.texto))


def pdf_de_palavras(palavras: list[Palavra], altura: int = 900) -> bytes:
    """PDF com texto real posicionado a partir das palavras sintéticas (Helvetica, latin-1)."""
    ops = "".join(
        f"BT /F1 6 Tf {_escala(p):.0f} Tz 1 0 0 1 {p.x0:.2f} {altura - p.topo - 8:.2f} Tm "
        f"({p.texto.replace(chr(92), chr(92) * 2).replace('(', chr(92) + '(').replace(')', chr(92) + ')')}) Tj ET\n"
        for p in palavras
    ).encode("latin-1")
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 {altura}] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>".encode(),
        b"<< /Length " + str(len(ops)).encode() + b" >>\nstream\n" + ops + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = b"%PDF-1.4\n"
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for o in offs:
        out += f"{o:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out
