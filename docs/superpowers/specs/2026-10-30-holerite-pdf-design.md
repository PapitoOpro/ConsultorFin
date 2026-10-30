# Holerite em PDF (fase 3) — design

Decisões do dono em 30/10/2026 (rodada Q1–Q5, todas "sim"). Base: v0.1 + OFX + categorias + redesign + faturas (migração v9).

## Intenção
Importar o holerite (PDF, sem senha) para ver quanto entra de verdade: bruto, descontos item a item (INSS, pensão, consignado) e líquido; casar o líquido com o crédito do extrato; e deixar o consultor responder "quanto paguei de pensão este ano?". Leitura 100% local, só texto (`pdfplumber`), sem OCR/LLM/nuvem. Só pelo painel; o consultor apenas consulta (o PDF tem dados sensíveis).

## Escopo
- Dois layouts vistos (empresa do dono): **Adiantamento** ("Adiantamento", ex.: Outubro/2026, líquido R$ 1.400,00) e **Folha Mensal** (ex.: Setembro/2026, líquido R$ 3.800,00). Outros tipos (13º, férias, rescisão) e outras empresas: depois, um por vez. Layout não reconhecido → erro claro, nada é gravado.
- O PDF traz a via do funcionário duplicada na página e texto embaralhado; o leitor usa palavras com posição e lê só a primeira via.

## Modelo
- **Holerite**: competência `YYYY-MM`, tipo (`adiantamento` | `mensal`), empresa, total de vencimentos (bruto), total de descontos, líquido, hash do arquivo, nome do arquivo. **Não guardar** CPF, CNPJ, nome do funcionário, cargo, bases de cálculo, nem o PDF.
- **Itens**: código, descrição, referência (texto), natureza (`provento` | `desconto`), valor em centavos positivo, categoria opcional (regras "descrição contém"; sugestão para INSS, pensão, empréstimo/consignado, horas extras, etc.).
- **Conferência** (prévia): soma proventos = total de vencimentos; soma descontos = total de descontos; vencimentos − descontos = líquido. Divergência = aviso, não bloqueio.
- **Líquido no caixa**: uma linha em `lancamentos` (`tipo='entrada'`, `origem='holerite'`, categoria Salário) na conta corrente escolhida, valor = líquido. Os itens **não** entram em totais de caixa nem nos gráficos de gasto por categoria (o dinheiro já saiu antes de chegar à conta; contar de novo duplicaria). Eles alimentam a visão "Holerites" e o consultor.
- **Casamento com o extrato**: na prévia, procurar entrada existente com o mesmo valor (±0) e data entre o dia 1 do mês de competência e 45 dias depois, na conta escolhida; se achar, propor vincular (o dono confirma) em vez de criar a linha. Se não achar, criar a linha **pendente e estimada** na data prevista (adiantamento: dia 20 do mês de competência; mensal: dia 2 do mês seguinte; configuráveis em `configuracoes`), e o casamento já existente do app a resolve quando o extrato chegar.
- **Reimportar**: mesmo hash → "já importado". Mesma competência+tipo com hash diferente → pede confirmação para substituir (itens refeitos; categorias já definidas mantidas por descrição).

## Fluxo
Importar → "Holerite (PDF)" → escolher arquivo → prévia (tipo, competência, itens, conferência, crédito encontrado no extrato ou data prevista, conta) → confirmar. Nada é gravado antes. Tela **Holerites**: lista por competência (bruto, descontos, líquido, status do crédito) com linha expansível dos itens e categoria editável.

## Consultor (somente leitura)
`holerites [--ano AAAA]` (lista) e `holerites ver ID` (itens). Totais por item (ex.: pensão no ano) via `holerites --ano 2026 --item pensao` ou campo `totais_por_categoria`. Recusa `.pdf` em `importar` (já recusa PDF). Skill nova `holerite` e entrada no CLAUDE.md.

## Fora do escopo
Importação pelo consultor, outros tipos de holerite/empresas, insights de fim do consignado e "comprometimento mensal" (vão para o Consultor 2.0), OCR, holerite com senha, IRRF/13º/férias.

## Chamadas minhas (para o dono conferir)
- Itens do holerite ficam fora dos gráficos de gasto por categoria (evita contar duas vezes); a pergunta "quanto paguei de pensão?" é respondida pela visão/consultor de Holerites. Diverge um pouco do "aparecem nos gráficos" da conversa.
- Dias previstos (20 e dia 2 do mês seguinte) vêm dos dois créditos reais de out/2026.
- Janela de 45 dias e tolerância zero no valor para vincular crédito existente.

## Testes
Holerites **sintéticos** em texto (imitam os dois layouts; sem dado real). PDFs reais ficam em `Holerites/` fora do repositório e só alimentam um script local com saída mascarada. Migração v10 testada a partir de um banco v9 populado.
