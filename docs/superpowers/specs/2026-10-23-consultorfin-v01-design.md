# ConsultorFin v0.1: Design

Complementa o `CLAUDE.md` do projeto (princípios, esquema do banco, regras do Itaú, paleta, ordem de trabalho). Este documento registra o que foi decidido na conversa de 23/10/2026 e **muda o escopo da v0.1** em relação ao CLAUDE.md.

## 1. Intenção

Painel financeiro pessoal, local, que responde numa olhada:
1. Minhas contas estão positivas ou negativas?
2. Tenho contas vencidas ou a vencer?
3. Como ficam os próximos meses (parcelas, gastos, saldo projetado)?

Deve nascer de forma que possa ser vendido no futuro, mas a v0.1 é 100% local.

## 2. Objetivos, não-objetivos, anti-objetivos

**Objetivos**
- Tela Início de leitura imediata, em blocos pequenos e minimalistas.
- Saldo calculado batendo com o extrato do Itaú (teste de aceite do CLAUDE.md, seção 13).
- Controle de contas a pagar: parcelas e contas fixas, com vencidas e a vencer.
- Projeção simples dos próximos 6 meses.

**Não-objetivos da v0.1**
- OFX/OFC, PDF e faturas de cartão importadas (v0.2).
- Sincronização, nuvem, multiusuário, login.
- IA ou qualquer chamada externa.

**Anti-objetivos (seria fracasso mesmo funcionando)**
- Saldo que não bate com o banco.
- Lançamento duplicado ao reimportar.
- Tela poluída, estilo painel corporativo cheio de tabelas.
- Qualquer dado financeiro real no Git.

## 3. Mudança de escopo

O CLAUDE.md prevê só Início, Importar e Lançamentos na v0.1, sem vencimentos nem projeções. **Decisão do dono: vencimentos, parcelas, contas fixas e projeções entram na v0.1.** Isso exige a tela **Contas a pagar** (nova) e ajustes no esquema (seção 6).

## 4. Tela Início

Referência de estilo: grade de blocos, cada um com uma coisa só (inspiração em dashboards tipo Power BI; **não copiar layout**, ver CLAUDE.md seção 5). Paleta e fontes do CLAUDE.md, sem sombras, valores em fonte monoespaçada à direita. Gráficos pequenos e minimalistas: rosca, colunas finas, barras compactas.

Organização (mockup aprovado em `.superpowers/brainstorm/821-1792756663/content/inicio-minimalista.html`):
1. **Fileira de números:** saldo por conta (verde se positivo, vermelho se negativo) e saldo total.
2. **Vencimentos:** bloco em âmbar com contas vencidas e a vencer (janela de 7 dias).
3. **Rosca** de gastos por categoria do mês.
4. **Colunas pequenas:** parcelas dos próximos 6 meses e saldo projetado (mês com saldo negativo em âmbar).
5. **Barras compactas:** maiores categorias.
6. Também no Início (CLAUDE.md seção 9): seletor mês/ano, cartões de entradas, saídas e sobra do mês, entradas × saídas mês atual × anterior, tabela expansível Categoria › Subcategoria › Lançamentos, estado vazio com botão "Importar primeiro extrato". *Posição exata desses itens na grade fica para o construtor, respeitando a densidade minimalista.*

## 5. Contas a pagar (novo)

Modelo reaproveitado do Financeiro-2026 (apenas a ideia; não copiar código):
- Lançamento tem **data de vencimento** e **compensado** (pendente ou pago, com data de compensação).
- Pendente com vencimento passado = **vencida**. Pendente com vencimento futuro = **a vencer**.

**Parcelamento**
- Formulário recebe descrição, **valor total**, **número de parcelas**, primeiro vencimento, conta e categoria.
- O sistema cria N lançamentos pendentes, exibidos como "3/10". Divisão em centavos exatos; a última parcela absorve a diferença. *(decisão do construtor-assistente, confirmada implicitamente)*

**Contas fixas (aluguel, luz, internet)**
- Cadastro único: nome, dia de vencimento, categoria, conta, **valor estimado**.
- Todo mês o sistema gera o lançamento pendente marcado **"estimado"**, que entra nas projeções.
- Ao chegar a conta, o usuário informa o valor real e marca como pago; a marca "estimado" some.
- **Valor estimado do mês seguinte = último valor real confirmado** (decisão do dono). Se nunca houve valor real, usa o estimado do cadastro.
- Estimada e não paga após o vencimento aparece como vencida.

