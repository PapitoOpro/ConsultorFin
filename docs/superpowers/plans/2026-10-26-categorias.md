# Categorias (Entrada/Saída › Categoria › Subcategoria) e tela de edição: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trocar as categorias iniciais pela lista definida pelo dono (`docs/categorias-padrao.md`: tipo Entrada/Saída, categoria, subcategoria) e criar a tela **Categorias** para adicionar, renomear, mover e excluir categorias e subcategorias.

**Architecture:** Migração v6 acrescenta `tipo` a `categorias` e carrega a lista padrão a partir de um módulo de dados (`app/dominio/taxonomia.py`), reconciliando as 8 categorias antigas sem perder lançamentos. Regras de edição ficam em `app/dominio/categorias.py` (SQL em `app/repo/categorias.py`); a tela é um novo router/templates no padrão das outras telas; os seletores de categoria das demais telas passam a mostrar a hierarquia.

**Tech Stack:** Python 3.11+, FastAPI/Jinja2/HTMX já existentes, SQLite, pytest. Nenhuma dependência nova.

**Spec:** sem documento de design separado; decisões abaixo (aprovadas em conversa em 26/10/2026) e `docs/categorias-padrao.md` (fonte dos dados).

## Decisões

- Estrutura de 2 níveis: categoria (raiz) › subcategoria. Cada categoria tem **tipo** `entrada` ou `saida`; a subcategoria herda o tipo da categoria; mudar o tipo de uma categoria muda o das filhas. Subcategoria dentro de subcategoria não é permitida.
- Nomes únicos entre irmãos (comparação sem diferenciar maiúsculas, acentos e espaços nas pontas). Categorias raiz têm nome único entre todas as raízes, qualquer que seja o tipo. O mesmo nome de subcategoria pode existir em categorias diferentes (ex.: "Manutenção e reparos" em Moradia e em Transporte e Veículo).
- Lançamento e categoria: o tipo da categoria só filtra o que os formulários oferecem (lançamento positivo oferece categorias de entrada; negativo, de saída); o domínio não bloqueia combinações diferentes (um estorno pode cair em outra categoria), e regras automáticas continuam valendo por descrição.
- Excluir: recusado se a categoria tem subcategorias. Se estiver em uso (lançamentos, contas fixas/recorrentes, regras automáticas), o usuário escolhe **substituir por** outra categoria do mesmo tipo (tudo é reapontado, inclusive as regras) ou **deixar sem categoria** (lançamentos e recorrentes ficam sem categoria e as regras daquela categoria são apagadas). Nada é apagado em silêncio: a tela mostra quantos itens serão afetados e pede confirmação.
- Categorias antigas (Moradia, Mercado, Transporte, Saúde, Lazer, Assinaturas, Educação, Outros): as que têm o mesmo nome de uma categoria da nova lista (Moradia, Saúde, Educação) são aproveitadas; as demais são removidas se não estiverem em uso (sem lançamentos, recorrentes, regras ou filhas) e mantidas como categorias de saída se estiverem em uso. Nenhum lançamento perde categoria.
- Fora do escopo desta entrega: comandos de escrita de categoria no comando de consulta do Claude Code (o `categorias` apenas passa a mostrar tipo e hierarquia), importar/exportar categorias, ordenação manual, mesclar categorias.

## Global Constraints

