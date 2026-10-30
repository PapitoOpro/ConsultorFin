# Holerite em PDF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Importar holerite (PDF sem senha, layouts Adiantamento e Folha Mensal) pelo painel, guardar totais e itens, ligar o líquido ao crédito do extrato (ou prever a entrada) e expor tudo ao consultor em modo só leitura.

**Architecture:** Espelha as faturas: leitor `app/importacao/holerite/` (única parte que importa pdfplumber), domínio `app/dominio/holerites.py` (prévia + gravação numa transação), SQL só em `app/repo/holerites.py` e `app/repo/lancamentos.py`, migração v10, rotas `/holerites`, comandos `holerites` no consultor. O líquido é uma linha de `lancamentos` (`tipo='entrada'`, `origem='holerite'`); os itens ficam só em `holerite_itens` e nunca entram em totais de caixa nem em gastos por categoria.

**Tech Stack:** Python 3.11, FastAPI + Jinja2 + HTMX, SQLite, pdfplumber (já no `requirements.txt`), pytest (`.venv/Scripts/python -m pytest`).

**Spec:** `docs/superpowers/specs/2026-10-30-holerite-pdf-design.md`

## Global Constraints

- Camadas: SQL só em `app/db.py` e `app/repo/`; sem `import sqlite3` em `app/dominio|importacao|consultor|web`; funções de repo não fazem commit; escrita com `transacao(con)` (ou `_escrita` do domínio, que usa SAVEPOINT dentro de transação aberta).
- Migração nova = função `_migracao_10` acrescentada ao fim de `MIGRACOES` em `app/db.py` (nunca editar a 1–9). Dinheiro em centavos inteiros; datas ISO no banco; UI em pt-BR.
- Só `app/importacao/holerite/texto.py` importa `pdfplumber`. Entre prévia e confirmação guardar apenas o dado lido (sem bytes do PDF).
- **Não guardar** CPF, CNPJ, nome do funcionário, cargo, bases de cálculo nem o PDF. Nunca logar valores nem nomes: só `event=... chave=valor` com contagens e tipo de erro.
- Respostas com prévia usam `Cache-Control: no-store`. PDF só pelo painel: o `importar` do consultor continua recusando `.pdf`.
- Testes só com holerites **sintéticos** (texto inventado). PDFs reais ficam em `C:\Users\rafae\OneDrive\Documentos\Holerites` (fora do repositório); nunca copiar texto real para testes ou commits.
- Layouts suportados: `adiantamento` e `mensal` (empresa do dono). Outro layout → `HoleriteIlegivel` com mensagem clara, nada gravado.
- Dias previstos (configuráveis em `configuracoes`): adiantamento = dia 20 do mês da competência; mensal = dia 2 do mês seguinte. Janela para vincular crédito existente: do dia 1 do mês da competência até 45 dias depois; valor exato.
- Commits: terminar mensagem com `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Nunca `git stash` (pasta no OneDrive).

## Review Focus

- PDF de fatura de cartão enviado como holerite (e vice-versa) → erro claro, nada gravado.
- Holerite cuja conferência não fecha (proventos − descontos ≠ líquido) → aviso na prévia, importação permitida.
- Dois holerites do mesmo mês e tipo, hash diferente → pede confirmação para substituir; mesmo hash → "já importado".
- Crédito do extrato já vinculado a outro holerite não pode ser reutilizado.
- Substituir um holerite que tinha crédito vinculado mantém o vínculo; que tinha entrada prevista atualiza o valor/data da prevista.
- Nenhum item de holerite aparece em `despesas_do_mes`, `consumo_do_mes` nem no saldo da conta.

---

### Task 1: Migração v10 e repositório de holerites

**Files:**
- Modify: `app/db.py` (nova `_migracao_10`, acrescentar a `MIGRACOES`)
- Create: `app/repo/holerites.py`
- Modify: `app/repo/lancamentos.py` (duas funções novas)
- Test: `tests/test_holerite_repo.py`

**Interfaces:**
- Produces (tabelas): `holerites(id, uuid UNIQUE, conta_id → contas, competencia TEXT 'YYYY-MM', tipo TEXT CHECK IN ('adiantamento','mensal'), empresa TEXT, total_vencimentos_centavos, total_descontos_centavos, liquido_centavos, arquivo_hash NOT NULL, arquivo_nome, lancamento_id → lancamentos NULL, criado_em, UNIQUE(competencia, tipo))`; `holerite_itens(id, uuid UNIQUE, holerite_id → holerites ON DELETE CASCADE, ordem, codigo TEXT, descricao NOT NULL, referencia TEXT, natureza TEXT CHECK IN ('provento','desconto'), valor_centavos (positivo), categoria_id → categorias NULL)` com índice por `holerite_id`; índice único parcial `ux_holerites_lancamento` em `holerites(lancamento_id) WHERE lancamento_id IS NOT NULL`.
- Produces (`app/repo/holerites.py`): `criar(con, *, conta_id: int, competencia: str, tipo: str, empresa: str | None, total_vencimentos: int, total_descontos: int, liquido: int, arquivo_hash: str, arquivo_nome: str | None, lancamento_id: int | None, criado_em: str) -> int`; `obter(con, holerite_id: int) -> sqlite3.Row | None`; `por_competencia_tipo(con, competencia: str, tipo: str) -> sqlite3.Row | None`; `listar(con, ano: int | None = None) -> list[sqlite3.Row]` (competência desc, depois tipo); `atualizar_cabecalho(con, holerite_id: int, *, conta_id: int, empresa: str | None, total_vencimentos: int, total_descontos: int, liquido: int, arquivo_hash: str, arquivo_nome: str | None, lancamento_id: int | None) -> None`; `inserir_item(con, holerite_id: int, *, ordem: int, codigo: str | None, descricao: str, referencia: str | None, natureza: str, valor_centavos: int, categoria_id: int | None) -> int`; `itens(con, holerite_id: int) -> list[sqlite3.Row]` (por ordem); `excluir_itens(con, holerite_id: int) -> None`; `atualizar_categoria_item(con, item_id: int, categoria_id: int | None) -> None`; `obter_item(con, item_id: int) -> sqlite3.Row | None`; `total_por_descricao(con, ano: int, trecho: str) -> int` (soma dos descontos do ano cuja descrição normalizada contém `trecho`, em centavos).
- Produces (`app/repo/lancamentos.py`): `inserir_holerite_previsto(con, *, conta_id: int, data: str, descricao: str, valor_centavos: int, categoria_id: int | None) -> int` (`tipo='entrada'`, `origem='holerite'`, `compensado=0`, `estimado=1`, chave `holerite:<uuid>`); `entradas_candidatas(con, conta_id: int, valor_centavos: int, de_iso: str, ate_iso: str) -> list[sqlite3.Row]` (entradas compensadas da conta com esse valor exato e data no intervalo, que não estejam em `holerites.lancamento_id`, por data).

- [ ] **Step 1: Write failing tests** em `tests/test_holerite_repo.py`: `test_migracao_10_a_partir_da_v9_populada` (abre banco migrado até a versão 9 com conta e lançamento, migra, `PRAGMA user_version == 10`, tabelas existem, dados antigos intactos); `test_criar_e_listar_por_ano`; `test_unique_competencia_tipo` (segunda criação com mesma competência+tipo levanta `sqlite3.IntegrityError`); `test_vinculo_unico_de_lancamento`; `test_itens_em_ordem_e_cascade` (excluir holerite apaga itens); `test_total_por_descricao` (soma só descontos do ano pedido); `test_entradas_candidatas_ignora_vinculadas_e_fora_da_janela`; `test_inserir_holerite_previsto_e_pendente_estimado`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m pytest tests/test_holerite_repo.py -q` → FAIL (módulo/migração inexistentes).
- [ ] **Step 3: Implement** `_migracao_10` (padrão da `_migracao_8`), as duas listas já descritas e `app/repo/holerites.py`; `total_por_descricao` usa `LIKE` sobre a descrição em maiúsculas sem acento (reutilizar `normalizar_descricao` do domínio é proibido no repo: receba `trecho` já normalizado e compare com a coluna via `LOWER`; o domínio normaliza o trecho).
- [ ] **Step 4: Run** o teste do passo 2 e `tests/test_db.py` → PASS.
- [ ] **Step 5: Commit** `feat(holerite): migração v10 e repositório de holerites`.

