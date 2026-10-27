"""Valida localmente as faturas reais em PDF e imprime só um resumo mascarado.

Uso (na raiz do repositório): python scripts/validar_faturas_locais.py "Faturas Testes"
Senha opcional apenas pela variável de ambiente FATURA_SENHA (nunca por argumento).
Nunca imprime valores, descrições, nomes, datas ou finais de cartão.
"""

import os
import sys
from collections import Counter
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.importacao.fatura import ler_fatura  # noqa: E402
from app.importacao.fatura.modelo import TIPOS_ITEM, FaturaLida  # noqa: E402


def resumo_mascarado(f: FaturaLida) -> str:
    """Resumo sem dados sensíveis: emissor, bandeira, produto, contagens e conferência."""
    tipos = Counter(i.tipo for i in f.itens)
    contagem = " ".join(f"{t}={tipos.get(t, 0)}" for t in TIPOS_ITEM)
    confere = "sim" if f.conferencia().confere else "nao"
    return (f"emissor={f.emissor} bandeira={f.bandeira or '-'} produto={f.produto or '-'} "
            f"{contagem} confere={confere} avisos={len(f.avisos)}")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print('uso: python scripts/validar_faturas_locais.py "<pasta>"')
        return 2
    pasta = Path(argv[0])
    if not pasta.is_dir():
        print("pasta não encontrada")
        return 2
    senha = os.environ.get("FATURA_SENHA") or None
    pdfs = sorted(p for p in pasta.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")
    for n, pdf in enumerate(pdfs, 1):
        try:
            linha = resumo_mascarado(ler_fatura(pdf.read_bytes(), senha))
        except Exception as e:  # só o tipo do erro: a mensagem pode conter dados
            linha = f"erro={type(e).__name__}"
        print(f"fatura#{n} {linha}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main(sys.argv[1:]))