- Python 3.11+; type hints nas funções públicas; mensagens ao usuário em português do Brasil; Jinja com autoescape, nenhum `|safe` em nome de categoria.
- NUNCA `import sqlite3` em `app/dominio`, `app/importacao`, `app/consultor`, `app/web`; SQL só em `app/db.py` e `app/repo/`; funções novas de repo não fazem `commit`; trabalho em várias etapas dentro de `transacao(con)` (`app.db`); antes de chamar uma função com `transacao` depois de escritas simples, `con.commit()`.
- Migrações são acrescentadas a `MIGRACOES` em `app/db.py` (a última hoje é a v5); a v6 deve funcionar em banco v5 com dados e ser idempotente. Teste de upgrade em banco v5 com dados é obrigatório.
- A lista padrão vive em `app/dominio/taxonomia.py` (dados puros); um teste lê `docs/categorias-padrao.md` e confere que o módulo tem exatamente as mesmas linhas.
- Testes primeiro, saída RED literal; suíte completa antes de cada commit (`.venv/Scripts/python -m pytest -q`); saída limpa; commits com `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Branch: `feat/categorias`.

## Review Focus

1. Upgrade de banco v5 com lançamentos, recorrentes e regras apontando para as categorias antigas: nada perde categoria; migração idempotente (rodar duas vezes não duplica) (Task 1).
2. Exclusão com "substituir por": atômica, reaponta lançamentos, recorrentes e regras; "deixar sem categoria" apaga só as regras da categoria; recusa quando há subcategorias (Task 1).
3. Nomes duplicados com variação de maiúsculas/acentos/espaços são recusados; subcategoria dentro de subcategoria é recusada; mudar o tipo da categoria propaga às filhas (Task 1).
4. Nome com `<script>` ou aspas aparece escapado em todas as telas (Task 2).
5. Os seletores das telas Lançamentos, Contas a pagar e Importar mostram "Categoria › Subcategoria" e oferecem só categorias compatíveis com o sinal do lançamento nos formulários de criação (Task 2).

---

## Estrutura de arquivos

```
app/dominio/taxonomia.py          # dados: lista (tipo, categoria, subcategoria)
app/db.py                         # + migração v6
app/repo/categorias.py            # + funções de árvore, uso, mover, reapontar
app/dominio/categorias.py         # + regras de edição (criar/renomear/mover/tipo/excluir)
app/dominio/lancamentos.py        # opcoes_categorias passa a devolver rótulo hierárquico e tipo
app/web/rotas/categorias.py       # tela /categorias
app/web/templates/categorias/*.html
app/consultor/consultas.py        # `categorias` mostra tipo e hierarquia
.claude/skills/categorizar/SKILL.md, docs/PASSO-A-PASSO.md, README.md
tests/test_categorias_*.py, tests/test_web_categorias.py
```

## Task 1: Dados, migração v6 e regras de edição

**Files:** Create `app/dominio/taxonomia.py`; Modify `app/db.py`, `app/repo/categorias.py`, `app/dominio/categorias.py`; Test `tests/test_taxonomia.py`, `tests/test_categorias_edicao.py`, `tests/test_db.py` (upgrade v5→v6).

**Interfaces:**
- Produces (`taxonomia.py`): `PADRAO: tuple[tuple[str, str, str], ...]` com `(tipo, categoria, subcategoria)` onde `tipo` é `"entrada"` ou `"saida"`, na ordem de `docs/categorias-padrao.md`.
- Produces (migração v6): coluna `categorias.tipo TEXT CHECK (tipo IN ('entrada','saida'))` (NULL permitido só para legado antes da migração; depois da v6 toda categoria tem tipo); índice único em `(pai_id, lower(nome))` tratando raízes (`pai_id IS NULL`) como grupo único; a carga da lista padrão e a reconciliação descrita em "Decisões" (idempotente, dentro da transação da migração). A migração não importa `app.dominio` (SQL puro dentro de `app/db.py`; a lista padrão é passada por import de `app.dominio.taxonomia`, que é só dados — permitido).
- Produces (repo): `arvore(con) -> list[dict]` (raízes com `tipo`, `filhas`, e contagem de uso por nó: lançamentos, recorrentes, regras), `uso(con, categoria_id) -> dict[str, int]`, `criar(con, nome, pai_id, tipo)`, `atualizar_tipo(con, categoria_id, tipo)` (cascata nas filhas), `mover(con, categoria_id, novo_pai_id)`, `reapontar(con, de_id, para_id) -> dict[str, int]`, `limpar(con, categoria_id) -> dict[str, int]` (zera categoria em lançamentos/recorrentes e apaga regras), `nome_existe(con, nome, pai_id, ignorar_id=None) -> bool` (comparação sem caixa/acento/espaços; use a mesma normalização do projeto, ex.: função SQL `sem_acento` registrada em `conectar`).
- Produces (domínio): `criar_categoria(con, nome, tipo)`, `criar_subcategoria(con, pai_id, nome)`, `renomear_categoria(con, id, nome)`, `alterar_tipo(con, id, tipo)`, `mover_subcategoria(con, id, novo_pai_id)` (só para categoria do mesmo tipo), `remover_categoria(con, id, substituir_por: int | None = None, deixar_sem: bool = False)` retornando o resumo do que foi reapontado/apagado, `class ErroCategoria(ValueError)` com mensagens em português; `arvore_para_tela(con)` (estrutura pronta para o template).

- [ ] **Step 1: Write the failing tests.** (a) `test_taxonomia.py`: `PADRAO` tem exatamente as linhas de `docs/categorias-padrao.md` (parse da tabela markdown no teste, mesma ordem), 21 entradas e 121 saídas, 6 categorias de entrada e 23 de saída, sem duplicata `(tipo, categoria, subcategoria)`. (b) `test_db.py`: banco novo termina com a lista padrão completa (categorias e subcategorias com o tipo certo) e as categorias antigas não usadas ausentes; banco v5 simulado com lançamentos/recorrentes/regras em "Moradia", "Mercado" (em uso, não consta na lista nova) e "Outros" (sem uso): depois da v6 nenhum lançamento perdeu categoria, "Moradia" é a mesma linha (mesmo id) agora com tipo saída e subcategorias novas, "Mercado" ficou como categoria de saída, "Outros" sumiu; rodar `migrar` de novo não muda nada (contagens iguais). (c) `test_categorias_edicao.py`: criar/renomear com duplicata (variando caixa, acento e espaços) recusado; subcategoria sob subcategoria recusada; `alterar_tipo` propaga às filhas; `mover_subcategoria` recusa categoria de tipo diferente; `remover_categoria` com filhas recusada; em uso sem escolha recusada com contagens na mensagem; `substituir_por` reaponta lançamentos, recorrentes e regras (inclusive exigindo tipo igual); `deixar_sem=True` zera e apaga só as regras daquela categoria; tudo atômico (falha no meio não deixa metade feita; simular com categoria destino inexistente).
- [ ] **Step 2: Run to verify they fail.** `.venv/Scripts/python -m pytest tests/test_taxonomia.py tests/test_categorias_edicao.py tests/test_db.py -q` → FAIL.
- [ ] **Step 3: Implement** `taxonomia.py` (dados, gerados a partir do markdown, sem lógica), a migração v6 em `app/db.py` e as funções de repo/domínio acima. Manter compatibilidade das funções já existentes que outras telas usam (`criar_categoria`, `criar_subcategoria`, `listar_categorias`, `listar_subcategorias`, `renomear_categoria`, `remover_categoria` com a assinatura antiga continuando válida onde possível — ajuste os chamadores existentes se a assinatura mudar).
- [ ] **Step 4: Run to verify they pass.** Mesmo comando e depois a suíte inteira → PASS.
- [ ] **Step 5: Commit** `feat: categorias padrão com tipo e regras de edição`.

## Task 2: Tela Categorias e seletores hierárquicos

**Files:** Create `app/web/rotas/categorias.py`, `app/web/templates/categorias/index.html` (e parciais); Modify `app/web/rotas/__init__.py`/`app/main.py` (registrar router), `app/web/templates/base.html` (item de menu "Categorias"), `app/dominio/lancamentos.py` (`opcoes_categorias`), os templates que usam seletores de categoria (`lancamentos/`, `contas_pagar/`, `importar/` se houver), `app/web/static/css/tema.css` (só se faltar algo); Test `tests/test_web_categorias.py` (+ ajustes nos testes web existentes).

**Interfaces:**
- Consumes: Task 1 (`arvore_para_tela`, `criar_categoria`, `criar_subcategoria`, `renomear_categoria`, `alterar_tipo`, `mover_subcategoria`, `remover_categoria`, `ErroCategoria`).
- Produces: `GET /categorias` (abas ou colunas **Entradas** e **Saídas**; cada categoria é um bloco expansível com suas subcategorias, contagem de uso e ações), `POST /categorias/criar`, `/categorias/{id}/subcategoria`, `/categorias/{id}/renomear`, `/categorias/{id}/tipo`, `/categorias/{id}/mover`, `/categorias/{id}/excluir`; `opcoes_categorias(con, tipo: str | None = None) -> list[tuple[int, str, str]]` devolvendo `(id, rótulo "Categoria › Subcategoria" ou só "Categoria", tipo)` ordenado por tipo, categoria e subcategoria; os `<select>` de categoria ficam agrupados (`<optgroup>` por categoria) e, nos formulários de criação, restritos ao tipo compatível com o sinal escolhido (Entrada/Saída).
- Excluir pela tela: se a categoria estiver em uso, a tela mostra quantos lançamentos, contas fixas/recorrentes e regras serão afetados e oferece "Substituir por…" (lista de categorias do mesmo tipo) ou "Deixar sem categoria", com confirmação; erros de `ErroCategoria` viram mensagem em português (400), nunca 500.

- [ ] **Step 1: Write the failing tests** (`TestClient`, banco temporário, relógio injetável quando preciso): `GET /categorias` lista a lista padrão (ex.: "Salário e Trabalho" na aba de entradas com "13º salário"; "Música e Equipamentos" em saídas) e o menu tem "Categorias"; criar categoria de saída e subcategoria aparece; nome duplicado (outra caixa/acento) → mensagem em português e nada criado; renomear; mudar tipo propaga e a tela reflete; mover subcategoria entre categorias do mesmo tipo e recusa entre tipos diferentes; excluir categoria sem uso funciona; excluir em uso mostra as contagens e exige escolha; "substituir por" reaponta (verificar num lançamento real, numa regra e numa recorrente); "deixar sem categoria" zera; excluir com subcategorias recusado; ids não numéricos/inexistentes → mensagem amigável; Origin cruzado em POST → 403 (middleware existente); nome `<script>alert(1)</script>` aparece escapado nas telas Categorias, Lançamentos (seletor e linhas) e Contas a pagar; os seletores de categoria de Lançamentos e Contas a pagar mostram "Moradia › Aluguel" e, no formulário de lançamento manual, escolher Entrada oferece só categorias de entrada.
- [ ] **Step 2: Run to verify they fail.** `.venv/Scripts/python -m pytest tests/test_web_categorias.py -q` → FAIL.
- [ ] **Step 3: Implement** o router e os templates seguindo o estilo das telas existentes (tema escuro, sem sombras, variáveis CSS do projeto; inline com HTMX opcional, mas funcional sem JS), a nova `opcoes_categorias` e a troca dos seletores, incluindo a atualização dos testes web existentes afetados pelo novo formato.
- [ ] **Step 4: Run to verify they pass.** Teste novo e suíte inteira → PASS.
- [ ] **Step 5: Verificação visual** (se tiver o navegador do Chrome DevTools disponível: iniciar o painel com banco temporário FORA do repositório pelo script `iniciar.bat` não, use um wrapper em pasta temporária com `create_app(caminho)` e porta de teste; capturar `/categorias` e conferir layout escuro, sem overflow horizontal, sem erros no console; diga claramente se não fez).
- [ ] **Step 6: Commit** `feat: tela Categorias com adicionar, renomear, mover e excluir`.

## Task 3: Comando de consulta, skills e documentação

**Files:** Modify `app/consultor/consultas.py` (`categorias`), `.claude/skills/categorizar/SKILL.md`, `CLAUDE.md` (apenas se citar categorias), `docs/PASSO-A-PASSO.md`, `README.md`; Test `tests/test_consultor_consultas.py`, `tests/test_consultor_skills.py` (se necessário).

- [ ] **Step 1: Write the failing tests:** `categorias` do comando de consulta devolve `{"categorias": [{"id", "nome", "tipo", "subcategorias": [{"id","nome"}]}]}` com os dados da lista padrão e continua sendo somente leitura (hash do banco igual); o teste de skills continua passando (comandos e opções citados existem).
- [ ] **Step 2: Run to verify they fail.** → FAIL.
- [ ] **Step 3: Implement** a mudança no comando; atualizar a skill `categorizar` (usar o id da subcategoria quando existir, senão o da categoria; mencionar que o tipo da categoria deve combinar com o sinal do lançamento; para criar/editar categorias dizer ao usuário para usar a tela **Categorias** do painel); documentar a tela no passo a passo (seção de organizar lançamentos: como adicionar/renomear/mover/excluir, o que acontece ao excluir em uso) e uma linha no README.
- [ ] **Step 4: Run to verify they pass.** Suíte inteira → PASS.
- [ ] **Step 5: Commit** `feat: categorias no consultor e documentação da tela Categorias`.
