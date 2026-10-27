# Fatura de cartão em PDF (fase 2) — design

Decisões fechadas com o dono em 27/10/2026 (entrevista "grilling"). Base: v0.1 + OFX + categorias + redesign.

## Escopo
- Importar PDF de fatura de cartão de 3 emissores / 5 layouts: **Itaú** (Mastercard Gold, Visa Platinum, Visa Passaí), **Porto** e **Mercado Pago**. Outros emissores depois, um por vez (um parser por emissor).
- Leitura 100% local, só texto (`pdfplumber`; `pycryptodome` para RC4/AES). Sem OCR, sem nuvem, sem LLM. PDF sem texto → aviso claro.
- Senha do PDF: digitada no painel no envio, só em memória; nunca gravada, logada, nem enviada ao Claude/consultor. O consultor não importa PDF; só consulta o já importado.
- Lógica de extração portada (reescrita e testada) do Financeiro-2026, sem Supabase, credenciais, `print` de valores nem float.

## Modelo
- **Fatura** = uma por cartão principal e mês (chave: conta + competência `YYYY-MM` do vencimento). Tem uma linha em `lancamentos` (`tipo='fatura'`, na conta-cartão, data = vencimento, valor = −total calculado, sem categoria, pendente até ser paga) e N **itens** (`fatura_itens`).
- Itens: `compra`, `servico`, `internacional`, `encargo`, `credito` (estorno/crédito = valor positivo). Convenção de sinal = a de `lancamentos` (despesa negativa). Cada item guarda data da compra (ano inferido), descrição, parcela n/N, final do cartão do portador (principal/adicional), categoria (`categoria_id`) e a categoria impressa pelo banco (Itaú).
- Fora dos itens: pagamento do mês anterior, saldo anterior/financiado, simulações, limites, CET, "próximas faturas", bloco "Principal + Juros". Pagamento/saldo financiado servem só à conferência.
- Encargos: itens da categoria "Encargos"; no Itaú, onde só há bloco/resumo, um item sintético "Encargos da fatura".
- **Conferência** (na prévia): `total impresso == −soma(itens) + ajustes_resumo` (ajustes = saldo anterior − pagamentos + saldo financiado, lidos do resumo). Divergência = aviso, não bloqueio.
- **Parcelas futuras**: para item `n/N` com n<N, criar **previstas** (`lancamentos` pendentes, `estimado=1`, `origem='fatura_prevista'`, conta-cartão, mesmo `grupo_parcela_uuid`, data = vencimento da fatura + k meses) para n+1..N. Ao importar a fatura seguinte, o item real (mesmo cartão, descrição normalizada parecida, parcela n, valor dentro de max(R$2; 2%)) apaga a prevista. Previsão sem correspondente na fatura seguinte fica sinalizada para descarte.
- **Pagamento × fatura**: bancárias `pagamento_fatura` ficam como hoje até casarem. App propõe casar (valor = total impresso, data até ±7 dias do vencimento, mesma conta corrente); o dono confirma. Casada: a fatura fica paga (`compensado=1`, `data_compensacao` = data do banco) e a linha do banco deixa de contar como gasto. Sem casar, continua contando como despesa (com aviso na lista).
- **Totais**: caixa/saldo/Contas a pagar/projeção usam a linha da fatura (vencimento; compensada = data do pagamento). Gráficos de gasto e categoria usam os **itens** pela data da compra, nunca o total da fatura.
- **Reimportar**: mesmo cartão+competência. Mesmo hash → "já importada". Hash diferente → pede confirmação para substituir (itens e previstas refeitos; categorias já definidas são mantidas por descrição+data+valor).
- Conta-cartão ausente: a prévia oferece criá-la (emissor, bandeira, produto, final lidos do PDF).

## Fluxo
Importar → escolher PDF (+ senha se pedir) → prévia (itens, categoria sugerida por regras e pela categoria do banco, conferência, avisos, conta-cartão) → confirmar. Nada é gravado antes. Lançamentos mostra a fatura como linha expansível (total, sem categoria); ao expandir, itens com categoria editável item a item.

## Testes
Faturas **sintéticas** em texto (imitam cada layout; nenhum valor/nome real). PDFs reais ficam em `Faturas Testes/` (git-ignorado) e só alimentam um script local que imprime resultados mascarados. Migração v8 testada a partir de um banco v7 populado.

## Fora do escopo
OCR, emissores além dos 3, importação por consultor, desfazer casamento em lote, fatura em moeda estrangeira detalhada (o valor em R$ vale; moeda/cotação ficam na descrição).
