---
name: vencimentos
description: Lista contas vencidas e a vencer. Use quando o usuário perguntar o que precisa pagar. Exemplos - "o que vence esta semana?", "tenho conta atrasada?", "o que vence nos próximos 15 dias?", "quanto tenho a pagar?"
---

# Vencimentos

1. Rode `.venv/Scripts/python -m app.consultor vencimentos` (janela padrão de 7 dias). Para outro prazo: `.venv/Scripts/python -m app.consultor vencimentos --dias 15` (0 a 366).
2. Leia: `vencidas[]` e `a_vencer[]` (cada item: `id`, `descricao`, `data_br`, `valor`, `estimado`, `recorrente`, `parcela`); totais `vencidas_total_a_pagar` e `a_vencer_total_a_pagar`; janela até `ate_br`.
3. Apresente primeiro as vencidas (destaque, há quantos dias), depois as a vencer em ordem de data, com os totais. Diga a janela analisada ("de hoje até 30/10/2026"). Itens `estimado` são previsões de contas fixas: avise que o valor pode mudar.
4. Sem itens: diga que está tudo em dia e ofereça olhar um prazo maior.
5. Para marcar como paga, só se o usuário pedir: confirme qual conta, valor e data, e rode `.venv/Scripts/python -m app.consultor pagar --lancamento ID` (opcionais: `--valor "1.234,56"`, sempre em formato brasileiro com vírgula; `--data AAAA-MM-DD`, hoje ou anterior, nunca futura; padrão: valor previsto e hoje). Fatura de cartão (descrição "Fatura ...") nunca se marca como paga: o `pagar` recusa; oriente casar o pagamento do banco no painel, em Lançamentos.
6. Este comando cria, uma vez, os lançamentos estimados das contas fixas; não mencione a menos que perguntem.
