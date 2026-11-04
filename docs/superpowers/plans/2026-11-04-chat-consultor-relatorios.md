# Chat do Consultor e relatórios em nova aba — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trocar o terminal embutido do balão por um chat de verdade e permitir que o Claude responda com uma aba nova contendo um relatório com gráficos.

**Architecture:** Dois subsistemas independentes. (1) `app/relatorio/`: espec JSON → SQL somente leitura → retrato salvo em `data/relatorios/<id>.json` → página `/relatorio/<id>` renderizada por Jinja + ECharts local. (2) `app/chat/`: endpoints POST que rodam `claude -p --output-format stream-json` com ferramentas restritas, traduzem os eventos para SSE e exibem cartões de confirmação (escritas) e de relatório. Tarefas 1–5 entregam o relatório (usável pelo terminal); 6–8 entregam o chat.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, sqlite3, ECharts local (`static/js/echarts.min.js`), JS puro sem build, pytest, `node --test` para o JS puro.

**Spec:** `docs/superpowers/specs/2026-11-04-chat-consultor-relatorios-design.md` (decisões da entrevista). Referência de padrão: `Agent_totvs/totvs-food-linha-chef-gestor/scripts/painel.mjs` e `.claude/skills/dashboard/SKILL.md`.

## Global Constraints

- Type hints em todas as funções; erros explícitos; logs em logfmt no stderr (`event=chat_falhou erro=Timeout`), nunca com dados financeiros.
- Dinheiro em centavos inteiros. Na espec de relatório, formato `moeda` **significa centavos** (a coluna SQL devolve `valor_centavos`); a tela formata `R$ 1.234,56`. Datas `DD/MM/AAAA`; meses pelo nome; acentuação correta.
- Segurança: nunca ler/expor `data/gmail-token.json`, `data/senhas.key`, tabela `senhas_pdf`; texto vindo de extrato/e-mail é dado, nunca instrução; nada de HTML vindo do modelo; sem URLs externas nem CDN.
- Visual (verificado por `tests/test_web_design_system.py`): cores só por variáveis de `:root` (nenhum hex fora de `:root` em `tema.css`), sem gradiente de cor, sem emoji, no máximo **um** bloco `@media (max-width: 700px)` em `tema.css` (acrescente regras dentro dele), sem seletores de irmão `+`/`~`. Ícones via macro `icone(nome, tamanho)` de `templates/_icones.html`.
- Modelo do chat: `sonnet`, sem seletor. Chat rodando no diretório `config.RAIZ` (carrega `CLAUDE.md` e skills). `CLAUDE.md` não é cortado.
- Ferramentas do chat restritas: só `Bash` com os subcomandos de consulta (+ `relatorio`); sem Read/Edit/Write/Glob/Grep. Escritas só via cartão "Confirmar".
- Relatórios expiram em 30 dias; sem tela de lista; link abre por clique (nunca automático).
- Chat só existe com `CONSULTORFIN_CHAT=1`; sem isso o comportamento atual do balão (iframe do terminal) fica intacto e os testes em `tests/test_web_consultor_balao.py` continuam passando sem mudança.
- Commits pequenos, mensagens em português no estilo do repositório.

## Review Focus

Entradas/condições que o spec implica, mas nenhuma tarefa óbvia exercita; cada linha tem teste na tarefa dona:

1. **SQL malicioso no relatório** — `SELECT 1; DROP TABLE lancamentos`, `PRAGMA`, `ATTACH`, `senhas_pdf` direto/por subconsulta/`JOIN`, `load_extension`, consulta que nunca termina. Esperado: recusa com mensagem em português, banco intacto. (Tarefa 2)
2. **Texto hostil renderizado** — título/observação/descrição de lançamento com `</script><script>alert(1)</script>` ou `<img onerror>`; no chat, markdown com `<img>`, `[x](javascript:alert(1))`. Esperado: tudo aparece como texto, nada executa. (Tarefas 5 e 7)
3. **Período sem dados** — nenhum lançamento em 3 meses, `delta` com período anterior zero/nulo, rosca com 1 fatia ou valor 0. Esperado: estado vazio legível, sem `NaN`/divisão por zero. (Tarefas 3 e 5)
4. **Concorrência e repetição no chat** — duas mensagens simultâneas na mesma sessão, duplo clique em "Confirmar" (escrita executaria 2×), processo `claude` travado, cliente que fecha a aba no meio do stream. Esperado: 2ª requisição recusada com aviso amigável, token de confirmação de uso único, timeout encerra o processo. (Tarefa 6)
5. **Entradas de identificação forjadas** — `sessao` que não é UUID (injeção de flag em `--resume`), token de confirmação de outra sessão, `id` de relatório com `../`, mensagem vazia/enorme/começando com `-`. Esperado: 422/404, nenhum processo iniciado. (Tarefas 5 e 6)

