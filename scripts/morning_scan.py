"""
Job Manha — roda uma vez por dia as 8h BRT.

1. Busca todos os jogos do dia nas 10 ligas (10 req The Odds API)
2. Aplica filtros de forma, score e vetos
3. Envia grade + selecao via Telegram
4. Salva selecoes em data/selecoes_hoje.json para o job noturno
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

from core.odds_client import OddsClient
from core.filter_engine import analyze_match
from core.database import Database
from core.scheduler import (
    _montar_grade, _montar_grade_filtrados,
    _load_leagues, _sport_key_to_league,
)
from alerts.telegram_bot import TelegramBot

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SELECOES_PATH = os.path.join(DATA_DIR, "selecoes_hoje.json")


def main() -> None:
    logger.info("=== Job Manha iniciado ===")
    os.makedirs(DATA_DIR, exist_ok=True)

    db = Database()
    db.initialize()
    telegram = TelegramBot(db=db)
    odds_client = OddsClient()

    leagues = _load_leagues()
    sport_keys = [lg["odds_api_key"] for lg in leagues if lg.get("active")]

    todos: list[dict] = []
    selecoes: list[dict] = []

    for sport_key in sport_keys:
        raw = odds_client._fetch_odds(sport_key)
        liga_nome = _sport_key_to_league(sport_key)
        for match in raw:
            match["competition"] = liga_nome
            match["start_time"] = match.get("commence_time", "")
            match["event"] = f"{match.get('home_team', '')} vs {match.get('away_team', '')}"
            todos.append(match)

            parsed = odds_client._parse_match(match)
            if parsed:
                result = analyze_match(parsed)
                if result["status"] == "APROVADO":
                    parsed["score"] = result["score"]
                    parsed["motivo"] = result["motivo"]
                    parsed["form"] = result["form"]
                    selecoes.append(parsed)

    logger.info(f"Total: {len(todos)} jogos | {len(selecoes)} aprovados")

    # Envia grade completa
    msg_grade = _montar_grade(todos)
    telegram.send(msg_grade, tipo_alerta="GRADE")

    # Envia selecao filtrada
    if selecoes:
        msg_sel = _montar_grade_filtrados(selecoes)
        telegram.send(msg_sel, tipo_alerta="FILTRADOS")
    else:
        telegram.send("Nenhum jogo aprovado pelos criterios hoje.", tipo_alerta="FILTRADOS")

    # Salva selecoes para o job noturno
    hoje = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")
    payload = {
        "data": hoje,
        "total_jogos": len(todos),
        "selecoes": [
            {
                "market_id": s.get("market_id", ""),
                "event": s.get("event", ""),
                "competition": s.get("competition", ""),
                "sport_key": s.get("sport_key", ""),
                "start_time": s.get("start_time", ""),
                "home_team": s.get("home_team", ""),
                "away_team": s.get("away_team", ""),
                "favorito": s.get("favorito", ""),
                "odd_favorito": s.get("odd_favorito", 0),
                "odd_empate": s.get("odd_empate", 0),
                "odd_zebra": s.get("odd_zebra", 0),
                "num_bookmakers": s.get("num_bookmakers", 0),
                "score": s.get("score", 0),
                "motivo": s.get("motivo", ""),
            }
            for s in selecoes
        ],
    }
    with open(SELECOES_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    logger.info(f"Selecoes salvas em {SELECOES_PATH}")
    logger.info("=== Job Manha concluido ===")


if __name__ == "__main__":
    main()
