# Monitor de estoque - Maersk Container Sales

Bot que monitora o estoque de contêineres (Brasil) no site da Maersk
Container Sales e envia alertas no Telegram quando:

- chega estoque novo
- o estoque de um item aumenta
- o estoque de um item diminui
- um item zera (esgota)

Também envia um **resumo diário** (08:00 horário de Brasília) com o total
por tipo/condição.

Tipos de contêiner monitorados: **20' Dry Standard**, **40' Dry High**
(equivalente a 40' High Cube) e **40' Dry Standard**, na região **Brasil**.

## Configuração necessária (uma única vez)

No repositório do GitHub, vá em **Settings → Secrets and variables →
Actions → New repository secret** e crie estes 4 secrets:

| Nome              | Valor                                             |
|-------------------|----------------------------------------------------|
| `MAERSK_EMAIL`    | e-mail de login no site da Maersk Container Sales   |
| `MAERSK_PASSWORD` | senha de login no site                              |
| `TELEGRAM_BOT_TOKEN` | token do bot, obtido com o @BotFather            |
| `TELEGRAM_CHAT_ID`  | seu chat_id no Telegram                           |

Nenhuma dessas informações fica exposta no código — os Secrets do GitHub
são criptografados e só ficam visíveis durante a execução do workflow.

## Como funciona

O arquivo `.github/workflows/monitor.yml` agenda duas execuções:

- a cada 15 minutos: roda `scraper.py`, compara com o último estado salvo
  em `state/snapshot.json` e manda alertas se algo mudou
- uma vez por dia (08:00 Brasília): roda `scraper.py --summary`, mandando
  o resumo consolidado

Depois de cada execução, o workflow salva o novo snapshot de volta no
repositório automaticamente (`git commit` + `git push`).

## Rodar manualmente

Na aba **Actions** do repositório, escolha o workflow "Monitor de estoque
Maersk" e clique em **Run workflow** para testar sem esperar o agendamento.

## Ajustar tipos ou região monitorada

Edite as constantes no topo do arquivo `scraper.py`:

```python
CONTAINER_TYPES = ["20' Dry Standard", "40' Dry High", "40' Dry Standard"]
COUNTRY = "BR"
```
