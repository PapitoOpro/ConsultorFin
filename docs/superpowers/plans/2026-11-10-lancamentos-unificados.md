# Lançamentos unificados e Cadastros — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unificar Lançamentos e Contas a pagar numa só tela e mover contas, recorrentes e categorias para uma tela Cadastros.

**Architecture:** Rotas finas em `app/web/rotas/`, regras em `app/dominio/`, SQL em `app/repo/`. Lançamentos ganha o parâmetro `aba` (todos / a-pagar / realizados) que traduz para `Filtros.status` e para um período próprio; o formulário único chama uma função de domínio que delega às criações existentes (manual, parcelamento, recorrente). Cadastros é uma página com três seções reaproveitando os templates atuais. Sem migração de banco.

**Tech Stack:** FastAPI, Jinja2, HTMX, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-11-10-lancamentos-unificados-design.md`

## Global Constraints

- Valores em centavos inteiros; datas ISO no banco, `DD/MM/AAAA` na tela.
- Interface em português do Brasil; tema escuro, só variáveis CSS de `tema.css`; sem emojis, sem CDN.
- Regras de negócio em `dominio/`, nunca nas rotas; SQL só em `repo/`.
- Type hints em funções públicas; `pytest` inteiro deve passar ao fim de cada tarefa.
- Repositório público: nenhum dado real em testes ou docs.
- Mensagens de erro em português, sem stack trace (padrão `_executar` das rotas atuais).

## Review Focus

- `GET /contas-a-pagar#vencidas` e outros links antigos com âncora: o redirecionamento 303 mantém a rota, a âncora é preservada pelo navegador.
- Aba A pagar com banco sem nenhuma conta cadastrada: não pode quebrar (estado vazio com link para Cadastros).
- Parcelado com total menor que o nº de parcelas, ou com sinal Entrada: erro em português, nada gravado.
- "Já pago" marcado com data futura: erro, nada gravado.
- Excluir/editar grupo de parcelas e pular recorrente devolvem à mesma aba e filtro (`voltar`).
- `abrir_painel` gerava as recorrentes ao abrir a tela; a aba A pagar precisa continuar gerando (idempotente).

---

### Task 1: Cadastros (contas, recorrentes, categorias)

**Files:**
- Create: `app/web/rotas/cadastros.py`, `app/web/templates/cadastros/index.html`, `app/web/templates/cadastros/_contas.html`, `app/web/templates/cadastros/_recorrentes.html`
- Modify: `app/web/rotas/categorias.py` (renderiza a página de Cadastros), `app/web/templates/categorias/index.html` → vira `cadastros/_categorias.html` (git mv), `app/web/rotas/__init__.py`, `app/web/templates/base.html:36-38`
- Test: `tests/test_web_cadastros.py`; atualizar `tests/test_web_categorias.py`, `tests/test_web_contas_pagar.py`, `tests/test_cheque_especial_conta.py`, `tests/test_web_design_system.py` conforme as novas rotas

**Interfaces:**
- Produces: `GET /cadastros?secao=contas|categorias|recorrentes` (padrão `contas`; `secao` inválida cai em `contas`); `POST /cadastros/conta`, `POST /cadastros/conta/{conta_id}/cheque-especial`, `POST /cadastros/recorrente`, `POST /cadastros/recorrente/{recorrente_id}/desativar` (mesmos campos de formulário das rotas atuais, sem `horizonte`); `GET /categorias` responde 303 para `/cadastros?secao=categorias&aba=<aba>`; os POSTs `/categorias/...` ficam como estão e re-renderizam Cadastros com `secao=categorias` (o `aba` entrada/saída continua sendo o filtro interno das categorias).
- Contexto de `cadastros/index.html`: `ativo="cadastros"`, `secao`, e o contexto que cada parcial já usa hoje (`contas`, `tipos_conta`, `cheque_cfg`, `recorrentes`, `categorias_saida`, `categorias_entrada`, `arvore`, `aba`, `todas`, `excluindo`).

