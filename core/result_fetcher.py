"""
Busca resultados de jogos e calcula CLV (Closing Line Value).
Usado pelo job noturno e pelo dashboard para auto-atualizar apostas.

CLV = (odd_entrada - odd_fechamento) / odd_fechamento * 100
Positivo = apostamos melhor que o mercado fechou = evidencia de valor real.
"""
import json
import logging
import os
import requests
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from core.form_checker import _name_match
from core.scheduler import _load_leagues

logger = logging.getLogger(__name__)

FD_BASE = "https://api.football-data.org/v4"
AF_BASE = "https://v3.football.api-sports.io"

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
CLOSING_ODDS_PATH = os.path.join(DATA_DIR, "closing_odds.json")

# Ligas sem suporte no plano gratuito do football-data.org
FD_UNSUPPORTED = {"CLI", "MLS", "BSA", "EL", ""}


def _fd_headers() -> dict:
    return {"X-Auth-Token": os.getenv("FOOTBALL_DATA_KEY", "")}


def _af_headers() -> dict:
    return {"x-apisports-key": os.getenv("API_FOOTBALL_KEY", "")}


def get_league_codes(liga_name: str) -> tuple[str, int]:
    """Retorna (football_data_code, api_football_id) para o nome de uma liga."""
    for lg in _load_leagues():
        if lg["name"] == liga_name:
            return lg.get("football_data_code", ""), lg.get("api_football_id", 0)
    return "", 0


def buscar_resultado(
    home: str, away: str, competition_code: str, data_jogo: str, api_football_id: int = 0
) -> dict:
    """
    Busca resultado de um jogo especifico.
    Usa football-data.org para ligas suportadas, API-Football como fallback.
    Retorna dict com chave 'resultado': HOME_WIN | AWAY_WIN | DRAW | PENDENTE | UNKNOWN | NAO_ENCONTRADO
    """
    if competition_code and competition_code not in FD_UNSUPPORTED:
        result = _buscar_fd(home, away, competition_code, data_jogo)
        if result["resultado"] not in ("UNKNOWN", "NAO_ENCONTRADO"):
            return result

    if api_football_id:
        result = _buscar_af(home, away, api_football_id, data_jogo)
        if result["resultado"] not in ("UNKNOWN", "NAO_ENCONTRADO"):
            return result

    return {"resultado": "NAO_ENCONTRADO", "home_goals": None, "away_goals": None}


def _buscar_fd(home: str, away: str, competition_code: str, data_jogo: str) -> dict:
    url = f"{FD_BASE}/competitions/{competition_code}/matches"
    params = {"dateFrom": data_jogo, "dateTo": data_jogo}
    try:
        r = requests.get(url, headers=_fd_headers(), params=params, timeout=10)
        if r.status_code == 403:
            logger.warning(f"FD: {competition_code} nao disponivel no plano gratuito")
            return {"resultado": "UNKNOWN"}
        r.raise_for_status()
        matches = r.json().get("matches", [])
    except requests.RequestException as e:
        logger.error(f"FD erro ao buscar {home} vs {away}: {e}")
        return {"resultado": "UNKNOWN"}

    for m in matches:
        mhome = m["homeTeam"]["name"]
        maway = m["awayTeam"]["name"]
        if not (_name_match(home, mhome) and _name_match(away, maway)):
            continue
        if m.get("status", "") not in ("FINISHED", "FT"):
            return {"resultado": "PENDENTE", "home_goals": None, "away_goals": None}
        score = m["score"]["fullTime"]
        hg = score.get("home") or 0
        ag = score.get("away") or 0
        resultado = "HOME_WIN" if hg > ag else ("AWAY_WIN" if ag > hg else "DRAW")
        return {"resultado": resultado, "home_goals": hg, "away_goals": ag}

    return {"resultado": "NAO_ENCONTRADO", "home_goals": None, "away_goals": None}


def _buscar_af(home: str, away: str, api_football_id: int, data_jogo: str) -> dict:
    url = f"{AF_BASE}/fixtures"
    params = {"league": api_football_id, "date": data_jogo}
    try:
        r = requests.get(url, headers=_af_headers(), params=params, timeout=10)
        r.raise_for_status()
        fixtures = r.json().get("response", [])
    except requests.RequestException as e:
        logger.error(f"API-Football erro ao buscar {home} vs {away}: {e}")
        return {"resultado": "UNKNOWN"}

    for fix in fixtures:
        teams = fix.get("teams", {})
        mhome = teams.get("home", {}).get("name", "")
        maway = teams.get("away", {}).get("name", "")
        if not (_name_match(home, mhome) and _name_match(away, maway)):
            continue
        status_short = fix.get("fixture", {}).get("status", {}).get("short", "")
        if status_short not in ("FT", "AET", "PEN"):
            return {"resultado": "PENDENTE", "home_goals": None, "away_goals": None}
        goals = fix.get("goals", {})
        hg = goals.get("home") or 0
        ag = goals.get("away") or 0
        resultado = "HOME_WIN" if hg > ag else ("AWAY_WIN" if ag > hg else "DRAW")
        return {"resultado": resultado, "home_goals": hg, "away_goals": ag}

    return {"resultado": "NAO_ENCONTRADO", "home_goals": None, "away_goals": None}


def favorito_ganhou(favorito: str, home: str, resultado: dict) -> Optional[bool]:
    """True=WIN, False=LOSS, None=inconclusivo."""
    res = resultado.get("resultado", "UNKNOWN")
    if res in ("UNKNOWN", "PENDENTE", "NAO_ENCONTRADO"):
        return None
    fav_eh_home = _name_match(favorito, home)
    if res == "HOME_WIN":
        return fav_eh_home
    if res == "AWAY_WIN":
        return not fav_eh_home
    return False  # DRAW = favorito nao ganhou


def calcular_clv(market_id: str, odd_entrada: float) -> Optional[float]:
    """
    Closing Line Value: quanto melhor (ou pior) apostamos vs a odd de fechamento.
    Requer closing_odds.json gerado pelo job das 14h30 BRT.
    """
    if not market_id or not os.path.exists(CLOSING_ODDS_PATH):
        return None
    try:
        with open(CLOSING_ODDS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        closing_odd = data.get("market_odds", {}).get(market_id)
        if closing_odd and closing_odd > 1.0 and odd_entrada > 1.0:
            clv = round((odd_entrada - closing_odd) / closing_odd * 100, 2)
            return clv
    except Exception as e:
        logger.error(f"Erro ao calcular CLV para {market_id}: {e}")
    return None
