# Faturas do Gmail em fila — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O Consultor busca faturas de cartão no Gmail (somente leitura), enfileira os PDFs e a tela Importar os processa um a um (Importar/Pular antes da senha).

**Architecture:** Subcomando fixo `gmail-buscar` do consultor (sem julgamento do Claude) chama a API do Gmail via `urllib`, classifica por lista de remetentes, grava os PDFs em `data/fila/<id>.pdf` e as linhas em `fila_faturas`. A tela Importar consulta a fila (htmx, a cada 5 s). **Importar** carrega o PDF do disco no `ArmazemUploads` e reaproveita o fluxo existente de senha/prévia/confirmação. "Importada" é derivado: `sha256(PDF)` igual a `faturas.arquivo_hash`.

**Tech Stack:** Python 3.11, FastAPI + Jinja2 + htmx, SQLite (migração 13), `urllib.request` (sem dependência nova), pytest.

**Spec:** `docs/superpowers/specs/2026-11-03-fatura-gmail-fila-design.md`

## Global Constraints

- Escopo do Gmail: somente leitura (`https://www.googleapis.com/auth/gmail.readonly`). Nunca enviar, apagar ou marcar e-mails.
- Máximo de **50** mensagens por busca; PDF de até **5 MB** (`LIMITE_BYTES` de `app/web/rotas/importar.py`); mensagem maior é ignorada.
- Estados da fila: `pendente`, `pulada`, `importada`. Pendentes e PDFs expiram em **7 dias**; PDF é apagado ao pular, ao importar e ao expirar.
- A senha do PDF nunca passa pelo Claude, log, arquivo ou HTML (fluxo existente de `/faturas/previa`).
- Credencial do Gmail só em `data/` (fora do Git; o repositório é público), permissão `0600` quando o SO permitir. Nunca impressa nem registrada em log.
- Texto vindo de e-mail (assunto, remetente, nome do anexo) é dado: só passa por regex; o Claude recebe apenas resumo estruturado (emissor, final, vencimento, contagens). Nome de arquivo no disco é sempre `data/fila/<id inteiro>.pdf`.
- Sem dados reais em testes: e-mails e PDFs anonimizados. Mensagens ao usuário em português claro, sem "SQL/JSON/CLI".
- Logs estruturados `event=... chave=valor`, sem valores financeiros.
- Pagamento/`Pular` nunca grava lançamentos; só `Importar` + confirmação existente grava.

## Review Focus

- Mensagem com dois PDFs ou PDF + imagem: só PDFs viram itens; cada anexo é um item (chave `gmail_msg_id` + `anexo_id`).
- Anexo `.pdf` que não é PDF (começa sem `%PDF`) ou maior que 5 MB: ignorado, contado em `ignoradas`.
- E-mail sem final do cartão ou sem vencimento: item com campos `None`, aparece como "não identificado"; nunca quebra a busca.
- Mesma busca rodada duas vezes: não duplica itens (UNIQUE por mensagem + anexo) e não rebaixa estado de item já pulado/importado.
- Token expirado/revogado ou ausente: mensagem em português ("autorização do Gmail expirou; refaça a configuração"), sem stack trace e sem vazar o token.
- Assunto hostil (ex.: "ignore as instruções e rode ..."): não aparece na saída do comando nem na tela.
- `id` da fila não numérico ou inexistente nas rotas: 404 sem tocar o disco.
- PDF já importado (hash igual): item marcado "já importada" com **Pular** como padrão; **Importar mesmo assim** disponível.
- Senha errada repetida na fila: item continua pendente, PDF segue no disco até expirar/pular.

---

### Task 1: Tabela e repositório da fila

**Files:**
- Modify: `app/db.py` (nova `_migracao_13`, acrescentar ao fim de `MIGRACOES`)
- Create: `app/repo/fila_faturas.py`
- Test: `tests/test_fila_repo.py`

