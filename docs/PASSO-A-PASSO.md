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

## 3. Importar o primeiro extrato (Itaú, OFX ou CSV)

1. No internet banking do Itaú, baixe o extrato da conta corrente em **OFX** (recomendado: vem direto do banco, sem conversão), de dias inteiros (não corte um dia ao meio). Alternativa: baixe em Excel e salve como **CSV** (o que funcionava antes).
2. Abra **Importar**, escolha a **Conta de destino** e envie o arquivo (**Ver prévia**).
3. Confira a prévia: primeiras linhas já formatadas (R$ e DD/MM/AAAA), erros de leitura destacados e o resumo (novos / já existentes / erros). Nada é gravado ainda.
4. Clique em **Confirmar importação**.
5. Na tela de resultado, veja a **Conciliação de saldo**:
   - "Saldo confere com o banco": o saldo calculado bate com os saldos do extrato. É o resultado esperado.
   - Se aparecer uma data de divergência, há lançamento faltando ou duplicado a partir daquele dia.
   - Se o app avisar que há lançamentos **anteriores à data do saldo inicial**, use **Ajustar saldo inicial da conta** (o saldo anterior do extrato aparece como sugestão) e envie de novo.

**O que muda na conferência de saldo com OFX**
- O CSV traz um saldo por dia; o OFX do banco traz **um só saldo**, da hora em que você baixou o arquivo. Por isso, em vez de "Saldo confere", você pode ver uma caixa informativa: "Saldo do banco em DD/MM/AAAA ... O arquivo cobre até DD/MM/AAAA ... O saldo calculado está R$ X acima/abaixo do saldo do banco". Não é erro: movimentos depois da última data do arquivo, como rendimentos, podem explicar uma diferença pequena.
- O OFX não traz o saldo anterior. O app calcula um valor **estimado** a partir do saldo do banco. Se o saldo do banco for de uma data depois do fim do arquivo, o valor não é preenchido (ele não inclui os movimentos do intervalo): confira o saldo no internet banking ou use o saldo anterior de um extrato CSV. Se você usar a estimativa como saldo inicial, o app não diz "confere" na mesma importação, pois seria circular.
- O app sugere a conta pelo número que vem no arquivo, se o "Final da conta" cadastrado combinar: digite os últimos dígitos como aparecem no fim do número da conta no arquivo do banco (no OFX, o fim do ACCTID).
- Você pode importar o mesmo período em OFX depois do CSV (ou o contrário) sem duplicar lançamentos.

Importar o mesmo arquivo, ou outro com período sobreposto, não duplica lançamentos.

Outros bancos ou formatos: se o app não reconhecer o arquivo, ele mostra o **Mapeamento de colunas** (data, descrição, valor). Aceita OFX, CSV e Excel (.xlsx), não .xls. Arquivos OFC e OFX de cartão de crédito são recusados com uma mensagem; para o Itaú, baixe o OFX da conta corrente. Fatura de cartão em PDF tem passo próprio: veja a seção "Importar fatura do cartão".

### Desfazer uma importação
Em **Histórico de importações**, o botão **Desfazer** aparece só na importação mais recente de cada conta. Para desfazer uma mais antiga, desfaça antes as mais novas.

## Importar fatura do cartão

**O que você precisa:** o PDF da fatura, baixado do app ou site do emissor, e a senha do PDF (se ele tiver). Emissores suportados: Itaú, Porto e Mercado Pago. PDF escaneado (foto) não é lido.

**Passo a passo** (tela **Importar**, bloco **Importar fatura do cartão (PDF)**):
1. Escolha o arquivo e digite a senha, se houver. A senha só é usada para abrir o arquivo, fica na memória e não é guardada em lugar nenhum.
2. Clique em **Ver prévia da fatura**. Nada é gravado ainda.
3. **Conferência do total:** o app soma os itens, tira créditos e estornos, soma encargos e compara com o total impresso na fatura. Se aparecer **Divergente**, algum item pode ter sido lido errado; confira antes de confirmar.
4. **Conta-cartão:** se já existe uma conta de cartão com o mesmo final, a fatura vai para ela. Se não existe, marque **Criar conta-cartão** para criá-la.
5. **Categoria item a item:** o app sugere categorias (suas regras e a categoria do banco). Altere o que quiser.
6. Clique em **Confirmar importação**.