---

### Task 2: Leitor de PDF do holerite

**Files:**
- Create: `app/importacao/holerite/__init__.py` (exporta `ler_holerite`), `modelo.py`, `texto.py`, `leitor.py`
- Create: `tests/holerite_util.py` (gera texto sintético; ver Step 1), `tests/test_holerite_leitor.py`

**Interfaces:**
- Produces (`modelo.py`): `class HoleriteIlegivel(ValueError)`, `class PdfInvalido(HoleriteIlegivel)`, `class PdfSemTexto(HoleriteIlegivel)`, `class LayoutNaoSuportado(HoleriteIlegivel)`; `@dataclass(frozen=True) class ItemHolerite: codigo: str | None; descricao: str; referencia: str | None; natureza: str  # 'provento'|'desconto'; valor_centavos: int  # sempre positivo`; `@dataclass class HoleriteLido: tipo: str  # 'adiantamento'|'mensal'; competencia: str  # 'YYYY-MM'; empresa: str | None; total_vencimentos_centavos: int; total_descontos_centavos: int; liquido_centavos: int; itens: list[ItemHolerite]; avisos: list[str]` com `conferencia() -> Conferencia` onde `Conferencia(soma_proventos, soma_descontos, total_vencimentos, total_descontos, liquido)` tem `confere: bool` (proventos == total_vencimentos, descontos == total_descontos, vencimentos − descontos == líquido).
- Produces (`texto.py`, único com `pdfplumber`): `@dataclass(frozen=True) class Palavra: x0: float; x1: float; topo: float; texto: str`; `def abrir_pdf(conteudo: bytes) -> list[list[Palavra]]` (uma lista de palavras por página; o holerite tem 1 página; lê só a metade superior da página quando houver via duplicada: cortar na primeira ocorrência do título/cabeçalho repetido, ver Step 3).
- Produces (`leitor.py`): `def ler_holerite(conteudo: bytes) -> HoleriteLido`; função pura `def interpretar(palavras: list[Palavra]) -> HoleriteLido` (testável sem PDF).

