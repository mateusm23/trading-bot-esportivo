"""
Job Manha — roda uma vez por dia as 8h BRT.

1. Busca jogos das proximas 24h nas 10 ligas (10 req The Odds API)
2. Aplica filtros de forma, score e vetos
3. Envia grade + selecao via Telegram
4. Salva selecoes em data/selecoes_hoje.json para o job noturno
5. Salva grade_completa.json (todos os jogos com odds) para o portal
6. Salva api_quota.json com status de uso de requisicoes
"""

import json
import logging
import os
import sys
from calendar import monthrange
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
SELECOES_PATH      = os.path.join(DATA_DIR, "selecoes_hoje.json")
ODDS_SNAPSHOT_PATH = os.path.join(DATA_DIR, "odds_snapshot.json")
GRADE_PATH         = os.path.join(DATA_DIR, "grade_completa.json")
QUOTA_PATH         = os.path.join(DATA_DIR, "api_quota.json")

ODDS_API_TIER = 500  # requisicoes/mes no plano gratuito


def _eh_proximas_24h(start_time: str) -> bool:
    """Retorna True se o jogo comeca nas proximas 24 horas."""
    try:
        dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        agora = datetime.now(timezone.utc)
        return agora <= dt <= agora + timedelta(hours=24)
    except Exception:
        return False


def _calcular_odds_display(match: dict) -> dict:
    """Calcula odds medias home/draw/away para exibicao (sem filtro de range)."""
    pool: dict[str, list[float]] = {}
    for bm in match.get("bookmakers", []):
        for mkt in bm.get("markets", []):
            if mkt.get("key") != "h2h":
                continue
            for outcome in mkt.get("outcomes", []):
                pool.setdefault(outcome["name"], []).append(outcome["price"])
    if not pool:
        return {}
    avg = {name: round(sum(p) / len(p), 2) for name, p in pool.items()}
    home = match.get("home_team", "")
    away = match.get("away_team", "")
    return {
        "odd_home": avg.get(home, 0),
        "odd_draw": avg.get("Draw", 0),
        "odd_away": avg.get(away, 0),
    }


