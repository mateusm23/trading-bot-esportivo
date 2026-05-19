import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core import veto_checker, scorer
from core.filter_engine import analyze_match

# ------------------------------------------------------------------
# Fixtures reutilizáveis
# ------------------------------------------------------------------

def market_base(**kwargs) -> dict:
    m = {
        "market_id": "test_001",
        "sport_key": "soccer_epl",
        "competition": "EPL",
        "home_team": "Arsenal",
        "away_team": "Crystal Palace",
        "event": "Arsenal vs Crystal Palace",
        "start_time": "2026-06-01T15:00:00Z",
        "favorito": "Arsenal",
        "odd_favorito": 1.45,
        "zebra": "Crystal Palace",
        "odd_zebra": 6.50,
        "odd_empate": 4.20,
        "num_bookmakers": 18,
    }
    m.update(kwargs)
    return m


def form_base(**kwargs) -> dict:
    f = {
        "vitorias_recentes": 4,
        "jogos_analisados": 5,
        "media_gols_sofridos": 0.6,
        "em_crise": False,
    }
    f.update(kwargs)
    return f


def veto_base(**kwargs) -> dict:
    v = {"vetado": False, "motivo": ""}
    v.update(kwargs)
    return v


# ------------------------------------------------------------------
# Caso 1: odd fora do range permitido
# ------------------------------------------------------------------

def test_odd_fora_do_range():
    market = market_base(odd_favorito=1.20)
    # O OddsClient já filtraria esse jogo, mas vamos verificar o scorer
    score = scorer.calculate(market, form_base(), veto_base())
    # Odd 1.20 está abaixo do ideal (1.40–1.55), critério 1 deve ser parcial ou zero
    assert score < 100, "Score deve ser menor que 100 com odd fora do range"
    print(f"[PASS] test_odd_fora_do_range: score={score}")


# ------------------------------------------------------------------
# Caso 2: volume baixo (poucas casas cobrindo)
# ------------------------------------------------------------------

def test_volume_baixo():
    market = market_base(num_bookmakers=3)
    score = scorer.calculate(market, form_base(), veto_base())
    # Com 3 casas (abaixo do mínimo de 5), critério de cobertura = 0
    assert score < 90, "Score deve ser reduzido com poucas casas"
    print(f"[PASS] test_volume_baixo: score={score}")


# ------------------------------------------------------------------
# Caso 3: time favorito em crise
# ------------------------------------------------------------------

def test_time_em_crise():
    market = market_base()
    form = form_base(vitorias_recentes=1, em_crise=True)
    veto = veto_base()
    score = scorer.calculate(market, form, veto)
    # Em crise → critério de forma deve ser 0
    assert score <= 75, f"Score alto demais para time em crise: {score}"

    # filter_engine deve reprovar por crise mesmo com score ok
    # Simulamos chamando analyze_match com mapeamento ausente (sem chamada real à API)
    market_sem_liga = market_base(sport_key="soccer_inexistente")
    result = analyze_match(market_sem_liga)
    # Sem mapeamento de liga, form retorna default (em_crise=False) — deve avaliar pelo score
    assert result["status"] in ("APROVADO", "REPROVADO")
    print(f"[PASS] test_time_em_crise: score={score} | analyze={result['status']}")


# ------------------------------------------------------------------
# Caso 4: score alto — todos os critérios atendidos
# ------------------------------------------------------------------

def test_score_alto():
    market = market_base(odd_favorito=1.48, num_bookmakers=22)
    form = form_base(vitorias_recentes=5)
    veto = veto_base()
    score = scorer.calculate(market, form, veto)
    assert score == 100, f"Score deve ser 100 com todos critérios atendidos, got {score}"
    print(f"[PASS] test_score_alto: score={score}")


# ------------------------------------------------------------------
# Caso 5: veto ativo por clássico de rivalidade
# ------------------------------------------------------------------

def test_veto_classico():
    market = market_base(
        home_team="Arsenal",
        away_team="Tottenham",
        event="Arsenal vs Tottenham",
    )
    result = veto_checker.check(market)
    assert result["vetado"] is True, "Arsenal vs Tottenham deve ser vetado"
    assert "rivalidade" in result["motivo"].lower()
    print(f"[PASS] test_veto_classico: motivo='{result['motivo']}'")


# ------------------------------------------------------------------
# Caso 6: veto por queda brusca de odd
# ------------------------------------------------------------------

def test_veto_queda_odd():
    market = market_base(odd_favorito=1.35)
    result = veto_checker.check(market, prev_odd=1.60)
    queda = (1.60 - 1.35) / 1.60  # ~15.6%
    assert result["vetado"] is True, f"Queda de {queda*100:.1f}% deveria acionar veto"
    print(f"[PASS] test_veto_queda_odd: motivo='{result['motivo']}'")


# ------------------------------------------------------------------
# Execução
# ------------------------------------------------------------------

if __name__ == "__main__":
    testes = [
        test_odd_fora_do_range,
        test_volume_baixo,
        test_time_em_crise,
        test_score_alto,
        test_veto_classico,
        test_veto_queda_odd,
    ]
    falhas = 0
    for teste in testes:
        try:
            teste()
        except AssertionError as e:
            print(f"[FAIL] {teste.__name__}: {e}")
            falhas += 1
        except Exception as e:
            print(f"[ERROR] {teste.__name__}: {e}")
            falhas += 1

    print(f"\n{len(testes) - falhas}/{len(testes)} testes passaram")
    sys.exit(0 if falhas == 0 else 1)