- [ ] **Step 1: Escrever testes que falham** em `tests/test_web_cadastros.py`: `test_cadastros_abre_na_secao_contas`, `test_secao_invalida_cai_em_contas`, `test_categorias_get_redireciona_para_cadastros` (303 e `Location` com `secao=categorias&aba=entrada` quando `?aba=entrada`), `test_criar_conta_em_cadastros`, `test_configurar_cheque_especial_em_cadastros`, `test_criar_e_desativar_recorrente_em_cadastros`, `test_menu_mostra_cadastros_e_nao_categorias`.
- [ ] **Step 2: Rodar** `.venv/Scripts/python -m pytest tests/test_web_cadastros.py -q` — esperado: FAIL (404/rota inexistente).
- [ ] **Step 3: Criar `cadastros.py`** com `router`, a função `pagina_cadastros(request, con, secao, *, aviso, erro, status, aba, excluindo) -> HTMLResponse` e os quatro POSTs, copiando o corpo de `contas_pagar.nova_conta`, `configurar_cheque_especial`, `nova_recorrente`, `desativar` (chamam `contas_pagar_acoes`). Usar o mesmo padrão `_executar` (aviso/erro, 400/404/500).
- [ ] **Step 4: Templates.** Mover as seções `contas` e `recorrentes` de `contas_pagar/index.html` para `_contas.html` e `_recorrentes.html` (trocar `action="/contas-a-pagar/..."` por `/cadastros/...`, remover o campo `horizonte`); `git mv` de `categorias/index.html` para `cadastros/_categorias.html` (links internos `?aba=` passam a `/cadastros?secao=categorias&aba=`). `index.html` mostra abas por link (`<a>` com `aria-current`) e inclui a parcial da seção. O script do tipo da recorrente (`r-tipo`/`.cat-sinal`) vai junto da parcial.
- [ ] **Step 5: `categorias.py`:** `_pagina` passa a chamar `pagina_cadastros(..., secao="categorias", aba=aba, excluindo=excluindo)`; `tela` (GET) devolve `RedirectResponse(f"/cadastros?secao=categorias&aba={_aba(...)}", 303)`.
- [ ] **Step 6: `base.html`:** trocar o item "Categorias" por `item_menu("cadastros", "/cadastros", "Cadastros", "tags")`; registrar `cadastros.router` em `rotas/__init__.py`.
- [ ] **Step 7: Rodar** `.venv/Scripts/python -m pytest tests -q -k "cadastros or categorias or cheque or design"` — esperado: PASS (ajustar os testes antigos que apontavam para as rotas movidas).
- [ ] **Step 8: Commit** `git commit -m "feat: tela Cadastros (contas, recorrentes, categorias)"`.

### Task 2: Formulário único de novo lançamento (domínio)

**Files:**
- Modify: `app/dominio/lancamentos.py` (nova função, ao lado de `criar_manual_de_formulario`)
- Test: `tests/test_lancamento_avulso.py`

**Interfaces:**
- Consumes: `criar_manual_de_formulario(...)` (`lancamentos.py`), `contas_pagar_acoes.criar_parcelamento_de_formulario(...)`, `contas_pagar_acoes.criar_recorrente_de_formulario(...)`.
- Produces: `criar_avulso_de_formulario(con, *, modo: str, data: str, descricao: str, valor: str, sinal: str, conta_id: str, categoria_id: str, observacao: str, ja_pago: bool, n_parcelas: str, hoje: date) -> str` — devolve a mensagem de aviso. `modo` ∈ `avista | parcelado | recorrente`; modo inválido → `ErroLancamento("Escolha À vista, Parcelado ou Repetir todo mês.")`.

- [ ] **Step 1: Testes que falham** (`test_lancamento_avulso.py`): `test_avista_passado_ja_pago_fica_compensado`; `test_avista_futuro_fica_pendente`; `test_ja_pago_com_data_futura_e_recusado` (nada gravado); `test_parcelado_cria_n_parcelas_pendentes` (valor total 300,00 em 3 → 3 linhas de −100,00, `parcela_total == 3`); `test_parcelado_entrada_e_recusado`; `test_parcelado_ja_pago_e_recusado`; `test_recorrente_usa_dia_da_data_e_valor_estimado` (cria linha em `recorrentes` com `dia == data.day`); `test_recorrente_ja_pago_e_recusado`; `test_modo_invalido`.
- [ ] **Step 2: Rodar** `.venv/Scripts/python -m pytest tests/test_lancamento_avulso.py -q` — esperado: FAIL (função inexistente).
- [ ] **Step 3: Implementar** `criar_avulso_de_formulario`: `avista` → valida `ja_pago` (data > hoje → `ErroLancamento("Só é possível marcar como pago com data de hoje ou anterior.")`) e chama `criar_manual_de_formulario(compensado=ja_pago)`; `parcelado` → recusa `sinal != "saida"` ("Parcelamento só para saídas.") e `ja_pago`, delega a `criar_parcelamento_de_formulario` (valor = total, primeiro vencimento = `data`) e devolve o texto de aviso já usado na rota atual; `recorrente` → recusa `ja_pago`, delega a `criar_recorrente_de_formulario` (dia = dia de `data`, valor_estimado = `valor`, tipo = `sinal`). Importar `contas_pagar_acoes` dentro da função se houver import circular.
- [ ] **Step 4: Rodar** o mesmo comando — esperado: PASS.
- [ ] **Step 5: Commit** `git commit -m "feat: criar lançamento avulso, parcelado ou recorrente por um só formulário"`.

