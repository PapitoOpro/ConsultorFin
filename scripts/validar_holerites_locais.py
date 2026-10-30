"""Valida localmente os holerites reais em PDF e imprime só um resumo mascarado.

Uso (na raiz do repositório): python scripts/validar_holerites_locais.py "<pasta>"
Nunca imprime descrições, empresa, nomes, nomes de arquivo nem valores completos.
"""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.importacao.holerite import ler_holerite  # noqa: E402
from app.importacao.holerite.modelo import HoleriteLido  # noqa: E402


def mascarar_valor(centavos: int) -> str:
    """R$ 1.234,56 -> 'R$ 1.2**,**'; abaixo de mil mantém só o primeiro dígito."""
    inteiro, cent = divmod(abs(centavos), 100)
    txt = f"{inteiro:,}".replace(",", ".") + f",{cent:02d}"
    visivel = 3 if inteiro >= 1000 else 1
    mascarado = txt[:visivel] + "".join("*" if c.isdigit() else c for c in txt[visivel:])
    return f"R$ {mascarado}"


def resumo_mascarado(h: HoleriteLido) -> str:
    """Resumo sem dados sensíveis: tipo, competência, contagens, conferência e líquido mascarado."""
    prov = sum(i.natureza == "provento" for i in h.itens)
    desc = sum(i.natureza == "desconto" for i in h.itens)
    confere = "sim" if h.conferencia().confere else "nao"
    digitos = "sim" if any(c.isdigit() for c in h.empresa or "") else "nao"
    return (f"tipo={h.tipo} competencia={h.competencia} proventos={prov} descontos={desc} "
            f"confere={confere} liquido={mascarar_valor(h.liquido_centavos)} "
            f"empresa_com_digitos={digitos}")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print('uso: python scripts/validar_holerites_locais.py "<pasta>"')
        return 2
    pasta = Path(argv[0])
    if not pasta.is_dir():
        print("pasta não encontrada")
        return 2
    pdfs = sorted(p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    for n, pdf in enumerate(pdfs, 1):
        try:
            linha = resumo_mascarado(ler_holerite(pdf.read_bytes()))
        except Exception as e:  # só o tipo do erro: a mensagem pode conter dados
            linha = f"erro={type(e).__name__}"
        print(f"holerite#{n} {linha}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))