**Depois de importar**
- Em **Lançamentos**, a fatura aparece como uma linha (**Fatura <cartão> · venc. dd/mm**, com selo **a pagar** ou **paga**). Clique na linha para expandir os itens; em cada item você escolhe a categoria, e pode marcar vários e categorizar de uma vez.
- **Parcelas previstas:** compras parceladas geram as parcelas dos meses seguintes como previstas (aparecem em Contas a pagar e na projeção). Quando a parcela chega numa fatura seguinte, a prevista é baixada sozinha.
- **Prevista que não apareceu na fatura:** se a fatura do mês já foi importada e não trouxe uma parcela prevista (compra cancelada, quitada antes ou renegociada), a prevista ganha o selo **não apareceu na fatura** em Lançamentos. Abra **Ações** na linha e clique em **Descartar** para apagar só essa parcela prevista; as demais continuam.
- **Casar o pagamento:** quando o pagamento da fatura aparece no extrato da conta corrente, o app propõe casá-lo com a fatura. Confirme: o pagamento deixa de contar como despesa (as compras já contam pelos itens) e a fatura vira **paga**. Pagamento não casado continua contando como despesa, com um aviso na linha.
- **Nunca marque a fatura como paga à mão.** A fatura só fica paga casando o pagamento do banco (botão **Casar pagamento** na linha da fatura, em Lançamentos). Em Contas a pagar, a linha da fatura não tem **Marcar como pago**: ela leva para Lançamentos. Se você ainda não importou o extrato com o pagamento, importe-o primeiro. Desfazer a importação desse extrato volta a fatura para **a pagar**.
- **Reimportar ou substituir:** o mesmo PDF não duplica. Se o emissor gerar uma versão diferente da fatura do mesmo mês, marque **Substituir a fatura existente deste mês**; as categorias já escolhidas nos itens iguais são mantidas.

**Gasto por categoria x saídas:** o gasto por categoria segue a **data da compra** no cartão. As **saídas** seguem a **data do pagamento** (quando o dinheiro sai da conta). Por isso os dois números podem ser diferentes no mesmo mês.

**No consultor (Claude Code):** peça "mostra minhas faturas", "mostra as faturas do cartão X" (filtro por conta: `faturas --conta ID`) ou "mostra os itens da fatura 1". Na lista, `divergencia_sentido` diz se o total impresso ficou **acima** ou **abaixo** da soma dos itens (ou **zero**). Ele só consulta e sugere categorias; a importação do PDF, a escolha da categoria de cada item, casar o pagamento e descartar uma prevista são no painel. Ele recusa marcar a fatura como paga (`pagar`). O consultor recusa PDF de propósito: a senha do PDF nunca passa pelo Claude.

## 4. Organizar os lançamentos

Em **Lançamentos**:

- **Filtros**: período, conta, categoria, tipo, status e busca no texto da descrição. Os quatro cartões de total no topo (**Entradas**, **Saídas**, **Resultado líquido** e **Pendentes no filtro**) acompanham o filtro.
- **Categoria**: escolha direto na linha, ou marque várias linhas (ou **Marcar todas as linhas da página**, na caixa do cabeçalho da tabela) e use **Categorizar selecionados em**; o contador ao lado diz quantas linhas estão marcadas.
- **Menu Ações** (botão **Ações** no fim de cada linha): reúne o que se faz com aquele lançamento.
  - **Criar regra**: gera "sempre que a descrição contiver X, categoria Y" e aplica nos lançamentos ainda sem categoria.
  - **Marcar tipo**: marque como **transferência** (movimento entre suas contas, não conta como gasto) ou **pagamento de fatura**. Se o pagamento estiver casado com uma fatura importada, ele não conta como gasto; sem casar, conta como despesa.
  - **Marcar como pago**: aparece nos lançamentos pendentes; informe o valor real e a data.
  - **Excluir**: aparece só nos lançamentos manuais; os importados não podem ser excluídos aqui.
- **Novo lançamento**: o botão no topo da página abre o formulário **Novo lançamento manual**, para o que não está em nenhum extrato.
- Em telas pequenas (celular), a data, a conta, o tipo e o valor aparecem sob a descrição; arraste a tabela para o lado para ver a categoria e o menu **Ações**.

### Organizar as categorias

> **Backup antes de atualizar:** Antes de atualizar para esta versão, copie o arquivo `data/consultorfin.db` para outro lugar como backup; a atualização reorganiza as categorias automaticamente.

