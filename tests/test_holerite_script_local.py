from pathlib import Path

import pytest

from app.importacao.holerite import ler_holerite
from app.importacao.holerite.modelo import HoleriteLido
from scripts.validar_holerites_locais import main, resumo_mascarado
from tests.holerite_util import (
    CNPJ, CPF, EMPRESA, NOME, palavras_sinteticas, pdf_de_palavras,
)


def test_script_nao_imprime_dados_pessoais_nem_descricoes(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "segredo-fulano.pdf").write_bytes(pdf_de_palavras(palavras_sinteticas("adiantamento")))
    (tmp_path / "invalido 123.PDF").write_bytes(b"nao e pdf")
    assert main([str(tmp_path)]) == 0
    saida = capsys.readouterr().out
    assert "confere=sim" in saida and "tipo=adiantamento" in saida
    assert "competencia=2026-10" in saida and "proventos=1 descontos=1" in saida
    assert "liquido=R$ 1.4*" in saida and "PdfInvalido" in saida
    for proibido in (EMPRESA, NOME, CPF, CNPJ, "ADIANTAMENTO TESTE", "FICTICIO", "segredo",
                     "123", "1.400,00", "1400", "1.500,00"):
        assert proibido not in saida


def test_resumo_mascara_liquido() -> None:
    h = ler_holerite(pdf_de_palavras(palavras_sinteticas("mensal")))
    assert "liquido=R$ 3.8**,**" in resumo_mascarado(h)
    assert "empresa_com_digitos=nao" in resumo_mascarado(h)


def test_resumo_sinaliza_empresa_com_digitos_sem_mostrar() -> None:
    h = HoleriteLido("mensal", "2026-09", "EMPRESA 99", 100, 0, 100)
    linha = resumo_mascarado(h)
    assert "empresa_com_digitos=sim" in linha and "EMPRESA" not in linha and "99" not in linha


def test_main_pasta_inexistente(tmp_path: Path) -> None:
    assert main([str(tmp_path / "nao_existe")]) == 2