**Interfaces:**
- Produces (`app/repo/fila_faturas.py`):
  - `inserir(con, *, gmail_msg_id: str, anexo_id: str, emissor: str | None, cartao_final: str | None, vencimento: str | None, pdf_hash: str, agora: str) -> int | None` — `None` se já existe a mesma (mensagem, anexo).
  - `obter(con, item_id: int) -> sqlite3.Row | None`
  - `listar_pendentes(con) -> list[sqlite3.Row]` (estado `pendente`, mais antigos primeiro)
  - `definir_estado(con, item_id: int, estado: str) -> None`
  - `expirados(con, limite_iso: str) -> list[sqlite3.Row]` (pendentes com `criado_em < limite_iso`)
  - `hash_ja_importado(con, pdf_hash: str) -> bool` (existe `faturas.arquivo_hash` igual)
  - `fatura_existente(con, cartao_final: str, vencimento: str) -> bool` (join `faturas`×`contas.final_numero` e `data_vencimento`)
- Tabela `fila_faturas(id INTEGER PRIMARY KEY, gmail_msg_id TEXT NOT NULL, anexo_id TEXT NOT NULL, emissor TEXT, cartao_final TEXT, vencimento TEXT, pdf_hash TEXT NOT NULL, estado TEXT NOT NULL DEFAULT 'pendente' CHECK (estado IN ('pendente','pulada','importada')), criado_em TEXT NOT NULL, UNIQUE (gmail_msg_id, anexo_id))`.

- [ ] **Step 1: Escrever os testes** em `tests/test_fila_repo.py`: `test_inserir_duplicado_devolve_none`, `test_estado_invalido_rejeitado` (CHECK → `sqlite3.IntegrityError`), `test_listar_pendentes_ordem_e_filtro`, `test_expirados_so_pendentes_antigos`, `test_hash_ja_importado` e `test_fatura_existente_por_final_e_vencimento` (criar conta com `final_numero="1111"` e fatura com `data_vencimento="2026-11-05"` via helpers de `tests/test_projecao.py`/`repo.contas.criar_conta` e `repo.faturas.criar`).
- [ ] **Step 2: Rodar** `.venv/Scripts/python -m pytest tests/test_fila_repo.py -q` → FALHA (módulo inexistente).
- [ ] **Step 3: Implementar** `_migracao_13` (CREATE TABLE acima + índice em `estado`) e o repositório com os sinais acima, SQL parametrizado.
- [ ] **Step 4: Rodar** o arquivo de teste e `tests/test_db*.py` → PASS (versão do banco passa a 13).
- [ ] **Step 5: Commit** `feat: tabela e repositório da fila de faturas`

### Task 2: Classificador de e-mails de fatura

**Files:**
- Create: `app/gmail/__init__.py`, `app/gmail/classificador.py`
- Test: `tests/test_gmail_classificador.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Remetente: emissor: str; contem: tuple[str, ...]` (trechos minúsculos do endereço `From`).
  - `REMETENTES_PADRAO: tuple[Remetente, ...]` com Itaú (`itau`), Nubank (`nubank`), Porto (`portoseguro`, `portobank`), Mercado Pago (`mercadopago`). **Palpite a validar** na caixa real (Task 6); ajustável por `data/gmail-remetentes.json` (lista de `{"emissor", "contem": [...]}`) via `carregar_remetentes(caminho: Path | None = None) -> tuple[Remetente, ...]`.
  - `@dataclass(frozen=True) class Candidata: emissor: str; cartao_final: str | None; vencimento: str | None` (ISO).
  - `classificar(de: str, assunto: str, trecho: str, remetentes: tuple[Remetente, ...]) -> Candidata | None` — `None` se o remetente não está na lista. `cartao_final`: regex `final\s*(\d{4})` em assunto+trecho. `vencimento`: `venc\w*\s*(?:em|dia|:)?\s*(\d{2}/\d{2}/\d{4})` convertida para ISO; data inválida → `None`.

- [ ] **Step 1: Escrever os testes**: `test_itau_assunto_com_final` (assunto "A fatura do seu cartão final 0000 fechou" → final "0000"), `test_porto_trecho_com_vencimento` (trecho "vencimento em 05/11/2026" → "2026-11-05"), `test_remetente_desconhecido_none`, `test_data_invalida_vira_none` ("vencimento em 31/02/2026"), `test_assunto_hostil_nao_quebra` e `test_json_remetentes_invalido_usa_padrao`. Use endereços e textos fictícios.
- [ ] **Step 2: Rodar** → FALHA.
- [ ] **Step 3: Implementar** as assinaturas acima (comparação de `From` em minúsculas; JSON inválido → padrão, log `event=gmail_remetentes_invalido`).
- [ ] **Step 4: Rodar** → PASS.
- [ ] **Step 5: Commit** `feat: classificador de e-mails de fatura`

