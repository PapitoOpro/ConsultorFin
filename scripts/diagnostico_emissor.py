"""Diagnóstico de reconhecimento do emissor de uma fatura em PDF (roda no seu PC).

Uso (na raiz do projeto):
    .venv/Scripts/python scripts/diagnostico_emissor.py caminho/da/fatura.pdf

A senha é pedida no terminal (não aparece na tela, nunca é gravada nem enviada).
A saída mostra só se cada parser reconhece o PDF e quais textos de identificação existem
(nomes de empresas e bandeiras). Nenhum valor, nome de pessoa ou lançamento é impresso.
"""
import getpass
import re
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from app.importacao.fatura.deteccao import PARSERS  # noqa: E402
from app.importacao.fatura.modelo import PdfProtegido, SenhaIncorreta  # noqa: E402
from app.importacao.fatura.texto import abrir_pdf  # noqa: E402

MARCADORES = ("banco itau", "itau cartoes", "itaucard", "itau unibanco", "itau uni", "mastercard",
              "visa", "portoseg", "mercado pago")
_EMPRESA = re.compile(r"\b[A-ZÀ-Ú][\wÀ-Úà-ú.&\- ]{2,50}\b(?:S\.?A\.?|LTDA|CNPJ)\b", re.I)


def _sem_acento(texto: str) -> str:
    de, para = "áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ", "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC"
    return texto.translate(str.maketrans(de, para)).lower()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Uso: diagnostico_emissor.py caminho/da/fatura.pdf")
        return 2
    caminho = Path(argv[1])
    if not caminho.is_file():
        print("Arquivo não encontrado.")
        return 2
    conteudo = caminho.read_bytes()
    senha: str | None = None
    while True:
        try:
            paginas = abrir_pdf(conteudo, senha)
            break
        except PdfProtegido:
            senha = getpass.getpass("Senha do PDF (não aparece ao digitar): ")
        except SenhaIncorreta:
            senha = getpass.getpass("Senha incorreta. Tente de novo: ")
        except Exception as exc:  # noqa: BLE001 - só o tipo, sem conteúdo
            print(f"Não foi possível abrir o PDF ({type(exc).__name__}).")
            return 1
    texto = "\n".join(p.texto for p in paginas)
    plano = _sem_acento(texto)
    print(f"Páginas: {len(paginas)}")
    print("Parsers que reconhecem o PDF:")
    for parser in PARSERS:
        print(f"  {parser.emissor}: {'SIM' if parser.reconhece(paginas) else 'não'}")
    print("Textos de identificação presentes:")
    for m in MARCADORES:
        print(f"  {m!r}: {'sim' if m in plano else 'não'}")
    empresas = sorted({m.group(0).strip() for m in _EMPRESA.finditer(texto)})[:10]
    print("Nomes de empresa encontrados:", "; ".join(empresas) if empresas else "(nenhum)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
