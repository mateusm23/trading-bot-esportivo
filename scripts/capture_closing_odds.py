"""
Captura odds de fechamento para calculo de CLV.
Roda as 14h30 BRT (17h30 UTC) — antes da maioria dos jogos europeus.

So busca sport_keys com selecoes ativas no dia, economizando cota da API.
Salva data/closing_odds.json para ser lido pelo job noturno e pelo dashboard.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

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

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SELECOES_PATH   = os.path.join(DATA_DIR, "selecoes_hoje.json")
CLOSING_ODDS_PATH = os.path.join(DATA_DIR, "closing_odds.json")
QUOTA_PATH      = os.path.join(DATA_DIR, "api_quota.json")


def _avg_fav_odd(match: dict) -> float:
    pool: dict[str, list[float]] = {}
    for bm in match.get("bookmakers", []):
        for mkt in bm.get("markets", []):
            if mkt.get("key") != "h2h":
                continue
            for outcome in mkt.get("outcomes", []):
                pool.setdefault(outcome["name"], []).append(outcome["price"])
    if not pool:
        return 0.0
    avg = {name: sum(p) / len(p) for name, p in pool.items()}
    team_odds = {k: v for k, v in avg.items() if k.lower() != "draw"}
    return round(min(team_odds.values()), 3) if len(team_odds) >= 2 else 0.0


def main() -> None:
    logger.info("=== Captura de Odds de Fechamento iniciada ===")
    os.makedirs(DATA_DIR, exist_ok=True)

    if not os.path.exists(SELECOES_PATH):
        logger.warning("selecoes_hoje.json nao encontrado — nenhuma selecao para capturar")
        return

    with open(SELECOES_PATH, encoding="utf-8") as f:
        payload = json.load(f)

    selecoes = payload.get("selecoes", [])
    if not selecoes:
        logger.info("Nenhuma selecao ativa hoje — pulando captura de odds de fechamento")
        return

    sport_keys = list({s["sport_key"] for s in selecoes if s.get("sport_key")})
    logger.info(f"Capturando odds de fechamento para {len(sport_keys)} liga(s): {sport_keys}")

    odds_client = OddsClient()
    closing: dict[str, float] = {}

    for sport_key in sport_keys:
        raw = odds_client._fetch_odds(sport_key)
        for match in raw:
            market_id = match.get("id", "")
            if not market_id:
                continue
            odd = _avg_fav_odd(match)
            if odd > 1.0:
                closing[market_id] = odd

    payload_out = {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "sport_keys_capturados": sport_keys,
        "market_odds": closing,
    }
    with open(CLOSING_ODDS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload_out, f, indent=2)

    # Atualiza quota
    remaining = odds_client.requests_remaining
    if remaining is not None and os.path.exists(QUOTA_PATH):
        try:
            with open(QUOTA_PATH, encoding="utf-8") as f:
                quota = json.load(f)
            quota["requests_remaining"] = remaining
            quota["requests_used"] = quota.get("tier_total", 500) - remaining
            with open(QUOTA_PATH, "w", encoding="utf-8") as f:
                json.dump(quota, f, indent=2)
        except Exception:
            pass

    logger.info(
        f"Odds de fechamento salvas: {len(closing)} mercados | "
        f"API restante: {odds_client.requests_remaining}"
    )
    logger.info("=== Captura de Odds de Fechamento concluida ===")


if __name__ == "__main__":
    main()
