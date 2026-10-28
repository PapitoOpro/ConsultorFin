"""Parser de fatura de cartão Itaú (Visa Platinum, Mastercard Gold, Visa Passaí)."""

import re
import unicodedata
from dataclasses import dataclass

from app.importacao.fatura.modelo import (
    FaturaIlegivel, FaturaLida, ItemFatura, inferir_ano, valor_br_para_centavos,
)
from app.importacao.fatura.texto import PaginaTexto

_VAL = r"-?\d{1,3}(?:\.\d{3})*,\d{2}-?"
_ITEM = re.compile(rf"^(\d{{2}}/\d{{2}})\s+(.+?)(?:\s+(\d{{2}}/\d{{2}}))?\s+({_VAL})$")
_CATEGORIA = re.compile(r"^([a-zà-ÿ]+(?: [a-zà-ÿ]+)*)(?= ?[A-ZÀ-Ý]|$)")
_CATEGORIA_COLADA = re.compile(r"^([a-zà-ÿ]{3,})(?=[A-ZÀ-Ý])")  # 'categoriaLOJA' no início
_DETALHE_INTL = re.compile(rf"^[\d.]+,\d{{2}}\s+[A-Z]{{3}}\s+{_VAL}$")
_IOF_INTL = re.compile(rf"^Repasse de IOF em R\$\s*({_VAL})$")
_PRODUTO = re.compile(r"^\s*(Gold|Platinum|Black|Infinite|Signature|Classic|Standard)\s*$", re.M)
_CARTAO = re.compile(r"Cart[aã]o\s+(\d)\d{3}\.[X*]{4}\.[X*]{4}\.(\d{4})")
_VENCIMENTO = re.compile(r"Vencimento:\s*(\d{2}/\d{2}/\d{4})")
_ENCARGOS_BLOCO = re.compile(rf"Total de encargos em R\$\s*({_VAL})")
_ENCARGOS_RESUMO = re.compile(rf"Encargos \(Financiamento[^)]*\)\s*({_VAL})")
_FINAL = re.compile(r"\(\s*final\s*\*?(\d{4})\s*\)", re.I)
_FIM = ("total dos lançamentos atuais", "l total dos lançamentos atuais", "compras parceladas")
_SEM_ITENS = "Nenhum lançamento lido na fatura: confira o arquivo."
_IGNORAR = ("lançamentos", "total", "principal (r$", "data", "próxima fatura", "demais faturas")
_SECOES = (
    ("pagamentos efetuados", "pagamentos"),
    ("lançamentos: compras e saques", "compras"),
    ("lançamentos: produtos e serviços", "servico"),
    ("lançamentos internacionais", "internacional"),
    ("limites de crédito", ""),
    ("encargos cobrados", ""),
)
_TIPO_SECAO = {"compras": "compra", "servico": "servico", "internacional": "internacional"}


def _sem_acento(txt: str) -> str:
    base = unicodedata.normalize("NFKD", txt)
    return "".join(c for c in base if not unicodedata.combining(c)).lower()


def _centavos(txt: str) -> int:
    """Valor BR; aceita o sinal depois do número ('9,99-')."""
    t = txt.strip()
    if t.endswith("-"):
        t = "-" + t[:-1]
    return valor_br_para_centavos(t)


@dataclass
class _Bruto:
    data: str
    descricao: str
    centavos: int  # como impresso (despesa positiva)
    secao: str
    parcela: tuple[int, int] | None
    final: str
    categoria: str | None = None