def _salvar_quota(odds_client: OddsClient) -> None:
    remaining = odds_client.requests_remaining
    if remaining is None or remaining < 0:
        return
    usado = ODDS_API_TIER - remaining
    agora_brt = datetime.now(timezone.utc) - timedelta(hours=3)
    dias_no_mes = monthrange(agora_brt.year, agora_brt.month)[1]
    dias_restantes = dias_no_mes - agora_brt.day + 1
    budget_dia = round(remaining / dias_restantes, 1) if dias_restantes > 0 else 0

    if budget_dia >= 10:
        status = "SAFE"
    elif budget_dia >= 5:
        status = "WARNING"
    else:
        status = "CRITICAL"

    payload = {
        "ultimo_scan": datetime.now(timezone.utc).isoformat(),
        "tier_total": ODDS_API_TIER,
        "requests_remaining": remaining,
        "requests_used": usado,
        "dias_restantes_mes": dias_restantes,
        "budget_por_dia": budget_dia,
        "status": status,
    }
    with open(QUOTA_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    logger.info(f"Quota: {remaining} restantes | {budget_dia} req/dia seguro | {status}")


def main() -> None:
    logger.info("=== Job Manha iniciado ===")
    os.makedirs(DATA_DIR, exist_ok=True)

    db = Database()
    db.initialize()
    telegram = TelegramBot(db=db)
    odds_client = OddsClient()

    leagues = _load_leagues()
    sport_keys = [lg["odds_api_key"] for lg in leagues if lg.get("active")]

    # Carrega snapshot anterior para o veto de queda de odd
    prev_odds: dict[str, float] = {}
    if os.path.exists(ODDS_SNAPSHOT_PATH):
        try:
            with open(ODDS_SNAPSHOT_PATH, encoding="utf-8") as f:
                prev_odds = json.load(f)
            logger.info(f"Snapshot anterior carregado: {len(prev_odds)} mercados")
        except Exception as e:
            logger.warning(f"Nao foi possivel carregar odds_snapshot.json: {e}")

    todos: list[dict] = []
    selecoes: list[dict] = []
    grade_jogos: list[dict] = []
    snapshot_atual: dict[str, float] = {}

    for sport_key in sport_keys:
        raw = odds_client._fetch_odds(sport_key)
        liga_nome = _sport_key_to_league(sport_key)
        for match in raw:
            start = match.get("commence_time", "")

            if not _eh_proximas_24h(start):
                continue

            match["competition"] = liga_nome
            match["start_time"] = start
            match["event"] = f"{match.get('home_team', '')} vs {match.get('away_team', '')}"
            todos.append(match)

            odds_display = _calcular_odds_display(match)
            num_bm = len(match.get("bookmakers", []))

            grade_entry: dict = {
                "liga": liga_nome,
                "sport_key": sport_key,
                "market_id": match.get("id", ""),
                "event": match["event"],
                "home_team": match.get("home_team", ""),
                "away_team": match.get("away_team", ""),
                "start_time": start,
                "num_bookmakers": num_bm,
                "selecionado": False,
                "score": 0,
                "motivo": "",
                "status_filtro": "SEM_ODDS",
                **odds_display,
            }

            parsed = odds_client._parse_match(match)
            if parsed:
                market_id = parsed.get("market_id", "")
                prev_odd = prev_odds.get(market_id)
                if market_id:
                    snapshot_atual[market_id] = parsed.get("odd_favorito", 0.0)

                result = analyze_match(parsed, prev_odd=prev_odd)
                grade_entry["status_filtro"] = result["status"]
                grade_entry["favorito"] = parsed.get("favorito", "")
                grade_entry["odd_favorito"] = parsed.get("odd_favorito", 0)

                if result["status"] == "APROVADO":
                    parsed["score"] = result["score"]
                    parsed["motivo"] = result["motivo"]
                    parsed["form"] = result["form"]
                    grade_entry["selecionado"] = True
                    grade_entry["score"] = result["score"]
                    grade_entry["motivo"] = result["motivo"]
                    grade_entry["form_vitorias"] = result["form"].get("vitorias_recentes", 0)
                    selecoes.append(parsed)
            else:
                grade_entry["status_filtro"] = "FORA_RANGE"

            grade_jogos.append(grade_entry)

    # Salva snapshot atual
    with open(ODDS_SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot_atual, f, indent=2)
    logger.info(f"Snapshot de odds salvo: {len(snapshot_atual)} mercados")

    # Salva grade completa para o portal
    agora_brt = datetime.now(timezone.utc) - timedelta(hours=3)
    grade_jogos_ord = sorted(grade_jogos, key=lambda j: j.get("start_time") or "")
    with open(GRADE_PATH, "w", encoding="utf-8") as f:
        json.dump({
            "gerado_em": datetime.now(timezone.utc).isoformat(),
            "janela": "proximas_24h",
            "total_jogos": len(grade_jogos),
            "total_selecionados": len(selecoes),
            "jogos": grade_jogos_ord,
        }, f, ensure_ascii=False, indent=2)

    # Salva info de quota
    _salvar_quota(odds_client)

    logger.info(f"Total: {len(todos)} jogos | {len(selecoes)} aprovados")

    # Envia grade completa pelo Telegram
    msg_grade = _montar_grade(todos)
    telegram.send(msg_grade, tipo_alerta="GRADE")

    # Envia selecoes filtradas
    if selecoes:
        msg_sel = _montar_grade_filtrados(selecoes)
        telegram.send(msg_sel, tipo_alerta="FILTRADOS")
    else:
        telegram.send("Nenhum jogo aprovado pelos criterios nas proximas 24h.", tipo_alerta="FILTRADOS")

    # Salva selecoes para o job noturno
    hoje = agora_brt.strftime("%Y-%m-%d")
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
                "form_vitorias": s.get("form", {}).get("vitorias_recentes", 0),
                "form_media_gols_sofridos": s.get("form", {}).get("media_gols_sofridos", 0.0),
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
