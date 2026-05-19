import json
import logging
import os
from typing import Optional

from core import form_checker, veto_checker, scorer

logger = logging.getLogger(__name__)

MIN_SCORE = 50

_LEAGUES_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "leagues.json")


def _load_sport_key_map() -> dict[str, str]:
    """Mapeia odds_api_key → football_data_code a partir de leagues.json."""
    try:
        with open(_LEAGUES_PATH, encoding="utf-8") as f:
            leagues = json.load(f)["leagues"]
        return {lg["odds_api_key"]: lg["football_data_code"] for lg in leagues}
    except Exception as e:
        logger.error(f"Falha ao carregar leagues.json: {e}")
        return {}


_SPORT_KEY_MAP: dict[str, str] = _load_sport_key_map()


def analyze_match(market: dict, prev_odd: Optional[float] = None) -> dict:
    """Aplica form_checker → veto_checker → scorer em sequência.

    Retorna {status, motivo, score, form, veto}.
    status pode ser 'APROVADO' ou 'REPROVADO'.
    """
    event = market.get("event", "?")
    sport_key = market.get("sport_key", "")
    favorito = market.get("favorito", "")
    competition_code = _SPORT_KEY_MAP.get(sport_key, "")

    logger.info(f"--- Analisando: {event} ---")

    # 1. Forma recente do favorito
    if competition_code:
        form = form_checker.get_form(favorito, competition_code)
    else:
        logger.warning(f"Sem mapeamento football-data para sport_key='{sport_key}'")
        form = form_checker.default_form()

    # 2. Vetos
    veto = veto_checker.check(market, prev_odd)

    # 3. Pontuação
    score = scorer.calculate(market, form, veto)

    # 4. Decisão em cascata — primeiro veto detectado vence
    if veto["vetado"]:
        return _resultado("REPROVADO", veto["motivo"], score, form, veto)

    if form.get("em_crise"):
        motivo = f"Favorito em crise ({form['vitorias_recentes']}/5 vitórias recentes)"
        return _resultado("REPROVADO", motivo, score, form, veto)

    if score < MIN_SCORE:
        motivo = f"Score insuficiente ({score}/100 < mínimo {MIN_SCORE})"
        return _resultado("REPROVADO", motivo, score, form, veto)

    motivo = (
        f"Score {score}/100 | "
        f"{form['vitorias_recentes']}/5 vitórias | "
        f"{market.get('num_bookmakers', 0)} casas"
    )
    return _resultado("APROVADO", motivo, score, form, veto)


def _resultado(
    status: str, motivo: str, score: int, form: dict, veto: dict
) -> dict:
    logger.info(f"Resultado: {status} — {motivo}")
    return {
        "status": status,
        "motivo": motivo,
        "score": score,
        "form": form,
        "veto": veto,
    }