class ParserItau:
    emissor = "itau"

    def reconhece(self, paginas: list[PaginaTexto]) -> bool:
        txt = _sem_acento("\n".join(p.texto for p in paginas))
        return "banco itau s.a." in txt and "itau cartoes" in txt

    def ler(self, paginas: list[PaginaTexto]) -> FaturaLida:
        p1 = paginas[0].texto
        todo = "\n".join(p.texto for p in paginas)
        venc = self._vencimento(p1)
        m_cartao = _CARTAO.search(p1)
        if not m_cartao:
            raise FaturaIlegivel("Não foi possível ler o cartão da fatura.")
        final = m_cartao.group(2)
        total = self._valor(r"Total desta fatura", p1, "o total da fatura")
        ajustes = self._ajustes(p1)
        brutos, iof = self._lancamentos(paginas, venc, final)
        itens = [self._item(b) for b in brutos]
        itens.extend(self._encargos(iof, todo, p1, venc, final))
        avisos = [] if brutos else [_SEM_ITENS]
        return FaturaLida(
            emissor=self.emissor,
            bandeira={"4": "Visa", "5": "Mastercard"}.get(m_cartao.group(1)),
            produto=self._produto(p1),
            final_cartao=final,
            data_vencimento=venc,
            data_fechamento=None,
            total_impresso_centavos=total,
            ajustes_resumo_centavos=ajustes,
            itens=itens,
            avisos=avisos,
        )

    # ---------- resumo ----------

    @staticmethod
    def _vencimento(p1: str) -> str:
        m = _VENCIMENTO.search(p1)
        if not m:
            raise FaturaIlegivel("Não foi possível ler o vencimento da fatura.")
        dd, mm, aaaa = m.group(1).split("/")
        return f"{aaaa}-{mm}-{dd}"

    @staticmethod
    def _valor(rotulo: str, texto: str, nome: str) -> int:
        m = re.search(rf"{rotulo}\s+({_VAL})", texto)
        if not m:
            raise FaturaIlegivel(f"Não foi possível ler {nome}.")
        return _centavos(m.group(1))

    def _ajustes(self, p1: str) -> int:
        """Saldo financiado (do resumo); sem ele, saldo anterior − pagamentos.
        O que ficou sem pagar já vem rolado no saldo financiado: somar os dois contaria em dobro."""
        def opcional(padrao: str) -> int:
            m = re.search(padrao, p1)
            return _centavos(m.group(1)) if m else 0

        anterior = opcional(rf"Total da fatura anterior\s+({_VAL})")
        pagamentos = opcional(rf"Pagamentos?\s+efetuados?(?:\s+em\s+\d{{2}}/\d{{2}}/\d{{4}})?\s+({_VAL})")
        financiado = opcional(rf"Saldo financiado\s+({_VAL})")
        return financiado if financiado else anterior - abs(pagamentos)

    @staticmethod
    def _produto(p1: str) -> str | None:
        if re.search(r"FIC\s+Ita[uú]\s+CBD", p1, re.I):
            return "Passaí"
        m = _PRODUTO.search(p1)
        return m.group(1) if m else None

    # ---------- lançamentos ----------

    def _lancamentos(self, paginas: list[PaginaTexto], venc: str,
                     principal: str) -> tuple[list[_Bruto], list[_Bruto]]:
        brutos: list[_Bruto] = []
        iof: list[_Bruto] = []
        secao = ""
        ultimo: _Bruto | None = None
        quer_categoria = False
        quer_continuacao = False
        cartao = principal
        for pagina in paginas:
            for linha in (pagina.esquerda + "\n" + pagina.direita).splitlines():
                s = " ".join(linha.split())
                baixa = s.lower()
                if baixa.startswith(_FIM):
                    return brutos, iof
                nova = next((v for k, v in _SECOES if baixa.startswith(k)), None)
                if nova is not None:
                    secao, ultimo = nova, None
                    quer_categoria = quer_continuacao = False
                    continue
                if secao in ("", "pagamentos") or not s:
                    continue
                m = _ITEM.match(s)
                if m:
                    ultimo = self._bruto(m, secao, venc, cartao)
                    brutos.append(ultimo)
                    quer_categoria = secao == "compras"
                    quer_continuacao = secao == "servico"
                    continue
                if baixa.startswith("lançamentos no cartão"):
                    cartao, ultimo = principal, None
                    continue
                m_final = _FINAL.search(s)
                if m_final:
                    cartao, ultimo = m_final.group(1), None
                    continue
                if baixa.startswith(_IGNORAR):
                    continue
                if secao == "internacional":
                    mi = _IOF_INTL.match(s)
                    if mi and _centavos(mi.group(1)) != 0:
                        data = brutos[-1].data if brutos and brutos[-1].secao == "internacional" else venc
                        iof.append(_Bruto(data, "Repasse de IOF", _centavos(mi.group(1)), "encargo",
                                          None, cartao))
                    elif ultimo and (_DETALHE_INTL.match(s) or baixa.startswith("dólar")):
                        ultimo.descricao += " " + s
                elif secao == "compras" and quer_categoria and ultimo:
                    mc = _CATEGORIA.match(s)
                    if mc:
                        ultimo.categoria = mc.group(1)
                        quer_categoria = False
                elif secao == "servico" and quer_continuacao and ultimo:
                    ultimo.descricao += " " + s
                    quer_continuacao = False
        return brutos, iof

    @staticmethod
    def _bruto(m: re.Match[str], secao: str, venc: str, final: str) -> _Bruto:
        desc = m.group(2).strip()
        parcela: tuple[int, int] | None = None
        if m.group(3):
            atual, total = (int(x) for x in m.group(3).split("/"))
            if 1 <= atual <= total:
                parcela = (atual, total)
            else:
                desc = f"{desc} {m.group(3)}"
        try:
            data = inferir_ano(m.group(1), venc)
        except ValueError:
            raise FaturaIlegivel("Data inválida em um lançamento da fatura.") from None
        categoria: str | None = None
        colada = _CATEGORIA_COLADA.match(desc)
        if colada:  # a linha de categoria logo abaixo, se houver, prevalece
            categoria, desc = colada.group(1), desc[colada.end():].strip()
        return _Bruto(data, desc, _centavos(m.group(4)), secao, parcela, final, categoria)

    @staticmethod
    def _item(b: _Bruto) -> ItemFatura:
        if b.secao == "encargo":
            tipo = "encargo"
        elif b.centavos < 0:
            tipo = "credito"
        else:
            tipo = _TIPO_SECAO[b.secao]
        return ItemFatura(
            data=b.data, descricao=b.descricao, valor_centavos=-b.centavos, tipo=tipo,
            parcela_atual=b.parcela[0] if b.parcela else None,
            parcela_total=b.parcela[1] if b.parcela else None,
            final_cartao=b.final, categoria_banco=b.categoria,
        )

    def _encargos(self, iof: list[_Bruto], todo: str, p1: str, venc: str,
                  final: str) -> list[ItemFatura]:
        itens = [self._item(b) for b in iof]
        m = _ENCARGOS_BLOCO.search(todo) or _ENCARGOS_RESUMO.search(p1)
        if m and _centavos(m.group(1)) > 0:
            itens.append(ItemFatura(
                data=venc, descricao="Encargos da fatura", valor_centavos=-_centavos(m.group(1)),
                tipo="encargo", parcela_atual=None, parcela_total=None,
                final_cartao=final, categoria_banco=None,
            ))
        return itens