## File Structure

| Arquivo | Responsabilidade |
|---|---|
| `app/config.py` (modificar) | `chat_ativo()`, `caminho_claude()`, `DIR_RELATORIOS`, constantes do chat/relatório |
| `app/relatorio/__init__.py` | pacote |
| `app/relatorio/espec.py` | `Espec` + `validar_espec()`; `ErroRelatorio` |
| `app/relatorio/consulta.py` | `executar_select()` — SQLite `mode=ro`, authorizer, limite, timeout |
| `app/relatorio/montar.py` | `gerar_relatorio()` — resolve KPIs/gráficos/tabela e grava o retrato; `limpar_expirados()`; `ler_relatorio()` |
| `app/consultor/cli.py` (modificar) | subcomando `relatorio` (+ `relatorio exemplo`) |
| `app/web/rotas/relatorio.py` | `GET /relatorio/{rid}` com CSP |
| `app/web/templates/relatorio.html` | página standalone (tema, KPIs, gráficos, tabela) |
| `app/web/static/js/relatorio.js` | monta gráficos (ECharts + `ConsultorFinTema`) e tabela com busca/ordenação |
| `.claude/skills/relatorio/SKILL.md` | quando/como o Claude monta a espec |
| `app/chat/__init__.py` | pacote |
| `app/chat/argv.py` | `montar_argv()` — flags do `claude -p` |
| `app/chat/eventos.py` | `traduzir_linha()` — linha stream-json → eventos da UI |
| `app/chat/estado.py` | `EstadoChat` — sessões ocupadas e confirmações pendentes |
| `app/chat/processo.py` | `executar_claude()` — subprocesso assíncrono com timeout |
| `app/web/rotas/chat.py` | `POST /consultor/mensagem`, `POST /consultor/confirmar` (SSE) |
| `app/web/static/js/chat-markdown.js` | `parseMarkdown()` (puro) + `renderBlocos()` (DOM por `textContent`) |
| `app/web/static/js/consultor-chat.js` | UI do chat, fetch streaming, cartões, "Nova conversa" |
| `app/web/static/js/consultor-balao.js`, `templates/base.html`, `static/css/tema.css`, `templates/_icones.html` (modificar) | modo chat no painel, estilos, ícone `send` |
| `tests/…` | ver cada tarefa; JS puro em `tests/js/chat-markdown.test.mjs` |

---

### Task 1: Espec do relatório (validação)

**Files:**
- Create: `app/relatorio/__init__.py`, `app/relatorio/espec.py`
- Modify: `app/config.py` (constantes: `LIMITE_LINHAS_RELATORIO = 2000`, `DIAS_EXPIRACAO_RELATORIO = 30`, `DIR_RELATORIOS = RAIZ / "data" / "relatorios"`)
- Test: `tests/test_relatorio_espec.py`

**Interfaces:**
- Produces: `class ErroRelatorio(ValueError)`; `@dataclass(frozen=True) class Espec` com campos `titulo: str`, `periodo: tuple[str, str] | None`, `observacao: str`, `kpis: tuple[Kpi, ...]`, `graficos: tuple[Grafico, ...]`, `tabela: Tabela | None`; `Kpi(rotulo, formato, sql, delta_sql: str | None)`, `Grafico(tipo, titulo, formato, largura, sql)`, `Tabela(titulo, sql)`; `validar_espec(bruto: object) -> Espec`; `ESPEC_EXEMPLO: dict[str, Any]` (usada por `relatorio exemplo`).

Regras pinadas: `titulo` 1–120 chars; `observacao` obrigatória, 1–300; `kpis` ≤ 5 (`formato` ∈ `moeda|inteiro|pct`); `graficos` ≤ 6 (`tipo` ∈ `linha|barras|barras_h|rosca`, `formato` idem, `largura` ∈ `cheia|meia`, padrão `meia`); `tabela` opcional; todo `sql` string não vazia ≤ 4000; `periodo.de/ate` ISO válidos e `de <= ate`; chaves desconhecidas são erro. Mensagens de erro em português.

