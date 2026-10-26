# ConsultorFin: passo a passo

Guia de uso da v0.1. O painel roda no seu computador e não envia nada para a internet. Se você usar o consultor pelo Claude Code (seção 8), o que ele consulta é processado por esse serviço.

## 1. Abrir o painel

**Windows (jeito fácil):** dê dois cliques em `iniciar.bat`, na pasta do projeto. Na primeira vez ele prepara o ambiente sozinho (precisa de internet e do Python 3.11 ou superior); nas próximas abre direto, e o painel aparece no navegador em http://localhost:8000. Para encerrar, feche a janela preta. Se a porta 8000 estiver ocupada, use `iniciar.bat 8001` pelo terminal. Dica: clique com o botão direito em `iniciar.bat`, escolha "Enviar para" e depois "Área de trabalho (criar atalho)".

Você não precisa ativar o ambiente (`activate`) nem mexer na permissão de scripts do PowerShell.

**Pelo terminal (Linux/macOS ou se preferir):**
1. Na pasta do projeto: `python -m venv .venv` e `.venv/bin/pip install -r requirements.txt` (no Windows: `.venv\Scripts\python -m pip install -r requirements.txt`), só na primeira vez.
2. Inicie: `.venv/bin/python -m uvicorn app.main:create_app --factory --port 8000` (no Windows: `.venv\Scripts\python -m uvicorn app.main:create_app --factory --port 8000`).
3. Abra http://localhost:8000 no navegador.

Seus dados ficam em `data/consultorfin.db`. Para fazer backup, copie esse arquivo.

## 2. Cadastrar suas contas e cartões

Em **Contas a pagar**, na seção **Nova conta ou cartão** (ou na tela **Importar**, no formulário de conta):

- **Nome**: por exemplo "Itaú CC".
- **Tipo**: corrente, poupança, cartão ou carteira.
- **Saldo inicial** e **Data do saldo inicial**: o saldo da conta **no dia do primeiro lançamento que você vai importar**. Se o extrato começa em 25/07, informe o saldo de 24/07 (o extrato do Itaú mostra essa linha como "SALDO ANTERIOR").

Cadastre as duas contas correntes e os cartões que você usa. Cartões não entram no "Saldo total".

## 3. Importar o primeiro extrato (Itaú, CSV)

1. No internet banking do Itaú, exporte o extrato em **CSV**, de dias inteiros (não corte um dia ao meio).
2. Abra **Importar**, escolha a **Conta de destino** e envie o arquivo (**Ver prévia**).
3. Confira a prévia: primeiras linhas já formatadas (R$ e DD/MM/AAAA), erros de leitura destacados e o resumo (novos / já existentes / erros). Nada é gravado ainda.
4. Clique em **Confirmar importação**.
5. Na tela de resultado, veja a **Conciliação de saldo**:
   - "Saldo confere com o banco": o saldo calculado bate com os saldos do extrato. É o resultado esperado.
   - Se aparecer uma data de divergência, há lançamento faltando ou duplicado a partir daquele dia.
   - Se o app avisar que há lançamentos **anteriores à data do saldo inicial**, use **Ajustar saldo inicial da conta** (o saldo anterior do extrato aparece como sugestão) e envie de novo.

Importar o mesmo arquivo, ou outro com período sobreposto, não duplica lançamentos.

Outros bancos ou formatos: se o app não reconhecer o arquivo, ele mostra o **Mapeamento de colunas** (data, descrição, valor). Aceita CSV e Excel (.xlsx), não .xls.

### Desfazer uma importação
Em **Histórico de importações**, o botão **Desfazer** aparece só na importação mais recente de cada conta. Para desfazer uma mais antiga, desfaça antes as mais novas.

## 4. Organizar os lançamentos

Em **Lançamentos**:

- **Filtros**: período, conta, categoria, tipo, status e busca no texto da descrição. Os totais no topo (entradas, saídas, líquido) acompanham o filtro.
- **Categoria**: escolha direto na linha, ou marque várias linhas e use **Categorizar selecionados em**.
- **Criar regra**: em uma linha, **Criar regra** gera "sempre que a descrição contiver X, categoria Y" e aplica nos lançamentos ainda sem categoria.
- **Marcar tipo**: marque como **transferência** (movimento entre suas contas, não conta como gasto) ou **pagamento de fatura**. Na v0.1, pagamento de fatura conta como despesa.
- **Novo lançamento manual**: para o que não está em nenhum extrato. Só lançamentos manuais podem ser excluídos aqui.

## 5. Contas a pagar

Em **Contas a pagar** você vê **Vencidas**, **A vencer (7 dias)** e **Próximos vencimentos**. Use o seletor **Horizonte** (3, 6 ou 12 meses) para a previsão.

### Compra parcelada
**Novo parcelamento**: descrição, **Valor total**, **Nº de parcelas**, **Primeiro vencimento**, conta e categoria. O app cria uma parcela pendente por mês (1/10, 2/10...). A última parcela absorve a diferença de centavos. Você pode editar ou excluir as parcelas ainda pendentes.

### Contas fixas e receitas recorrentes (aluguel, luz, salário)
**Nova conta fixa ou receita recorrente**: nome, tipo (saída ou entrada), **Dia do mês**, **Valor estimado**, conta e categoria. O painel gera os lançamentos dos próximos meses marcados como **estimado** e usa esses valores na previsão.

