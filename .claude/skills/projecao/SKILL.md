---
name: projecao
description: Projeta o saldo dos próximos meses com contas fixas, parcelas e pendências. Use para planejamento. Exemplos - "como fica meu saldo nos próximos 6 meses?", "vou fechar o ano no vermelho?", "dá para fazer uma compra parcelada agora?", "projeção de 12 meses"
---

# Projeção

1. Rode `.venv\Scripts\python -m app.consultor projecao --meses 6`. Use 3 ou 12 conforme o pedido.
2. Leia `pontos[]`, um por mês: `mes` (AAAA-MM), `saldo_final`, `negativo` (true se o saldo fica abaixo de zero), `parcelas`, `saida_pendente`, `entrada_pendente`. Período: `primeiro_mes` a `ultimo_mes`.
3. Apresente o saldo mês a mês (nomes dos meses), diga o período e aponte o primeiro mês com `negativo` true e o que o causa (parcelas ou contas pendentes). Se nenhum mês fica negativo, diga isso.
4. Explique que a projeção considera só o que já está cadastrado (contas fixas, parcelas, pendências), não gastos variáveis futuros. Sugira ações se houver mês negativo (adiar, reduzir, antecipar entrada).
5. Este comando cria, uma vez, os lançamentos estimados das contas fixas; não mencione a menos que perguntem.
