---
name: painel
description: Abre o painel web do ConsultorFin (gráficos, importar com mapeamento de colunas, ajustar saldo inicial, contas a pagar). Exemplos - "abre o painel", "quero ver os gráficos", "preciso ajustar o saldo inicial", "abre a tela de importar"
---

# Painel

1. Verifique se já está no ar: `curl -s -o NUL -w "%{http_code}" http://localhost:8000/` (200 = rodando). Se estiver, apenas informe o endereço.
2. Se não estiver, inicie em segundo plano a partir da raiz do repositório: `.venv\Scripts\python -m uvicorn app.main:create_app --factory --port 8000` (em segundo plano, sem esperar terminar). Confirme de novo com o passo 1.
3. Diga ao usuário: "Abra http://localhost:8000 no navegador". Se pediu algo específico, indique a tela (Início, Lançamentos, Importar, Contas a pagar) e, para saldo inicial, a opção "Ajustar saldo inicial da conta" na tela Importar.
4. Se a porta 8000 estiver ocupada por outro programa ou não subir, avise de forma simples e ofereça outra porta (`--port 8001`).
5. Quando o usuário pedir para fechar o painel, encerre o processo iniciado por você.