### Task 3: Cliente Gmail somente leitura

**Files:**
- Create: `app/gmail/cliente.py`
- Test: `tests/test_gmail_cliente.py`

**Interfaces:**
- Produces:
  - `class ErroGmail(Exception)` com mensagem em português para o usuário.
  - `@dataclass(frozen=True) class Anexo: id: str; nome: str; tamanho: int`
  - `@dataclass(frozen=True) class Mensagem: id: str; de: str; assunto: str; trecho: str; anexos: tuple[Anexo, ...]`
  - `class ClienteGmail:` `__init__(self, token_path: Path, transporte: Callable[[Request], bytes] | None = None)`; `buscar(self, de: date, ate: date, limite: int = 50) -> list[Mensagem]` (query `has:attachment filename:pdf after:AAAA/MM/DD before:AAAA/MM/DD`, só `messages.list` + `messages.get?format=metadata/full` conforme necessário, ao máximo `limite` mensagens); `baixar_anexo(self, mensagem_id: str, anexo_id: str) -> bytes` (base64 url-safe decodificado).
  - Token: JSON em `data/gmail-token.json` (caminho por env `CONSULTORFIN_GMAIL_TOKEN`) com `client_id`, `client_secret`, `refresh_token`; o cliente troca por access token em `https://oauth2.googleapis.com/token` (cache em memória). `invalid_grant`/401 → `ErroGmail("A autorização do Gmail expirou. Refaça a configuração.")`; arquivo ausente → `ErroGmail("O Gmail ainda não foi configurado.")`. Nunca incluir o token em mensagem ou log.
  - `transporte` injetável permite testar sem rede.

- [ ] **Step 1: Escrever os testes** com transporte falso: `test_buscar_monta_query_e_limita_a_50`, `test_baixar_anexo_decodifica_base64url`, `test_token_ausente_mensagem_clara`, `test_invalid_grant_mensagem_clara_sem_token` (assert que o texto do erro não contém o `refresh_token` de teste), `test_so_escopo_leitura` (nenhuma chamada usa método diferente de GET/POST de token).
- [ ] **Step 2: Rodar** → FALHA.
- [ ] **Step 3: Implementar** com `urllib.request` (timeout 20 s), `User-Agent` fixo, sem dependências novas.
- [ ] **Step 4: Rodar** → PASS.
- [ ] **Step 5: Commit** `feat: cliente Gmail somente leitura`

### Task 4: Serviço de busca e subcomando do consultor

**Files:**
- Create: `app/dominio/fila_faturas.py`
- Modify: `app/consultor/cli.py` (subcomando `gmail-buscar` em `ESCRITAS`, e `_escreve`), `app/consultor/operacoes.py` (função `gmail_buscar`), `app/config.py` (`DIR_FILA = RAIZ / "data" / "fila"`, `LIMITE_MENSAGENS_GMAIL = 50`, `DIAS_EXPIRACAO_FILA = 7`)
- Test: `tests/test_fila_servico.py`, `tests/test_consultor_gmail.py`

**Interfaces:**
- Consumes: `ClienteGmail`, `Mensagem` (Task 3); `classificar`, `carregar_remetentes` (Task 2); repositório (Task 1).
- Produces (`app/dominio/fila_faturas.py`):
  - `@dataclass(frozen=True) class ResumoBusca: encontradas: int; novas: int; ja_na_fila: int; ja_importadas: int; ignoradas: int`
  - `buscar_para_fila(con, cliente: ClienteGmail, de: date, ate: date, agora: datetime, dir_fila: Path) -> ResumoBusca` — para cada mensagem classificada e cada anexo `.pdf` ≤ 5 MB que comece com `%PDF`: baixa, calcula sha256, insere (se novo) e grava `dir_fila/<id>.pdf`; se `hash_ja_importado` ou `fatura_existente` → grava o item mas conta em `ja_importadas`. Antes, chama `expirar(...)`.
  - `expirar(con, agora: datetime, dir_fila: Path) -> int` — pendentes com mais de 7 dias: estado `pulada` e PDF apagado.
  - `ja_importada(con, item: sqlite3.Row) -> bool` — hash do PDF em `faturas` **ou** `fatura_existente(final, vencimento)` quando ambos existem.
  - `descartar_pdf(dir_fila: Path, item_id: int) -> None` (idempotente).