- [ ] **Step 1: Inspect the real layout locally (no commit of real data).** Com `PYTHONIOENCODING=utf-8 .venv/Scripts/python`, abra os dois PDFs reais da pasta `Holerites` (fora do repo) com `pdfplumber`, imprima `extract_words()` com `x0, x1, top` da primeira via só com números e rótulos (mascare nomes/CPF/CNPJ) e anote: x do cabeçalho "Vencimentos" e "Descontos", como "Adiantamento"/"Folha Mensal" e "Mês de AAAA" aparecem, a ordem das colunas (Código, Descrição, Referência, Vencimentos, Descontos), e onde estão "Total de Vencimentos", "Total de Descontos" e "Valor Líquido". A página tem a via do funcionário repetida e rótulos de rodapé invertidos ("oiránoicnuF") que devem ser ignorados.
- [ ] **Step 2: Write failing tests** em `tests/test_holerite_leitor.py`. `tests/holerite_util.py` expõe `palavras_sinteticas(tipo: str, ...) -> list[Palavra]` que posiciona as palavras nas mesmas colunas do real, com valores inventados. Testes: `test_interpreta_adiantamento` (tipo `adiantamento`, competência `2026-10`, provento 1.500,00, desconto 100,00, líquido 1.400,00, confere); `test_interpreta_mensal` (tipo `mensal`, competência `2026-09`, 6 proventos que somam 5.000,00, 5 descontos que somam 1.200,00, líquido 3.800,00, confere — usar descrições e valores inventados que totalizem exatamente isso); `test_natureza_pela_coluna` (valor na coluna Vencimentos = provento, Descontos = desconto); `test_referencia_nao_vira_valor` (coluna Referência como "22,00", "2:30", "20,00" não entra como valor); `test_divergencia_gera_aviso`; `test_layout_desconhecido_levanta`; `test_ler_holerite_pdf_sem_texto` e `test_ler_holerite_arquivo_nao_pdf` (bytes inválidos → `PdfInvalido`, mensagem sem conteúdo do arquivo); `test_sem_dados_pessoais_no_resultado` (nenhum campo do `HoleriteLido` contém o nome/CPF/CNPJ fornecidos no texto sintético).
- [ ] **Step 3: Implement** `abrir_pdf` (agrupa palavras da página; a via duplicada é descartada cortando em `topo` da segunda ocorrência do cabeçalho "Código Descrição"), `interpretar` (agrupa palavras por linha por `topo` com tolerância; classifica valores monetários pelo x do cabeçalho das colunas; ignora referência; tipo vem do rótulo "Adiantamento" ou "Folha Mensal"; competência do texto "Mês de AAAA" mapeando nome do mês em pt-BR; empresa = linha do cabeçalho da empresa; totais pelos rótulos), e `ler_holerite` (PDF → palavras → `interpretar`; qualquer exceção de leitura → `PdfInvalido`/`PdfSemTexto` sem repassar mensagem original).
- [ ] **Step 4: Run** `.venv/Scripts/python -m pytest tests/test_holerite_leitor.py -q` → PASS.
- [ ] **Step 5: Validate locally on the real PDFs** com um script descartável (não commitar): `ler_holerite` nos dois arquivos reais e comparar `liquido` (conferido à mão contra o PDF, sem anotar o valor), totais e `confere == True`. Ajustar o leitor até os dois baterem.
- [ ] **Step 6: Commit** `feat(holerite): leitor de PDF (adiantamento e folha mensal)`.

