# Trading Bot — Sensor de Odds + Alertas Telegram

Sistema de monitoramento de odds para trading esportivo nas 10 principais ligas.
Detecta oportunidades pré-jogo, filtra por forma recente e score, e envia alertas via Telegram.
Você executa os trades manualmente na Bolsa de Aposta.

---

## Stack

| Componente | Fonte | Custo |
|---|---|---|
| Odds pré-jogo | The Odds API | Grátis (500 req/mês) |
| Eventos ao vivo | API-Football | Grátis (100 req/dia) |
| Forma recente | football-data.org | Grátis (10 req/min) |
| Alertas | Telegram Bot API | Grátis |
| Banco de dados | SQLite (local) | Grátis |
| Dashboard | Flask (localhost) | Grátis |

---

## Instalação

```bash
pip install -r requirements.txt
```

---

## Configuração

### 1. Copie o arquivo de variáveis de ambiente

```bash
cp .env.example .env
```

### 2. Preencha o `.env`

```env
ODDS_API_KEY=          # the-odds-api.com → Get API Key
API_FOOTBALL_KEY=      # dashboard.api-football.com → My Account → API Key
FOOTBALL_DATA_KEY=     # football-data.org → Get API Key

TELEGRAM_BOT_TOKEN=    # veja instruções abaixo
TELEGRAM_CHAT_ID=      # veja instruções abaixo

BANCA_INICIAL=1000
STAKE_PERCENT=2.5
STOP_DIARIO_PERCENT=5.0
```

### 3. Criar o Telegram Bot

1. Abra o Telegram e pesquise `@BotFather`
2. Envie `/newbot`
3. Escolha um nome e um username (ex: `meu_trading_bot`)
4. Copie o token exibido → `TELEGRAM_BOT_TOKEN`
5. Pesquise `@userinfobot` no Telegram → envie qualquer mensagem
6. Copie o `Id` exibido → `TELEGRAM_CHAT_ID`

---

## Como rodar

```bash
python main.py
```

O bot vai:
- Varrer as 10 ligas a cada 5 minutos
- Filtrar jogos aprovados por odd, forma e score
- Enviar alerta Telegram para jogos com kickoff em menos de 90 minutos
- Monitorar jogos ao vivo a cada 3 minutos
- Salvar tudo no banco SQLite em `data/trading.db`

### Dashboard

Acesse em **http://localhost:5000** enquanto o bot estiver rodando.

---

## Ligas monitoradas

| Liga | País |
|---|---|
| Premier League | Inglaterra |
| La Liga | Espanha |
| Serie A | Itália |
| Bundesliga | Alemanha |
| Ligue 1 | França |
| Brasileirão Série A | Brasil |
| Champions League | Europa |
| Europa League | Europa |
| Copa Libertadores | América do Sul |
| MLS | EUA |

---

## Critérios de aprovação

| Critério | Condição |
|---|---|
| Odd do favorito | Entre 1.35 e 1.60 |
| Cobertura mínima | 5+ casas de apostas |
| Forma recente | Não em crise (> 2 vitórias nos últimos 5 jogos) |
| Score mínimo | 50/100 |
| Veto — clássico | Jogos de alta rivalidade são descartados |
| Veto — odd suspeita | Queda > 15% na odd é descartada |

### Pontuação (0–100)

| Critério | Pontos |
|---|---|
| Odd ideal (1.40–1.55) | +30 |
| 5 vitórias nos últimos 5 jogos | +25 |
| Mais de 10 casas cobrindo | +20 |
| Sem vetos | +25 |

---

## Testes

```bash
python tests/test_filters.py
```

---

## Estrutura do projeto

```
trading_bot/
├── core/
│   ├── odds_client.py      # The Odds API
│   ├── filter_engine.py    # Orquestrador de filtros
│   ├── form_checker.py     # football-data.org
│   ├── veto_checker.py     # Regras de veto
│   ├── scorer.py           # Pontuação 0-100
│   ├── database.py         # SQLite
│   ├── bankroll.py         # Controle de banca
│   └── live_monitor.py     # API-Football ao vivo
├── alerts/
│   ├── telegram_bot.py
│   └── alert_formatter.py
├── dashboard/
│   ├── app.py
│   └── templates/index.html
├── data/
│   ├── leagues.json
│   └── trading.db          # criado automaticamente
├── logs/
│   └── trading_bot.log     # criado automaticamente
├── tests/
│   └── test_filters.py
├── main.py
├── requirements.txt
└── .env
```