- CLI: `python -m app.consultor gmail-buscar --mes AAAA-MM [--ate AAAA-MM]`; intervalo máximo 12 meses; saída JSON `{"periodo": "...", "encontradas": n, "novas": n, ...}` sem texto de e-mails; `ErroGmail` vira `{"erro": mensagem}`.

- [ ] **Step 1: Escrever os testes** (cliente falso): `test_busca_enfileira_pdf_valido`, `test_ignora_nao_pdf_e_acima_de_5mb`, `test_dois_pdfs_na_mesma_mensagem`, `test_busca_repetida_nao_duplica_nem_rebaixa_estado`, `test_ja_importada_por_hash` e `..._por_final_e_vencimento`, `test_campos_ausentes_viram_none`, `test_expirar_apaga_pdf_e_marca_pulada`, e no CLI: `test_gmail_buscar_saida_sem_texto_de_email` (assunto hostil ausente do JSON), `test_gmail_buscar_erro_de_token_traduzido`, `test_periodo_invalido_e_maior_que_12_meses`.
- [ ] **Step 2: Rodar** → FALHA.
- [ ] **Step 3: Implementar** o serviço e o subcomando (reuso de `interpretar_mes` de `app/dominio/painel.py` para `--mes`/`--ate`; `gmail-buscar` entra em `ESCRITAS` pois grava).
- [ ] **Step 4: Rodar** `pytest tests/test_fila_servico.py tests/test_consultor_gmail.py tests/test_consultor*.py -q` → PASS.
- [ ] **Step 5: Commit** `feat: busca de faturas no Gmail para a fila`

### Task 5: Fila na tela Importar

**Files:**
- Create: `app/web/rotas/fila.py`, `app/web/templates/importar/_fila.html`
- Modify: `app/web/rotas/__init__.py` (registrar router), `app/web/templates/importar/index.html` (incluir `<div id="fila-faturas" hx-get="/faturas/fila" hx-trigger="load, every 5s" hx-swap="innerHTML">` acima do formulário de fatura), `app/web/rotas/importar.py` se necessário para expor o contexto
- Test: `tests/test_web_fila.py`

**Interfaces:**
- Consumes: repositório e serviço (Tasks 1 e 4); `request.app.state.uploads.guardar(nome, conteudo) -> str`; `_pagina_inicial(request, con, fatura=...)` e `_PedirSenha`/contexto `{"token", "nome", "pedir_senha": True}` de `app/web/rotas/faturas.py`.
- Produces rotas:
  - `GET /faturas/fila` → fragmento HTML: um cartão por item pendente mostrando emissor, "final XXXX" ou "final não identificado", vencimento (`DD/MM/AAAA`) ou "vencimento não identificado", selo "já importada" quando `ja_importada`; botões **Importar** (ou **Importar mesmo assim**) e **Pular**; sem item pendente, retorna fragmento vazio. Antes de listar, marca como `importada` (e apaga o PDF) itens cujo hash agora existe em `faturas` e não foram marcados manualmente.
  - `POST /faturas/fila/{item_id}/pular` → estado `pulada`, PDF apagado, devolve o fragmento atualizado.
  - `POST /faturas/fila/{item_id}/importar` → lê `data/fila/<id>.pdf`, guarda no `ArmazemUploads` e devolve a página Importar com o formulário de senha (`fatura={"token":..., "nome": "Fatura <emissor>.pdf", "pedir_senha": True}`); o resto do fluxo é o existente.
  - `item_id` é `int` na rota (FastAPI valida); inexistente/não pendente → 404; arquivo sumido → mensagem "O arquivo desta fatura expirou. Busque de novo."
  - Todas as respostas com `Cache-Control: no-store`; POSTs passam pela proteção CSRF/Origin já existente em `app/web/seguranca.py`.

