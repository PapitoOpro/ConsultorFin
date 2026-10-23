# ConsultorFin

Painel financeiro pessoal, local. Seus dados nunca saem do computador: a v0.1 não faz nenhuma chamada externa nem usa IA.

## Requisitos
- Python 3.11+

## Instalação
```
python -m venv .venv
.venv\Scripts\activate        # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

## Executar
```
uvicorn app.main:create_app --factory --port 8000
```
Abra http://localhost:8000.

## Onde ficam os dados
`data/consultorfin.db` (SQLite, ignorado pelo git). Backup = copiar esse arquivo.

## Importar o primeiro extrato (Itaú CSV)
1. Crie a conta informando o saldo inicial.
2. Em Importar, envie o CSV do extrato.
3. Confira a prévia e confirme.
4. Verifique o aviso "Saldo confere com o banco".

## Conceitos
- **Pendente vs compensado**: lançamento previsto ainda não confirmado no banco vs já efetivado.
- **Parcelamento**: compra dividida em parcelas mensais, cada uma um lançamento.
- **Contas fixas / receitas recorrentes**: modelos que geram lançamentos pendentes a cada mês.
- **Casamento com o extrato**: ao importar, lançamentos do extrato são casados com os pendentes e os compensam.

## Testes
```
pytest
```

## Configuração
`CONSULTORFIN_HOSTS`: nomes de Host extras aceitos, separados por vírgula.

## Limitações conhecidas da v0.1
- Só Itaú CSV tem detecção automática; outros formatos via mapeamento manual.
- OFX, PDF e faturas de cartão ficam para a v0.2.
- Pagamento de fatura conta como despesa até a v0.2.
- Os links "Ver no extrato" do Início são aproximados.
- Linhas idênticas no mesmo dia em arquivos que cortam o dia ao meio podem ser chaveadas de forma diferente (exporte dias inteiros).
- Sem login (uso local): não exponha a porta na rede.