### Task 3: Lançamentos com abas e ações de pendentes

**Files:**
- Modify: `app/dominio/lancamentos.py` (`LinhaExtrato` + `listar`), `app/repo/lancamentos.py` (nenhuma mudança de SQL: `l.*` já traz as colunas), `app/dominio/contas_pagar.py` (`contar_vencidas`), `app/web/rotas/lancamentos.py`, `app/web/templates/lancamentos/index.html`
- Test: `tests/test_web_lancamentos_abas.py`, `tests/test_contas_pagar.py` (`contar_vencidas`)

**Interfaces:**
- Consumes: `criar_avulso_de_formulario` (Task 2); `acoes.pular`, `acoes.editar_grupo`, `acoes.excluir_grupo` (`contas_pagar_acoes.py`); `gerar_recorrentes(con, hoje, meses)` (`app/dominio/recorrentes.py`).
- Produces:
  - `LinhaExtrato` ganha `recorrente_id: int | None = None`, `grupo: str | None = None` (valor de `grupo_parcela_uuid`), `parcela_atual: int | None = None`, `parcela_total: int | None = None`; `listar` preenche a partir de `r["recorrente_id"]`, `r["grupo_parcela_uuid"]`, `r["parcela_atual"]`, `r["parcela_total"]`.
  - `contas_pagar.contar_vencidas(con, hoje: date) -> int` = `len(vencimentos(con, hoje, 0).vencidas)`.
  - `GET /lancamentos?aba=todos|a-pagar|realizados` (padrão `todos`; inválida → 400 com mensagem). `a-pagar`: `status=pendentes`, `de` vazio, `ate` = último dia do 6º mês a partir de hoje, chama `gerar_recorrentes(con, hoje, 6)` antes de listar; `realizados`: `status=compensados`. Parâmetros explícitos `de`/`ate`/`status` do usuário prevalecem sobre os padrões da aba. O `voltar` das ações inclui `aba`.
  - `POST /lancamentos/novo` passa a receber `modo`, `n_parcelas`, `ja_pago` (substitui `compensado`) e chama `criar_avulso_de_formulario`.
  - `POST /lancamentos/{id}/pular`, `POST /lancamentos/grupo/{grupo}/editar`, `POST /lancamentos/grupo/{grupo}/excluir` (campo `voltar`), mesmos efeitos e mensagens das rotas de Contas a pagar.
  - Contexto de template: `aba`, `n_vencidas`, `hoje`.

- [ ] **Step 1: Testes que falham** (`test_web_lancamentos_abas.py`): `test_aba_a_pagar_lista_pendentes_vencidos_e_futuros` (inclui vencida do mês passado, exclui compensados); `test_aba_realizados_so_compensados`; `test_aba_invalida_400`; `test_aba_a_pagar_gera_recorrentes`; `test_aba_a_pagar_sem_contas_mostra_estado_vazio`; `test_a_pagar_mostra_contador_e_destaque_de_vencidas`; `test_novo_parcelado_pelo_formulario_unico`; `test_novo_recorrente_pelo_formulario_unico`; `test_pular_recorrente_na_aba`; `test_excluir_grupo_volta_para_a_mesma_aba`; `test_linha_parcelada_mostra_etiqueta_3_de_10`; `test_fatura_na_aba_a_pagar_sem_botao_pagar`. Em `test_contas_pagar.py`: `test_contar_vencidas`.
- [ ] **Step 2: Rodar** `.venv/Scripts/python -m pytest tests/test_web_lancamentos_abas.py tests/test_contas_pagar.py -q` — esperado: FAIL.
- [ ] **Step 3: Domínio:** estender `LinhaExtrato`/`listar`; adicionar `contar_vencidas`.
- [ ] **Step 4: Rota:** função `_filtros_da_aba(params, aba, hoje) -> Filtros` (usa `Filtros.de_parametros` e aplica os padrões da aba só para chaves ausentes); `extrato` e `_pagina` passam `aba`/`n_vencidas`; incluir `aba` em `como_parametros`/`voltar`; novas rotas `pular` e de grupo reaproveitando `executar(...)`; `novo` usa `criar_avulso_de_formulario`.
- [ ] **Step 5: Template:** abas (links `<a>` com `aria-current`) acima dos filtros, com selo de vencidas em "A pagar"; faixa "N vencidas · R$ X" e classe de destaque em linhas pendentes com `data < hoje`; formulário único com seletor de modo (À vista / Parcelado em N / Repetir todo mês), campo `n_parcelas` visível só em Parcelado, "Já pago" visível só em À vista e marcado por padrão quando `data <= hoje` (JS leve junto do script existente); etiqueta `({{ l.parcela_atual }}/{{ l.parcela_total }})` na descrição; no painel de ações: "Pular este mês" quando `l.recorrente_id`, "Editar/Excluir pendentes do parcelamento" quando `l.grupo`; rótulo "Confirmar valor e pagar" para recorrente.
- [ ] **Step 6: Rodar** `.venv/Scripts/python -m pytest tests -q -k "lancamentos or contas_pagar or avulso"` — esperado: PASS.
- [ ] **Step 7: Commit** `git commit -m "feat: Lançamentos com abas Todos/A pagar/Realizados e criação unificada"`.

