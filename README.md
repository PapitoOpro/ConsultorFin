# ConsultorFin

Painel financeiro pessoal com um consultor de IA. Importa extratos (OFX, CSV, Excel), faturas de cartão e holerites (PDF), mostra saldo, vencimentos e projeção, e responde perguntas em português. Roda no seu computador ou numa VPS própria.

O painel em si não faz chamadas externas. O consultor usa o Claude Code: os dados que ele consulta são processados por esse serviço. Gmail (opcional) é só leitura. Credenciais de banco nunca entram no sistema.

Guia de uso completo: [docs/PASSO-A-PASSO.md](docs/PASSO-A-PASSO.md).

## Requisitos
- Python 3.11+

## Instalar e executar
**Windows:** dê dois cliques em `iniciar.bat`. Na primeira vez ele cria o ambiente e instala as dependências; depois abre o painel no navegador (http://localhost:8000). Outra porta: `iniciar.bat 8001`.

**Linux/macOS (ou manual):**
```
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:create_app --factory --port 8000
```
No Windows manual, use `.venv\Scripts\python` no lugar de `.venv/bin/python`.

## Telas
Início, Lançamentos, Importar, Holerites, Contas a pagar e Categorias. Um balão no canto abre o chat do Consultor.

## Consultor
- **Chat no painel** (`CONSULTORFIN_CHAT=1`): conversa com streaming; consultas rodam direto e escritas (importar, casar, categorizar, regras, pagar, criar conta, buscar no Gmail) viram um cartão **Confirmar / Cancelar**. A conversa vive só enquanto o painel está aberto. Pedidos de gráfico geram um **relatório** em nova aba.
- **Terminal / Claude Code:** abra a pasta no Claude Code e converse em português. Skills: `saldo`, `holerite`, `vencimentos`, `projecao`, `gastos`, `importar`, `categorizar`, `painel`, `faturas-gmail`, `relatorio`. Sem `CONSULTORFIN_CHAT`, o balão abre o terminal web (`CONSULTORFIN_TERMINAL_URL`).
- Escritas só com o seu "sim". Ele nunca pede nem guarda senha, token ou chave de banco; senhas de PDF nunca passam pelo Claude.
- Privacidade: o Claude vê os dados que consulta; no terminal, o histórico fica salvo no computador.

Detalhes: seções 8 a 10 de [docs/PASSO-A-PASSO.md](docs/PASSO-A-PASSO.md).

## Faturas do Gmail (opcional)
O consultor busca e-mails de fatura (Itaú, Nubank, Porto, Mercado Pago), baixa os PDFs e monta uma **fila na tela Importar**: você decide Importar ou Pular e informa a senha. Acesso somente leitura. Atenção: o e-mail da Nubank é reconhecido, mas o PDF dela ainda não é lido (só Itaú, Porto e Mercado Pago); use Pular. Configuração única: [docs/GMAIL-CONFIGURACAO.md](docs/GMAIL-CONFIGURACAO.md).

## Onde ficam os dados
Tudo em `data/` (ignorado pelo git):
- `consultorfin.db`: banco SQLite. Backup = copiar esse arquivo.
- `senhas.key`: chave que cifra as senhas de PDF guardadas (AES-256-GCM). Sem ela, as senhas guardadas ficam ilegíveis.
- `gmail-token.json`: token somente leitura do Gmail.
- `fila/`: PDFs temporários da fila do Gmail (expiram em 7 dias).
- `relatorios/`: retratos dos relatórios (expiram em 30 dias).

## Configuração (variáveis de ambiente)
| Variável | Efeito |
|---|---|
| `CONSULTORFIN_HOSTS` | Nomes de Host extras aceitos, separados por vírgula |
| `CONSULTORFIN_CHAT` | `1` liga o chat próprio do consultor no painel |
| `CONSULTORFIN_CLAUDE_BIN` | Caminho do executável `claude` usado pelo chat (padrão: `claude` no PATH) |
| `CONSULTORFIN_TERMINAL_URL` | URL https do terminal web do consultor (balão ou link "Abrir terminal") |
| `CONSULTORFIN_GMAIL_TOKEN` | Caminho do token do Gmail (padrão `data/gmail-token.json`) |
| `CONSULTORFIN_SENHAS_KEY` | Caminho da chave das senhas de PDF (padrão `data/senhas.key`) |

## Produção (VPS)
O app não tem login: nunca exponha a porta diretamente. Em produção roda como serviço systemd (uvicorn em 127.0.0.1) atrás de Cloudflare Tunnel + Cloudflare Access (e-mail com código). Atualizar: `git pull` e reiniciar o serviço (as migrações rodam na subida). O chat sobe com limite de memória no serviço (o `claude -p` roda dentro dele) e só liga se o porteiro aceitar a configuração do `.claude/` (sem `permissions.allow`, `hooks`, `env` ou `disableAllHooks` em `settings*.json`). Backup semanal criptografado do banco via rclone (cron).

O repositório é público: nunca versione extratos, faturas, holerites, banco, chaves ou tokens.

## Importar o primeiro extrato (Itaú OFX ou CSV)
1. Crie a conta informando o saldo na data do primeiro lançamento do extrato.
2. Em Importar, envie o extrato (OFX baixado do Itaú, recomendado, ou CSV).
3. Confira a prévia e confirme.
4. Verifique o aviso "Saldo confere com o banco".

## Faturas e holerites
- **Fatura de cartão (PDF Itaú, Porto, Mercado Pago):** Importar → prévia com conferência do total → confirmar. A senha do PDF pode ser guardada cifrada por cartão ("Lembrar a senha deste cartão"). O pagamento da fatura é casado com a saída do extrato; casado, não conta como despesa em dobro. O consultor só consulta.
- **Holerite (PDF, layouts Adiantamento e Folha Mensal):** prévia com conferência dos totais e do crédito do líquido; grava só ao confirmar; o arquivo não é guardado.

## Conceitos
- **Pendente vs compensado**: lançamento previsto ainda não confirmado no banco vs já efetivado.
- **Parcelamento**: compra dividida em parcelas mensais, cada uma um lançamento.
- **Contas fixas / receitas recorrentes**: modelos que geram lançamentos pendentes a cada mês.
- **Casamento com o extrato**: o app propõe ligar linhas do extrato a itens já lançados; você confirma, e o item não é contado duas vezes.
- **Consumo vs caixa**: gasto por categoria segue a data da compra (itens da fatura); saídas seguem a data do pagamento.
- **Resíduo da fatura**: saldo da fatura anterior carregado após pagamento parcial; não é compra e não conta como gasto.

## Testes
```
pytest
```

## Limitações conhecidas
- Itaú CSV e OFX (conta corrente) têm detecção automática; outros formatos via mapeamento manual.
- OFC e OFX de cartão de crédito não são suportados. Faturas de cartão entram em PDF, só pelo painel ou pela fila do Gmail; PDF escaneado (imagem) não é lido.
- Pagamento de fatura só é casado automaticamente com valor exato e candidata única; pagamento parcial exige confirmação, e o prazo do saldo financiado pode ficar impreciso.
- Os links "Ver no extrato" do Início são aproximados.
- Linhas idênticas no mesmo dia em arquivos que cortam o dia ao meio podem ser chaveadas de forma diferente (exporte dias inteiros).
- Só a importação mais recente de cada conta pode ser desfeita.
- Sem login: use só localmente ou atrás de um proxy autenticado (Cloudflare Access).
