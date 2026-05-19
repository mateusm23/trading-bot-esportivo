import logging

logger = logging.getLogger(__name__)

# Pontuação máxima por critério (total = 100)
SCORE_ODD_IDEAL = 30        # odd entre 1.40 e 1.55
SCORE_FORMA = 25            # 5 vitórias nos últimos 5 jogos
SCORE_COBERTURA = 20        # > 10 casas cobrindo o jogo
SCORE_SEM_VETO = 25         # nenhum veto ativo

ODD_IDEAL_MIN = 1.40
ODD_IDEAL_MAX = 1.55
ODD_RANGE_MIN = 1.35        # limite inferior do filtro global
ODD_RANGE_MAX = 1.60        # limite superior do filtro global
BOOKMAKERS_ALTO = 10
BOOKMAKERS_MINIMO = 5


def calculate(market: dict, form: dict, veto: dict) -> int:
    """Pontua o mercado de 0 a 100 conforme os critérios da cartilha."""
    score = 0
    odd: float = market.get("odd_favorito", 0.0)
    vitorias: int = form.get("vitorias_recentes", 0)
    num_bm: int = market.get("num_bookmakers", 0)

    # Critério 1: odd no range ideal (1.40–1.55)
    if ODD_IDEAL_MIN <= odd <= ODD_IDEAL_MAX:
        score += SCORE_ODD_IDEAL
    else:
        # Pontuação parcial para odds fora do ideal mas dentro do range permitido
        distancia = min(abs(odd - ODD_IDEAL_MIN), abs(odd - ODD_IDEAL_MAX))
        range_total = (ODD_RANGE_MAX - ODD_RANGE_MIN) / 2
        fator = max(0.0, 1 - distancia / range_total)
        score += round(SCORE_ODD_IDEAL * fator)

    # Critério 2: forma recente (vitórias nos últimos 5 jogos)
    if vitorias == 5:
        score += SCORE_FORMA
    elif vitorias >= 3:
        score += round(SCORE_FORMA * vitorias / 5)

    # Critério 3: cobertura de casas de apostas (proxy de liquidez)
    if num_bm > BOOKMAKERS_ALTO:
        score += SCORE_COBERTURA
    elif num_bm >= BOOKMAKERS_MINIMO:
        score += round(SCORE_COBERTURA * num_bm / BOOKMAKERS_ALTO)

    # Critério 4: sem vetos ativos
    if not veto.get("vetado", False):
        score += SCORE_SEM_VETO

    logger.info(
        f"Score: {score}/100 "
        f"(odd={odd} | vitorias={vitorias}/5 | casas={num_bm} | veto={veto.get('vetado')})"
    )
    return score