### Task 4: Remover Contas a pagar, redirecionar e atualizar links

**Files:**
- Delete: `app/web/rotas/contas_pagar.py`, `app/web/templates/contas_pagar/` (os dois templates)
- Create: redirecionamento em `app/web/rotas/lancamentos.py` (`GET /contas-a-pagar` → 303 `/lancamentos?aba=a-pagar`)
- Modify: `app/web/rotas/__init__.py`, `app/web/templates/base.html:36` (remover item; selo de vencidas em Lançamentos), `app/main.py` (context processor `n_vencidas`), `app/web/templates/inicio/index.html` (linhas 69, 70, 148, 152, 270, 285, 286, 292), `app/dominio/painel.py:340,417`, `docs/PASSO-A-PASSO.md`, `README.md`, `CLAUDE.md` (seção 8), `.claude/skills/vencimentos`
- Test: `tests/test_web_redirecionamentos.py`; ajustar `tests/test_web_inicio.py`, `tests/test_painel.py`, `tests/test_web_paginas_redesign.py`, `tests/test_web_base.py`

**Interfaces:**
- Consumes: `contar_vencidas` (Task 3), rotas de Cadastros (Task 1).
- Produces: `GET /contas-a-pagar` → 303 `/lancamentos?aba=a-pagar`; variável de template global `n_vencidas: int` (0 em qualquer falha); mapa de links: `#vencidas` e `#a-vencer` → `/lancamentos?aba=a-pagar`; `#contas` (cheque especial) → `/cadastros?secao=contas`; "Ver em Contas a pagar" → "Ver em Lançamentos" com `link="/lancamentos?aba=a-pagar"`.

- [ ] **Step 1: Testes que falham** (`test_web_redirecionamentos.py`): `test_contas_a_pagar_redireciona` (303, `Location` correto); `test_menu_nao_tem_contas_a_pagar`; `test_menu_mostra_selo_de_vencidas` (com uma pendente vencida, o item Lançamentos exibe o número); `test_menu_sem_vencidas_nao_mostra_selo`; `test_inicio_aponta_para_lancamentos_a_pagar` (nenhum `href="/contas-a-pagar"` no Início); `test_painel_links_apontam_para_lancamentos` (`link` dos itens de `painel.py`).
- [ ] **Step 2: Rodar** `.venv/Scripts/python -m pytest tests/test_web_redirecionamentos.py -q` — esperado: FAIL.
- [ ] **Step 3: Implementar:** remover o router e os templates de Contas a pagar; adicionar a rota de redirecionamento; context processor em `main.py` (`Jinja2Templates(..., context_processors=[...])` retornando `{"n_vencidas": n}` com `conectar(request.app.state.caminho_db)` de vida curta, `try/except Exception` → 0, log estruturado `event=n_vencidas_falhou`); selo no `item_menu` de Lançamentos; atualizar links do Início e de `painel.py`.
- [ ] **Step 4: Docs e skill:** atualizar `docs/PASSO-A-PASSO.md`, `README.md`, seção 8 do `CLAUDE.md` (menu: Início, Lançamentos, Importar, Holerites, Cadastros) e o texto da skill `vencimentos` para citar a aba A pagar.
- [ ] **Step 5: Suíte completa** `.venv/Scripts/python -m pytest -q` — esperado: tudo PASS (corrigir testes que ainda citem rotas antigas).
- [ ] **Step 6: Commit** `git commit -m "feat: remove Contas a pagar; redirecionamentos, selo de vencidas e links atualizados"`.

## Self-review

- Cobertura da spec: 1 → T3/T4; 2 → T2/T3; 3 → T3; 4 → T3; 5 → T3/T4; 6 → T1; 7 → T4. Sem lacunas.
- Consistência: `criar_avulso_de_formulario` (T2) é usada em T3; `contar_vencidas` (T3) em T4; `pagina_cadastros` (T1) em `categorias.py`.
- Proporção: 4 tarefas, assinaturas e testes nomeados; corpos de função ficam com o implementador.