---

### Task 3: Domínio — prévia, gravação e vínculo com o extrato

**Files:**
- Create: `app/dominio/holerites.py`
- Modify: `app/dominio/categorias.py` apenas se faltar helper (preferir reutilizar `categorizar`/regras existentes)
- Test: `tests/test_holerite_dominio.py`

**Interfaces:**
- Consumes: `HoleriteLido`/`ItemHolerite` (Task 2); `repo.holerites` e `repo.lancamentos.entradas_candidatas/inserir_holerite_previsto` (Task 1); `repo.configuracoes.obter/definir`; `repo.categorias.id_por_nome("Salário")`.
- Produces: `class ErroHolerite(ValueError)`; `class HoleriteJaImportado(ValueError)`; `class HoleriteDiferenteExistente(ValueError)`; `@dataclass class Credito: lancamento_id: int; data: str; descricao: str; valor_centavos: int`; `@dataclass class PreviaHolerite: holerite: HoleriteLido; arquivo_hash: str; situacao: Literal["novo","identico","substitui"]; creditos_candidatos: list[Credito]; data_prevista: str; categorias_sugeridas: list[int | None]`; `@dataclass class ResumoHolerite: holerite_id: int; lancamento_id: int; itens: int; vinculado: bool`; `def preparar_previa(con, h: HoleriteLido, arquivo_hash: str, conta_id: int) -> PreviaHolerite`; `def importar_holerite(con, h: HoleriteLido, arquivo_hash: str, arquivo_nome: str, conta_id: int, hoje: date, categorias: list[int | None], lancamento_id: int | None, substituir: bool = False) -> ResumoHolerite`; `def alterar_categoria_item(con, item_id: int, categoria_id: int | None) -> None`; `def detalhe(con, holerite_id: int) -> tuple[Row, list[Row]]` (levanta `ErroHolerite` se inexistente); `def data_prevista(con, tipo: str, competencia: str) -> str` (lê `configuracoes` chaves `holerite_dia_adiantamento` e `holerite_dia_mensal`; padrões 20 e 2; dia limitado ao último do mês).

- [ ] **Step 1: Write failing tests** (banco em memória, conta corrente, holerites sintéticos de `tests/holerite_util.py`): `test_previa_nova_sem_credito_prevê_data` (adiantamento 2026-10 → `2026-10-20`; mensal 2026-09 → `2026-10-02`); `test_previa_acha_credito_exato_na_janela`; `test_previa_ignora_credito_de_outro_valor_e_fora_da_janela`; `test_importar_sem_credito_cria_entrada_prevista_estimada` (linha pendente, `origem='holerite'`, valor = líquido, categoria Salário); `test_importar_com_credito_vincula_sem_criar_linha` (`lancamento_id` do crédito gravado; nenhuma linha nova; categoria Salário aplicada se estava sem); `test_credito_ja_vinculado_nao_reaproveita` (segundo holerite com mesmo valor: candidato não aparece); `test_mesmo_hash_levanta_ja_importado`; `test_hash_diferente_exige_substituir`; `test_substituir_refaz_itens_e_mantem_categorias_por_descricao`; `test_substituir_mantem_vinculo_e_atualiza_prevista`; `test_itens_nao_entram_em_despesas_nem_consumo_nem_saldo` (após importar, `despesas_do_mes`, `consumo_do_mes` e saldo da conta iguais ao antes); `test_importar_e_atomico` (falha no meio, p.ex. categoria inexistente → nada gravado); `test_dias_previstos_configuraveis`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** o módulo copiando o estilo de `app/dominio/faturas.py` (`_escrita`, validação antes de gravar, `transacao`). Sugerir categoria dos itens por `categorizar` (regras "descrição contém") e, se vazio, deixar `None`. `lancamento_id` informado deve estar entre `creditos_candidatos` (revalidar na gravação); `None` cria a prevista.
- [ ] **Step 4: Run** → PASS; rodar `tests/test_fatura_totais.py` para garantir que os totais de caixa não mudaram.
- [ ] **Step 5: Commit** `feat(holerite): domínio de holerites (prévia, importação e vínculo)`.

