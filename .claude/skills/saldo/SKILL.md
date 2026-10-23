---
name: saldo
description: Mostra o saldo de cada conta e o total. Use quando o usuário perguntar como estão as contas ou quanto tem. Exemplos - "como estão minhas contas?", "quanto tenho no banco?", "qual meu saldo?", "quanto dinheiro eu tenho hoje?"
---

# Saldo

1. Rode `.venv/Scripts/python -m app.consultor saldos` (raiz do repositório).
2. Leia: `contas[]` (`nome`, `tipo`, `saldo`) e `saldo_total`. O total não inclui contas de cartão (`observacao`).
3. Se der erro dizendo que não há banco ou conta, oriente: ainda não há dados; ofereça a skill `importar` (criar a conta e importar o primeiro extrato).
4. Apresente: uma linha por conta e o total, com a data de hoje (`hoje_br`). Quando útil, rode também `.venv/Scripts/python -m app.consultor resumo` e comente a sobra do mês (`entradas`, `saidas`, `sobra`) em relação ao mês anterior (`comparativo`).
5. Se houver `vencidas` maior que zero no resumo, avise e sugira a skill `vencimentos`.
