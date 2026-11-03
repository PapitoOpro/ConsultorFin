# Configurar o Gmail (somente leitura)

Permite ao Consultor achar e-mails de fatura. O acesso é só de leitura. Faça uma vez, no seu PC.

## 1. Projeto no Google Cloud
1. Acesse console.cloud.google.com e entre com a sua conta Google.
2. Crie um projeto novo (ex.: "ConsultorFin").
3. Em **APIs e serviços > Biblioteca**, procure **Gmail API** e clique em **Ativar**.

## 2. Tela de consentimento
1. **APIs e serviços > Tela de permissão OAuth**: tipo **Externo**, preencha nome do app e seu e-mail.
2. Em **Escopos**, adicione `.../auth/gmail.readonly`.
3. **Publique o app em produção** (botão "Publicar app"). Se ficar em "Teste", o token expira em 7 dias.
4. Ao autorizar, o Google mostra "app não verificado". É esperado e seguro para uso pessoal: clique em **Avançado > Acessar ConsultorFin (não seguro)**.

## 3. Credencial
1. **APIs e serviços > Credenciais > Criar credenciais > ID do cliente OAuth**.
2. Tipo de aplicativo: **App para computador**.
3. Baixe o JSON e salve como `client_secret.json` na raiz do projeto.

## 4. Autorizar
Na raiz do projeto, no PC:

```
.venv/Scripts/python scripts/gmail_autorizar.py client_secret.json gmail-token.json
```

O navegador abre; aceite o acesso. Será criado `gmail-token.json`.

## 5. Copiar para a VPS
```
scp gmail-token.json usuario@IP-DA-VPS:~/consultorfin/data/gmail-token.json
ssh usuario@IP-DA-VPS "chmod 600 ~/consultorfin/data/gmail-token.json"
```
Não precisa reiniciar. Depois apague `client_secret.json` e `gmail-token.json` do PC, se não for mais usar.

## 6. Testar
No chat do Consultor: "pesquisar faturas de 10/2026". As faturas encontradas aparecem na tela **Importar**.

## Segurança
- `gmail-token.json` e `client_secret.json` nunca vão para o Git nem são colados em chat, e-mail ou issue.
- Para revogar: myaccount.google.com/permissions, selecione o app e clique em **Remover acesso**. Apague o token da VPS.