---

### Task 4: Telas — importar, prévia, lista e itens

**Files:**
- Modify: `app/web/uploads.py` (novo `ArmazemHolerites` e `HoleritePendente`), `app/main.py` (registrar `app.state.holerites`), `app/web/rotas/__init__.py` (registrar router), `app/web/templates/importar/index.html` (nova seção), `app/web/templates/base.html` (link "Holerites")
- Create: `app/web/rotas/holerites.py`, `app/web/templates/holerites/{index.html,previa.html,_itens.html,_item_categoria.html}`
- Test: `tests/test_web_holerites.py`

**Interfaces:**
- Consumes: Task 2 (`ler_holerite`, `HoleriteIlegivel`) e Task 3 (`preparar_previa`, `importar_holerite`, `alterar_categoria_item`, `detalhe`, erros). Reaproveitar `_ler_pdf`/`LIMITE_BYTES`/`ErroImportacao`/`_pagina_inicial` de `faturas.py`/`importar.py` sem duplicar (extrair helper comum só se necessário).
- Produces (rotas): `POST /holerites/previa` (arquivo PDF + `conta_id`) → prévia; `POST /holerites/confirmar` (`token`, `categoria[]`, `credito` = id do lançamento ou vazio, `substituir`) → 303 para `/holerites`; `GET /holerites?ano=AAAA` lista por competência (bruto, descontos, líquido, status do crédito: "recebido em DD/MM" ou "previsto para DD/MM"); `GET /holerites/{id}/itens` fragmento HTMX com itens; `POST /holerites/itens/{item_id}/categoria` altera categoria. `ArmazemHolerites.guardar(nome: str, arquivo_hash: str, holerite: HoleriteLido, conta_id: int) -> str` e `.obter(token)`/`.descartar(token)` (TTL e limite do `_Armazem`).

- [ ] **Step 1: Write failing tests** em `tests/test_web_holerites.py` (padrão de `test_web_faturas.py`, PDFs gerados por monkeypatch de `ler_holerite` com `HoleriteLido` sintético): `test_importar_mostra_secao_holerite`; `test_previa_mostra_totais_itens_conferencia_e_credito_encontrado`; `test_previa_sem_credito_mostra_data_prevista`; `test_confirmar_grava_e_redireciona`; `test_confirmar_token_expirado_pede_novo_envio`; `test_arquivo_nao_pdf_recusado_sem_gravar`; `test_confirmacao_dupla_retorna_ja_importado`; `test_substituir_exige_marcacao`; `test_lista_por_ano_e_expande_itens`; `test_categoria_do_item_altera_e_responde_fragmento`; `test_respostas_com_cache_no_store`; `test_pagina_nao_vaza_dados_pessoais` (nome/CPF/CNPJ presentes só no texto lido nunca aparecem no HTML).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** rotas e templates seguindo `rotas/faturas.py` e `templates/faturas/*`: erros de regra com mensagem própria, qualquer outro erro vira "Não foi possível importar o holerite. Nada foi gravado." (loga só o tipo da exceção). Seção em Importar: arquivo `.pdf` até 5 MB, seletor de conta corrente (só contas não-cartão), botão "Ver prévia do holerite". Prévia: tipo e competência, tabela de proventos e descontos com categoria editável por item, quadro de conferência, bloco "Crédito no extrato" (lista de candidatos com opção "nenhum, prever entrada em DD/MM"), aviso de substituir quando `situacao == "substitui"`.
- [ ] **Step 4: Run** `tests/test_web_holerites.py tests/test_web_importar.py tests/test_web_base.py tests/test_web_design_system.py -q` → PASS.
- [ ] **Step 5: Verify in the browser** (Chrome DevTools MCP) com holerite sintético: fluxo envio → prévia → confirmar → lista → expandir itens; tela estreita (celular) sem rolagem horizontal.
- [ ] **Step 6: Commit** `feat(holerite): telas de importação, prévia e lista de holerites`.

