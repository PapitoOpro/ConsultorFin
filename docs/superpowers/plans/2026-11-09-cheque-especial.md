# Cheque especial Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Contar os dias do mês em que a conta Itaú ficou negativa e alertar (faixa vermelha + pop-up) a partir de 8 dos 10 dias sem juros.

**Architecture:** Regra pura em `app/dominio/cheque_especial.py` (conta dias a partir de somas diárias de lançamentos compensados); configuração e ajuste manual em `configuracoes` (JSON, sem migração); `resumo_inicio` expõe o resultado; rota POST própria para configurar; card, faixa e `<dialog>` no template do Início.

**Tech Stack:** Python 3.11, FastAPI + Jinja2, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-11-09-cheque-especial-design.md`

## Global Constraints

- Valores em centavos (inteiros); nunca `float`.
- Type hints em funções públicas; mensagens em português; regra em `dominio/`, SQL só em `repo/`.
- Sem migração de banco: chaves `cheque_especial` e `cheque_especial_dias` em `configuracoes`.
- Padrões: limite 441000, `dias_isentos` 10, `alerta_a_partir_de` 8. Uma conta só.
- Dia de uso = saldo ao fim do dia < 0, só lançamentos compensados (`compensado = 1`, `data_compensacao`), de dia 1 do mês até hoje; dias antes de `data_saldo_inicial` não contam.
- Estados: `ok` (< alerta_a_partir_de), `atencao` (>= alerta_a_partir_de e < dias_isentos), `esgotado` (>= dias_isentos).
- Visual: só variáveis CSS do `tema.css` (`--saida` etc.), sem gradiente/neon/emoji; estado nunca só por cor; ícones pela macro `icone`; `prefers-reduced-motion` respeitado.
- Pop-up: `localStorage` chave `chequeEspecial:AAAA-MM-DD:estado` dentro de try/catch.

## Review Focus

- Saldo volta ao positivo no meio do mês: dias acumulam, não zeram (teste na Task 1).
- Mês sem nenhum lançamento / conta sem `data_saldo_inicial`: sem erro, 0 dias (Task 1).
- Virada de mês: dia 1 com saldo negativo conta 1, ajuste manual do mês anterior é ignorado (Tasks 1 e 2).
- Limite configurado menor que o uso atual: mostra `excesso_centavos`, sem percentual acima de 100 na barra (Task 1).
- Formulário com limite vazio, negativo ou texto, ou conta inexistente: erro em português, nada gravado (Task 3).

---

### Task 1: Regra de domínio e persistência

**Files:**
- Create: `app/dominio/cheque_especial.py`
- Modify: `app/repo/lancamentos.py` (acrescentar `somas_diarias_compensados` depois de `somas_diarias_conciliacao`)
- Test: `tests/test_cheque_especial.py`

**Interfaces:**
- Produces (`app/repo/lancamentos.py`): `somas_diarias_compensados(con, conta_id: int, ate_iso: str) -> list[tuple[str, int]]` — `(data_compensacao, soma)` de todos os compensados com `data_compensacao <= ate_iso`, ordem crescente.
- Produces (`app/dominio/cheque_especial.py`):
  - `@dataclass(frozen=True) ConfigChequeEspecial(conta_id: int, limite_centavos: int, dias_isentos: int = 10, alerta_a_partir_de: int = 8)`
  - `@dataclass(frozen=True) ChequeEspecial(config: ConfigChequeEspecial, em_uso_centavos: int, excesso_centavos: int, dias_calculados: int, dias_informados: int | None, dias_usados: int, estado: str, dias_restantes: int, pct_limite: int)` com `dias_restantes = max(0, dias_isentos - dias_usados)` e `pct_limite` limitado a 0–100.
  - `contar_dias_negativos(saldo_inicial_centavos: int, data_saldo_inicial: str | None, somas: list[tuple[str, int]], primeiro_dia: date, hoje: date) -> int`
  - `estado_de(dias: int, cfg: ConfigChequeEspecial) -> str`
  - `validar_config(conta_id: int, limite_centavos: int, dias_isentos: int, alerta_a_partir_de: int) -> ConfigChequeEspecial` — `ValueError` em português se limite <= 0, dias_isentos fora de 1–31, alerta fora de 1..dias_isentos, ou `limite_centavos` acima de `verificar_magnitude`.
  - `carregar_config(con) -> ConfigChequeEspecial | None`, `salvar_config(con, cfg) -> None` (JSON na chave `cheque_especial`, via `app/repo/configuracoes.py`).
  - `informar_dias(con, hoje: date, dias: int) -> None` — grava `{"mes": "AAAA-MM", "dias": n}` em `cheque_especial_dias`; `ValueError` se `dias` fora de 0–31.
  - `calcular(con, hoje: date) -> ChequeEspecial | None` — `None` sem configuração ou com conta inexistente; ajuste só vale se `mes` == mês de `hoje`.

- [ ] **Step 1: Escrever os testes em `tests/test_cheque_especial.py`** (fixture `con` do conftest; helper `_lanc` como em `tests/test_fechamento.py`, com `compensado=True`; conta Itaú criada com `saldo_inicial_centavos=0, data_saldo_inicial="2026-10-01"`). Testes e asserts:
  - `test_contagem_acumula_e_nao_zera_ao_voltar_positivo`: saldo -100 em 03/11, +200 em 05/11, -150 em 08/11, hoje 10/11 → `dias_calculados == 2 + 3 == 5` (dias 3 e 4, depois 8, 9, 10; 5–7 positivo).
  - `test_virada_de_mes_zera`: negativo em 28–31/10 e positivo em 01/11, hoje 02/11 → 0.
  - `test_dia_primeiro_do_mes_negativo_conta_um`: saldo negativo desde 31/10, hoje 01/11 → 1.
  - `test_dia_antes_do_saldo_inicial_nao_conta`: `data_saldo_inicial="2026-11-05"`, saldo inicial -500, hoje 07/11 → 3 (dias 5, 6, 7).
  - `test_sem_lancamentos_nem_data_inicial`: conta com `data_saldo_inicial=None` e saldo inicial 0 → 0, sem exceção.
  - `test_estado_nos_limites`: dias 7→`ok`, 8→`atencao`, 9→`atencao`, 10→`esgotado`, 11→`esgotado`.
  - `test_excesso_e_percentual_limitado`: limite 100000, saldo atual -150000 → `em_uso_centavos == 150000`, `excesso_centavos == 50000`, `pct_limite == 100`.
  - `test_pendente_nao_conta`: lançamento negativo com `compensado=False` → 0 dias.
  - `test_ajuste_manual_substitui_no_mes_e_expira_no_seguinte`: `informar_dias(con, date(2026,11,10), 9)` → `calcular` em 10/11 dá `dias_usados == 9`, `dias_informados == 9`; `calcular` em 01/12 dá `dias_informados is None`.
  - `test_calcular_sem_config_retorna_none`.
  - `test_validar_config_rejeita`: limite 0, limite negativo, alerta 11 com isentos 10, isentos 0 → `ValueError`.
  - `test_config_ida_e_volta`: `salvar_config` + `carregar_config` devolvem objeto igual.

- [ ] **Step 2: Rodar `.venv/Scripts/python -m pytest tests/test_cheque_especial.py -q`** — esperado: falha por import (`app.dominio.cheque_especial` não existe).

- [ ] **Step 3: Implementar** `somas_diarias_compensados` (mesma forma de `somas_diarias_conciliacao`, sem o filtro de conciliação, com `data_compensacao <= ?`) e `app/dominio/cheque_especial.py` com as assinaturas acima. `contar_dias_negativos` percorre de `max(primeiro_dia, data_saldo_inicial)` a `hoje`, acumulando `saldo_inicial + soma(somas até o dia)`; `calcular` usa `primeiro_dia = hoje.replace(day=1)`, `saldo_inicial_da_conta` de `app/repo/contas.py`, e `em_uso = max(0, -saldo_atual)`, `excesso = max(0, em_uso - limite)`. Logar `event=cheque_especial_calculado estado=... dias=...` (sem valores).

- [ ] **Step 4: Rodar o mesmo comando** — esperado: todos passam.

- [ ] **Step 5: Commit**

```bash
git add app/dominio/cheque_especial.py app/repo/lancamentos.py tests/test_cheque_especial.py docs/superpowers
git commit -m "feat: regra de dias de uso do cheque especial"
```

---

### Task 2: Expor no resumo do Início

**Files:**
- Modify: `app/dominio/painel.py` (`Resumo` ganha `cheque_especial: ChequeEspecial | None = None`; `resumo_inicio` preenche com `cheque_especial.calcular(con, hoje)`)
- Test: `tests/test_painel.py` (acrescentar)

**Interfaces:**
- Consumes: `calcular(con, hoje) -> ChequeEspecial | None` (Task 1).
- Produces: `Resumo.cheque_especial: ChequeEspecial | None`.

- [ ] **Step 1: Teste `test_resumo_inclui_cheque_especial`**: com config salva e conta negativa, `resumo_inicio(con, HOJE, 2026, 11, 6).cheque_especial.dias_usados` igual ao esperado; sem config, `is None`. Ao consultar outro mês (`ano, mes = 2026, 10`) o valor continua sendo o do mês de `hoje` (o cheque especial sempre usa a data atual).
- [ ] **Step 2: Rodar `.venv/Scripts/python -m pytest tests/test_painel.py -q -k cheque`** — esperado: falha (`AttributeError`).
- [ ] **Step 3: Implementar** o campo e o preenchimento em `resumo_inicio`.
- [ ] **Step 4: Rodar** `.venv/Scripts/python -m pytest tests/test_painel.py tests/test_cheque_especial.py -q` — esperado: passa.
- [ ] **Step 5: Commit** `feat: resumo do início inclui cheque especial`.

---

### Task 3: Rotas de configuração e ajuste

**Files:**
- Create: `app/web/rotas/cheque_especial.py`
- Modify: `app/web/rotas/__init__.py` (importar e registrar `cheque_especial.router` em `routers`)
- Test: `tests/test_web_cheque_especial.py` (reutilizar `cliente`/`db` de `tests/test_web_fila.py`, como em `tests/test_web_senhas_pdf.py`)

**Interfaces:**
- Consumes: `validar_config`, `salvar_config`, `informar_dias` (Task 1); `para_centavos` de `app/dominio/dinheiro.py`; `get_con` de `app/web/deps.py`.
- Produces: `POST /cheque-especial` (campos de formulário `conta_id`, `limite`, `dias_isentos`, `alerta_a_partir_de`) e `POST /cheque-especial/dias` (campo `dias`). Ambas respondem `303` para `/` em sucesso. Em erro de validação, `303` para `/?erro_cheque=<mensagem url-encoded>`; nada é gravado.

- [ ] **Step 1: Testes**: configuração válida grava e redireciona para `/`; `limite=""`, `limite="-5"`, `limite="abc"` e `conta_id=9999` redirecionam com `erro_cheque` e `carregar_config` continua `None`; `dias=7` grava ajuste; `dias=40` e `dias=abc` rejeitados.
- [ ] **Step 2: Rodar `.venv/Scripts/python -m pytest tests/test_web_cheque_especial.py -q`** — esperado: falha (404).
- [ ] **Step 3: Implementar** as duas rotas seguindo `app/web/rotas/senhas.py` (APIRouter, `Depends(get_con)`, `RedirectResponse(..., status_code=303)`); converter `limite` com `para_centavos`, capturar `ValueError`, checar conta com `app/repo/contas.obter`. Nenhum valor financeiro nos logs.
- [ ] **Step 4: Rodar** o mesmo comando — esperado: passa.
- [ ] **Step 5: Commit** `feat: rotas para configurar cheque especial`.

---

### Task 4: Card, faixa vermelha e pop-up no Início

**Files:**
- Modify: `app/web/rotas/inicio.py` (aceitar `erro_cheque` da query e passá-lo ao template no contexto de `inicio`); `app/web/templates/inicio/index.html`; `app/web/static/css/tema.css`
- Create: `app/web/static/js/cheque-especial.js`
- Test: `tests/test_web_inicio.py` (acrescentar)

**Interfaces:**
- Consumes: `resumo.cheque_especial` (Task 2); rotas da Task 3.
- Produces (HTML): `#cheque-faixa` (só em `atencao`/`esgotado`, `role="alert"`, ícone + texto "Cheque especial: X de 10 dias sem juros usados. Faltam N."; em `esgotado`: "Os 10 dias sem juros acabaram."), `#cheque-card`, `<dialog id="cheque-dialogo" data-estado data-dia>` com botão "Entendi".

