from app.dominio.normalizacao import normalizar_descricao


def test_barra_e_espaco_sao_iguais() -> None:
    assert normalizar_descricao("PIX TRANSF RR Supo22/10") == normalizar_descricao(
        "PIX TRANSF RR Supo22 10") == "pix transf rr supo22 10"


def test_acentos_caixa_pontuacao_e_espacos() -> None:
    assert normalizar_descricao("  Pagamento -- Açaí  &  CAFÉ!!  ") == "pagamento acai cafe"
    assert normalizar_descricao("A.B*C_D") == "a b c d"


def test_descricoes_diferentes_continuam_diferentes() -> None:
    assert normalizar_descricao("PIX JOAO") != normalizar_descricao("PIX JOSE")
    assert normalizar_descricao("ab") != normalizar_descricao("a b")


def test_vazio_e_so_pontuacao() -> None:
    assert normalizar_descricao("") == ""
    assert normalizar_descricao("  ***  ") == ""


def test_unicode() -> None:
    assert normalizar_descricao("ＡＢＣ １２３") == "abc 123"  # largura total (NFKD)
    assert normalizar_descricao("Ñandú") == "nandu"
    assert normalizar_descricao("日本 語") == "日本 語"
