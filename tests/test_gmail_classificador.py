import json
from pathlib import Path

from app.gmail.classificador import (
    REMETENTES_PADRAO,
    Candidata,
    carregar_remetentes,
    classificar,
)


def test_itau_assunto_com_final() -> None:
    c = classificar("Fatura <aviso@itau.exemplo.com>", "A fatura do seu cartão final 0000 fechou", "", REMETENTES_PADRAO)
    assert c == Candidata("Itaú", "0000", None)


def test_porto_trecho_com_vencimento() -> None:
    c = classificar("no-reply@portobank.exemplo.com", "Sua fatura", "vencimento em 05/11/2026", REMETENTES_PADRAO)
    assert c is not None and c.vencimento == "2026-11-05"


def test_remetente_desconhecido_none() -> None:
    assert classificar("x@outro.exemplo.com", "final 1234", "", REMETENTES_PADRAO) is None


def test_data_invalida_vira_none() -> None:
    c = classificar("a@nubank.exemplo.com", "Fatura", "vencimento em 31/02/2026", REMETENTES_PADRAO)
    assert c is not None and c.vencimento is None


def test_assunto_hostil_nao_quebra() -> None:
    hostil = "final " * 5000 + "venc" * 5000 + "\x00'; DROP TABLE x;--"
    c = classificar("a@mercadopago.exemplo.com", hostil, hostil, REMETENTES_PADRAO)
    assert c is not None and c.cartao_final is None


def test_json_remetentes_invalido_usa_padrao(tmp_path: Path) -> None:
    p = tmp_path / "r.json"
    p.write_text("{nao é json", encoding="utf-8")
    assert carregar_remetentes(p) == REMETENTES_PADRAO
    p.write_text(json.dumps([{"emissor": "X", "contem": ["xis"]}]), encoding="utf-8")
    assert carregar_remetentes(p)[0].emissor == "X"
    assert carregar_remetentes(tmp_path / "nao-existe.json") == REMETENTES_PADRAO


def test_display_name_falsificado_nao_classifica() -> None:
    assert classificar("Nubank <x@evil.example>", "final 1234", "", REMETENTES_PADRAO) is None
    c = classificar("Qualquer <x@nubank.exemplo.com>", "Fatura", "", REMETENTES_PADRAO)
    assert c is not None and c.emissor == "Nubank"


def test_contem_invalido_cai_no_padrao(tmp_path: Path) -> None:
    p = tmp_path / "r.json"
    for contem in ("itau", [""], [], [1], ["ok", "  "]):
        p.write_text(json.dumps([{"emissor": "X", "contem": contem}]), encoding="utf-8")
        assert carregar_remetentes(p) == REMETENTES_PADRAO, contem