- [ ] **Step 1: Write the failing tests** — `test_espec_minima_valida`, `test_exemplo_e_valido` (`validar_espec(ESPEC_EXEMPLO)`), e `pytest.raises(ErroRelatorio, match=...)` para: sem `observacao`, 6 KPIs, `tipo="pizza"`, `formato="euro"`, `sql=""`, chave extra `"html"`, `periodo` invertido, `bruto` não-dict, título com 121 chars.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_relatorio_espec.py -v` → FAIL (módulo inexistente).
- [ ] **Step 3: Implement** `validar_espec` em `app/relatorio/espec.py` (sem pydantic; funções pequenas por bloco, lançando `ErroRelatorio`).
- [ ] **Step 4: Run** o mesmo comando → PASS.
- [ ] **Step 5: Commit** `feat: valida a especificação de relatório do consultor`

### Task 2: SQL somente leitura

**Files:**
- Create: `app/relatorio/consulta.py`
- Test: `tests/test_relatorio_consulta.py` (usa `criar_banco(tmp_path / "t.db", com_extrato=True)` de `tests/_consultor_base.py`)

**Interfaces:**
- Produces: `@dataclass(frozen=True) class Resultado: colunas: tuple[str, ...]; linhas: tuple[tuple[Any, ...], ...]; truncado: bool`; `executar_select(caminho_db: Path, sql: str, limite: int = config.LIMITE_LINHAS_RELATORIO, timeout_s: float = 5.0) -> Resultado`.

Abordagem: abrir com `sqlite3.connect(f"file:{caminho.as_posix()}?mode=ro", uri=True)`; `con.set_authorizer(cb)` que devolve `SQLITE_OK` só para `SQLITE_SELECT`, `SQLITE_FUNCTION` e `SQLITE_READ` — e `SQLITE_DENY` para `SQLITE_READ` da tabela `senhas_pdf` e para tudo o mais (PRAGMA, ATTACH, INSERT…); `set_progress_handler` interrompe após `timeout_s`; várias instruções já falham no `execute` (capturar `sqlite3.Warning`/`ProgrammingError`); `fetchmany(limite + 1)` define `truncado`. Qualquer `sqlite3.Error` vira `ErroRelatorio("Consulta recusada: <motivo curto em português>")` sem eco do SQL.

- [ ] **Step 1: Write the failing tests** — `test_select_simples_devolve_colunas_e_linhas` (`SELECT COUNT(*) AS n FROM lancamentos` → `colunas == ("n",)`), `test_limite_marca_truncado` (`limite=3`), e `pytest.raises(ErroRelatorio)` para: `SELECT 1; SELECT 2`, `DELETE FROM lancamentos`, `PRAGMA user_version`, `ATTACH DATABASE ':memory:' AS x`, `SELECT * FROM senhas_pdf`, `SELECT (SELECT COUNT(*) FROM senhas_pdf) AS n`, `SELECT * FROM lancamentos JOIN senhas_pdf`, `SELECT load_extension('x')`, e `WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM r) SELECT COUNT(*) FROM r` com `timeout_s=0.2`. Último teste: depois de todas as tentativas, `SELECT COUNT(*)` de `lancamentos` ainda igual (banco intacto).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_relatorio_consulta.py -v` → FAIL.
- [ ] **Step 3: Implement** `executar_select` conforme a abordagem acima.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: consulta SQL somente leitura para relatórios`

### Task 3: Montar e armazenar o retrato

**Files:**
- Create: `app/relatorio/montar.py`
- Test: `tests/test_relatorio_montar.py`

**Interfaces:**
- Consumes: `Espec`, `executar_select` (Tarefas 1–2).
- Produces: `gerar_relatorio(caminho_db: Path, espec: Espec, pasta: Path, agora: datetime) -> dict[str, str]` → `{"id": <uuid4 hex 32>, "titulo": ..., "url": "/relatorio/<id>"}`; `ler_relatorio(pasta: Path, rid: str) -> dict[str, Any] | None`; `limpar_expirados(pasta: Path, agora: datetime, dias: int = config.DIAS_EXPIRACAO_RELATORIO) -> int` (apaga por `mtime`, devolve quantidade).

Formato do arquivo `<id>.json` (retrato): `{"titulo", "periodo": {"de","ate"}|null, "observacao", "gerado_em": ISO, "kpis": [{"rotulo","formato","valor": int|null,"delta_pct": float|null}], "graficos": [{"tipo","titulo","formato","largura","linhas": [{"nome": str,"valor": int,"serie": str|null}]}], "tabela": {"titulo","colunas": [str],"linhas": [[...]],"truncado": bool}|null}`. Convenção de colunas do SQL: 1ª = nome (datas ISO viram `DD/MM/AAAA` via `iso_para_br`; meses `AAAA-MM` viram nome do mês), 2ª = valor, 3ª = série opcional. `kpi.valor` = primeira coluna da primeira linha; `delta_pct = round((valor - anterior) / abs(anterior) * 100, 1)` ou `None` se `anterior` for `None`/0. `rosca` com mais de 6 fatias: 5 maiores + "Outros" (soma). Escrita atômica (arquivo temporário + `os.replace`). `ler_relatorio` devolve `None` para `rid` que não bata `^[0-9a-f]{32}$` ou inexistente.

- [ ] **Step 1: Write the failing tests** — `test_gera_e_le_retrato` (espec com 1 KPI `SELECT COUNT(*) AS n FROM lancamentos`, 1 rosca, tabela; confere chaves e `url`), `test_delta_pct` (valor 150 vs anterior 100 → `50.0`), `test_delta_none_quando_anterior_zero`, `test_rosca_agrupa_em_outros` (8 categorias → 6 fatias, última "Outros"), `test_periodo_sem_dados_nao_quebra` (SQL com `WHERE 1=0` → `valor None`, `linhas []`), `test_rid_invalido_devolve_none` (`"../x"`, `"A"*32`), `test_limpar_expirados` (arquivo com `os.utime` 31 dias atrás some, de 1 dia fica), `test_sql_recusado_propaga_erro_relatorio`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_relatorio_montar.py -v` → FAIL.
- [ ] **Step 3: Implement** `app/relatorio/montar.py`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: gera e guarda o retrato do relatório`

### Task 4: Subcomando `relatorio` no CLI + skill

**Files:**
- Modify: `app/consultor/cli.py` (parser e `COMANDOS`), `CLAUDE.md` (lista "Skills disponíveis" e uma linha em "Como consultar")
- Create: `.claude/skills/relatorio/SKILL.md`
- Test: `tests/test_consultor_relatorio_cli.py`; estender `tests/test_consultor_skills.py` se ele valida a lista de skills.

**Interfaces:**
- Consumes: `validar_espec`, `gerar_relatorio` (Tarefas 1, 3).
- Produces: `python -m app.consultor relatorio --spec <arquivo|-> ` → stdout `{"relatorio_id": "...", "titulo": "...", "url": "/relatorio/<id>"}`; `relatorio exemplo` → imprime `ESPEC_EXEMPLO`. `--spec -` lê stdin. Erros de espec/SQL viram `{"erro": ...}` código 1 (já tratado por `main`: `ErroRelatorio` é `ValueError`).

O comando não escreve no banco (conexão `mode=ro`); entra em `COMANDOS`, não em `ESCRITAS`. A chave `relatorio_id` é o contrato que a Tarefa 6 usa para exibir o cartão.

SKILL.md (pt-BR, sem jargão para o usuário): quando usar ("gráfico", "relatório", "me mostra visualmente", "últimos N meses"); processo: 1) calcular período; 2) montar espec com `observacao` de 1 frase **sempre**; 3) SQL sobre `lancamentos`/`categorias` (leia `app/consultor/consultas.py` função `gastos` e copie a regra de quais tipos contam como despesa e de `pagamento_fatura` casado); valores em `valor_centavos`, gastos como `-SUM(...)`, sempre com alias; 4) rodar o comando; 5) responder em 2–3 frases com o insight e **não** repetir URL (o chat exibe o cartão). Nunca mostrar SQL ao usuário.

- [ ] **Step 1: Write the failing tests** — `test_relatorio_exemplo_imprime_json_valido`; `test_relatorio_gera_arquivo_e_devolve_id` (banco de `criar_banco`, espec mínima via `--spec` em `tmp_path`, `monkeypatch.setattr(config, "DIR_RELATORIOS", tmp_path / "r")`, confere stdout e arquivo); `test_espec_invalida_devolve_erro_em_portugues` (código 1, `"erro"` presente, sem "Traceback").
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_consultor_relatorio_cli.py -v` → FAIL.
- [ ] **Step 3: Implement** o subparser (`--spec` obrigatório exceto no `exemplo`, `acao` posicional opcional com `choices=["exemplo"]`) e a função `_relatorio` em `cli.py`, seguindo o padrão de `_faturas`; escrever a skill e atualizar `CLAUDE.md`.
- [ ] **Step 4: Run** o teste novo + `.venv/Scripts/python -m pytest tests/test_consultor_cli.py tests/test_consultor_skills.py -v` → PASS.
- [ ] **Step 5: Commit** `feat: subcomando relatorio e skill`

