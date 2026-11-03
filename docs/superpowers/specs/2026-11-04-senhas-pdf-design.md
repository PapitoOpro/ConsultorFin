# Senhas de PDF guardadas por cartão

## Intenção
Cada cartão tem uma senha fixa para o PDF da fatura. Hoje o usuário a digita a cada importação. Guardar
a senha, criptografada, por (emissor, final do cartão) e usá-la sozinho na importação.

## Regras (aprovadas pelo usuário em 2026-11-04)
- Opção B: guardar no servidor. Uma senha por cartão.
- Guardar só **depois** que o PDF abriu com sucesso com a senha digitada, e só se a caixa
  "Lembrar a senha deste cartão" estiver marcada. Senha errada nunca é guardada.
- Criptografia AES-256-GCM com `pycryptodome` (já é dependência; nenhuma dependência nova). Nonce aleatório
  de 12 bytes por senha; o par (emissor, final) entra como dado associado (AAD).
- Chave: 32 bytes aleatórios em arquivo `data/senhas.key` (env `CONSULTORFIN_SENHAS_KEY` pode apontar outro
  caminho), criado na primeira gravação com permissão 0600 (0700 no diretório quando criado). Fora do
  banco e do Git (`data/` já é ignorado).
- Uso: ao importar (upload manual ou "Importar" da fila) de um PDF protegido sem senha digitada, tentar as
  senhas guardadas: primeiro as do (emissor, final) da dica da fila, depois as do mesmo emissor, depois as
  demais. A primeira que abre vence; segue direto para a prévia (sem formulário de senha). Se nenhuma
  abrir, cai no formulário atual, e o usuário pode substituir marcando "Lembrar".
- Gerenciar: bloco na tela Importar lista cartões com senha guardada (emissor, final; **nunca** a senha)
  com botão "Esquecer" (POST, mesma proteção de origem já existente).
- Segredo nunca em: log, HTML, mensagem de erro, JSON do consultor, saída do CLI, URL, token de upload.
- Claude/Consultor: nunca lê `data/senhas.key` nem a tabela `senhas_pdf`; o CLI do consultor não tem
  comando que as exponha. Registrar isso em `CLAUDE.md` (Segurança). Limite assumido: mesmo usuário Linux.
- Chave ausente com senhas na tabela: tratar como "sem senhas guardadas" (log `event=senhas_chave_ausente`)
  e cair no formulário; nunca falhar com traceback.

## Fora do escopo
Cofre com senha mestra; sincronização; guardar senha de holerite (só fatura de cartão); leitor Nubank.

## Decisões do assistente
- Tabela `senhas_pdf(id, emissor, cartao_final, nonce, cifra, criado_em, UNIQUE(emissor, cartao_final))`,
  migração 14. `emissor`/`cartao_final` vêm da fatura já lida (`FaturaLida`).
- Dica da fila: `Envio` do `ArmazemUploads` ganha `dica_emissor`/`dica_final` opcionais, preenchidos pela rota
  de importar da fila.
