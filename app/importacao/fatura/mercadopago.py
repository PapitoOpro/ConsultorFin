"""Parser de fatura de cartão Mercado Pago (Visa): uma coluna, campo `texto`."""

import re

from app.importacao.fatura.modelo import (
    FaturaIlegivel, FaturaLida, ItemFatura, inferir_ano, valor_br_para_centavos,
)
from app.importacao.fatura.texto import PaginaTexto

_VAL = r"\d{1,3}(?:\.\d{3})*,\d{2}"
_ITEM = re.compile(
    rf"^(\d{{2}}/\d{{2}})\s+(.+?)(?:\s+Parcela\s+(\d+)\s+de\s+(\d+))?\s+R\$\s*({_VAL})$")
_CARTAO = re.compile(r"Cart[aã]o\s+(Visa|Mastercard)\s+\[\*+(\d{4})\]", re.I)
_DATA = re.compile(r"(\d{2}/\d{2}/\d{4})")
_RS = re.compile(rf"R\$\s*({_VAL})")
_FECHAMENTO = re.compile(r"Fechamento da fatura\s+(\d{2}/\d{2}/\d{4})")
_ANTERIOR = re.compile(rf"Total da fatura de \S+\s+R\$\s*({_VAL})")
_INICIO = "movimentações na fatura"
_FIM = ("parcele a fatura", "lançamentos futuros")
_ENCARGO = ("juros", "multa", "iof", "tarifa")


def _iso(data_br: str) -> str:
    dd, mm, aaaa = data_br.split("/")
    return f"{aaaa}-{mm}-{dd}"


class ParserMercadoPago:
    emissor = "mercadopago"

    def reconhece(self, paginas: list[PaginaTexto]) -> bool:
        txt = "\n".join(p.texto for p in paginas).lower()
        return "mercado pago" in txt and _INICIO in txt

    def ler(self, paginas: list[PaginaTexto]) -> FaturaLida:
        todo = "\n".join(p.texto for p in paginas)
        m_cartao = _CARTAO.search(todo)
        if not m_cartao:
            raise FaturaIlegivel("Não foi possível ler o cartão da fatura.")
        total, venc = self._total_e_vencimento(todo)
        avisos: list[str] = []
        itens, pagamentos = self._lancamentos(todo, venc, m_cartao.group(2), avisos)
        m_ant = _ANTERIOR.search(todo)
        ajustes = valor_br_para_centavos(m_ant.group(1)) - pagamentos if m_ant else 0
        m_fech = _FECHAMENTO.search(todo)
        return FaturaLida(
            emissor=self.emissor,
            bandeira=m_cartao.group(1).capitalize(),
            produto=None,
            final_cartao=m_cartao.group(2),
            data_vencimento=venc,
            data_fechamento=_iso(m_fech.group(1)) if m_fech else None,
            total_impresso_centavos=total,
            ajustes_resumo_centavos=ajustes,
            itens=itens,
            avisos=avisos,
        )

    @staticmethod
    def _total_e_vencimento(todo: str) -> tuple[int, str]:
        linhas = [" ".join(ln.split()) for ln in todo.splitlines()]
        for i, ln in enumerate(linhas):
            if ln.lower().startswith("total a pagar") and i + 1 < len(linhas):
                m_rs, m_dt = _RS.search(linhas[i + 1]), _DATA.search(linhas[i + 1])
                if m_rs and m_dt:
                    return valor_br_para_centavos(m_rs.group(1)), _iso(m_dt.group(1))
        raise FaturaIlegivel("Não foi possível ler o total da fatura.")

    def _lancamentos(self, todo: str, venc: str, final: str,
                     avisos: list[str]) -> tuple[list[ItemFatura], int]:
        itens: list[ItemFatura] = []
        pagamentos = 0
        ativo = False
        em_cartao = False
        for linha in todo.splitlines():
            s = " ".join(linha.split())
            baixa = s.lower()
            if baixa.startswith(_FIM):
                ativo = False
                continue
            if baixa.startswith(_INICIO):
                ativo, em_cartao = True, False
                continue
            if not ativo:
                continue
            if _CARTAO.search(s):
                em_cartao = True
                continue
            m = _ITEM.match(s)
            if not m:
                continue
            desc, valor = m.group(2).strip(), valor_br_para_centavos(m.group(5))
            if not em_cartao and desc.lower().startswith("pagamento"):
                pagamentos += valor
                continue
            try:
                data = inferir_ano(m.group(1), venc)
            except ValueError:
                raise FaturaIlegivel("Data inválida em um lançamento da fatura.") from None
            tipo, assinado = "compra", -valor
            if not em_cartao:
                baixa_d = desc.lower()
                if baixa_d.startswith(("crédito", "credito", "estorno")):
                    tipo, assinado = "credito", valor
                elif baixa_d.startswith(_ENCARGO):
                    tipo = "encargo"
                else:
                    avisos.append("Movimentação não reconhecida tratada como compra.")
            atual = int(m.group(3)) if m.group(3) else None
            total = int(m.group(4)) if m.group(4) else None
            itens.append(ItemFatura(
                data=data, descricao=desc, valor_centavos=assinado, tipo=tipo,
                parcela_atual=atual, parcela_total=total, final_cartao=final,
                categoria_banco=None,
            ))
        return itens, pagamentos
