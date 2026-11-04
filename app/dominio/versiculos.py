"""Versículos exibidos no topo do Início (texto local, sem rede; Almeida Revista e Corrigida)."""
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Versiculo:
    texto: str
    referencia: str
    tema: str


VERSICULOS: tuple[Versiculo, ...] = (
    Versiculo("O Senhor é o meu pastor, nada me faltará.", "Salmos 23:1", "provisão"),
    Versiculo("E o meu Deus, segundo as suas riquezas, suprirá todas as vossas necessidades em glória, por Cristo Jesus.", "Filipenses 4:19", "provisão"),
    Versiculo("Mas, buscai primeiro o reino de Deus, e a sua justiça, e todas essas coisas vos serão acrescentadas.", "Mateus 6:33", "prioridades"),
    Versiculo("Porque onde estiver o vosso tesouro, aí estará também o vosso coração.", "Mateus 6:21", "prioridades"),
    Versiculo("Os pensamentos do diligente tendem só à abundância; mas todo apressado, só à penúria.", "Provérbios 21:5", "planejamento"),
    Versiculo("Confia ao Senhor as tuas obras, e os teus pensamentos serão estabelecidos.", "Provérbios 16:3", "planejamento"),
    Versiculo("Os pensamentos dissipam-se onde não há conselho, mas com a multidão de conselheiros se confirmam.", "Provérbios 15:22", "sabedoria"),
    Versiculo("Confia no Senhor de todo o teu coração, e não te estribes no teu próprio entendimento.", "Provérbios 3:5", "confiança"),
    Versiculo("Entrega o teu caminho ao Senhor; confia nele, e ele o fará.", "Salmos 37:5", "confiança"),
    Versiculo("A mão fraca empobrece, mas a mão dos diligentes enriquece.", "Provérbios 10:4", "trabalho"),
    Versiculo("Em todo o trabalho há proveito, mas o falar dos lábios só encaminha à penúria.", "Provérbios 14:23", "trabalho"),
    Versiculo("E tudo quanto fizerdes, fazei-o de todo o coração, como ao Senhor, e não aos homens.", "Colossenses 3:23", "trabalho"),
    Versiculo("A fazenda que procede de vaidade diminuirá, mas quem a ajunta com o trabalho a aumentará.", "Provérbios 13:11", "trabalho"),
    Versiculo("O rico domina sobre os pobres, e o que toma emprestado é servo do que empresta.", "Provérbios 22:7", "dívidas"),
    Versiculo("A ninguém devais coisa alguma, a não ser o amor com que vos ameis uns aos outros.", "Romanos 13:8", "dívidas"),
    Versiculo("Mas é grande ganho a piedade com contentamento.", "1 Timóteo 6:6", "contentamento"),
    Versiculo("Sejam os vossos costumes sem avareza, contentando-vos com o que tendes; porque ele disse: Não te deixarei, nem te desampararei.", "Hebreus 13:5", "contentamento"),
    Versiculo("A alma generosa engordará, e o que regar também será regado.", "Provérbios 11:25", "generosidade"),
    Versiculo("Cada um contribua segundo propôs no seu coração, não com tristeza ou por necessidade; porque Deus ama ao que dá com alegria.", "2 Coríntios 9:7", "generosidade"),
    Versiculo("Ao Senhor empresta o que se compadece do pobre, e ele lhe pagará o seu benefício.", "Provérbios 19:17", "generosidade"),
    Versiculo("Procura conhecer o estado das tuas ovelhas e põe o teu coração sobre os teus rebanhos.", "Provérbios 27:23", "cuidado"),
    Versiculo("Tudo tem o seu tempo determinado, e há tempo para todo o propósito debaixo do céu.", "Eclesiastes 3:1", "tempo"),
    Versiculo("Não temas, porque eu sou contigo; não te assombres, porque eu sou o teu Deus; eu te fortaleço, e te ajudo, e te sustento com a destra da minha justiça.", "Isaías 41:10", "coragem"),
    Versiculo("Não to mandei eu? Esforça-te, e tem bom ânimo; não temas, nem te espantes, porque o Senhor, teu Deus, é contigo, por onde quer que andares.", "Josué 1:9", "coragem"),
    Versiculo("Não estejais inquietos por coisa alguma; antes as vossas petições sejam em tudo conhecidas diante de Deus pela oração e súplica, com ação de graças.", "Filipenses 4:6", "paz"),
)


def sortear(rng: random.Random | None = None) -> Versiculo:
    """Um versículo ao acaso (`rng` permite teste determinístico)."""
    return (rng or random).choice(VERSICULOS)