---

### Task 5: Consultor (somente leitura), skill e documentação

**Files:**
- Modify: `app/consultor/consultas.py` (função `holerites`), `app/consultor/cli.py` (comando `holerites`), `.claude/skills/` (nova `holerite/SKILL.md`), `tests/test_consultor_skills.py` (`ESPERADAS` ganha `"holerite"`), `CLAUDE.md`, `README.md`, `docs/PASSO-A-PASSO.md`
- Test: `tests/test_consultor_holerites.py`

**Interfaces:**
- Consumes: `repo.holerites.listar/obter/itens/total_por_descricao`.
- Produces: `consultas.holerites(con, hoje: date, ano: int | None = None, item: str | None = None) -> dict` com `holerites[]` (`id, competencia, tipo, bruto, descontos, liquido, credito` onde `credito` = `{"status": "recebido"|"previsto", "data_br": ...}`), `totais_ano` (bruto, descontos, líquido do ano) e, com `item`, `total_item` (soma dos descontos cujo texto contém o termo, sem acento/caixa, ex.: `pensao`); `consultas.holerite_detalhe(con, holerite_id: int) -> dict` com `itens[]` (`descricao, natureza, valor, referencia, categoria`). CLI: `holerites [--ano AAAA] [--item TERMO]` e `holerites ver ID`.

- [ ] **Step 1: Write failing tests** em `tests/test_consultor_holerites.py` (padrão de `test_consultor_faturas.py`, JSON UTF-8, sem Traceback): `test_holerites_lista_e_totais_do_ano`; `test_holerites_item_soma_descontos_por_termo` (pensão em dois meses soma ambos; INSS não entra); `test_holerites_ver_lista_itens`; `test_holerites_sem_dados_retorna_lista_vazia`; `test_importar_do_consultor_continua_recusando_pdf`; e em `test_consultor_skills.py` `test_skill_holerite_existe_e_comando_valido` via `ESPERADAS`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** consultas e comando; skill `holerite` explicando: consultar `holerites`, sempre dizer o período analisado, **não** importar PDF (só pelo painel), tratar pensão/consignado como dado sensível e não repetir em outro lugar além da resposta. CLAUDE.md: acrescentar `holerites` à lista de consultas e a regra de que holerite só se importa pelo painel. README e PASSO-A-PASSO: seção curta "Importar holerite".
- [ ] **Step 4: Run** `tests/test_consultor_*.py` → PASS.
- [ ] **Step 5: Commit** `feat(holerite): consultor holerites, skill e documentação`.

---

### Task 6: Validação local com os PDFs reais e fechamento

**Files:**
- Create: `scripts/validar_holerites_locais.py`
- Modify: `.gitignore` (garantir `Holerites/` ignorada)
- Test: `tests/test_holerite_script_local.py`

**Interfaces:**
- Consumes: `ler_holerite` e `preparar_previa`. Produces: script `python scripts/validar_holerites_locais.py <pasta>` que lê todos os PDFs da pasta e imprime por arquivo apenas: tipo, competência, quantidades de proventos/descontos, `confere` e líquido mascarado (`R$ 1.2**,**`); nunca descrições, empresa nem nome.

- [ ] **Step 1: Write failing test** `test_script_nao_imprime_dados_pessoais_nem_descricoes` (pasta temporária com PDF sintético gerado em memória; saída não contém empresa/descrições; contém "confere").
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** o script no padrão de `scripts/validar_faturas_locais.py`. **Step 4: Run** o teste → PASS.
- [ ] **Step 5: Run** o script na pasta real `C:\Users\rafae\OneDrive\Documentos\Holerites` e conferir: 2 arquivos lidos, ambos `confere`.
- [ ] **Step 6: Run** a suíte completa `.venv/Scripts/python -m pytest -q` → todos verdes (hoje 988).
- [ ] **Step 7: Commit** `feat(holerite): script de validação local e fechamento`. Depois do OK do dono: merge na `main`, `git push`, e na VPS `cd ~/consultorfin && git pull && sudo systemctl restart consultorfin` (a migração v10 roda sozinha na primeira abertura; o backup semanal já cobre o banco).
