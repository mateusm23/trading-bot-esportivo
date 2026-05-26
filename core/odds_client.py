import requests
import logging
import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

ODD_MIN = 1.35
ODD_MAX = 1.49  # backtest: faixas 1.50-1.60 tem ROI -7% a -8% historicamente
MIN_BOOKMAKERS = 5
BASE_URL = "https://api.the-odds-api.com/v4"


class OddsClient:
    def __init__(self) -> None:
        self._api_key: str = os.getenv("ODDS_API_KEY", "")
        self._session = requests.Session()
        self._requests_remaining: Optional[int] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_active_markets(self, sport_keys: list[str]) -> list[dict]:
        """Busca mercados 1x2 para as ligas fornecidas.
        Retorna apenas jogos com odd_favorito em [1.35, 1.60] e >= 5 casas cobrindo."""
        results: list[dict] = []
        for sport_key in sport_keys:
            raw = self._fetch_odds(sport_key)
            for match in raw:
                parsed = self._parse_match(match)
                if parsed:
                    results.append(parsed)

        logger.info(
            f"{len(results)} mercados aprovados em {len(sport_keys)} ligas "
            f"| Requisições restantes: {self._requests_remaining}"
        )
        return results

    @property
    def requests_remaining(self) -> Optional[int]:
        return self._requests_remaining

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_odds(self, sport_key: str) -> list[dict]:
        url = f"{BASE_URL}/sports/{sport_key}/odds/"
        params = {
            "apiKey": self._api_key,
            "regions": "eu",
            "markets": "h2h",
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        }
        try:
            r = self._session.get(url, params=params, timeout=10)
            self._requests_remaining = int(r.headers.get("x-requests-remaining", -1))
            r.raise_for_status()
            data = r.json()
            logger.info(f"{sport_key}: {len(data)} jogos retornados pela API")
            return data
        except requests.HTTPError as e:
            logger.error(f"HTTP error em {sport_key}: {e} — resposta: {e.response.text[:200]}")
            return []
        except requests.RequestException as e:
            logger.error(f"Falha de conexão em {sport_key}: {e}")
            return []

    def _parse_match(self, match: dict) -> Optional[dict]:
        home: str = match.get("home_team", "")
        away: str = match.get("away_team", "")
        bookmakers: list = match.get("bookmakers", [])

        if len(bookmakers) < MIN_BOOKMAKERS:
            logger.debug(f"{home} vs {away}: {len(bookmakers)} casas < {MIN_BOOKMAKERS} — skip")
            return None

        # Agrega as odds de cada outcome por todas as casas disponíveis
        odds_pool: dict[str, list[float]] = {}
        for bm in bookmakers:
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                for outcome in market.get("outcomes", []):
                    odds_pool.setdefault(outcome["name"], []).append(outcome["price"])

        if not odds_pool:
            return None

        avg: dict[str, float] = {
            name: sum(prices) / len(prices)
            for name, prices in odds_pool.items()
        }

        # Favorito/zebra são sempre times — exclui empate da comparação
        team_odds = {k: v for k, v in avg.items() if k.lower() != "draw"}
        if len(team_odds) < 2:
            return None

        favorito = min(team_odds, key=lambda k: team_odds[k])
        zebra = max(team_odds, key=lambda k: team_odds[k])
        odd_favorito = round(team_odds[favorito], 3)
        odd_zebra = round(team_odds[zebra], 3)
        odd_empate = round(avg.get("Draw", 0.0), 3)

        if not (ODD_MIN <= odd_favorito <= ODD_MAX):
            logger.debug(f"{home} vs {away}: odd_fav {odd_favorito} fora de [{ODD_MIN},{ODD_MAX}] — skip")
            return None

        return {
            "market_id": match.get("id", ""),
            "sport_key": match.get("sport_key", ""),
            "competition": match.get("sport_title", ""),
            "home_team": home,
            "away_team": away,
            "event": f"{home} vs {away}",
            "start_time": match.get("commence_time"),
            "favorito": favorito,
            "odd_favorito": odd_favorito,
            "zebra": zebra,
            "odd_zebra": odd_zebra,
            "odd_empate": odd_empate,
            "num_bookmakers": len(bookmakers),
        }