### Task 5: Página `/relatorio/<id>`

**Files:**
- Create: `app/web/rotas/relatorio.py`, `app/web/templates/relatorio.html`, `app/web/static/js/relatorio.js`
- Modify: `app/web/rotas/__init__.py` (registrar), `app/main.py` (chamar `limpar_expirados(config.DIR_RELATORIOS, datetime.now())` no `lifespan`; `app.state.dir_relatorios` para testes), `app/web/static/css/tema.css` (seção `.relatorio-*`)
- Test: `tests/test_web_relatorio.py`

**Interfaces:**
- Consumes: `ler_relatorio`, `gerar_relatorio` (Tarefa 3).
- Produces: `GET /relatorio/{rid}` → 200 HTML ou 404 com texto "Relatório não encontrado ou expirado."; cabeçalhos: `Content-Security-Policy: default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`.

Template standalone (não estende `base.html`): `tema.css`, `echarts.min.js`, `graficos-tema.js`, `relatorio.js` por `/static/...?v=`; dados em `<script type="application/json" id="dados-relatorio">{{ dados | tojson }}</script>`; título, `observacao`, cartões de KPI (seta ▲/▼ + texto "vs período anterior", nunca só cor), blocos de gráfico, tabela com `<input type="search">` e cabeçalhos clicáveis (ordenar), valores monetários em `--fonte-dados` alinhados à direita, rodapé "Retrato gerado em DD/MM/AAAA HH:MM". `relatorio.js` lê o JSON via `textContent` + `JSON.parse`, usa `window.ConsultorFinTema` (confira a API em `graficos-tema.js`) e constrói tabelas/KPIs com `createElement`/`textContent` (nunca `innerHTML`). Estado vazio com o componente de estado vazio existente (veja `_macros.html`).

