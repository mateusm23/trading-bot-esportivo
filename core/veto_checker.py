import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Clássicos de alta rivalidade — ambos os times precisam estar presentes
RIVALIDADES: list[frozenset] = [
    frozenset(["Real Madrid", "Barcelona"]),
    frozenset(["Inter", "AC Milan"]),
    frozenset(["Manchester United", "Manchester City"]),
    frozenset(["Liverpool", "Everton"]),
    frozenset(["Arsenal", "Tottenham"]),
    frozenset(["Borussia Dortmund", "Schalke 04"]),
    frozenset(["Flamengo", "Fluminense"]),
    frozenset(["Flamengo", "Vasco da Gama"]),
    frozenset(["Corinthians", "Palmeiras"]),
    frozenset(["São Paulo", "Corinthians"]),
    frozenset(["Boca Juniors", "River Plate"]),
    frozenset(["Celtic", "Rangers"]),
    frozenset(["Fenerbahçe", "Galatasaray"]),
]

ODD_QUEDA_LIMITE = 0.15     # 15% de queda = suspeita de insider


def check(market: dict, prev_odd: Optional[float] = None) -> dict:
    """Verifica vetos no mercado. Retorna {vetado: bool, motivo: str}."""
    home = market.get("home_team", "")
    away = market.get("away_team", "")
    odd_atual = market.get("odd_favorito", 0.0)

    # Veto 1: clássico de alta rivalidade
    for rivalidade in RIVALIDADES:
        if _match_rivalidade(home, away, rivalidade):
            motivo = f"Clássico de rivalidade: {home} vs {away}"
            logger.info(f"VETO — {motivo}")
            return {"vetado": True, "motivo": motivo}

    # Veto 2: queda brusca de odd (possível informação privilegiada)
    if prev_odd and prev_odd > 0 and odd_atual > 0:
        queda = (prev_odd - odd_atual) / prev_odd
        if queda >= ODD_QUEDA_LIMITE:
            motivo = (
                f"Queda suspeita de odd: {prev_odd:.2f} -> {odd_atual:.2f} "
                f"({queda * 100:.1f}%)"
            )
            logger.info(f"VETO — {motivo}")
            return {"vetado": True, "motivo": motivo}

    return {"vetado": False, "motivo": ""}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _normalize(name: str) -> str:
    return name.lower().strip()


def _match_rivalidade(home: str, away: str, rivalidade: frozenset) -> bool:
    nomes = [_normalize(home), _normalize(away)]
    hits = sum(
        any(_normalize(r) in n or n in _normalize(r) for n in nomes)
        for r in rivalidade
    )
    return hits == 2
