from pathlib import Path

import pytest

from app.importacao.fatura.modelo import FaturaLida, ItemFatura
from scripts.validar_faturas_locais import main, resumo_mascarado


def _fatura() -> FaturaLida:
    itens = [
        ItemFatura("2027-01-05", "LOJA SECRETA XYZ", -4321, "compra", 2, 5, "7788", "saude"),
        ItemFatura("2027-01-06", "ESTORNO SECRETO", 1234, "credito", None, None, "7788", None),
        ItemFatura("2027-01-07", "JUROS SECRETOS", -567, "encargo", None, None, "7788", None),
    ]
    return FaturaLida(
        emissor="itau", bandeira="Visa", produto="Platinum", final_cartao="7788",
        data_vencimento="2027-02-10", data_fechamento="2027-02-01",
        total_impresso_centavos=3654, ajustes_resumo_centavos=0,
        itens=itens, avisos=["Aviso com 99,99"],
    )


def test_resumo_mascarado_sem_dados_sensiveis() -> None:
    txt = resumo_mascarado(_fatura())
    for proibido in ("LOJA", "SECRET", "7788", "43,21", "4321", "36,54", "3654", "2027",
                     "12,34", "1234", "99,99", "saude"):
        assert proibido not in txt
    assert "itau" in txt and "Visa" in txt and "Platinum" in txt
    assert "compra=1" in txt and "credito=1" in txt and "encargo=1" in txt
    assert "confere=sim" in txt
    assert "avisos=1" in txt


def test_resumo_mascarado_diverge() -> None:
    f = _fatura()
    f.total_impresso_centavos += 1
    assert "confere=nao" in resumo_mascarado(f)


def test_main_sem_pdfs_sai_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "invalido 123.pdf").write_bytes(b"nao e pdf")
    assert main([str(tmp_path)]) == 0
    saida = capsys.readouterr().out
    assert "123" not in saida
    assert "PdfInvalido" in saida


def test_main_pasta_inexistente(tmp_path: Path) -> None:
    assert main([str(tmp_path / "nao_existe")]) == 2


def test_main_aceita_extensao_maiuscula(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "a.pdf").write_bytes(b"x")
    (tmp_path / "b.PDF").write_bytes(b"x")
    (tmp_path / "c.txt").write_bytes(b"x")
    assert main([str(tmp_path)]) == 0
    linhas = capsys.readouterr().out.splitlines()
    assert [ln.split()[0] for ln in linhas] == ["fatura#1", "fatura#2"]
