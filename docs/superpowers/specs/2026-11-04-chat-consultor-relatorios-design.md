# Chat do Consultor e relatórios em nova aba — decisões

Status: decisões fechadas em entrevista (grill-me) em 04/11/2026. Próximo passo: plano de implementação (`writing-plans`). Nenhum código foi alterado.

## Problema

O balão do Consultor abre um iframe com o terminal (ttyd/tmux, `CONSULTORFIN_TERMINAL_URL`, ver `app/config.py` `url_terminal()` e `app/web/static/js/consultor-balao.js`). Aparece o TUI do Claude Code (prompt `❯`, barra do tmux, "manual mode", aviso de update). O usuário quer algo com cara de chat, e que perguntas como "quanto gastei com saúde nos últimos 3 meses" respondam com uma aba nova com gráfico e informações.

## Decisões

### Chat
- **Abordagem C:** chat próprio é o padrão no painel; o terminal continua acessível por "Abrir terminal em nova aba".
- **Backend:** novo endpoint no FastAPI chama o Claude Code não interativo: `claude -p --output-format stream-json` com `--resume <session_id>`.
- **Sem histórico persistente.** A conversa vive só enquanto o painel está aberto (some ao recarregar). Dentro dela o Claude mantém contexto via `--resume`. Botão "Nova conversa" zera tudo. Nada é guardado no servidor além da sessão atual.
- **Streaming** palavra por palavra (SSE), com indicador "digitando…" até a primeira palavra.
- **Atividade das ferramentas:** uma linha discreta traduzida ("Consultando saldos…"); nunca comandos, JSON ou erro cru (regra do `CLAUDE.md`). Some quando a resposta termina.
- **Escritas** (`importar --confirmar`, `casar`, `categorizar`, `regra`, `pagar`, `criar-conta`, `gmail-buscar`) aparecem como cartão "Confirmar / Cancelar" no chat; consultas rodam direto. Formaliza o "sim" que o `CLAUDE.md` já exige.
- **Modelo:** Sonnet 5.5 por padrão, sem seletor na tela (custo).
- **Acesso:** mesma URL e mesmo Cloudflare Access do painel; nada novo exposto.
- **Renderização:** markdown (negrito, listas, tabelas) com sanitização obrigatória (textos de extrato e e-mail são dados não confiáveis); nada de HTML cru do modelo; valores monetários em fonte monoespaçada alinhados à direita.
- **Visual:** tema escuro atual (variáveis de `tema.css`, sem gradiente, neon nem emoji). Mensagens do usuário em bolha à direita (`--superficie-2`); as do consultor à esquerda, sem bolha pesada. Ícone de enviar via macro `icone()` de `templates/_icones.html`. O balão flutuante verde continua abrindo o painel.

### Contexto e segurança do chat
- O `CLAUDE.md` **não é cortado**: o modo desenvolvimento permanece (usuário quer evoluir o projeto).
- O chat roda com **ferramentas restritas**: só os comandos do consultor (`.venv/.../python -m app.consultor ...`, incluindo o novo `relatorio`). Sem Edit/Write nem shell livre. Evita que pergunta ou texto malicioso de extrato/e-mail mexa no código do servidor. Desenvolvimento continua no terminal ou VS Code.
- Mantidas as regras: nunca ler `data/gmail-token.json`, `data/senhas.key`, tabela `senhas_pdf`; nunca pedir credencial de banco.

### Relatório em nova aba
Inspirado em `Agent_totvs/totvs-food-linha-chef-gestor` (`scripts/painel.mjs`, skill `dashboard`): o modelo **não escreve HTML**, monta uma especificação.

- **Subcomando novo:** `python -m app.consultor relatorio` recebe a espec JSON (stdin ou arquivo). Um `relatorio exemplo` imprime uma espec modelo.
- **Espec:** `titulo`, `periodo`, `observacao` (insight em 1 frase, sempre incluir), `kpis` (com `delta_sql` do período anterior → seta ▲/▼), `graficos` (`barras`, `barras_h`, `linha`, `rosca`; rosca máx. 6 fatias, resto vira "Outros"), `tabela` (busca + ordenação por coluna, limite de linhas). Colunas por convenção: 1ª categoria/dia, 2ª valor, 3ª série (opcional). Sempre dar alias às colunas.
- **Dados:** SQL **somente leitura** (conexão SQLite `mode=ro`), só `SELECT`, uma instrução, limite de linhas, e tabela `senhas_pdf` **bloqueada no nível do banco** (authorizer). Alternativa descartada: fontes fixas (travam perguntas novas).
- **Geração:** HTML autossuficiente com dados já calculados (retrato do momento da pergunta), ECharts local + tema (`static/js/graficos-tema.js`, `ConsultorFinTema`), sem CDN. Todo texto escapado.
- **Entrega:** salvo em `data/relatorios/<id>.html` e servido pelo FastAPI em `/relatorio/<id>` atrás do Cloudflare Access, com CSP restritivo (sem scripts externos). Apagado sozinho após **30 dias**; **sem tela de lista**.
- **No chat:** cartão "Abrir relatório (Saúde, últimos 3 meses)" que abre em nova aba por clique do usuário. Não abre sozinho (bloqueio de pop-up).
- **Formato pt-BR:** `R$ 1.234,56`, `DD/MM/AAAA`, meses pelo nome (nunca `2026-01`), acentuação correta.

### Escopo da v1 do relatório
Inclui: `observacao`, KPIs com comparação ao período anterior, barras/linha/rosca, tabela de lançamentos com busca e ordenação.
**Fica para a etapa 2:** drill-down categoria → subcategoria → lançamentos (o esquema já tem `categorias.pai_id`; no Agent_totvs é `niveis` com `{{pai}}`) e "salvar como meu padrão".

## Sobre tokens (resposta dada na entrevista)
Compartilhar ou separar sessão pouco muda o consumo. O que pesa: histórico longo (use `--resume` + "Nova conversa"), custo de início frio (o `CLAUDE.md` é grande, e a decisão de manter o modo desenvolvimento foi do usuário), e modelo (Sonnet em vez de Opus). "Compartilhar" a sessão do terminal não é viável com `claude -p` (exigiria raspar o tmux).

## Pontos para o plano de implementação
1. Endpoint do chat (SSE) + gerenciamento de `session_id` por aba, com `--allowedTools` restrito e `--model sonnet`.
2. Mapeamento dos eventos `stream-json` para: texto, linha de status traduzida, cartão de confirmação, cartão de relatório.
3. Como o cartão "Confirmar/Cancelar" interrompe e retoma o `claude -p` (modo de permissão do CLI vs. confirmação no próprio chat). **Ainda precisa ser investigado.**
4. UI do chat em `base.html`/`consultor-balao.js` + CSS no `tema.css`; manter o redimensionamento atual do painel; terminal como fallback.
5. `app/consultor/relatorio.py` (validação da espec, SQL somente leitura com authorizer, render), rota `/relatorio/<id>`, limpeza de 30 dias, skill `relatorio` em `.claude/skills/`, atualização do `CLAUDE.md` (lista de skills e regras do relatório).
6. Testes: `test_consultor_relatorio.py` (SQL bloqueado, `senhas_pdf` negada, escape de HTML, CSP), `test_web_consultor_chat.py`, ajuste de `test_web_consultor_balao.py`.
7. Conferir com o design system (`test_web_design_system.py`): sem gradiente, sem emoji, cores só das variáveis.