Em **Categorias** ficam todas as categorias, em dois níveis: categoria › subcategoria. As abas **Entradas** e **Saídas** separam o que é dinheiro que entra do que é dinheiro que sai. A lista inicial vem da tabela em `docs/categorias-padrao.md`; você pode alterá-la à vontade.

- **Adicionar categoria**: na aba desejada, digite o nome e clique em **Adicionar categoria**. Ela nasce com o tipo da aba.
- **Adicionar subcategoria**: dentro da categoria, digite o nome e clique em **Adicionar subcategoria**. Não existe subcategoria dentro de subcategoria.
- **Renomear**: altere o nome e clique em **Renomear**. Lançamentos, contas fixas e regras que usam a categoria continuam ligados a ela.
- **Mover uma subcategoria**: escolha a nova categoria e clique em **Mover**. Só dá para mover para uma categoria do mesmo tipo.
- **Mudar o tipo** de uma categoria (entrada ou saída): o tipo de todas as subcategorias muda junto. O tipo só decide quais categorias os formulários oferecem (valor positivo oferece categorias de entrada; negativo, de saída). Nada que já está categorizado é alterado.
- **Nomes**: de 1 a 80 caracteres, sem caracteres de controle. Não pode haver dois nomes iguais no mesmo lugar, sem diferenciar maiúsculas, acentos nem espaços nas pontas.
- **Excluir**: é recusado se a categoria tem subcategorias (exclua ou mova-as antes). Se a categoria está em uso (lançamentos, contas fixas ou regras automáticas), a tela mostra quantos itens serão afetados e você escolhe: **Substituir por** outra categoria do mesmo tipo (tudo é passado para ela, inclusive as regras) ou **Deixar sem categoria** (lançamentos e contas fixas ficam sem categoria e as regras daquela categoria são apagadas). Nada é excluído sem essa confirmação.

Ao atualizar, as categorias que você já tinha foram reconciliadas com a nova lista: Moradia, Saúde e Educação foram mantidas; as antigas que não estavam em uso foram removidas; as antigas que estavam em uso foram mantidas como categorias de saída. Nenhum lançamento perdeu a categoria.

O consultor do Claude Code apenas lê as categorias; para criar, renomear, mover ou excluir, use esta tela.

## 5. Contas a pagar

Em **Contas a pagar** você vê, em cartões, **Vencidas** (borda vermelha), **A vencer (7 dias)** (borda âmbar), **Próximos vencimentos**, **Receitas previstas**, **Possíveis correspondências no extrato**, **Parcelamentos**, **Contas fixas e receitas recorrentes** e **Contas e cartões**. Use o seletor **Horizonte** (3, 6 ou 12 meses) no topo e clique em **Atualizar** para mudar a previsão.

Em cada linha de vencimento, o botão **Ações** abre **Marcar como pago** (valor real e data) e, nas contas fixas, **Pular este mês**. A linha da **fatura de cartão** não tem **Marcar como pago**: use **Casar pagamento**, que leva a Lançamentos (veja "Importar fatura do cartão").

### Compra parcelada
**Novo parcelamento**: descrição, **Valor total**, **Nº de parcelas**, **Primeiro vencimento**, conta e categoria. O app cria uma parcela pendente por mês (1/10, 2/10...). A última parcela absorve a diferença de centavos. Para editar ou excluir as parcelas ainda pendentes, use o botão **Ações** da linha do parcelamento (**Editar pendentes** e **Excluir pendentes**).

### Contas fixas e receitas recorrentes (aluguel, luz, salário)
**Nova conta fixa ou receita recorrente**: nome, tipo (saída ou entrada), **Dia do mês**, **Valor estimado**, conta e categoria. O painel gera os lançamentos dos próximos meses marcados como **estimado** e usa esses valores na previsão.

Quando a conta chegar:
1. No botão **Ações** da linha, use **Marcar como pago** (nas contas fixas, **Confirmar valor e pagar**): informe o **valor real** e a data do pagamento.
2. A marca "estimado" some e os meses futuros passam a usar esse último valor real como estimativa.
3. **Pular este mês** remove só aquele mês; **Desativar** para de gerar meses futuros (as vencidas ficam).

### Casamento com o extrato
Quando uma conta que você lançou aparece depois no extrato importado, o app propõe a ligação em **Possíveis correspondências no extrato** (valor parecido, data próxima). Clique em **Confirmar** para ligar os dois: o item vira pago com o valor do banco e não é contado duas vezes. Nada é confirmado sozinho.

