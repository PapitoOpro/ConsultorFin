# Cheque especial: dias de uso e alerta

## Intenção

O dono tem R$ 4.410,00 de limite no Itaú, com **10 dias por mês sem juros**. Hoje só vê o uso no app do banco e
já foi pego de surpresa. O painel deve mostrar, sozinho, quantos dias do mês a conta ficou negativa e avisar com
folga antes dos 10 dias, para ele cobrir o saldo a tempo.

## Regras (confirmadas pelo dono)

- Cada dia com a conta no negativo soma 1 dia de uso.
- Os dias **acumulam no mês**; não zeram quando o saldo volta a ficar positivo. Zeram na virada do mês.
- Alerta a partir de **8 dias** (2 de folga). Ao chegar a 10, o alerta vira "esgotado".
- Aviso em vermelho fixo no Início **e** pop-up.

## Objetivos

- Card "Cheque especial" no Início: valor em uso, limite, dias usados de 10 e barra de progresso (como o app do Itaú).
- Cálculo automático a partir do saldo diário da conta; sem digitar o uso todo dia.
- Alerta fixo em vermelho no Início quando dias usados >= limiar.
- Pop-up uma vez por dia ao abrir o Início enquanto o alerta estiver ativo.

## Fora do escopo

- Calcular juros ou IOF; prever dias futuros pela projeção; suporte a mais de uma conta com cheque especial;
  integração com o consultor/chat (pode vir depois); notificação fora do navegador (e-mail, push).

## Anti-objetivos

- Falso alarme por saldo estimado de conta sem dados: só calcular dentro do período com saldo conhecido.
- Pop-up que reaparece a cada clique ou não pode ser dispensado.
- Estado só por cor: o alerta traz texto e ícone.

## Decisões de design

Itens marcados "(minha escolha)" não foram discutidos com o dono.

1. **Configuração por conta (minha escolha):** guardada em `configuracoes`, chave `cheque_especial`, valor JSON
   `{"conta_id", "limite_centavos", "dias_isentos": 10, "alerta_a_partir_de": 8}`. Sem migração de banco.
   Valores iniciais sugeridos no formulário: 441000, 10, 8. Uma conta só.
2. **Tela de configuração (minha escolha):** link "Configurar" no próprio card (conta, limite, dias isentos,
   limiar). Sem a configuração, o card mostra um convite "Configurar cheque especial".
3. **Dia de uso (minha escolha):** o dia conta quando o **saldo ao fim do dia** (lançamentos compensados com
   `data_compensacao` <= dia) é negativo. Percorre do dia 1 do mês até hoje. O dia de hoje conta se o saldo
   atual já está negativo. Só lançamentos compensados; pendentes não entram.
4. **Datas sem saldo conhecido:** dias anteriores à `data_saldo_inicial` da conta não são contados.
5. **Valor em uso:** `max(0, -saldo atual)`. Se passar do limite, mostrar também o excesso em vermelho.
6. **Correção manual (minha escolha):** o app pode divergir do banco (lançamentos fora de ordem, critério do
   Itaú). O card permite informar "dias usados segundo o banco" para o mês corrente. Isso vira um ajuste
   (`dias_informados` + mês) que substitui a contagem calculada até a virada do mês.
7. **Estados:** `ok` (< limiar), `atencao` (limiar <= dias < isentos), `esgotado` (dias >= isentos). Sem dias de uso
   e saldo positivo: card neutro, sem alerta.
8. **Alerta fixo:** faixa vermelha no topo do Início, com ícone (macro `icone`), texto "Cheque especial: X de 10
   dias sem juros usados" e quantos faltam. Segue o tema (`--saida`, sem gradiente/neon/emoji).
9. **Pop-up (minha escolha):** `<dialog>` acessível (foco, ESC, botão "Entendi"), exibido no Início quando o
   estado é `atencao` ou `esgotado`. Dispensar grava em `localStorage` a chave `chequeEspecial:AAAA-MM-DD:estado`;
   volta no dia seguinte ou se o estado piorar. `localStorage` envolvido em try/catch; sem ele, o pop-up só
   aparece uma vez por carga de página. Respeitar `prefers-reduced-motion`.
10. **Arquitetura:** regra em `app/dominio/cheque_especial.py` (função pura que recebe saldos diários e config e
    devolve dias, estado, uso); leitura de saldos diários em `app/repo/`; `resumo_inicio` em `painel.py` inclui o
    resultado; template e CSS no Início. Valores em centavos; type hints; mensagens em português.

## A cargo do construtor

Consulta eficiente dos saldos diários do mês (uma soma por dia em uma query); nomes de arquivos e classes de CSS;
texto exato das mensagens; testes. Testes mínimos: contagem acumulada com saldo que volta ao positivo, virada de
mês zera, dia anterior ao saldo inicial ignorado, estados nos limites 7/8/9/10, ajuste manual vence o cálculo,
card e alerta renderizam sem configuração.

## Documentação

Atualizar `docs/PASSO-A-PASSO.md` (Início: cheque especial) e a seção Telas do `CLAUDE.md`/README se citarem os
cartões do Início.