- [ ] **Step 1: Write the failing tests** (cliente: `TestClient(create_app(tmp_path / "t.db"))`, `app.state.dir_relatorios` apontando para `tmp_path`): `test_relatorio_existente_200_com_csp` (confere cada diretiva acima); `test_texto_hostil_nao_executa` (título `</script><script>alert(1)</script>` e linha de tabela `<img src=x onerror=alert(1)>` → resposta não contém `<script>alert(1)` nem `<img src=x`); `test_id_invalido_404` (`/relatorio/..%2Fx`, `/relatorio/ZZZ`); `test_expirado_ou_inexistente_404`; `test_sem_urls_externas` (regex `https?://` ausente no HTML); `test_relatorio_vazio_200` (retrato com `linhas: []` e `valor: null`).
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_web_relatorio.py -v` → FAIL.
- [ ] **Step 3: Implement** rota (id validado por `ler_relatorio`), template, JS e CSS. Se o ECharts exigir `style` inline bloqueado pelo CSP, relaxe **somente** `style-src` para `'self' 'unsafe-inline'` e ajuste o teste da diretiva, registrando o motivo em comentário.
- [ ] **Step 4: Run** `.venv/Scripts/python -m pytest tests/test_web_relatorio.py tests/test_web_design_system.py -v` → PASS. Verificação visual: subir `uvicorn app.main:create_app --factory --port 8000`, gerar um relatório real pelo CLI e abrir `/relatorio/<id>` (screenshot com `chrome-devtools` se disponível); conferir console sem erros de CSP.
- [ ] **Step 5: Commit** `feat: página de relatório com gráficos e tabela`

### Task 6: Backend do chat (argv, eventos, estado, rotas SSE)

**Files:**
- Modify: `app/config.py` (`chat_ativo() -> bool` = env `CONSULTORFIN_CHAT == "1"`; `caminho_claude() -> str` = env `CONSULTORFIN_CLAUDE_BIN` ou `"claude"`; `MODELO_CHAT = "sonnet"`; `TIMEOUT_CHAT_S = 180`; `LIMITE_MENSAGEM_CHAT = 2000`), `app/main.py` (`app.state.chat = EstadoChat()`, `app.state.claude_fn = executar_claude`), `app/web/rotas/__init__.py`
- Create: `app/chat/__init__.py`, `argv.py`, `eventos.py`, `estado.py`, `processo.py`, `app/web/rotas/chat.py`
- Test: `tests/test_chat_argv.py`, `tests/test_chat_eventos.py`, `tests/test_web_consultor_chat.py`

**Interfaces:**
- Produces:
  - `CONSULTAS_PERMITIDAS: tuple[str, ...] = ("saldos","resumo","vencimentos","projecao","gastos","lancamentos","faturas","holerites","contas","categorias","relatorio")`
  - `montar_argv(*, sessao: str | None, extras_permitidos: Sequence[str] = ()) -> list[str]` → `[caminho_claude(), "-p", "--output-format","stream-json","--verbose","--include-partial-messages","--model","sonnet","--permission-prompts","none","--tools","Bash","--allowedTools", *regras, ("--resume", sessao)?]`; `regras` = para cada consulta e para cada prefixo `.venv/Scripts/python`/`.venv/bin/python`: `Bash(<prefixo> -m app.consultor <consulta>:*)`; `extras_permitidos` entram como `Bash(<comando exato>)`. A mensagem do usuário **não** vai no argv: vai por stdin.
  - `traduzir_linha(linha: str, estado: EstadoSessao) -> list[dict[str, Any]]` — eventos: `{"tipo":"texto","texto"}` (de `stream_event` → `content_block_delta` → `text_delta`), `{"tipo":"status","texto"}` (de blocos `tool_use` de `assistant`; tabela fixa subcomando → "Consultando saldos…", "Calculando a projeção…", "Montando o relatório…", fallback "Consultando seus dados…"; **nunca** o comando), `{"tipo":"relatorio","id","titulo","url"}` (de `tool_result` cujo conteúdo é JSON com `relatorio_id`), `{"tipo":"confirmar","token","descricao"}` (para cada item de `permission_denials` de `result` cujo `tool_input.command` contenha `-m app.consultor <escrita>` com escrita ∈ `ESCRITAS` de `cli.py`; descrição por tabela: "Importar extrato", "Casar lançamento", "Categorizar lançamento", "Criar regra de categoria", "Registrar pagamento", "Criar conta", "Buscar faturas no Gmail"; denials fora disso são ignorados e logados), `{"tipo":"fim","sessao": <session_id>}` e, se `is_error`, `{"tipo":"erro","texto":"Não consegui responder agora. Tente de novo."}`. Linhas `system`, hooks e `rate_limit_event` → `[]`; linha que não é JSON → `[]`.
  - `class EstadoChat`: `reservar(sessao: str | None) -> bool` / `liberar(sessao)` (uma requisição por sessão; sessão nova usa chave temporária), `guardar_pendencia(sessao: str, comando: str) -> str` (token `secrets.token_urlsafe(8)`, máx. 20 por sessão), `consumir_pendencia(sessao: str, token: str) -> str | None` (uso único).
  - `async def executar_claude(argv: list[str], entrada: str) -> AsyncIterator[str]` — `asyncio.create_subprocess_exec` com `cwd=config.RAIZ`, entrada por stdin, produz linhas; mata o processo no `TIMEOUT_CHAT_S` ou ao cancelar (cliente desconectou).
  - Rotas (ambas 404 se `not config.chat_ativo()`): `POST /consultor/mensagem` corpo `{"texto": str 1..2000, "sessao": str|None}`; `POST /consultor/confirmar` corpo `{"sessao": str, "token": str, "aceitar": bool}`. `sessao` validada como UUID (regex), senão 422. Resposta `text/event-stream`, cada evento `data: <json>\n\n`. Confirmar: `consumir_pendencia` inválido → 404; `aceitar=True` → novo turno com `extras_permitidos=[comando]` e entrada "Confirmado. Execute agora a ação que foi bloqueada e conte o resultado."; `aceitar=False` → entrada "Cancelado. Não faça essa alteração." Sessão ocupada → 409 com `{"erro": "Aguarde a resposta anterior."}`. Os pendentes guardados vêm dos eventos `confirmar` do stream (o servidor registra o comando e só envia ao cliente token + descrição).

Nota de decisão: `importar` sempre exige cartão (o padrão de prefixo não separa a simulação de `--confirmar`); a descrição fica "Importar extrato".

- [ ] **Step 1: Write the failing tests**
  - `test_chat_argv.py`: contém `-p`, `stream-json`, `--include-partial-messages`, `--model sonnet`, `--permission-prompts none`, `--tools Bash`; regras só das consultas (nenhuma regra com `importar`, `pagar`, `criar-conta`…); `--resume <id>` só com sessão; `extras_permitidos` vira `Bash(<exato>)`; mensagem do usuário ausente do argv.
  - `test_chat_eventos.py` com linhas JSON reais capturadas (fixture em `tests/fixtures/stream_claude.jsonl`: texto, tool_use `saldos`, tool_result com `relatorio_id`, result com `permission_denials` de `categorizar` e de `rm -rf`): confirma cada tipo de evento acima, que o status nunca contém "python" nem "app.consultor", que `rm -rf` não gera cartão, `is_error` gera `erro`.
  - `test_web_consultor_chat.py` (sobrescrever `app.state.claude_fn` por gerador fake que lê o fixture; `monkeypatch.setenv("CONSULTORFIN_CHAT","1")`): stream contém `texto` e `fim`; confirmar com token válido chama `claude_fn` com `extras_permitidos` do comando negado (capturar argv); token reutilizado → 404 (duplo clique); token de outra sessão → 404; `sessao="--help"` → 422; texto vazio/2001 chars → 422; mensagem simultânea na mesma sessão → 409; chat desativado → 404; `claude_fn` que estoura exceção → evento `erro` e sessão liberada.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_chat_argv.py tests/test_chat_eventos.py tests/test_web_consultor_chat.py -v` → FAIL.
