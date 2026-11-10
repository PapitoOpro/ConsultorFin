# Lançamentos unificados e Cadastros — design

Data: 10/11/2026. Decisões tomadas com o dono em conversa de design (grilling).

## Problema
"Lançamentos" (extrato) e "Contas a pagar" (pendentes) gravam na mesma tabela `lancamentos` e na prática
são a mesma coisa; criar um lançamento avulso, um parcelamento ou uma conta fixa exige ir a telas diferentes.

## Decisões
1. **Lançamentos** vira a tela única, com abas **Todos / A pagar / Realizados** (abre em Todos, por data).
   Contas a pagar sai do menu; `/contas-a-pagar` redireciona para `/lancamentos?aba=a-pagar`.
2. **Formulário único "Novo lançamento"**: entrada ou saída, valor, data, conta, categoria e modo
   *À vista* / *Parcelado em N* / *Repetir todo mês*. Data futura vira pendente. "Já pago" só vale para data
   de hoje ou passada (só no modo À vista).
3. **Parcelas** são linhas normais com a etiqueta "3/10" e ação de grupo (editar/excluir pendentes).
4. **Ações da linha por status**: pendente → Pagar e Pular (Pular só em recorrente); realizado → categoria,
   regra, excluir. Fatura pendente: sem "Pagar", com "Casar com pagamento" (já existe).
5. **Vencidas**: contador e destaque na aba A pagar; selo com a quantidade no item de menu Lançamentos.
6. **Cadastros** substitui Categorias, com seções **Contas e cartões**, **Categorias** e **Recorrentes**.
   O cheque especial vira seção da conta corrente. `/categorias` (GET) redireciona para
   `/cadastros?secao=categorias`. O botão "Ajustar" dos dias continua no Início.
7. Links do Início, de `painel.py` e do consultor/skills deixam de apontar para `/contas-a-pagar`.

## Fora de escopo
Sem mudança de banco. Parcelamento só de saída (como hoje). Aba "A pagar" usa horizonte fixo de 6 meses
(o seletor 3/6/12 some).

## Premissas assumidas (não confirmadas)
- Parcelado e Repetir todo mês não aceitam "já pago".
- Parcelamento de entrada é recusado com mensagem clara.
