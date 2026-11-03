"""Serviço da fila de faturas do Gmail (cliente falso, dados inventados)."""
import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path

from app.dominio import fila_faturas as svc
from app.gmail.cliente import Anexo, Mensagem
from app.repo import fila_faturas as repo
from app.repo.contas import criar_conta

AGORA = datetime(2026, 11, 3, 10, 0, 0)
PDF = b"%PDF-1.4 conteudo ficticio"


class ClienteFalso:
    def __init__(self, mensagens: list[Mensagem], arquivos: dict[str, bytes]) -> None:
        self.mensagens, self.arquivos = mensagens, arquivos
        self.baixados: list[str] = []

    def buscar(self, de: date, ate: date, limite: int = 50) -> list[Mensagem]:
        return self.mensagens

    def baixar_anexo(self, mensagem_id: str, anexo_id: str) -> bytes:
        self.baixados.append(anexo_id)
        return self.arquivos[anexo_id]


def _msg(mid: str = "m1", anexos: tuple[Anexo, ...] = (Anexo("a1", "f.pdf", 100),),
         de: str = "Nubank <no-reply@nubank.com.br>",
         assunto: str = "Fatura final 1234 vencimento 05/11/2026") -> Mensagem:
    return Mensagem(mid, de, assunto, "", anexos)


def _buscar(con, cliente, tmp_path: Path, agora: datetime = AGORA) -> svc.ResumoBusca:
    return svc.buscar_para_fila(con, cliente, date(2026, 11, 1), date(2026, 12, 1), agora, tmp_path)


def _fatura(con, conta: int, h: str, final: str, venc: str) -> None:
    con.execute("UPDATE contas SET final_numero = ? WHERE id = ?", (final, conta))
    con.execute(
        "INSERT INTO faturas (uuid, conta_id, competencia, data_vencimento,"
        " total_impresso_centavos, total_calculado_centavos, arquivo_hash, criado_em)"
        " VALUES (?,?,?,?,0,0,?,?)",
        (f"u-{h[:6]}", conta, venc[:7], venc, h, "2026-01-01T00:00:00"))
    con.commit()


def _conta(con) -> int:
    return criar_conta(con, "Cartao", "cartao", saldo_inicial_centavos=0,
                       data_saldo_inicial="2026-01-01")


def test_busca_enfileira_pdf_valido(con, tmp_path) -> None:
    r = _buscar(con, ClienteFalso([_msg()], {"a1": PDF}), tmp_path)
    assert (r.encontradas, r.novas, r.ja_na_fila, r.ja_importadas, r.ignoradas) == (1, 1, 0, 0, 0)
    item = repo.listar_pendentes(con)[0]
    assert (item["emissor"], item["cartao_final"], item["vencimento"]) == ("Nubank", "1234", "2026-11-05")
    assert item["pdf_hash"] == hashlib.sha256(PDF).hexdigest()
    assert (tmp_path / f"{item['id']}.pdf").read_bytes() == PDF


def test_ignora_nao_pdf_e_acima_de_5mb(con, tmp_path) -> None:
    anexos = (Anexo("a1", "x.png", 10), Anexo("a2", "grande.pdf", 5 * 1024 * 1024 + 1),
              Anexo("a3", "falso.pdf", 10))
    cli = ClienteFalso([_msg(anexos=anexos)], {"a3": b"nao e pdf"})
    r = _buscar(con, cli, tmp_path)
    assert (r.novas, r.ignoradas) == (0, 3)
    assert cli.baixados == ["a3"]
    assert repo.listar_pendentes(con) == []
    assert list(tmp_path.iterdir()) == []


def test_dois_pdfs_na_mesma_mensagem(con, tmp_path) -> None:
    anexos = (Anexo("a1", "a.pdf", 10), Anexo("a2", "b.PDF", 10))
    r = _buscar(con, ClienteFalso([_msg(anexos=anexos)], {"a1": PDF, "a2": PDF + b"2"}), tmp_path)
    assert r.novas == 2
    assert len(list(tmp_path.glob("*.pdf"))) == 2


def test_busca_repetida_nao_duplica_nem_rebaixa_estado(con, tmp_path) -> None:
    cli = ClienteFalso([_msg()], {"a1": PDF})
    _buscar(con, cli, tmp_path)
    item = repo.listar_pendentes(con)[0]
    repo.definir_estado(con, int(item["id"]), "pulada")
    con.commit()
    r = _buscar(con, cli, tmp_path)
    assert (r.novas, r.ja_na_fila) == (0, 1)
    assert cli.baixados == ["a1"]
    assert repo.obter(con, int(item["id"]))["estado"] == "pulada"


def test_ja_importada_por_hash(con, tmp_path) -> None:
    _fatura(con, _conta(con), hashlib.sha256(PDF).hexdigest(), "9999", "2026-01-10")
    r = _buscar(con, ClienteFalso([_msg()], {"a1": PDF}), tmp_path)
    assert (r.novas, r.ja_importadas) == (0, 1)
    assert len(repo.listar_pendentes(con)) == 1


def test_ja_importada_por_final_e_vencimento(con) -> None:
    _fatura(con, _conta(con), "hh1", "1234", "2026-11-05")

    def mk(final, venc, h):
        i = repo.inserir(con, gmail_msg_id=h, anexo_id="a", emissor="X", cartao_final=final,
                         vencimento=venc, pdf_hash=h, agora="2026-11-03T10:00:00")
        return repo.obter(con, i)

    assert svc.ja_importada(con, mk("1234", "2026-11-05", "n1"))
    assert not svc.ja_importada(con, mk("1234", "2026-12-05", "n2"))
    assert not svc.ja_importada(con, mk(None, "2026-11-05", "n3"))
    assert not svc.ja_importada(con, mk("1234", None, "n4"))


def test_campos_ausentes_viram_none(con, tmp_path) -> None:
    _buscar(con, ClienteFalso([_msg(assunto="Sua fatura")], {"a1": PDF}), tmp_path)
    item = repo.listar_pendentes(con)[0]
    assert item["cartao_final"] is None and item["vencimento"] is None


def test_remetente_desconhecido_e_ignorado(con, tmp_path) -> None:
    cli = ClienteFalso([_msg(de="x@spam.example")], {"a1": PDF})
    r = _buscar(con, cli, tmp_path)
    assert (r.encontradas, r.novas) == (1, 0) and cli.baixados == []


def test_expirar_apaga_pdf_e_marca_pulada(con, tmp_path) -> None:
    _buscar(con, ClienteFalso([_msg()], {"a1": PDF}), tmp_path)
    item = repo.listar_pendentes(con)[0]
    assert svc.expirar(con, AGORA + timedelta(days=6), tmp_path) == 0
    assert svc.expirar(con, AGORA + timedelta(days=8), tmp_path) == 1
    assert repo.obter(con, int(item["id"]))["estado"] == "pulada"
    assert not (tmp_path / f"{item['id']}.pdf").exists()
    svc.descartar_pdf(tmp_path, int(item["id"]))  # idempotente
