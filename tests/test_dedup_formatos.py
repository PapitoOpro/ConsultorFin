from datetime import date
from pathlib import Path

from app.db import MIGRACOES
from app.dominio.lancamentos import chave_deduplicacao_legada
from app.importacao.itau_csv import ParserItauCsv
from app.importacao.ofx import ParserOfx
from app.importacao.servico import importar, simular_importacao
from app.repo.contas import criar_conta, uuid_da_conta

FIXTURE = Path(__file__).parent / "fixtures" / "extrato_itau_exemplo.csv"
HOJE = date(2026, 10, 23)


def _linhas():
    return ParserItauCsv().ler(FIXTURE.read_bytes()).linhas


def _valor_ofx(centavos: int) -> str:
    sinal = "-" if centavos < 0 else ""
    inteiro, resto = divmod(abs(centavos), 100)
    return f"{sinal}{inteiro}.{resto:02d}"


def _ofx(linhas) -> bytes:
    """OFX 1.02 SGML com os mesmos eventos do CSV ('/' vira espaço na descrição)."""
    partes = ["OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\nSECURITY:NONE\nENCODING:USASCII\n"
              "CHARSET:1252\nCOMPRESSION:NONE\nOLDFILEUID:NONE\nNEWFILEUID:NONE\n\n"
              "<OFX>\n<BANKMSGSRSV1>\n<STMTTRNRS>\n<STMTRS>\n<CURDEF>BRL\n<BANKACCTFROM>\n"
              "<BANKID>0341\n<ACCTID>0001000000000\n<ACCTTYPE>CHECKING\n</BANKACCTFROM>\n"
              "<BANKTRANLIST>\n"]
    datas = sorted(l.data for l in linhas)
    partes.append(f"<DTSTART>{datas[0].replace('-', '')}100000[-03:EST]\n"
                  f"<DTEND>{datas[-1].replace('-', '')}100000[-03:EST]\n")
    for i, l in enumerate(linhas, 1):
        partes.append(
            f"<STMTTRN>\n<TRNTYPE>{'CREDIT' if l.valor_centavos > 0 else 'DEBIT'}\n"
            f"<DTPOSTED>{l.data.replace('-', '')}100000[-03:EST]\n"
            f"<TRNAMT>{_valor_ofx(l.valor_centavos)}\n<FITID>{900000 + i}\n"
            f"<MEMO>{l.descricao.replace('/', ' ')}\n</STMTTRN>\n")
    partes.append("</BANKTRANLIST>\n<LEDGERBAL>\n<BALAMT>1.00\n"
                  f"<DTASOF>{datas[-1].replace('-', '')}100000[-03:EST]\n</LEDGERBAL>\n"
                  "</STMTRS>\n</STMTTRNRS>\n</BANKMSGSRSV1>\n</OFX>")
    return "".join(partes).encode("cp1252")


def _total(con) -> int:
    return con.execute("SELECT COUNT(*) FROM lancamentos").fetchone()[0]


def _conta(con) -> int:
    return criar_conta(con, "Itaú", "corrente")


def _csv(con, conta, extra: bytes = b""):
    return importar(con, conta, "e.csv", FIXTURE.read_bytes() + extra, ParserItauCsv(), HOJE)


def _ofx_imp(con, conta, linhas=None):
    return importar(con, conta, "e.ofx", _ofx(linhas or _linhas()), ParserOfx(), HOJE)


def test_ofx_montado_le_as_mesmas_linhas() -> None:
    lidas = ParserOfx().ler(_ofx(_linhas()))
    assert lidas.erros == [] and len(lidas.linhas) == 18


def test_csv_depois_ofx_nao_cria_nada(con) -> None:
    conta = _conta(con)
    _csv(con, conta)
    sim = simular_importacao(con, conta, "e.ofx", _ofx(_linhas()), ParserOfx())
    assert (sim.novos, sim.ignorados) == (0, 18)
    r = _ofx_imp(con, conta)
    assert (r.novos, r.ignorados) == (0, 18)
    assert _total(con) == 18


def test_ofx_depois_csv_nao_cria_nada(con) -> None:
    conta = _conta(con)
    _ofx_imp(con, conta)
    r = _csv(con, conta)
    assert (r.novos, r.ignorados) == (0, 18)
    assert _total(con) == 18


def test_periodos_sobrepostos_em_formatos_diferentes(con) -> None:
    conta = _conta(con)
    linhas = _linhas()
    datas = sorted({l.data for l in linhas})
    so_ofx = [l for l in linhas if l.data >= datas[len(datas) // 2]]
    _ofx_imp(con, conta, so_ofx)
    r = _csv(con, conta)
    assert 0 < len(so_ofx) < 18
    assert r.novos == 18 - len(so_ofx) and r.ignorados == len(so_ofx)
    assert _total(con) == 18


def test_identicos_no_mesmo_dia_continuam_dois(con) -> None:
    conta = _conta(con)
    _ofx_imp(con, conta)
    padaria = con.execute(
        "SELECT COUNT(*) FROM lancamentos WHERE descricao LIKE 'PIX QRS PADARIA%'").fetchone()[0]
    assert padaria == 2
    assert _csv(con, conta).novos == 0
    assert _total(con) == 18


def _para_chaves_legadas(con, conta) -> None:
    """Reescreve as chaves como o app antigo (pré-v7) as gravava."""
    u = uuid_da_conta(con, conta)
    oc: dict[tuple[str, str, int], int] = {}
    for r in con.execute(
            "SELECT id, data, descricao, valor_centavos FROM lancamentos ORDER BY id").fetchall():
        trio = (r["data"], r["descricao"], r["valor_centavos"])
        oc[trio] = oc.get(trio, 0) + 1
        con.execute("UPDATE lancamentos SET chave_deduplicacao = ? WHERE id = ?",
                    (chave_deduplicacao_legada(u, *trio, oc[trio]), r["id"]))
    con.commit()


def test_banco_pre_v7_sem_migrar_ignora_reimportacao(con) -> None:
    conta = _conta(con)
    _csv(con, conta)
    _para_chaves_legadas(con, conta)
    r = _csv(con, conta, b"\r\n")  # conteúdo (hash) diferente, mesmas linhas
    assert (r.novos, r.ignorados) == (0, 18)
    assert _total(con) == 18


def test_banco_pre_v7_migrado_ignora_csv_e_ofx(con) -> None:
    conta = _conta(con)
    _csv(con, conta)
    _para_chaves_legadas(con, conta)
    con.execute("BEGIN")
    MIGRACOES[6](con)
    con.execute("COMMIT")
    assert _csv(con, conta, b"\r\n").novos == 0
    assert _ofx_imp(con, conta).novos == 0
    assert _total(con) == 18


def test_chaves_quitadas_legadas_continuam_ignoradas(con) -> None:
    conta = _conta(con)
    _csv(con, conta)
    _para_chaves_legadas(con, conta)
    ids = [r[0] for r in con.execute("SELECT id FROM lancamentos ORDER BY id LIMIT 3")]
    for i in ids:
        con.execute("INSERT INTO chaves_quitadas (chave) SELECT chave_deduplicacao FROM "
                    "lancamentos WHERE id = ?", (i,))
        con.execute("DELETE FROM lancamentos WHERE id = ?", (i,))
    con.commit()
    r = _csv(con, conta, b"\r\n")
    assert (r.novos, r.ignorados) == (0, 18)
    assert _total(con) == 15
