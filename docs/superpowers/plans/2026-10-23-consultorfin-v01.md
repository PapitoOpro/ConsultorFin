# ConsultorFin v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Painel financeiro pessoal local (FastAPI) que importa o extrato CSV do Itaú, concilia o saldo, controla contas a pagar (parcelas, fixas, receitas recorrentes) e projeta os próximos meses.

**Architecture:** SQLite local; regras de negócio em `app/dominio/` (funções puras ou recebendo `sqlite3.Connection`), SQL isolado em `app/db.py` (conexão e migrações) e `app/repo/` (consultas). Rotas FastAPI finas, templates Jinja2 + HTMX, gráficos ECharts embutido.

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, HTMX, ECharts, SQLite, pandas, openpyxl, pytest, httpx (TestClient).

**Spec:** `docs/superpowers/specs/2026-10-23-consultorfin-v01-design.md` (e `CLAUDE.md`, que traz esquema base, regras do Itaú, paleta e telas).

## Global Constraints

- Python 3.11+; type hints em toda função pública; erros de importação com mensagem em português.
- Dinheiro sempre em **centavos inteiros** (negativo = saída). Nunca `float`.
- Tela: `R$ 1.234,56` e `DD/MM/AAAA`; banco: ISO `AAAA-MM-DD`. Valores em fonte monoespaçada, alinhados à direita.
- Paleta e fontes do CLAUDE.md seção 5; sem sombras; **sem CDN** (Inter, JetBrains Mono e ECharts dentro de `app/web/static/`).
- `data/`, `*.csv` de dados, `*.xls*`, `*.db`, `.env` no `.gitignore`. Fixtures só anonimizadas.
- Domínio e rotas nunca importam `sqlite3`; só `app/db.py` e `app/repo/`.
- Tipos de lançamento: `entrada`, `saida`, `transferencia`, `pagamento_fatura`. Até a v0.2, `pagamento_fatura` **conta como despesa**; `transferencia` não conta.
- Janela "a vencer": 7 dias. Horizonte da projeção: seletor 3/6/12 meses, padrão 6. Categorias iniciais: Moradia, Mercado, Transporte, Saúde, Lazer, Assinaturas, Educação, Outros.
- Teste de aceite: extrato de exemplo, saldo R$ 2.148,08 em 24/07/2026 → R$ 1.409,14 em 22/10/2026.
- Commits pequenos; mensagem termina com `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Funções recebem `hoje: date` por parâmetro (nunca `date.today()` no domínio).

## Review Focus

1. CSV do Itaú com BOM, UTF-8 em vez de Latin-1, linha malformada ou vazia: erro claro em português listando a linha, sem importar nada em silêncio (Task 3).
2. Parcelamento com total que não divide (R$ 100,00 em 3), 1 parcela, 0 ou valor ≤ 0: centavos exatos, soma = total, entradas inválidas rejeitadas (Task 7).
3. Conta fixa no dia 31 em fevereiro (inclusive ano bissexto) e geração repetida ao reabrir o app: sem duplicar nem pular (Task 8).
4. Uma linha importada que combina com dois pendentes parecidos, ou um pendente que combina com duas linhas: nunca casa duas vezes sem confirmação (Task 9).
5. Dois PIX idênticos no mesmo dia, em arquivos que se sobrepõem: continuam sendo dois e não duplicam (Task 4).

---

## Estrutura de arquivos

```
app/main.py                    # create_app() -> FastAPI
app/config.py                  # DB_PATH, MESES_JANELA, JANELA_VENCER_DIAS
app/db.py                      # conectar(), migrar()
app/repo/{contas,lancamentos,categorias,recorrentes,importacoes}.py
app/dominio/{dinheiro,lancamentos,saldo,categorias,parcelas,recorrentes,casamento,projecao}.py
app/importacao/{base,itau_csv,excel,deteccao,servico}.py
app/web/{rotas/,templates/,static/}
tests/{conftest.py,fixtures/,...}
```

## Task 1: Base do projeto e banco

**Files:** Create `requirements.txt`, `.gitignore`, `app/config.py`, `app/db.py`, `app/__init__.py`, `tests/conftest.py`, `tests/test_db.py`.

**Interfaces:**
- Produces: `conectar(caminho: Path | None = None) -> sqlite3.Connection` (Row factory, `foreign_keys=ON`; `None` usa `config.DB_PATH`, `":memory:"` aceito); `migrar(con) -> None` (versionada por `PRAGMA user_version`, idempotente); fixture pytest `con` (memória, já migrada).
- Esquema = CLAUDE.md seção 7 **mais**: `lancamentos` ganha `data_compensacao TEXT`, `compensado INTEGER NOT NULL DEFAULT 1`, `estimado INTEGER NOT NULL DEFAULT 0`, `grupo_parcela_uuid TEXT`, `parcela_atual INTEGER`, `parcela_total INTEGER`, `recorrente_id INTEGER REFERENCES recorrentes(id)`; `lancamentos.data` é o vencimento (pendente) ou a data do movimento (importado). Tabela `recorrentes(id, uuid, nome, tipo CHECK IN ('entrada','saida'), dia INTEGER, valor_estimado_centavos, categoria_id, conta_id, ativa)`. Tabela `chaves_quitadas(chave TEXT PRIMARY KEY, pendente_id, importacao_id)`. Tabela `configuracoes(chave PRIMARY KEY, valor)`. Categorias iniciais semeadas na migração.

- [ ] **Step 1:** Escrever `test_migrar_cria_tabelas_e_categorias` (todas as tabelas existem; 8 categorias iniciais) e `test_migrar_idempotente` (rodar duas vezes não duplica categorias).
- [ ] **Step 2:** Implementar `conectar`/`migrar`, `requirements.txt` (fastapi, uvicorn, jinja2, python-multipart, pandas, openpyxl, pytest, httpx), `.gitignore`, `config.py`. Criar venv e `pip install -r requirements.txt`.
- [ ] **Step 3:** `pytest tests/test_db.py -v` → PASS.
- [ ] **Step 4:** Commit `feat: base do projeto e migrações`.

## Task 2: Dinheiro e datas

**Files:** Create `app/dominio/dinheiro.py`, `tests/test_dinheiro.py`.

**Interfaces:** Produces `para_centavos(texto: str) -> int` (aceita `"1.234,56"`, `"-603,89"`, `"R$ 10,00"`; `ValueError` em português se inválido), `formatar_brl(centavos: int) -> str` (`-123456` → `"- R$ 1.234,56"`), `data_br_para_iso(texto: str) -> str`, `iso_para_br(iso: str) -> str`, `somar_meses(d: date, n: int, dia: int | None = None) -> date` (ajusta dia ao fim do mês).

- [ ] **Step 1:** Testes: `para_centavos("1.234,56") == 123456`, `("-603,89") == -60389`, `("abc")` levanta `ValueError`; `formatar_brl(140914) == "R$ 1.409,14"`; `data_br_para_iso("22/10/2026") == "2026-10-22"`; `somar_meses(date(2026,1,31),1) == date(2026,2,28)`; `somar_meses(date(2028,1,31),1) == date(2028,2,29)`.
- [ ] **Step 2:** Implementar com `Decimal`/aritmética inteira.
- [ ] **Step 3:** `pytest tests/test_dinheiro.py -v` → PASS.
- [ ] **Step 4:** Commit `feat: utilitários de dinheiro e datas`.

## Task 3: Parser do Itaú CSV (e detecção)

**Files:** Create `app/importacao/{base,itau_csv,deteccao}.py`, `tests/fixtures/extrato_itau_exemplo.csv`, `tests/test_itau_csv.py`.

**Interfaces:**
- Produces (`base.py`): `@dataclass LinhaImportada(data: str, descricao: str, valor_centavos: int)`; `@dataclass ErroLeitura(linha: int, mensagem: str)`; `@dataclass ResultadoLeitura(linhas: list[LinhaImportada], saldos_dia: dict[str, int], saldo_anterior: tuple[str, int] | None, erros: list[ErroLeitura])`; `class Parser(Protocol): def ler(self, conteudo: bytes) -> ResultadoLeitura`.
- Produces: `ParserItauCsv().ler(...)`; `detectar(conteudo: bytes, nome: str) -> Parser | None`.
- Fixture **fabricada e anonimizada** (Latin-1, `;`, CRLF, cabeçalho do banco, linhas `SALDO ANTERIOR` e `SALDO TOTAL DISPONÍVEL DIA`, ordem decrescente) com saldo anterior 2.148,08 em 24/07/2026, soma dos lançamentos −738,94 e saldo final 1.409,14 em 22/10/2026; inclui descrição com data colada (`"PIX TRANSF RR Supo22/10"`) e dois lançamentos idênticos no mesmo dia.

- [ ] **Step 1:** Testes: cabeçalho ignorado; linhas `SALDO*` fora de `linhas`; `saldo_anterior == ("2026-07-24", 214808)`; `saldos_dia["2026-10-22"] == 140914`; soma de `valor_centavos` == −73894; datas em ISO; descrição original preservada; arquivo UTF-8 ou linha com valor inválido gera `ErroLeitura` em português (`"Linha 12: valor inválido 'x'"`), sem exceção; arquivo vazio → erro claro.
- [ ] **Step 2:** Implementar decodificando Latin-1 (aceitar BOM), linhas por `csv` com `;`. `detectar` reconhece o cabeçalho `data;lançamento;ag./origem;valor (R$);saldos (R$)`.
- [ ] **Step 3:** `pytest tests/test_itau_csv.py -v` → PASS.
- [ ] **Step 4:** Commit `feat: parser do extrato Itaú CSV`.

## Task 4: Contas, importação e deduplicação

**Files:** Create `app/repo/{contas,lancamentos,importacoes}.py`, `app/dominio/lancamentos.py`, `app/importacao/servico.py`, `tests/test_importacao.py`.

**Interfaces:**
- Consumes: Task 3 (`Parser`, `ResultadoLeitura`), Task 1 (`con`).
- Produces: `criar_conta(con, nome: str, tipo: str, instituicao: str | None = None, final_numero: str | None = None, saldo_inicial_centavos: int = 0, data_saldo_inicial: str | None = None) -> int`; `chave_deduplicacao(conta_uuid: str, data: str, descricao: str, valor_centavos: int, ocorrencia: int) -> str` (sha256 hex); `importar(con, conta_id: int, nome_arquivo: str, conteudo: bytes, parser: Parser, hoje: date) -> ResumoImportacao`; `@dataclass ResumoImportacao(importacao_id: int, total_lidos: int, novos: int, ignorados: int, erros: list[ErroLeitura])`; `desfazer_importacao(con, importacao_id: int) -> None`; `class ArquivoJaImportado(Exception)`. Lançamentos importados nascem `compensado=1`, `data_compensacao=data`, `origem='importado'`. Ocorrência = posição do lançamento idêntico (data, descrição, valor) dentro do arquivo, na ordem cronológica estável.

- [ ] **Step 1:** Testes: importar fixture cria N lançamentos; importar o mesmo arquivo de novo levanta `ArquivoJaImportado`; dois arquivos com período sobreposto (fatias da fixture) resultam no mesmo total que o arquivo inteiro; os dois lançamentos idênticos do mesmo dia continuam 2; `desfazer_importacao` remove só os importados dela, preserva manuais, marca `desfeita=1` e permite reimportar o mesmo arquivo.
- [ ] **Step 2:** Implementar. Dedup consulta também `chaves_quitadas` (Task 9). Tudo numa transação; erro de leitura não impede importar linhas válidas, mas é devolvido no resumo.
- [ ] **Step 3:** `pytest tests/test_importacao.py -v` → PASS.
- [ ] **Step 4:** Commit `feat: importação com deduplicação`.

## Task 5: Saldo e conciliação

**Files:** Create `app/dominio/saldo.py`, `tests/test_saldo.py`.

**Interfaces:**
- Produces: `saldo_em(con, conta_id: int, data_iso: str) -> int` (saldo inicial + soma de lançamentos `compensado=1` com `data_compensacao <= data_iso`, só se `data_iso >= data_saldo_inicial`); `saldo_atual(con, conta_id: int, hoje: date) -> int`; `conciliar(con, conta_id: int, saldos_dia: dict[str, int]) -> Conciliacao`; `@dataclass Conciliacao(ok: bool, primeira_divergencia: str | None, esperado_centavos: int | None, calculado_centavos: int | None)`.

- [ ] **Step 1:** Teste de aceite: conta com saldo inicial 214808 em 2026-07-24, importar a fixture → `saldo_em(..., "2026-10-22") == 140914` e `conciliar(...).ok`. Teste negativo: alterar um valor importado faz `primeira_divergencia` apontar a data do primeiro dia errado.
- [ ] **Step 2:** Implementar (comparar cada dia de `saldos_dia` em ordem crescente; considerar só lançamentos `origem='importado'` na conciliação, conforme spec seção 7).
- [ ] **Step 3:** `pytest tests/test_saldo.py -v` → PASS.
- [ ] **Step 4:** Commit `feat: saldo e conciliação`.

## Task 6: Tipos e categorias

**Files:** Create `app/dominio/categorias.py`, `app/repo/categorias.py`, `tests/test_categorias.py`.

**Interfaces:**
- Produces: `classificar_tipo(descricao: str, valor_centavos: int, nome_titular: str | None, padroes_fatura: list[str]) -> str` (PIX/TED/TRANSF com o nome do titular → `transferencia`; padrão de fatura na descrição → `pagamento_fatura`; senão `entrada` se valor > 0, `saida` caso contrário); `categorizar(con, descricao: str) -> int | None` (maior prioridade primeiro, "contém" sem diferenciar maiúsculas); `criar_regra(con, padrao: str, categoria_id: int, prioridade: int = 0) -> int`; `aplicar_regras(con, importacao_id: int | None = None) -> int` (só lançamentos sem categoria); CRUD de categorias e subcategorias; `despesas_do_mes(con, ano: int, mes: int) -> int` e `entradas_do_mes(...)`: contam `saida`/`pagamento_fatura` (e `entrada`) **compensados em contas não-cartão**, por `data_compensacao`; `transferencia` e lançamentos de contas `cartao` ficam de fora. Padrões de fatura padrão: `["PAG FATURA", "FATURA CARTAO", "PAGAMENTO CARTAO"]`; `nome_titular` e padrões lidos de `configuracoes`. `importar` chama `classificar_tipo` e `aplicar_regras`.

- [ ] **Step 1:** Testes: `PIX TRANSF <titular>` → `transferencia` e fora de `despesas_do_mes`; descrição com `PAG FATURA` → `pagamento_fatura` e **dentro** de `despesas_do_mes`; regra de maior prioridade vence; lançamento em conta `cartao` não entra em `despesas_do_mes`.
- [ ] **Step 2:** Implementar.
- [ ] **Step 3:** `pytest tests/test_categorias.py -v` → PASS.
- [ ] **Step 4:** Commit `feat: tipos de lançamento e categorias`.

## Task 7: Lançamentos manuais e parcelamento

**Files:** Create `app/dominio/parcelas.py`, `tests/test_parcelas.py`; Modify `app/repo/lancamentos.py`.

**Interfaces:**
- Produces: `criar_lancamento_manual(con, *, conta_id: int, data: date, descricao: str, valor_centavos: int, categoria_id: int | None = None, compensado: bool = False, observacao: str | None = None) -> int` (`origem='manual'`, chave `manual:<uuid>`); `dividir_em_parcelas(total_centavos: int, n: int) -> list[int]` (última parcela absorve a diferença; `ValueError` se `n < 1` ou `total <= 0`); `criar_parcelamento(con, *, descricao: str, valor_total_centavos: int, n_parcelas: int, primeiro_vencimento: date, conta_id: int, categoria_id: int | None) -> list[int]` (saídas pendentes negativas, mesmo `grupo_parcela_uuid`, vencimentos mensais com ajuste de fim de mês); `marcar_pago(con, lancamento_id: int, valor_real_centavos: int | None, data_pagamento: date) -> None` (limpa `estimado`); `editar_grupo_parcelas(con, grupo_uuid: str, *, valor_total_centavos: int | None = None, n_parcelas: int | None = None) -> None` e `excluir_grupo_parcelas(con, grupo_uuid: str) -> None` (atuam só em parcelas pendentes).

- [ ] **Step 1:** Testes: `dividir_em_parcelas(10000, 3) == [3333, 3333, 3334]`; soma sempre igual ao total; `n=1`; `n=0`, `total=0` e `total<0` levantam `ValueError`; parcelamento de 10 gera `parcela_atual/parcela_total` 1/10..10/10; vencimento 31/01 + 1 mês = 28/02; `excluir_grupo_parcelas` não toca parcelas já pagas; `marcar_pago` com valor real troca o valor.
- [ ] **Step 2:** Implementar.
- [ ] **Step 3:** `pytest tests/test_parcelas.py -v` → PASS.
- [ ] **Step 4:** Commit `feat: lançamento manual e parcelamento`.

## Task 8: Contas fixas e receitas recorrentes

**Files:** Create `app/dominio/recorrentes.py`, `app/repo/recorrentes.py`, `tests/test_recorrentes.py`.

**Interfaces:**
- Produces: `criar_recorrente(con, *, nome: str, tipo: str, dia: int, valor_estimado_centavos: int, conta_id: int, categoria_id: int | None) -> int`; `gerar_recorrentes(con, hoje: date, meses: int) -> int` (materializa pendentes `estimado=1` do mês corrente até `meses` à frente; idempotente por `(recorrente_id, mês)`; dia ajustado ao fim do mês; devolve quantidade criada); `confirmar_recorrente(con, lancamento_id: int, valor_real_centavos: int, data_pagamento: date) -> None` (marca pago, `estimado=0`, e atualiza o valor dos **meses futuros pendentes ainda estimados** do mesmo recorrente para o valor real); `pular_mes(con, lancamento_id: int) -> None`; `desativar_recorrente(con, recorrente_id: int) -> None` (apaga os pendentes futuros). Valor da geração: último valor real confirmado; sem nenhum, `valor_estimado_centavos`. `tipo='saida'` gera valores negativos.

- [ ] **Step 1:** Testes: gerar duas vezes não duplica; dia 31 em fevereiro de 2027 → 28 e em 2028 → 29; confirmar luz com 23000 altera os futuros estimados para 23000 e não o mês já pago; `pular_mes` remove só aquele mês e a geração seguinte não o recria; `desativar` apaga futuros pendentes e preserva os pagos; receita (`entrada`) gera valor positivo.
- [ ] **Step 2:** Implementar (marcar mês pulado numa coluna `ignorado` ou tabela auxiliar; escolha do construtor, coberta pelo teste).
- [ ] **Step 3:** `pytest tests/test_recorrentes.py -v` → PASS.
- [ ] **Step 4:** Commit `feat: contas fixas e receitas recorrentes`.

## Task 9: Casamento pendente × extrato

**Files:** Create `app/dominio/casamento.py`, `tests/test_casamento.py`; Modify `app/importacao/servico.py` (não casa sozinho; só expõe propostas), `app/repo/importacoes.py` (undo reabre pendentes).

**Interfaces:**
- Produces: `propor_casamentos(con, conta_id: int, importacao_id: int) -> list[Casamento]`; `@dataclass Casamento(pendente_id: int, lancamento_id: int, pontuacao: float)`; `confirmar_casamento(con, pendente_id: int, lancamento_id: int) -> None` (numa transação: pendente recebe valor real, `compensado=1`, `data_compensacao` da linha, `estimado=0`, `importacao_id`; a linha importada é removida e sua chave vai para `chaves_quitadas`); `desfazer_importacao` (Task 4) passa a reabrir pendentes casados (`compensado=0`, `data_compensacao=NULL`) e apagar as chaves de `chaves_quitadas` da importação.
- Critério da proposta: mesmo sinal, valor dentro de ±5% ou ±R$ 5,00 (o maior), data da linha entre 10 dias antes e 10 dias depois do vencimento, pontuação maior com descrição parecida; cada pendente e cada linha em no máximo **uma** proposta (melhor pontuação primeiro).

- [ ] **Step 1:** Testes: pendente Luz 21000 e linha `-23000` seis dias depois → uma proposta; confirmar → saldo conta uma só vez, lançamento importado não duplica, reimportar o mesmo arquivo não recria a linha; duas linhas candidatas para um pendente → só a de maior pontuação é proposta; dois pendentes e uma linha → uma proposta; desfazer a importação reabre o pendente.
- [ ] **Step 2:** Implementar.
- [ ] **Step 3:** `pytest tests/test_casamento.py -v` → PASS.
- [ ] **Step 4:** Commit `feat: casamento de pendentes com o extrato`.

## Task 10: Vencimentos e projeção

**Files:** Create `app/dominio/projecao.py`, `tests/test_projecao.py`.

**Interfaces:**
- Produces: `vencimentos(con, hoje: date, janela_dias: int = 7) -> Vencimentos`; `@dataclass Vencimentos(vencidas: list[sqlite3.Row], a_vencer: list[sqlite3.Row])` (pendentes com `data < hoje` e `hoje <= data <= hoje+janela`; saídas apenas); `projetar(con, hoje: date, meses: int) -> list[PontoProjecao]`; `@dataclass PontoProjecao(mes: str, parcelas_centavos: int, saida_pendente_centavos: int, entrada_pendente_centavos: int, saldo_final_centavos: int)`. Saldo inicial = soma dos saldos atuais das contas **não-cartão**; cada mês subtrai pendentes de saída (inclusive em contas `cartao`) e soma pendentes de entrada; vencidas contam no primeiro mês; `parcelas_centavos` = só lançamentos com `grupo_parcela_uuid`.

- [ ] **Step 1:** Testes: pendente de ontem é vencida e de daqui a 7 dias é a vencer, de daqui a 8 não; projeção com saldo 100000, parcela 30000 em cada um de 3 meses → saldos 70000, 40000, 10000; receita pendente de 50000 no mês 2 → 70000, 90000, 60000; recorrente `estimado` entra; compensados e do passado não entram; `meses` = 3, 6 e 12 devolvem esse número de pontos.
- [ ] **Step 2:** Implementar.
- [ ] **Step 3:** `pytest tests/test_projecao.py -v` → PASS.
- [ ] **Step 4:** Commit `feat: vencimentos e projeção`.

## Task 11: Base web (app, layout, tema, fontes)

**Files:** Create `app/main.py`, `app/web/templates/base.html`, `app/web/static/{css/tema.css,js/htmx.min.js,js/echarts.min.js,fontes/*}`, `app/web/rotas/__init__.py`, `tests/test_web_base.py`.

**Interfaces:** Produces `create_app(caminho_db: Path | None = None) -> FastAPI` (migra o banco na subida, injeta `con` por dependência `get_con`); filtros Jinja `brl` e `data_br` (Task 2); `base.html` com menu lateral (Início, Lançamentos, Importar, Contas a pagar) e blocos `titulo` e `conteudo`; `tema.css` com as variáveis exatas do CLAUDE.md seção 5, bordas 1px, sem sombras; `uvicorn app.main:app` na porta 8000. HTMX, ECharts, Inter e JetBrains Mono baixados uma vez (npm/fontsource ou releases oficiais) e **commitados** em `static/`.

- [ ] **Step 1:** Testes: `GET /` → 200 e contém `lang="pt-BR"`; `GET /static/css/tema.css` contém `--fundo: #121212`; nenhum `http://` ou `https://` em `base.html` e `tema.css` (sem CDN).
- [ ] **Step 2:** Implementar e baixar os assets.
- [ ] **Step 3:** `pytest tests/test_web_base.py -v` → PASS.
- [ ] **Step 4:** Commit `feat: base web e tema`.

## Task 12: Tela Importar

**Files:** Create `app/web/rotas/importar.py`, `app/web/templates/importar/*.html`, `app/importacao/excel.py`, `tests/test_web_importar.py`.

**Interfaces:** Rotas `GET /importar`, `POST /importar/previa` (upload, detecta formato/conta, mostra primeiras linhas e erros), `POST /importar/confirmar`, `POST /importar/{id}/desfazer`; `ParserExcel(mapeamento: dict[str, str]).ler(conteudo) -> ResultadoLeitura` e mapeamento manual de colunas (data, descrição, valor) quando `detectar` devolve `None`. Resumo antes de confirmar (novos / já existentes / erros), conciliação após importar, propostas de casamento (Task 9) com botões de confirmar, histórico de importações com desfazer. Cadastro de conta com saldo inicial acessível desta tela (pede saldo na data de início usando `saldo_anterior` do arquivo como sugestão).

- [ ] **Step 1:** Testes com `TestClient`: upload da fixture → prévia 200 com a primeira linha formatada (`R$`, `DD/MM/AAAA`); confirmar → resumo mostra novos; segundo upload do mesmo arquivo → mensagem "arquivo já importado" em português; arquivo inválido → mensagem de erro, nenhum lançamento criado; desfazer remove lançamentos importados; CSV genérico sem formato conhecido → tela de mapeamento de colunas.
- [ ] **Step 2:** Implementar.
- [ ] **Step 3:** `pytest tests/test_web_importar.py -v` → PASS.
- [ ] **Step 4:** Commit `feat: tela Importar`.

## Task 13: Tela Lançamentos

**Files:** Create `app/web/rotas/lancamentos.py`, `app/web/templates/lancamentos/*.html`, `tests/test_web_lancamentos.py`.

**Interfaces:** `GET /lancamentos` com filtros (período, conta, categoria, tipo, texto) e totais do filtro (entradas, saídas, líquido), saldo acumulado por linha, indicador importado/manual; HTMX: editar categoria inline, categorizar em lote, criar lançamento manual, criar regra a partir de uma linha, marcar `transferencia`/`pagamento_fatura`. Lógica em `app/dominio/lancamentos.py` (`listar(con, filtros: Filtros) -> PaginaLancamentos`).

- [ ] **Step 1:** Testes: filtro por texto reduz linhas e recalcula totais; lote atribui categoria a N linhas; criar regra a partir de linha categoriza os outros lançamentos sem categoria; marcar tipo remove a linha de `despesas_do_mes`; lançamento manual aparece com indicador "manual".
- [ ] **Step 2:** Implementar.
- [ ] **Step 3:** `pytest tests/test_web_lancamentos.py -v` → PASS.
- [ ] **Step 4:** Commit `feat: tela Lançamentos`.

## Task 14: Tela Contas a pagar

**Files:** Create `app/web/rotas/contas_pagar.py`, `app/web/templates/contas_pagar/*.html`, `tests/test_web_contas_pagar.py`.

**Interfaces:** `GET /contas-a-pagar` (vencidas em âmbar, a vencer, parcelamentos agrupados "3/10", recorrentes com marca "estimado"); formulários: parcelamento (Task 7), conta fixa/receita recorrente (Task 8), confirmar valor real e marcar pago, pular mês, desativar, editar/excluir grupo de parcelas; cadastro de contas e cartões (`tipo='cartao'`). `gerar_recorrentes(con, hoje, meses)` é chamado ao abrir o app e ao abrir esta tela, com `meses` = horizonte escolhido.

- [ ] **Step 1:** Testes: criar parcelamento 100,00 em 3x → 3 linhas "1/3..3/3" somando R$ 100,00; criar conta fixa gera pendentes estimados; confirmar com valor real remove a marca "estimado"; valor inválido (0, texto) → erro em português sem criar nada; pendente vencido aparece na seção vencidas.
- [ ] **Step 2:** Implementar.
- [ ] **Step 3:** `pytest tests/test_web_contas_pagar.py -v` → PASS.
- [ ] **Step 4:** Commit `feat: tela Contas a pagar`.

## Task 15: Tela Início

**Files:** Create `app/web/rotas/inicio.py`, `app/web/templates/inicio/*.html`, `app/web/static/js/graficos.js`, `tests/test_web_inicio.py`.

**Interfaces:** `GET /?mes=AAAA-MM&horizonte=6` seguindo o mockup `.superpowers/brainstorm/821-1792756663/content/inicio-minimalista.html` e a spec seção 4: saldo por conta (verde/vermelho) e total; entradas, saídas e sobra do mês; bloco de vencimentos em âmbar; rosca de gastos por categoria; colunas de parcelas e de saldo projetado (mês negativo em âmbar), com seletor de horizonte 3/6/12; barras de maiores categorias; entradas × saídas mês atual × anterior; tabela expansível Categoria › Subcategoria › Lançamentos com total; estado vazio com botão "Importar primeiro extrato". Dados dos gráficos vêm de `GET /inicio/dados.json`; `graficos.js` só renderiza com ECharts, cores das variáveis CSS.

- [ ] **Step 1:** Testes: banco vazio → página contém "Importar primeiro extrato"; com fixture importada → saldo `R$ 1.409,14` em verde; saldo negativo aparece com a classe de saída; vencida aparece no bloco âmbar; `dados.json` com `horizonte=3` devolve 3 pontos e `horizonte=12` devolve 12; mês inválido → 400 em português.
- [ ] **Step 2:** Implementar; conferir visualmente no navegador (skill `run`) contra o mockup.
- [ ] **Step 3:** `pytest tests/test_web_inicio.py -v` → PASS.
- [ ] **Step 4:** Commit `feat: tela Início`.

## Task 16: Revisão final

**Files:** Modify `README.md`.

- [ ] **Step 1:** `pytest -v` completo → tudo PASS, incluindo o teste de aceite da Task 5.
- [ ] **Step 2:** `git ls-files` não lista `data/`, `*.db`, CSV/Excel reais nem `.env`; fixtures só anonimizadas; nenhum `http(s)://` externo em templates/CSS/JS.
- [ ] **Step 3:** README com como instalar e rodar (`uvicorn app.main:app`), onde ficam os dados e como importar o primeiro extrato.
- [ ] **Step 4:** Commit `docs: README e revisão final`.
