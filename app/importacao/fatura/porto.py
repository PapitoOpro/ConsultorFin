"""Parser de fatura de cartão Porto (Porto Seguro): uma coluna, campo `texto`."""

import re

from app.importacao.fatura.modelo import (
    FaturaIlegivel, FaturaLida, ItemFatura, inferir_ano, valor_br_para_centavos,
)
from app.importacao.fatura.texto import PaginaTexto

_VAL = r"-?\d{1,3}(?:\.\d{3})*,\d{2}"
_ITEM = re.compile(rf"^(\d{{2}}/\d{{2}})\s+(.+?)\s+({_VAL})$")
_CARTAO = re.compile(r"Cart[aã]o\s+(\d)\d{3}\s+\d{2}\*+\s+\*+\s+\*(\d{3})")
_TITULAR = re.compile(r"^(.+?)\s*\(final \*(\d{3})\)$")
_VENCIMENTO = re.compile(r"Esta fatura vence em\s+(\d{2}/\d{2}/\d{4})")
_TOTAL = re.compile(rf"O valor total é\s+R\$\s*({_VAL})")
_PERIODO = re.compile(r"\(\d{2}/\d{2} a (\d{2}/\d{2})\)")
_PARCELA_NN = re.compile(r"\s(\d{2})/(\d{2})(?=\s|$)")
_PARCELA_ATUAL = re.compile(r"\s[A-Z]{2}/(\d{2})(?=\s|$)")
_INICIO = "lançamentos: compras e saques"
_FIM = ("contestações", "detalhamento geral", "lançamentos no cartão")
_ENCARGO = ("multa", "juros de mora", "iof", "encargo")
_PAGAMENTO = ("pagamento", "pgto", "pagto", "débito automático", "debito automatico")
_BANDEIRAS = {"4": "Visa", "5": "Mastercard"}


def _opcional(padrao: str, texto: str) -> int:
    m = re.search(rf"{padrao}\s*\(?[+-]?\)?\s*R\$\s*({_VAL})", texto)
    return valor_br_para_centavos(m.group(1)) if m else 0


class ParserPorto:
    emissor = "porto"

    def reconhece(self, paginas: list[PaginaTexto]) -> bool:
        txt = "\n".join(p.texto for p in paginas).lower()
        return "portoseg" in txt or "cartaoportoseguro.com.br" in txt

    def ler(self, paginas: list[PaginaTexto]) -> FaturaLida:
        todo = "\n".join(p.texto for p in paginas)
        m_cartao = _CARTAO.search(todo)
        m_venc = _VENCIMENTO.search(todo)
        m_total = _TOTAL.search(todo)
        if not m_cartao:
            raise FaturaIlegivel("Não foi possível ler o cartão da fatura.")
        if not m_venc:
            raise FaturaIlegivel("Não foi possível ler o vencimento da fatura.")
        if not m_total:
            raise FaturaIlegivel("Não foi possível ler o total da fatura.")
        dd, mm, aaaa = m_venc.group(1).split("/")
        venc = f"{aaaa}-{mm}-{dd}"
        final = m_cartao.group(2)
        avisos: list[str] = []
        itens = self._lancamentos(todo, venc, final, avisos)
        itens.extend(self._encargos_resumo(todo, itens, venc, final))
        return FaturaLida(
            emissor=self.emissor,
            bandeira=_BANDEIRAS.get(m_cartao.group(1)),
            produto=None,
            final_cartao=final,
            data_vencimento=venc,
            data_fechamento=self._fechamento(todo, venc),
            total_impresso_centavos=valor_br_para_centavos(m_total.group(1)),
            ajustes_resumo_centavos=self._ajustes(todo),
            itens=itens,
            avisos=avisos,
        )

    @staticmethod
    def _fechamento(todo: str, venc: str) -> str | None:
        m = _PERIODO.search(todo)
        if not m:
            return None
        try:
            return inferir_ano(m.group(1), venc)
        except ValueError:
            return None

    @staticmethod
    def _ajustes(todo: str) -> int:
        """Saldo anterior − pagamentos + saldo financiado (encargos viram item)."""
        anterior = _opcional(r"Saldo fatura anterior", todo)
        pagamento = _opcional(r"Pagamento/créditos", todo)
        financiado = _opcional(r"Saldo financiado", todo)
        return anterior - abs(pagamento) + financiado

    def _lancamentos(self, todo: str, venc: str, final: str, avisos: list[str]) -> list[ItemFatura]:
        itens: list[ItemFatura] = []
        cartao: str | None = None
        for linha in todo.splitlines():
            s = " ".join(linha.split())
            baixa = s.lower()
            if baixa.startswith(_FIM):
                cartao = None
                continue
            if baixa.startswith(_INICIO):
                cartao = final
                continue
            if cartao is None:
                continue
            m_tit = _TITULAR.match(s)
            if m_tit:
                cartao = m_tit.group(2)
                continue
            m = _ITEM.match(s)
            if m:
                item = self._item(m, venc, cartao, avisos)
                if item:
                    itens.append(item)
        return itens

    @staticmethod
    def _item(m: re.Match[str], venc: str, final: str, avisos: list[str]) -> ItemFatura | None:
        desc = m.group(2).strip()
        valor = valor_br_para_centavos(m.group(3))
        if valor < 0 and desc.lower().startswith(_PAGAMENTO):
            return None
        atual: int | None = None
        total: int | None = None
        m_p = _PARCELA_NN.search(" " + desc)
        m_a = _PARCELA_ATUAL.search(" " + desc)
        if m_p and 1 <= int(m_p.group(1)) <= int(m_p.group(2)):
            atual, total = int(m_p.group(1)), int(m_p.group(2))
            desc = (" " + desc).replace(m_p.group(0), "", 1).strip()
        elif m_a and int(m_a.group(1)) >= 1:
            atual = int(m_a.group(1))
            desc = (" " + desc).replace(m_a.group(0), "", 1).strip()
            avisos.append("Parcela sem o total na fatura: confira o número de parcelas.")
        try:
            data = inferir_ano(m.group(1), venc)
        except ValueError:
            raise FaturaIlegivel("Data inválida em um lançamento da fatura.") from None
        if valor < 0:
            tipo = "credito"
        elif desc.lower().startswith(_ENCARGO):
            tipo = "encargo"
        else:
            tipo = "compra"
        return ItemFatura(
            data=data, descricao=desc, valor_centavos=-valor, tipo=tipo,
            parcela_atual=atual, parcela_total=total, final_cartao=final, categoria_banco=None,
        )

    @staticmethod
    def _encargos_resumo(todo: str, itens: list[ItemFatura], venc: str, final: str) -> list[ItemFatura]:
        """Encargos do resumo que não aparecem como linha da tabela."""
        soma = sum(_opcional(rf"Encargos de {nome}", todo) for nome in (
            "rotativo", "compras parceladas", "parcelamento da fatura", "saque"))
        if soma <= 0 or any(i.descricao.lower().startswith("encargo") for i in itens):
            return []
        return [ItemFatura(
            data=venc, descricao="Encargos da fatura", valor_centavos=-soma, tipo="encargo",
            parcela_atual=None, parcela_total=None, final_cartao=final, categoria_banco=None,
        )]
