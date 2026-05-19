import requests
import logging
import os
import time
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

BASE_URL = "https://api.football-data.org/v4"
CRISIS_THRESHOLD = 2        # vitórias <= N nos últimos 5 = em crise
API_CALL_INTERVAL = 6.5     # segundos entre chamadas (respeita 10 req/min)
_last_call_time: float = 0.0

# Cache em memória para evitar chamar a API repetidamente pelo mesmo time
_CACHE: dict[str, dict] = {}


def get_form(team_name: str, competition_code: str) -> dict:
    """Retorna {vitorias_recentes, jogos_analisados, media_gols_sofridos, em_crise}
    com base nos últimos 5 jogos do time na competição."""
    cache_key = f"{team_name}|{competition_code}"
    if cache_key in _CACHE:
        logger.debug(f"Cache hit: {cache_key}")
        return _CACHE[cache_key]

    _rate_limit()

    headers = {"X-Auth-Token": os.getenv("FOOTBALL_DATA_KEY", "")}
    url = f"{BASE_URL}/competitions/{competition_code}/matches"
    params = {"status": "FINISHED", "limit": 50}

    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        if r.status_code == 403:
            logger.warning(f"{competition_code} indisponível no plano gratuito")
            return default_form()
        r.raise_for_status()
        matches = r.json().get("matches", [])
        logger.info(f"football-data.org [{competition_code}]: {len(matches)} jogos encontrados")
    except requests.RequestException as e:
        logger.error(f"Erro ao buscar forma de {team_name} em {competition_code}: {e}")
        return default_form()

    team_matches = [
        m for m in matches
        if _name_match(team_name, m["homeTeam"]["name"])
        or _name_match(team_name, m["awayTeam"]["name"])
    ][-5:]

    if not team_matches:
        logger.warning(f"Nenhum jogo encontrado para '{team_name}' em {competition_code}")
        return default_form()

    vitorias = 0
    gols_sofridos: list[int] = []

    for m in team_matches:
        score = m["score"]["fullTime"]
        home_gols: int = score.get("home") or 0
        away_gols: int = score.get("away") or 0
        jogando_em_casa = _name_match(team_name, m["homeTeam"]["name"])

        if jogando_em_casa:
            if home_gols > away_gols:
                vitorias += 1
            gols_sofridos.append(away_gols)
        else:
            if away_gols > home_gols:
                vitorias += 1
            gols_sofridos.append(home_gols)

    media_sofridos = round(sum(gols_sofridos) / len(gols_sofridos), 2) if gols_sofridos else 0.0
    em_crise = vitorias <= CRISIS_THRESHOLD

    result = {
        "vitorias_recentes": vitorias,
        "jogos_analisados": len(team_matches),
        "media_gols_sofridos": media_sofridos,
        "em_crise": em_crise,
    }
    _CACHE[cache_key] = result
    logger.info(
        f"Forma [{team_name}]: {vitorias}/5 vitórias | "
        f"{media_sofridos} gols sofridos/jogo | crise={em_crise}"
    )
    return result


def default_form() -> dict:
    return {
        "vitorias_recentes": 0,
        "jogos_analisados": 0,
        "media_gols_sofridos": 0.0,
        "em_crise": False,
    }


def clear_cache() -> None:
    _CACHE.clear()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _normalize(name: str) -> str:
    suffixes = [" fc", " cf", " sc", " afc", " bsc", " fk", " sk", " ac", " as", " if", " bk"]
    n = name.lower().strip()
    for s in suffixes:
        if n.endswith(s):
            n = n[: -len(s)].strip()
    return n


def _name_match(a: str, b: str) -> bool:
    na, nb = _normalize(a), _normalize(b)
    return na == nb or na in nb or nb in na


def _rate_limit() -> None:
    global _last_call_time
    elapsed = time.monotonic() - _last_call_time
    if elapsed < API_CALL_INTERVAL:
        time.sleep(API_CALL_INTERVAL - elapsed)
    _last_call_time = time.monotonic()
