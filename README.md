# ConsultorFin

Painel financeiro pessoal, local. O app em si não faz nenhuma chamada externa. Se você optar por usar o consultor pelo Claude Code, os dados que o Claude consulta são processados por esse serviço.

Guia de uso completo: [docs/PASSO-A-PASSO.md](docs/PASSO-A-PASSO.md).

## Requisitos
- Python 3.11+

## Instalar e executar
**Windows:** dê dois cliques em `iniciar.bat`. Na primeira vez ele cria o ambiente e instala as dependências; depois abre o painel no navegador (http://localhost:8000). Não precisa ativar o ambiente nem liberar scripts do PowerShell. Outra porta: `iniciar.bat 8001`.

**Linux/macOS (ou manual):**
```
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:create_app --factory --port 8000
```
No Windows manual, use `.venv\Scripts\python` no lugar de `.venv/bin/python`. Abra http://localhost:8000.

## Categorias
Categorias em dois níveis (categoria › subcategoria), com tipo entrada ou saída, editáveis na tela **Categorias** do painel. Antes de atualizar para esta versão, copie `data/consultorfin.db` como backup. Detalhes: [docs/PASSO-A-PASSO.md](docs/PASSO-A-PASSO.md).

## Consultor pelo Claude Code
Abra a pasta no Claude Code e converse em português. Skills: `saldo`, `vencimentos`, `projecao`, `gastos`, `importar`, `categorizar`, `painel`.
Escritas (importar, casar, categorizar, regras, pagar, criar conta) só com o seu "sim".
Ele nunca pede nem guarda senha, token ou chave de banco; só importa arquivos de extrato.
Privacidade: o Claude vê os dados que consulta e o histórico das conversas fica salvo no seu computador.
Detalhes: seção 8 de [docs/PASSO-A-PASSO.md](docs/PASSO-A-PASSO.md).

## Onde ficam os dados
`data/consultorfin.db` (SQLite, ignorado pelo git). Backup = copiar esse arquivo.

## Importar o primeiro extrato (Itaú OFX ou CSV)
1. Crie a conta informando o saldo na data do primeiro lançamento do extrato.
2. Em Importar, envie o extrato (OFX baixado do Itaú, recomendado, ou CSV).
3. Confira a prévia e confirme.
4. Verifique o aviso "Saldo confere com o banco".

## Conceitos
- **Pendente vs compensado**: lançamento previsto ainda não confirmado no banco vs já efetivado.
- **Parcelamento**: compra dividida em parcelas mensais, cada uma um lançamento.
- **Contas fixas / receitas recorrentes**: modelos que geram lançamentos pendentes a cada mês.
- **Casamento com o extrato**: o app propõe ligar linhas do extrato a itens já lançados (pendentes ou pagos à mão); você confirma, e o item não é contado duas vezes.

## Testes
```
pytest
```

## Configuração
`CONSULTORFIN_HOSTS`: nomes de Host extras aceitos, separados por vírgula.

## Limitações conhecidas da v0.1
- Itaú CSV e OFX (conta corrente) têm detecção automática; outros formatos via mapeamento manual.
- OFC e OFX de cartão de crédito não são suportados: o app recusa com uma mensagem. Faturas de cartão entram em PDF (Itaú, Porto e Mercado Pago), só pelo painel; PDF escaneado (imagem) não é lido.
- Pagamento de fatura casado com uma fatura importada não conta como despesa (as compras contam pelos itens da fatura). Sem casar, continua contando como despesa.
- Os links "Ver no extrato" do Início são aproximados.
- Linhas idênticas no mesmo dia em arquivos que cortam o dia ao meio podem ser chaveadas de forma diferente (exporte dias inteiros).
- Só a importação mais recente de cada conta pode ser desfeita.
- Sem login (uso local): não exponha a porta na rede.