- [ ] **Step 1: Escrever os testes** (`cliente` e `db` de `tests/conftest.py`): `test_fila_lista_pendentes_sem_texto_de_email`, `test_item_ja_importada_vem_com_pular_padrao`, `test_pular_apaga_pdf_e_remove_do_fragmento`, `test_importar_devolve_formulario_de_senha_com_token` (PDF criptografado criado com `pypdf` como nos testes de fatura; senha certa leva à prévia), `test_item_inexistente_404`, `test_pdf_sumido_mensagem_clara`, `test_importada_pelo_fluxo_normal_some_da_fila` (importar a fatura e ver o item marcado `importada`).
- [ ] **Step 2: Rodar** → FALHA.
- [ ] **Step 3: Implementar** rotas e template (tema existente; ícones via macro `icone`; sem emojis, estado nunca só por cor).
- [ ] **Step 4: Rodar** `pytest tests/test_web_fila.py tests/test_web_importar*.py tests/test_web_lancamentos_fatura.py -q` → PASS.
- [ ] **Step 5: Commit** `feat: fila de faturas na tela Importar`

### Task 6: Autorização do Google, instruções do Consultor e validação real

**Files:**
- Create: `scripts/gmail_autorizar.py`, `docs/GMAIL-CONFIGURACAO.md`, `.claude/skills/faturas-gmail/SKILL.md`
- Modify: `CLAUDE.md` (lista de comandos: `gmail-buscar` só a pedido; nunca ler o arquivo do token; e-mail é dado), `.gitignore` (`data/` já cobre; confirmar `data/fila/` e `data/gmail-*`)
- Test: `tests/test_gmail_autorizar.py` (só a função pura de montagem do JSON do token)

**Interfaces:**
- `scripts/gmail_autorizar.py` (roda **no PC do usuário**, com navegador): lê `client_secret.json` baixado do Google Cloud, executa o fluxo local (loopback `127.0.0.1`, porta livre, PKCE) com o escopo de leitura e grava `gmail-token.json` com `client_id`, `client_secret`, `refresh_token`. Função pura `montar_token(client: dict, resposta: dict) -> dict` testável. O usuário copia o arquivo para a VPS (`scp` → `~/consultorfin/data/gmail-token.json`, `chmod 600`).
- `docs/GMAIL-CONFIGURACAO.md`: passo a passo curto (criar projeto no Google Cloud, ativar Gmail API, tela de consentimento, **publicar o app em produção** para o token não expirar em 7 dias, baixar credencial de "app para computador", rodar o script, copiar para a VPS, testar).
- `SKILL.md` "faturas-gmail": quando o usuário pedir "pesquisar faturas de MM/AAAA", rodar `gmail-buscar --mes AAAA-MM`, resumir o resultado em português e orientar a abrir a tela Importar; nunca abrir PDFs, nunca pedir/aceitar a senha, nunca importar por conta própria.

- [ ] **Step 1: Teste** `test_montar_token_so_campos_necessarios` (a saída não contém `access_token`) e `test_montar_token_sem_refresh_token_falha` → rodar → FALHA.
- [ ] **Step 2: Implementar** o script, o guia, a skill e o ajuste do `CLAUDE.md`; rodar o teste → PASS.
- [ ] **Step 3: Suíte completa** `.venv/Scripts/python -m pytest -q` → todos passam.
- [ ] **Step 4: Commit** `feat: configuração do Gmail e instruções do Consultor`
- [ ] **Step 5 (manual, com o usuário): validar na caixa real.** Usuário segue `docs/GMAIL-CONFIGURACAO.md`; na VPS: `git pull && sudo systemctl restart consultorfin`; no chat: "pesquisar faturas de 10/2026". Esperado: resumo com Itaú/Nubank/Porto; fila aparece na tela Importar; conferir se os remetentes padrão (Task 2) acertaram e ajustar `data/gmail-remetentes.json`; importar uma fatura de ponta a ponta e ver o item sair da fila; pular outra e ver o PDF sumir de `data/fila/`.