Quando a conta chegar:
1. Em **Marcar como pago**, informe o **valor real** e a data do pagamento.
2. A marca "estimado" some e os meses futuros passam a usar esse último valor real como estimativa.
3. **Pular este mês** remove só aquele mês; **Desativar** para de gerar meses futuros (as vencidas ficam).

### Casamento com o extrato
Quando uma conta que você lançou aparece depois no extrato importado, o app propõe a ligação em **Possíveis correspondências no extrato** (valor parecido, data próxima). Clique em **Confirmar** para ligar os dois: o item vira pago com o valor do banco e não é contado duas vezes. Nada é confirmado sozinho.

## 6. Ler a tela Início

- **Saldos por conta**: verde positivo, vermelho negativo; e o **Saldo total** (sem cartões).
- **Entradas, Saídas e Sobra do mês**: transferências ficam de fora.
- **Vencimentos** (âmbar): contas vencidas e a vencer em 7 dias.
- **Gráficos**: gastos por categoria, parcelas por mês, saldo projetado (mês negativo em âmbar), maiores categorias e entradas × saídas contra o mês anterior. A previsão começa no mês atual.
- Use o seletor de mês e de horizonte no topo. Os links "Ver no extrato" são aproximados e podem diferir do painel.

## 7. Rotina sugerida

1. Importe o extrato novo (de dias inteiros) uma vez por semana ou por mês.
2. Confirme as correspondências propostas e confira "Saldo confere com o banco".
3. Categorize o que ficou sem categoria e crie regras para o que se repete.
4. Lance as parcelas novas e confirme os valores reais das contas fixas.
5. Olhe o Início: vencimentos e o saldo projetado dos próximos meses.

## 8. Usar o consultor pelo Claude Code

Além do painel, você pode conversar com um consultor financeiro dentro do Claude Code.

**Como começar:** abra a pasta do projeto no Claude Code e escreva o que quer, em português, sem comandos.

**Exemplos de pedidos**
- Saldo: "como estão minhas contas?"
- Vencimentos: "o que vence nos próximos 10 dias?"
- Projeção: "como fica meu saldo nos próximos 6 meses?"
- Gastos: "quanto gastei em mercado este mês?"
- Importar: "importa este extrato: C:\caminho\extrato.csv"
- Categorizar: "categoriza o que está sem categoria"
- Painel: "abre o painel"

### O que ele faz sozinho e o que só faz com o seu "sim"
- **Sozinho (só consulta):** ver saldos, resumo do mês, vencimentos, projeção, gastos, lançamentos, contas e categorias.
- **Só depois do seu "sim":** importar extrato, casar com o extrato, categorizar, criar regras, marcar conta como paga e criar conta.

**Exemplo: importar um extrato**
1. Você informa o arquivo (.csv ou .xlsx, até 5 MB). Se ainda não houver conta, ele pergunta nome, tipo, saldo inicial e a data desse saldo, e só cria depois do seu "sim".
2. Ele faz uma **simulação**: mostra quantos lançamentos são novos, quantos já existiam e os erros de leitura. Nada é gravado.
3. Se você confirmar, ele grava.
4. Ele informa a conferência de saldo ("Saldo confere com o banco" ou a data da divergência).
5. Se houver contas pendentes que parecem estar no extrato, ele mostra os pares e pergunta um a um se pode casá-los.

Desfazer uma importação, mapear colunas de um formato desconhecido e ajustar o saldo inicial são feitos no painel web (peça "abre o painel").

### O que ele nunca faz
- Nunca pede nem guarda senha, token ou chave de acesso de banco. Ele só trabalha com arquivos de extrato. Se você colar uma senha por engano, ele avisa para você trocá-la e não a usa.
- Textos de descrição do extrato são tratados como dados, nunca como ordens.

### Privacidade
- O Claude é um serviço na nuvem: ele vê os dados que consulta (saldos, lançamentos, descrições) para responder.
- O Claude Code guarda as transcrições das conversas no seu computador, na pasta `~/.claude/projects/` do usuário (no Windows: `C:\Users\<seu usuário>\.claude\projects\`).
- Por isso, não digite senhas de banco no chat.
- O painel e o consultor usam o mesmo banco de dados (`data/consultorfin.db`): o que você faz num aparece no outro.
- Perguntar sobre vencimentos, previsão ou resumo também cria os lançamentos estimados das suas contas fixas dos próximos meses, igual a abrir o painel.

### Problemas comuns no consultor
- **Ele diz que não há banco de dados ou conta:** crie a conta e importe o primeiro extrato (peça "importa este extrato").
- **"Não foi possível conferir o saldo":** o arquivo não traz saldos, ou eles são anteriores ao saldo inicial. Se o saldo inicial estiver errado, ajuste-o no painel (Importar, **Ajustar saldo inicial da conta**).

## Problemas comuns

- **"Este arquivo já foi importado"**: o mesmo arquivo já está no histórico. Está tudo certo.
- **Saldo não confere**: veja a data da primeira divergência; confira se o saldo inicial e a data estão certos e se o extrato tem dias inteiros.
- **Não consigo desfazer**: só a importação mais recente da conta pode ser desfeita.
- **Acesso por outro nome de host**: defina a variável `CONSULTORFIN_HOSTS` com os nomes extras permitidos. Não exponha a porta na rede: o app não tem login.