- [ ] **Step 1: Testes em `tests/test_web_inicio.py`**: sem config, GET `/` contém "Configurar cheque especial" e não contém `id="cheque-faixa"`; com config e 8 dias, contém `id="cheque-faixa"`, "8 de 10", `data-estado="atencao"`; com 10 dias, contém "acabaram" e `data-estado="esgotado"`; com 3 dias, sem faixa e sem `<dialog id="cheque-dialogo"`; com `?erro_cheque=Limite+inv%C3%A1lido`, a mensagem aparece escapada.
- [ ] **Step 2: Rodar `.venv/Scripts/python -m pytest tests/test_web_inicio.py -q -k cheque`** — esperado: falha.
- [ ] **Step 3: Implementar** no template, logo antes de `<section class="grade-kpi">`: faixa (condicional ao estado), card (valor em uso, limite, `dias_usados` de `dias_isentos`, barra com `--pct`, excesso em `--saida` quando > 0, formulário de ajuste "dias segundo o banco" e `<details>` com o formulário de configuração listando as contas que não são cartão), e o `<dialog>` condicional a `atencao`/`esgotado`. CSS novo usa só variáveis do tema, barra com classes `atencao`/`estouro` como o `.consumo` existente. `cheque-especial.js` (carregado com `defer`): abre o dialog com `showModal()` se `localStorage` não tiver a chave `chequeEspecial:<data-dia>:<estado>`, grava a chave ao fechar, tudo em try/catch; sem `localStorage`, abre uma vez por carga. Sem animações.
- [ ] **Step 4: Rodar** `.venv/Scripts/python -m pytest -q` — esperado: suíte inteira passa. Subir o app (`iniciar.bat`) e conferir no navegador os três estados (ok, atenção, esgotado) e o pop-up; pedir print se algo estiver visualmente errado.
- [ ] **Step 5: Commit** `feat: card, alerta e pop-up de cheque especial no início`.

---

### Task 5: Documentação

**Files:**
- Modify: `docs/PASSO-A-PASSO.md` (seção Início: como configurar, o que significam os estados, ajuste manual); `CLAUDE.md` seção 8 "Início" (uma linha citando o card de cheque especial).

- [ ] **Step 1: Atualizar os dois arquivos** (texto curto, em português, sem jargão).
- [ ] **Step 2: Rodar** `.venv/Scripts/python -m pytest -q` — esperado: passa.
- [ ] **Step 3: Commit** `docs: cheque especial no início`.