**Receitas recorrentes (decisão do dono)**
- Cadastro como o das contas fixas: nome, dia, valor estimado (ex.: salário), conta de destino.
- Geram lançamentos de entrada pendentes, marcados "estimado" até a confirmação, e entram na projeção.

**Casamento com o extrato (decisão do dono)**
- Ao importar, o sistema procura pendentes que correspondam a cada linha (valor, data próxima e descrição) e **propõe o casamento**; o pendente só é marcado como pago após **confirmação** do usuário. Linha sem casamento entra como lançamento novo. Sem dupla contagem.
- Desfazer importação reabre os pendentes que ela havia quitado.

**Cartões na v0.1 (decisão do dono)**
- Cartões são contas do tipo `cartao`, cadastradas à mão. Compras e parcelas de cartão são lançadas à mão.
- **Exceção ao CLAUDE.md seção 8:** até a v0.2 (faturas importadas), o `pagamento_fatura` **conta como despesa**, para os gastos de cartão não sumirem dos totais. Compras lançadas à mão em cartão não entram no saldo da conta corrente, para não contar duas vezes; a regra definitiva de despesas de cartão é rediscutida na v0.2.

## 6. Ajustes no esquema (sobre CLAUDE.md seção 7)

*Detalhes de colunas e nomes ficam para o construtor.* Necessário:
- `lancamentos`: vencimento, flag compensado + data de compensação, flag estimado, referências a parcelamento (grupo, parcela atual e total) e a conta fixa.
- Nova entidade de **contas fixas e receitas recorrentes** (nome, dia, valor estimado, tipo entrada/saída, categoria, conta, ativa).
- Lançamento pendente **não entra no saldo atual nem na conciliação**; entra só nas projeções. Importado do extrato nasce compensado.
- Regra do CLAUDE.md mantida: valores em centavos inteiros, deduplicação por chave, `data/` fora do Git.

## 7. Decisões de como construir

Decididas pelo dono:
- **Interface:** FastAPI + Jinja2 + HTMX. Gráficos com **ECharts incluído no projeto** (sem CDN, funciona offline).
- **Nome:** ConsultorFin.

Decididas pelo assistente (ajustáveis):
- **Categorias iniciais:** Moradia, Mercado, Transporte, Saúde, Lazer, Assinaturas, Educação, Outros; editáveis pelo usuário.
- Janela de "a vencer": 7 dias.
- **Horizonte da projeção:** escolhido pelo usuário num seletor (ex.: 3, 6 ou 12 meses; padrão 6). Escolha do dono: quantidade de meses definida previamente.
- **Contas iniciais:** duas contas correntes (a do Itaú com importação) e alguns cartões de crédito, todos cadastráveis pelo usuário com saldo inicial. Outras contas sem importação são lançadas à mão.
- **Datas:** o saldo considera item pago à mão na data de compensação; a conciliação com o extrato usa só lançamentos importados.
- **Contas fixas e receitas recorrentes:** geradas para o horizonte da projeção ao abrir o app, ajustando o dia 31 em meses curtos. Novo valor estimado vale só para os meses futuros ainda pendentes. O usuário pode editar, pular ou excluir um mês isolado e desativar o cadastro (remove os pendentes futuros).
- **Parcelas:** grupo de parcelas pendentes pode ser editado ou excluído como um todo.
- **Importação:** Excel e mapeamento manual de colunas ficam na v0.1, como no CLAUDE.md.
- **Testes:** o extrato de exemplo do Itaú é fabricado (anonimizado) para reproduzir R$ 2.148,08 → R$ 1.409,14.
- **Projeção:** saldo atual − pendentes (parcelas, fixas, estimadas) + receitas recorrentes pendentes.

Já definidas no CLAUDE.md: Python 3.11+, SQLite em `data/`, pandas e openpyxl, tema escuro, PT-BR, R$ 1.234,56 e DD/MM/AAAA na tela, ISO no banco.

## 8. Ordem de trabalho

Segue a seção 12 do CLAUDE.md, com **duas inserções**: (a) modelo de contas a pagar, parcelas e contas fixas, com testes, antes das telas; (b) tela Contas a pagar junto das demais, antes do Início. Cada etapa confirmada com o dono.

## 9. Deixado ao construtor

Estrutura exata das tabelas e migrações, cálculo da projeção, layout fino das grades, formato dos logs, textos de erro em português, escolha entre rotas e organização interna dentro das regras do CLAUDE.md (lógica em `dominio/`, só `db.py` acessa o banco).
