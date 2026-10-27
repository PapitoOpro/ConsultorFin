# Fatura de cartão em PDF — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Importar PDF de fatura de cartão (Itaú, Porto, Mercado Pago) com prévia, itens categorizáveis, parcelas previstas e casamento com o pagamento no banco.

**Architecture:** Pacote `app/importacao/fatura/` lê PDF→texto→`FaturaLida` (funções puras por emissor, testadas com texto sintético). `app/dominio/faturas.py` grava fatura (1 linha em `lancamentos` + itens em `fatura_itens`), gera/baixa previstas e casa o pagamento. Web e consultor consomem o domínio.

**Tech Stack:** Python 3.11, FastAPI/Jinja2/HTMX, SQLite, pytest, `pdfplumber` + `pycryptodome` (novas).

**Spec:** `docs/superpowers/specs/2026-10-27-fatura-cartao-pdf-design.md` (ler antes; ele é a fonte das regras).

## Global Constraints

- Camadas (CLAUDE.md): SQL só em `app/db.py` e `app/repo/`; **nenhum** `import sqlite3` em `app/dominio`, `app/importacao`, `app/consultor`, `app/web` (usar `con: Any`); repo não faz commit; trabalho multi-passo em `transacao(con)` de `app.db`; rotas finas.
- Dinheiro em centavos inteiros (`int`), nunca float; despesa negativa, crédito positivo (convenção de `lancamentos`). Datas ISO no banco, `DD/MM/AAAA` e `R$ 1.234,56` na tela. Mensagens em pt-BR.
- Migrações: acrescentar `_migracao_8` ao fim de `MIGRACOES` em `app/db.py`; testar subindo um banco v7 populado.
- Privacidade: senha do PDF só em memória (nunca em log/erro/armazém); sem `print`/log de valores ou nomes; erros usam `eco()` (`app/importacao/base.py`); nenhuma chamada de rede; PDFs/valores reais nunca em testes, fixtures ou git.
- Type hints obrigatórios; logs estruturados se houver; testes com `.venv/Scripts/python -m pytest`.
- Branch `feat/fatura-pdf`; commits pequenos; mensagem termina com a linha `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

1. PDF protegido sem senha / senha errada / arquivo que não é PDF → erro claro, sem vazar a senha nem o conteúdo.
2. PDF sem texto (imagem) → `PdfSemTexto` com mensagem; nunca fatura vazia gravada.
3. Fatura de janeiro com compras de dezembro (e 29/02) → ano inferido correto.
4. Duas compras idênticas (mesma data, descrição, valor) → ambas mantidas; reenviar o mesmo PDF não duplica; PDF diferente da mesma fatura pede confirmação.
5. Mercado Pago (pagamentos/créditos com valor positivo, encargos como linha) e cartão adicional (Porto) → sinal e portador corretos; total não conta pagamentos como compra.

---

## Mapa de arquivos

- Criar `app/importacao/fatura/{__init__,modelo,texto,deteccao,itau,porto,mercadopago,conferencia}.py`
- Criar `app/repo/faturas.py`, `app/dominio/faturas.py`, `app/dominio/faturas_pagamento.py`
- Criar `app/web/rotas/faturas.py`, templates `app/web/templates/faturas/{previa,_itens,_casamento}.html`; alterar `importar/index.html`, `lancamentos/index.html`
- Alterar `app/db.py`, `app/repo/{lancamentos,painel,categorias}.py`, `app/dominio/{categorias,lancamentos}.py`, `app/consultor/{cli,consultas}.py`, `.gitignore`, `requirements.txt`, `docs/PASSO-A-PASSO.md`, `README.md`, `CLAUDE.md` (limitações)
- Testes: `tests/test_fatura_*.py`, fixtures sintéticas `tests/fixtures/faturas/*.txt`, helper `tests/faturas_util.py`
- Script local: `scripts/validar_faturas_locais.py`

---

### Task 1: Dependências e proteção da pasta de faturas

**Files:** Modify `.gitignore`, `requirements.txt`; Test `tests/test_fatura_texto.py` (criado na Task 3).

**Produces:** `pdfplumber`, `pycryptodome` instalados no `.venv`.

- [ ] **Step 1:** Em `.gitignore` acrescentar `Faturas Testes/`, `*.pdf` e `scripts/_saida/`. Verificar: `git check-ignore -v "Faturas Testes/x.pdf"` imprime a regra.
- [ ] **Step 2:** Em `requirements.txt` fixar versões exatas atuais (instalar e usar `pip freeze` filtrado): `pdfplumber`, `pycryptodome`. Rodar `.venv/Scripts/pip install -r requirements.txt`; `python -c "import pdfplumber, Crypto"` sem erro.
- [ ] **Step 3:** Rodar a suíte inteira (770 verdes). Commit `chore: dependências de PDF e ignorar faturas reais`.

---

### Task 2: Migração v8, repositório de faturas e tipo `fatura`

**Files:** Modify `app/db.py`, `app/dominio/lancamentos.py` (constantes de tipo); Create `app/repo/faturas.py`; Test `tests/test_db.py` (acrescentar), `tests/test_fatura_repo.py`.

**Interfaces — Produces** (todas em `app/repo/faturas.py`, `con: sqlite3.Connection`):
- `criar(con, *, conta_id, competencia, data_vencimento, data_fechamento, emissor, bandeira, produto, total_impresso_centavos, total_calculado_centavos, ajustes_resumo_centavos, arquivo_hash, arquivo_nome, criado_em: str) -> int`
- `obter(con, fatura_id) -> Row | None`; `por_conta_competencia(con, conta_id, competencia) -> Row | None`; `por_lancamento(con, lancamento_id) -> Row | None`
- `definir_lancamento(con, fatura_id, lancamento_id) -> None`; `atualizar_totais(con, fatura_id, impresso, calculado, ajustes, arquivo_hash, arquivo_nome) -> None`
- `inserir_item(con, fatura_id, *, ordem, data, descricao, valor_centavos, tipo, categoria_id, categoria_banco, parcela_atual, parcela_total, grupo_parcela_uuid, final_cartao) -> int`
- `itens(con, fatura_id) -> list[Row]` (ordem); `obter_item(con, item_id) -> Row | None`; `excluir_itens(con, fatura_id) -> None`; `atualizar_categoria_item(con, ids: list[int], categoria_id: int | None) -> int`
- `listar(con, conta_id: int | None = None) -> list[Row]`; `marcar_pagamento(con, fatura_id, lancamento_id: int | None) -> None`

**Schema v8:** tabelas `faturas` (id, uuid UNIQUE, conta_id FK, competencia `YYYY-MM`, data_vencimento, data_fechamento, emissor, bandeira, produto, total_impresso_centavos, total_calculado_centavos, ajustes_resumo_centavos DEFAULT 0, arquivo_hash, arquivo_nome, lancamento_id FK, pagamento_lancamento_id FK, criado_em; `UNIQUE(conta_id, competencia)`) e `fatura_itens` (id, uuid, fatura_id FK `ON DELETE CASCADE`, ordem, data, descricao, valor_centavos, tipo, categoria_id FK, categoria_banco, parcela_atual, parcela_total, grupo_parcela_uuid, final_cartao); índice em `fatura_itens(fatura_id)`. `lancamentos.origem` aceita `'fatura'` e `'fatura_prevista'`; tipo `'fatura'` fora da lista editável pelo usuário (`TIPOS` mantém 4; criar `TIPO_FATURA = "fatura"`).

- [ ] **Step 1: Testes que falham** — `test_migracao_v8_cria_tabelas` (banco novo tem `faturas`/`fatura_itens`); `test_migracao_v8_a_partir_de_v7_populado` (criar banco até v7 com contas/lançamentos/importações, chamar `migrar`, dados antigos intactos, `user_version == 8`); `test_unicidade_conta_competencia` (segunda `criar` igual → `sqlite3.IntegrityError`); `test_excluir_itens_e_cascata`; `test_atualizar_categoria_item`.
- [ ] **Step 2:** Rodar → FAIL. **Step 3:** Implementar `_migracao_8` (+ acrescentar a `MIGRACOES`) e `app/repo/faturas.py`. **Step 4:** Rodar → PASS; suíte inteira verde. **Step 5:** Commit `feat: migração v8 e repositório de faturas`.

---

### Task 3: Modelo de leitura, valores BR, ano e abertura do PDF

**Files:** Create `app/importacao/fatura/{__init__,modelo,texto}.py`; Test `tests/test_fatura_texto.py`, `tests/faturas_util.py`.

**Produces — `modelo.py`:**
- `@dataclass(frozen=True) ItemFatura(data: str, descricao: str, valor_centavos: int, tipo: str, parcela_atual: int | None, parcela_total: int | None, final_cartao: str | None, categoria_banco: str | None)`; `tipo ∈ {"compra","servico","internacional","encargo","credito"}`.
- `@dataclass(frozen=True) Conferencia(calculado_centavos: int, impresso_centavos: int, ajustes_resumo_centavos: int)` com propriedades `diferenca_centavos` (= impresso − (calculado + ajustes)) e `confere`.
- `@dataclass FaturaLida(emissor: str, bandeira: str | None, produto: str | None, final_cartao: str, data_vencimento: str, data_fechamento: str | None, total_impresso_centavos: int, ajustes_resumo_centavos: int, itens: list[ItemFatura], avisos: list[str])` com `calculado_centavos` (= −soma dos valores) e `conferencia()`.
- `valor_br_para_centavos(txt: str) -> int` ("1.234,56", "-9,99", "R$ 9,99"; inválido → `ValueError`); `inferir_ano(dia_mes: str, vencimento_iso: str) -> str` ("DD/MM" → ISO: maior data ≤ vencimento; 29/02 só em bissexto).
- Exceções: `FaturaIlegivel(ValueError)`, `PdfInvalido`, `PdfProtegido` (precisa senha), `SenhaIncorreta`, `PdfSemTexto`, `EmissorNaoSuportado` (todas subclasses de `FaturaIlegivel`, mensagens pt-BR sem conteúdo do arquivo).

**Produces — `texto.py`:** `@dataclass(frozen=True) PaginaTexto(numero: int, texto: str, esquerda: str, direita: str)`; `abrir_pdf(conteudo: bytes, senha: str | None = None) -> list[PaginaTexto]`. `esquerda`/`direita` = texto do recorte em x = 57% da largura da página (Itaú tem 2 colunas); `texto` = `extract_text()` normal. Única função do pacote que importa `pdfplumber`. Senha nunca em mensagem/log.

**Produces — `tests/faturas_util.py`:** `paginas_de_fixture(nome: str) -> list[PaginaTexto]` lê `tests/fixtures/faturas/<nome>.txt` (marcadores `@@pagina`, `@@esq`, `@@dir`; sem `@@esq/@@dir`, esquerda=direita="").

- [ ] **Step 1: Testes que falham** — `test_valor_br`: `"1.234,56"→123456`, `"-9,99"→-999`, `"R$ 0,05"→5`, `"abc"`→`ValueError`; `test_inferir_ano`: `("28/12","2027-01-10")→"2026-12-28"`, `("05/01","2027-01-10")→"2027-01-05"`, `("29/02","2028-03-05")→"2028-02-29"`; `test_conferencia_confere_e_diferenca`; `test_abrir_pdf_sem_senha_em_pdf_protegido_levanta_PdfProtegido`, `test_senha_errada`, `test_nao_pdf_levanta_PdfInvalido`, `test_pdf_sem_texto_levanta_PdfSemTexto`, `test_abrir_pdf_valido_devolve_paginas` (PDFs mínimos gerados no teste: sem criptografia com texto "DD/MM TESTE 9,99"; criptografado com a senha "x" via `pypdf` — instalar `pypdf` só como dependência de teste em `requirements.txt` e usar `pytest.importorskip`); `test_senha_nao_aparece_na_mensagem_de_erro`.
- [ ] **Step 2:** FAIL. **Step 3:** Implementar. **Step 4:** PASS. **Step 5:** Commit `feat: modelo de fatura lida e abertura de PDF local`.

---

### Task 4: Detecção de emissor e parser Itaú

**Files:** Create `app/importacao/fatura/{deteccao,itau}.py`, fixtures `tests/fixtures/faturas/{itau_visa,itau_mastercard,itau_passai}.txt` (sintéticas); Test `tests/test_fatura_itau.py`.

**Produces:** `deteccao.detectar(paginas: list[PaginaTexto]) -> ParserFatura` (levanta `EmissorNaoSuportado`); `Protocol ParserFatura` com `emissor: str`, `reconhece(paginas) -> bool`, `ler(paginas) -> FaturaLida`; `ler_fatura(conteudo: bytes, senha: str | None = None) -> FaturaLida` em `__init__.py` (abre, detecta, lê). `itau.ParserItau`.

**Regras Itaú (do estudo dos layouts):** marcador "Banco Itaú S.A." + "Itaú Cartões" (normalizar acentos); bandeira pelo 1º dígito do cartão (4 Visa, 5 Mastercard), produto na linha de nível (Gold/Platinum/Passaí; sub-emissor "FIC Itaú CBD" = Passaí); final = 4 últimos dígitos de `9999.XXXX.XXXX.9999`. Lançamentos: ler `esquerda` depois `direita` das páginas de lançamentos (nunca `texto` cru). Linha: `DD/MM <desc> [NN/MM] <valor>`; linha seguinte sem data/valor = categoria do banco (`categoria_banco`) e cidade. Seções: compras → `compra`; "produtos e serviços" → `servico` (ignorar linha `Principal (R$…)+ Juros (R$…)`); "lançamentos internacionais" → `internacional` (valor em R$ da linha; "Repasse de IOF" → `encargo`; moeda/cotação só na descrição); bloco "Encargos cobrados nesta fatura" com total > 0 → item sintético `encargo` "Encargos da fatura" (data = vencimento); valores negativos → `credito`. **Parar** em "Total dos lançamentos atuais" / "Compras parceladas - próximas faturas"; ignorar "Pagamentos efetuados" (só soma em `ajustes`: ajustes = saldo anterior − pagamentos + saldo financiado, do resumo), simulações, limites, CET, pagamento mínimo. Total = "Total desta fatura"; vencimento = "Vencimento:"; fechamento = None (só previsão do próximo). Datas via `inferir_ano`. Compras idênticas mantidas todas.

- [ ] **Step 1: Escrever as 3 fixtures sintéticas** imitando a estrutura (2 colunas, seção futura, bloco de encargos, 2 pagamentos no Passaí, parcela `03/10`, uma compra internacional no Mastercard) com valores e nomes inventados; a soma deve bater com o total impresso.
- [ ] **Step 2: Testes que falham** — por fixture: `emissor == "itau"`, bandeira/produto/final, vencimento ISO, `total_impresso_centavos`, quantidade e tipos de itens, soma confere (`conferencia().confere`), parcela (`parcela_atual==3, parcela_total==10`), categoria_banco preenchida, itens futuros ausentes, pagamento ausente dos itens; Passaí: encargo sintético presente e `confere`; Mastercard: IOF = `encargo`, internacional = `internacional`; `test_compras_identicas_mantidas`; `test_detectar_nao_confunde_porto_com_itau` (boleto da Porto cita Itaú 341-7).
- [ ] **Step 3:** FAIL. **Step 4:** Implementar. **Step 5:** PASS. **Step 6:** Commit `feat: leitura de fatura Itaú (Visa, Mastercard, Passaí)`.

---

### Task 5: Parser Porto

**Files:** Create `app/importacao/fatura/porto.py`, fixture `porto.txt`; Test `tests/test_fatura_porto.py`.

**Regras:** marcador `PORTOSEG` / `cartaoportoseguro.com.br`; 1 coluna (`texto`); só a seção "Detalhamento da fatura / Lançamentos: compras e saques". Linha `DD/MM <desc> <valor>` sem "R$"; parcela `NN/MM` dentro da descrição (após a data inicial) ou `XX/NN` (só atual); `-valor` = pagamento (ignorar, entra em `ajustes`) ou crédito conforme descrição ("PAGAMENTO…" ignora; demais negativos → `credito`); "MULTA", "JUROS DE MORA", "IOF" na tabela → `encargo`. Total: rótulo "O valor total é" e valor na linha seguinte; vencimento após "Esta fatura vence em"; fechamento = fim do período "(DD/MM a DD/MM)" (ano via `inferir_ano`); final do principal em `Cartão 9999 99** **** *999` (3 dígitos); **cartão adicional**: cabeçalho `<NOME> (final *NNN)` define `final_cartao` dos itens seguintes; ajustes = anterior − pagamento + saldo financiado + "Encargos de rotativo/parcelados" do resumo que não são linha (somar também como item sintético `encargo` "Encargos da fatura" quando não houver linha). Ignorar simulação (págs de parcelamento), limites, boleto/linha digitável.

- [ ] **Step 1:** Fixture sintética (titular + adicional, parcela, pagamento PIX negativo, multa/juros/IOF, resumo com encargos de rotativo). **Step 2:** Testes: emissor/final/vencimento/total; itens do adicional com `final_cartao` do adicional; pagamento fora dos itens; `confere`; parcela `PA/07` sem total → `parcela_atual=7, parcela_total=None` e aviso; datas de dezembro em fatura de janeiro. **Step 3:** FAIL → implementar → PASS. **Step 4:** Commit `feat: leitura de fatura Porto`.

---

### Task 6: Parser Mercado Pago

**Files:** Create `app/importacao/fatura/mercadopago.py`, fixture `mercadopago.txt`; Test `tests/test_fatura_mercadopago.py`.

**Regras:** marcador "Mercado Pago" + `Cartão Visa [************9999]`; 1 coluna. Compras: `DD/MM <desc> R$ <valor>`; parcela `Parcela N de M` antes do valor. Seção "Movimentações na fatura": valores **positivos** — sinal pela descrição: `Pagamento…` ignora (ajustes), `Crédito…` → `credito` (valor positivo), `Juros…`, `Multa…`, `IOF…` → `encargo` (negativos). Total: "Total a pagar" (valor na linha seguinte); vencimento "Vence em"; fechamento "Fechamento da fatura"; usar `extract_words`/menor tolerância não é necessário no parser (opera sobre texto). Ignorar "Parcele a fatura", "Lançamentos futuros", limites, mínimo. Compras idênticas em datas distintas mantidas. Total da fatura anterior/pagamentos → ajustes.

- [ ] **Step 1:** Fixture sintética. **Step 2:** Testes: `credito` com valor positivo; `Pagamento da fatura de …` fora dos itens; encargos negativos; parcela `Parcela 3 de 10`; duas compras idênticas mantidas; `confere`. **Step 3:** FAIL → implementar → PASS. **Step 4:** Commit `feat: leitura de fatura Mercado Pago`.

---

### Task 7: Validação local com os PDFs reais (sem vazar dados)

**Files:** Create `scripts/validar_faturas_locais.py`; Test `tests/test_fatura_script_local.py`.

**Produces:** `python scripts/validar_faturas_locais.py "Faturas Testes"` varre `*.pdf`, abre cada um (senha opcional via variável de ambiente `FATURA_SENHA`, nunca argumento), e imprime **por arquivo apenas**: emissor, bandeira, produto, nº de itens por tipo, `confere` sim/não, nº de avisos. Nunca valores, descrições, nomes, finais. Código de saída 0 mesmo se divergir.

- [ ] **Step 1:** Teste: função `resumo_mascarado(f: FaturaLida) -> str` não contém nenhum dígito de valor/descrição do `FaturaLida` sintético passado (assert que nem `descricao` nem o final do cartão aparecem). **Step 2:** FAIL → implementar → PASS.
- [ ] **Step 3:** Rodar o script nas 5 faturas reais; para cada divergência, **ajustar o parser correspondente com um novo teste sintético que reproduza a estrutura (sem dados reais)** e repetir até as 5 conferirem. Reportar ao dono só o resumo mascarado. **Step 4:** Commit `feat: script de validação local mascarado e ajustes de parser`.

---

### Task 8: Domínio — importar fatura (prévia, gravação, previstas, substituição)

**Files:** Create `app/dominio/faturas.py`; Modify `app/repo/lancamentos.py` (helpers), `app/dominio/categorias.py` (regras para itens); Test `tests/test_fatura_dominio.py`.

**Interfaces — Consumes:** `FaturaLida`, `repo.faturas.*`, `repo.contas.ids_por_final_numero`, `app.dominio.normalizacao.normalizar_descricao`, `categorias.aplicar_regras`/`repo.listar_regras_ordenadas`.

**Produces (`app/dominio/faturas.py`):**
- `@dataclass PreviaFatura(fatura: FaturaLida, arquivo_hash: str, conta_id: int | None, situacao: Literal["nova","identica","substitui"], categorias_sugeridas: list[int | None], previstas_a_baixar: int, previstas_a_criar: int)`
- `preparar_previa(con: Any, fatura: FaturaLida, arquivo_hash: str) -> PreviaFatura` (acha a conta-cartão por `final_numero`; calcula situação por `(conta, competencia)` e hash; sugere categoria por regras de `regras_categoria` pela descrição e, se faltar, pelo nome de `categoria_banco` casando com `categorias.nome`).
- `@dataclass ResumoFatura(fatura_id: int, lancamento_id: int, itens: int, previstas_criadas: int, previstas_baixadas: int)`
- `importar_fatura(con: Any, fatura: FaturaLida, arquivo_hash: str, arquivo_nome: str, conta_id: int, hoje: date, categorias: list[int | None], substituir: bool = False) -> ResumoFatura` — numa `transacao`: se existir fatura da mesma conta+competência: mesmo hash → `FaturaJaImportada`; hash diferente sem `substituir` → `FaturaDiferenteExistente`; com `substituir` → refaz itens/previstas mantendo categorias já definidas por `(data, descricao_normalizada, valor)`. Cria a linha de `lancamentos` (`tipo="fatura"`, `origem="fatura"`, valor = −calculado, data = vencimento, `compensado=0`, `chave_deduplicacao="fatura:<uuid>"`, sem categoria), itens, **baixa previstas** casadas (mesma conta, `origem='fatura_prevista'`, `parcela_atual` igual, descrição normalizada similar, valor dentro de max(R$2; 2%)) e **cria previstas** n+1..N (`grupo_parcela_uuid` do item = o da prevista baixada ou novo; data = vencimento + k meses, dia limitado ao fim do mês; `estimado=1`; categoria do item).
- `criar_conta_cartao(con: Any, fatura: FaturaLida) -> int` (nome `"<Emissor> <Bandeira> <Produto> final <final>"`, tipo `cartao`).
- Exceções `FaturaJaImportada`, `FaturaDiferenteExistente` (pt-BR).
- `alterar_categoria_item(con: Any, item_id: int, categoria_id: int | None) -> None`; `categorizar_itens_em_lote(con: Any, ids: Sequence[int], categoria_id: int) -> int`; `criar_regra_e_aplicar_itens` não é necessário (regra existente passa a valer nos itens via `aplicar_regras_itens(con, fatura_id) -> int`).
- `previstas_orfas(con: Any) -> list[Row]` (previstas vencidas sem item correspondente, para sinalizar).

- [ ] **Step 1: Testes que falham** — `test_importa_grava_fatura_itens_e_lancamento` (linha `tipo=fatura`, pendente, valor −calculado, itens com categoria sugerida); `test_reenviar_mesmo_hash_levanta_FaturaJaImportada`; `test_hash_diferente_exige_substituir` e `test_substituir_mantem_categorias_ja_definidas`; `test_previstas_criadas_para_parcela_3_de_5` (2 previstas, datas +1 e +2 meses, 31→fim do mês); `test_fatura_seguinte_baixa_prevista` (parcela 4/5 real apaga a prevista 4/5 e mantém a 5/5); `test_tolerancia_valor_da_prevista` (R$1,50 de diferença casa; R$5 não casa e prevista fica órfã); `test_compras_identicas_viram_dois_itens`; `test_criar_conta_cartao_nome_e_tipo`; `test_categoria_banco_sugere_categoria_existente`; `test_importacao_atomica` (erro no meio → nada gravado).
- [ ] **Step 2:** FAIL → implementar (SQL novo só em `repo`) → PASS; suíte verde. **Step 3:** Commit `feat: importar fatura com itens, previstas e substituição`.

---

### Task 9: Pagamento × fatura e ajuste dos totais

**Files:** Create `app/dominio/faturas_pagamento.py`; Modify `app/repo/lancamentos.py` (`totais_extrato`, `listar_extrato` marcação), `app/repo/painel.py`, `app/repo/categorias.py`, `app/dominio/categorias.py`; Test `tests/test_fatura_pagamento.py`, `tests/test_fatura_totais.py`.

**Produces:**
- `@dataclass PropostaPagamento(fatura_id: int, lancamento_id: int, data: str, valor_centavos: int, pontuacao: float)`; `propor_pagamentos(con: Any, fatura_id: int) -> list[PropostaPagamento]` (lançamentos `tipo='pagamento_fatura'` não casados, conta não-cartão, |valor| == `total_impresso_centavos`, data em vencimento ± 7 dias; ordena por proximidade de data).
- `confirmar_pagamento(con: Any, fatura_id: int, lancamento_id: int) -> None` (fatura `compensado=1`, `data_compensacao` = data do banco; `faturas.pagamento_lancamento_id`); `desfazer_pagamento(con: Any, fatura_id: int) -> None`; `PagamentoInvalido(ValueError)`.

**Regras de totais (testar cada uma):**
1. Extrato (`totais_extrato`): linha `tipo='fatura'` compensada soma em saídas **mesmo em conta-cartão**; `pagamento_fatura` com `pagamento_lancamento_id` apontando para ele **não** soma; sem casamento continua somando; fatura pendente soma em pendentes.
2. Painel (`repo/painel.py`) e gasto mensal: mesmas regras de caixa.
3. Relatório por categoria/gasto do mês (`repo/categorias.py`, `dominio/categorias.py:despesas_do_mes`): somar **itens de fatura** pela data da compra (itens de crédito subtraem), excluir linhas `tipo='fatura'` e `pagamento_fatura` casadas; itens sem categoria entram como "Sem categoria".
4. Saldo total e projeção continuam excluindo conta-cartão do saldo, mas a fatura pendente e as previstas (pendentes na conta-cartão) são abatidas no mês da data (já é o comportamento de `projecao.py`; cobrir com teste).
5. Contas a pagar lista a fatura pendente (vencimento, total, conta-cartão) e some quando paga.

- [ ] **Step 1: Testes que falham** (um por regra acima + `test_propor_so_valor_exato_e_janela_7_dias`, `test_confirmar_marca_paga_e_nao_conta_duas_vezes`, `test_desfazer_volta_a_pendente_e_pagamento_conta_de_novo`, `test_pagamento_de_outra_fatura_rejeitado`).
- [ ] **Step 2:** FAIL → implementar → PASS; suíte inteira (os testes antigos de totais continuam valendo). **Step 3:** Commit `feat: casamento de pagamento de fatura e totais por item/vencimento`.

---

### Task 10: Web — importar PDF com prévia e confirmação

**Files:** Create `app/web/rotas/faturas.py`, `app/web/templates/faturas/previa.html`; Modify `app/web/rotas/importar.py` (detectar `.pdf` e delegar), `app/web/templates/importar/index.html` (aceitar `.pdf`, campo senha `type=password autocomplete=off`), `app/web/uploads.py` (armazém de `FaturaLida` com TTL, sem bytes do PDF nem senha), `app/main.py` (registrar rota); Test `tests/test_web_faturas.py`.

**Fluxo:** `POST /importar` com `.pdf` + senha opcional → `ler_fatura` → `preparar_previa` → guarda `FaturaLida` no armazém (token) → renderiza prévia (tabela de itens com categoria sugerida editável via o combobox existente `categoria-busca.js`, conferência "itens − créditos + encargos × total impresso" com selo OK/divergente, avisos, conta-cartão encontrada ou botão "Criar conta-cartão", situação nova/idêntica/substitui com checkbox de confirmação de substituição). `POST /faturas/confirmar` com token → `importar_fatura`. Erros (`PdfProtegido`, `SenhaIncorreta`, `PdfSemTexto`, `EmissorNaoSuportado`, `FaturaJaImportada`) viram mensagem pt-BR na própria tela; `PdfProtegido` reexibe o formulário pedindo senha. Senha nunca ecoada no HTML nem em log; resposta com `Cache-Control: no-store`.

- [ ] **Step 1: Testes que falham** (TestClient; PDF sintético mínimo e `monkeypatch` de `ler_fatura` para devolver `FaturaLida` fixa): upload `.pdf` mostra prévia com itens e conferência OK; divergência mostra aviso; PDF protegido sem senha pede senha e o campo não vem preenchido; senha errada → mensagem sem a senha no HTML; confirmar grava fatura e redireciona para Lançamentos filtrado na conta-cartão; reenviar mesmo hash → "já importada"; hash diferente exige marcar "substituir"; sem conta-cartão: criar conta e confirmar; token expirado → mensagem; categoria escolhida na prévia é gravada no item.
- [ ] **Step 2:** FAIL → implementar → PASS. **Step 3:** Verificar visualmente no navegador (Chrome DevTools MCP) a prévia em largura de desktop e celular. **Step 4:** Commit `feat: importar fatura em PDF pelo painel`.

---

### Task 11: Web — fatura expansível em Lançamentos, categoria por item e casamento

**Files:** Modify `app/web/rotas/lancamentos.py`, `app/web/templates/lancamentos/index.html`, `app/web/rotas/faturas.py`; Create `app/web/templates/faturas/{_itens,_casamento}.html`; Test `tests/test_web_lancamentos_fatura.py`.

**Rotas:** `GET /faturas/{id}/itens` (fragmento HTMX com itens e portador), `POST /faturas/itens/{item_id}/categoria` (reusa o combobox), `POST /faturas/{id}/itens/categoria-lote`, `GET /faturas/{id}/pagamento` (propostas), `POST /faturas/{id}/pagamento/confirmar`, `POST /faturas/{id}/pagamento/desfazer`.

**UI:** linha `tipo='fatura'` na lista: ícone cartão, descrição "Fatura <conta> · venc. DD/MM", total, **sem categoria**, botão expandir (`aria-expanded`); expandida mostra itens (data compra, descrição, parcela n/N, portador, categoria editável, valor); selo "paga/a pagar"; botão "Casar pagamento" quando houver propostas; `pagamento_fatura` sem fatura casada mostra aviso discreto "sem fatura importada"; previstas aparecem como "prevista" (estimado). Rótulo de tipo "Fatura" no select/filtro.

- [ ] **Step 1: Testes que falham** — lista mostra a fatura sem categoria e sem itens; fragmento de itens lista todos com categoria; alterar categoria de um item persiste e não altera o total; lote; casar pagamento muda selo para paga e o total do topo não conta duas vezes; desfazer; aviso de pagamento sem fatura; previstas rotuladas; HTML escapado (descrição com `<script>`).
- [ ] **Step 2:** FAIL → implementar → PASS. **Step 3:** Conferir no navegador (desktop e celular; expandir/colapsar por teclado). **Step 4:** Commit `feat: fatura expansível em Lançamentos com categoria por item`.

---

### Task 12: Consultor (somente consulta) e documentação

**Files:** Modify `app/consultor/cli.py`, `app/consultor/consultas.py`, `.claude/skills/importar/SKILL.md` (e a de `gastos` se citar totais), `CLAUDE.md` (seção de limitações e §Modo consultor), `docs/PASSO-A-PASSO.md`, `README.md`; Test `tests/test_consultor_faturas.py`.

**Produces:** `python -m app.consultor faturas [--conta ID]` (JSON: id, conta, competencia, vencimento, total, status paga/a pagar, nº itens, divergência) e `faturas ver ID` (itens com categoria, valores em centavos como os demais comandos). A skill `importar` passa a dizer: PDF de fatura só pelo painel (senha nunca passa pelo Claude); o consultor consulta e sugere categorização por item.

- [ ] **Step 1: Testes que falham** — saída JSON dos dois comandos; PDF não é aceito por `importar` do consultor com mensagem apontando o painel. **Step 2:** FAIL → implementar → PASS. **Step 3:** Atualizar README ("Limitações": remover "PDF e faturas de cartão não são suportados"; "pagamento de fatura conta como despesa até a v0.2" → regra nova), PASSO-A-PASSO (seção "Importar fatura do cartão": senha, prévia, conferência, expandir fatura, casar pagamento, previstas). **Step 4:** Rodar a suíte inteira; commit `docs: fatura de cartão no consultor, README e passo a passo`.

---

### Task 13: Verificação final

- [ ] **Step 1:** Suíte inteira verde; `git status` sem PDFs/`Faturas Testes/`; `git grep -n "print("` nos novos módulos vazio.
- [ ] **Step 2:** Rodar `scripts/validar_faturas_locais.py "Faturas Testes"`; as 5 faturas `confere`.
- [ ] **Step 3:** Teste manual no painel com uma fatura real (banco de teste/backup): importar, conferir total, expandir, categorizar um item, casar pagamento, reimportar (aviso "já importada").
- [ ] **Step 4:** Revisão final da branch (`superpowers:finishing-a-development-branch`) e PR.

---

## Self-review

- **Cobertura da spec:** escopo/privacidade (T1,T3), modelo e conferência (T2–T4), previstas (T8), pagamento×fatura e totais (T9), reimportar (T8,T10), conta-cartão (T8,T10), fluxo/UI (T10,T11), consultor/docs (T12), testes sintéticos e script local (T4–T7). Itens "fora do escopo" respeitados.
- **Consistência de nomes:** `FaturaLida`, `ItemFatura`, `PaginaTexto`, `ler_fatura`, `preparar_previa`, `importar_fatura`, `propor_pagamentos`, `confirmar_pagamento` usados igual nas tarefas.
- **Proporção:** tarefas de parser descrevem regras (não código); corpos ficam para o implementador.
- **Risco aceito:** divergências dos PDFs reais são resolvidas na Task 7 com testes sintéticos adicionais; saldo financiado de fatura anterior não paga entra só como "ajuste" de conferência.