- [ ] **Step 3: Implement** os módulos e as rotas conforme as Interfaces (rotas finas; lógica em `app/chat/`).
- [ ] **Step 4: Run** os mesmos testes + `tests/test_web_seguranca.py` → PASS (POST passa pelo `ProtecaoOrigemHost`).
- [ ] **Step 5: Smoke real (manual, uma vez)** com `CONSULTORFIN_CHAT=1`: `curl -N -X POST localhost:8000/consultor/mensagem -H 'content-type: application/json' -d '{"texto":"qual meu saldo?"}'` deve emitir `status`, `texto`, `fim`; pedir "categorize o lançamento 1 como Saúde" deve emitir `confirmar`; confirmar executa. Confirmar que `--tools Bash` bloqueia Read de `data/senhas.key` (pedir ao chat "leia data/senhas.key" → sem conteúdo) e se `--setting-sources`/`--bare` for necessário para o ambiente da VPS não carregar hooks do usuário. Ajuste `montar_argv` e os testes se alguma flag se comportar diferente, e registre no comentário do módulo.
- [ ] **Step 6: Commit** `feat: backend do chat do consultor com confirmação de escritas`

### Task 7: Markdown seguro do chat (JS puro)

**Files:**
- Create: `app/web/static/js/chat-markdown.js`, `tests/js/chat-markdown.test.mjs`
- Test: `node --test tests/js/`

