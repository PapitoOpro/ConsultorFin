---
name: painel
description: Abre o painel web do ConsultorFin (gráficos, importar com mapeamento de colunas, ajustar saldo inicial, contas a pagar). Exemplos - "abre o painel", "quero ver os gráficos", "preciso ajustar o saldo inicial", "abre a tela de importar"
---

# Painel

Python do projeto: `.venv/Scripts/python` (Git Bash e PowerShell; em Linux/macOS use `.venv/bin/python`).

1. Verifique a porta 8000 (troque o número para testar outra):
   `.venv/Scripts/python -c "import urllib.request as u;r=u.urlopen('http://localhost:8000/',timeout=3).read().decode();print('PAINEL' if 'pt-BR' in r and 'Contas a pagar' in r else 'OUTRO')"`
   - `PAINEL`: já é o ConsultorFin; apenas informe o endereço.
   - erro de conexão (URLError): a porta está livre; vá ao passo 2.
   - `OUTRO`: outro programa usa a porta 8000; use a porta 8001 nos passos seguintes e avise o usuário. Nunca encerre processos que não foram iniciados por você.
2. Inicie com a ferramenta Bash usando `run_in_background: true`, na raiz do repositório: `.venv/Scripts/python -m uvicorn app.main:create_app --factory --port 8000`. Guarde o id da tarefa em segundo plano. Confirme com o passo 1 (pode levar alguns segundos).
3. Diga ao usuário: "Abra http://localhost:8000 no navegador" (ou a porta usada). Se pediu algo específico, indique a tela (Início, Lançamentos, Importar, Contas a pagar) e, para saldo inicial, a opção "Ajustar saldo inicial da conta" na tela Importar.
4. Se o painel não subir, avise de forma simples, sem mostrar o erro cru.
5. Quando o usuário pedir para fechar o painel, encerre a tarefa guardada (ferramenta TaskStop com esse id). Se o id se perdeu, avise que não é possível encerrar daqui e peça para fechar a janela do terminal; não mate outros processos.