## 6. Ler a tela Início

- **Cartões do topo**: **Saldo total** (destaque verde, sem cartões de crédito), **Entradas do mês**, **Saídas do mês** (as duas comparadas ao mês anterior quando ele teve movimento) e **Resultado do mês**: verde com "superávit" quando sobra dinheiro, vermelho com "déficit" quando falta, "equilibrado" quando fecha em zero e "sem movimento" se o mês não teve nada. Transferências ficam de fora.
- **Fluxo de caixa**: entradas e saídas dos últimos 6 meses.
- **Gastos por categoria**: rosca com legenda; passe o mouse (ou toque) em uma fatia para ver o valor e a porcentagem.
- **Lançamentos recentes**: os últimos lançamentos já efetivados. **Ver todos** abre Lançamentos.
- **Próximos vencimentos**: primeiro as contas **vencidas** (até 3; se houver mais, aparece o link "+N vencidas"), depois **todas** as que vencem nos próximos 7 dias (até 5; se houver mais, "+N a vencer"). Se sobrar espaço, entram as próximas contas pendentes, até 6 itens no total. O subtítulo mostra quantas estão vencidas e quantas vencem em 7 dias. **Ver todas** abre Contas a pagar.
- **Contas**: o saldo atual de cada conta (verde positivo, vermelho negativo); cartões não entram no saldo total.
- **Saldo projetado**, **Parcelas futuras**, **Maiores categorias** e **Entradas × saídas** (contra o mês anterior): a previsão começa no mês atual.
- **Saídas por categoria**: lista expansível por categoria e subcategoria, com o **Total** no fim.
- No topo, escolha o **mês** (e clique em **Atualizar**) e o horizonte da projeção pelos botões **3 meses**, **6 meses** e **12 meses**. Os links "Ver no extrato" são aproximados e podem diferir do painel.

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
- Faturas: "mostra minhas faturas de cartão", "faturas do cartão 2" (`faturas --conta 2`), "quais itens da fatura 1 estão sem categoria?"

### O que ele faz sozinho e o que só faz com o seu "sim"
- **Sozinho (só consulta):** ver saldos, resumo do mês, vencimentos, projeção, gastos, lançamentos, faturas de cartão (lista e itens), contas e categorias.
- **Só depois do seu "sim":** importar extrato, casar com o extrato, categorizar, criar regras, marcar conta como paga e criar conta.

**Exemplo: importar um extrato**
1. Você informa o arquivo (.ofx, .csv ou .xlsx, até 5 MB). Se ainda não houver conta, ele pergunta nome, tipo, saldo inicial e a data desse saldo, e só cria depois do seu "sim".
2. Ele faz uma **simulação**: mostra quantos lançamentos são novos, quantos já existiam e os erros de leitura. Nada é gravado.
3. Se você confirmar, ele grava.
4. Ele informa a conferência de saldo ("Saldo confere com o banco", a data da divergência ou, no OFX, o saldo informativo do banco).
5. Se houver contas pendentes que parecem estar no extrato, ele mostra os pares e pergunta um a um se pode casá-los.

Desfazer uma importação, mapear colunas de um formato desconhecido e ajustar o saldo inicial são feitos no painel web (peça "abre o painel").

### O que ele nunca faz
- Nunca pede nem guarda senha, token ou chave de acesso de banco, nem a senha do PDF da fatura (fatura em PDF só se importa pelo painel). Ele só trabalha com arquivos de extrato. Se você colar uma senha por engano, ele avisa para você trocá-la e não a usa.
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
- **Caixa "Saldo do banco em ... O saldo calculado está R$ ... acima/abaixo..." (OFX)**: é informativa; o arquivo termina antes da data do saldo e movimentos posteriores (como rendimentos) podem explicar a diferença.
- **"OFC ainda não é suportado" ou "cartão de crédito ainda não é suportado"**: baixe o extrato da conta corrente em OFX.
- **"O arquivo não parece ser um OFX"**: o arquivo .ofx está corrompido ou é de outro tipo; baixe de novo no banco.
- **Saldo não confere**: veja a data da primeira divergência; confira se o saldo inicial e a data estão certos e se o extrato tem dias inteiros.
- **Não consigo desfazer**: só a importação mais recente da conta pode ser desfeita.
- **Acesso por outro nome de host**: defina a variável `CONSULTORFIN_HOSTS` com os nomes extras permitidos. Não exponha a porta na rede: o app não tem login.