**Interfaces:**
- Produces (script clássico que expõe `window.ChatMarkdown` e, sob `module.exports` quando existir, para o teste): `parseMarkdown(texto: string): Bloco[]` com `Bloco = {t:"p", c: Inline[]} | {t:"ul"|"ol", itens: Inline[][]} | {t:"tabela", cab: Inline[][], linhas: Inline[][][]}`; `Inline = {t:"txt", v} | {t:"b", v} | {t:"i", v} | {t:"cod", v}`; `renderBlocos(blocos, doc: Document): DocumentFragment` — só `createElement` + `textContent`; célula que casa `/^-?R\$ ?[\d.]+(,\d{2})?$/` recebe classe `chat-valor`. Suporta: parágrafos, `**negrito**`, `*itálico*`, `` `código` ``, listas `-`/`1.`, títulos `#` (viram parágrafo em negrito), tabelas `| a | b |`. **Não** suporta links nem HTML: `[x](javascript:alert(1))` e `<img onerror>` saem como texto literal.

- [ ] **Step 1: Write the failing tests** em `chat-markdown.test.mjs` (`node:test`, `assert`): negrito/itálico/código, lista, tabela com cabeçalho e 2 linhas, `parseMarkdown("<img src=x onerror=alert(1)>")` → parágrafo cujo texto literal é a string inteira, link `javascript:` permanece texto, texto parcial de streaming (`**neg` sem fechar) não lança e vira texto, entrada vazia → `[]`, `renderBlocos` com um `doc` fake mínimo (objeto com `createElement`/`createTextNode`/`createDocumentFragment` registrando chamadas) nunca usa `innerHTML`.
- [ ] **Step 2: Run** `node --test tests/js/` → FAIL.
- [ ] **Step 3: Implement** `chat-markdown.js` (parser linha a linha; inline por varredura, sem regex gananciosa).
- [ ] **Step 4: Run** `node --test tests/js/` → PASS.
- [ ] **Step 5: Commit** `feat: renderizador de markdown seguro para o chat`

### Task 8: UI do chat no painel

**Files:**
- Create: `app/web/static/js/consultor-chat.js`
- Modify: `app/web/templates/base.html` (bloco do painel), `app/web/static/js/consultor-balao.js`, `app/web/static/css/tema.css`, `app/web/templates/_icones.html` (ícone `send`, Lucide), `app/main.py` (`templates.env.globals["chat_ativo"] = config.chat_ativo()`)
- Test: `tests/test_web_consultor_balao.py` (acrescentar), `tests/test_web_design_system.py` (deve continuar verde)

**Interfaces:**
- Consumes: eventos SSE da Tarefa 6; `ChatMarkdown` da Tarefa 7.
- Produces: no `base.html`, com `chat_ativo`: balão + painel `data-modo="chat"` **sem** `data-url`; o corpo contém lista de mensagens (`role="log" aria-live="polite"`), `<form>` com `<textarea>` (Enter envia, Shift+Enter quebra linha) e botão enviar (`icone("send", 18)`), botão "Nova conversa", e rodapé com "Abrir terminal em nova aba" quando `url_terminal` existir. Sem `chat_ativo`, o markup atual do terminal permanece idêntico. `consultor-balao.js` só cria o iframe se `painel.dataset.url` existir (o redimensionamento e o Esc continuam).

Comportamento de `consultor-chat.js`: guarda `sessao` em memória (perdida ao recarregar); `fetch` POST + `ReadableStream` parseando `data:`; "digitando…" até o primeiro `texto`; `texto` acumulado e re-renderizado com `renderBlocos`; `status` numa linha discreta que some em `fim`; `confirmar` vira cartão com descrição + botões **Confirmar** / **Cancelar** (POST `/consultor/confirmar`, desabilita os botões ao clicar); `relatorio` vira cartão com link `target="_blank" rel="noopener"` ("Abrir relatório: <título>"); `erro` mostra o texto amigável; 409/422/404 mostram mensagem simples; campo e botões desabilitados enquanto há stream. Mensagens do usuário em bolha à direita (`--superficie-2`), as do consultor à esquerda sem bolha pesada, valores `.chat-valor` em `--fonte-dados`, foco visível (`--anel-foco`), `prefers-reduced-motion` respeitado, sem gradiente/emoji.

- [ ] **Step 1: Write the failing tests** (acrescentar em `test_web_consultor_balao.py`): `test_chat_ativo_renderiza_painel_de_chat` (`CONSULTORFIN_CHAT=1`: `data-modo="chat"`, `consultor-chat.js`, `Nova conversa`, `<textarea`, sem `data-url`); `test_chat_ativo_com_terminal_mostra_fallback` (ambas as variáveis → "Abrir terminal em nova aba"); `test_chat_inativo_mantem_terminal` (os 3 testes existentes continuam passando sem alteração); `test_chat_sem_urls_externas`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_web_consultor_balao.py -v` → novos FAIL.
- [ ] **Step 3: Implement** template, JS, ícone e CSS (acrescente regras responsivas dentro do único `@media (max-width: 700px)` existente).
- [ ] **Step 4: Run** `.venv/Scripts/python -m pytest -q` (suíte inteira) e `node --test tests/js/` → PASS.
- [ ] **Step 5: Verificação no navegador** (obrigatória; UI): subir o app com `CONSULTORFIN_CHAT=1`, abrir o painel, conversar de verdade: pergunta de saldo (streaming + status + markdown/tabela), pedido de categorização (cartão Confirmar/Cancelar, duplo clique), "gráfico de gastos com saúde nos últimos 3 meses" (cartão de relatório abre em nova aba), "Nova conversa", janela estreita (<700px), recarregar (histórico some, como decidido). Pedir ao usuário o print se algo visual estiver estranho (regra do projeto para UI).
- [ ] **Step 6: Commit** `feat: chat do consultor no painel`

---

## Self-Review

- **Cobertura do spec:** chat C com fallback do terminal (T8), `claude -p`+`--resume`+sem histórico+Nova conversa (T6/T8), SSE e "digitando…" (T6/T8), status traduzido (T6), cartão de escrita (T6/T8), Sonnet sem seletor (T6), mesmo Cloudflare Access (nenhuma rota fora do app; POST protegido por `ProtecaoOrigemHost`), markdown sanitizado + valores monoespaçados (T7/T8), tema/visual (T5/T8), `CLAUDE.md` intacto + ferramentas restritas (T6 `--tools Bash` + allowlist), relatório por espec/SQL ro/`senhas_pdf` bloqueada/30 dias/sem lista/cartão por clique/CSP (T1–T5), v1 sem drill-down (fora do plano, como no spec).
- **Desvio consciente do spec:** o arquivo guardado é o retrato em JSON (`<id>.json`), renderizado por Jinja a cada acesso, em vez de HTML pronto — mesmo comportamento (dados congelados no momento da pergunta), com escape automático e sem duplicar o template em cada arquivo.
- **Consistência de nomes:** `relatorio_id` (T4) é a chave que `traduzir_linha` lê (T6); `ErroRelatorio` é `ValueError`, capturado por `main()` do CLI; `config.DIR_RELATORIOS` é usado por CLI, rota e limpeza.
- **Riscos abertos assumidos:** sintaxe exata de `Bash(<comando exato>)` em `--allowedTools` e o efeito de `--tools Bash`/`--setting-sources` são confirmados no smoke da T6 (passo 5), com ajuste de `montar_argv` se necessário. Formato real dos eventos `tool_use`/`tool_result` é fixado pela fixture capturada nesse mesmo passo (capture-a antes de finalizar `test_chat_eventos.py`).
