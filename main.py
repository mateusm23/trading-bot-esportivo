import logging
import os
import sys
import time
import json
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("logs/trading_bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

from core.database import Database
from core.bankroll import Bankroll
from core.odds_client import OddsClient
from core.filter_engine import analyze_match
from core.live_monitor import LiveMonitor
from alerts.telegram_bot import TelegramBot
from dashboard.app import start_in_thread

SCAN_INTERVAL = 300       # segundos entre varreduras (5 min)
KICKOFF_WINDOW = 90       # minutos antes do jogo para enviar alerta
LEAGUES_PATH = os.path.join(os.path.dirname(__file__), "data", "leagues.json")


def load_sport_keys() -> list[str]:
    with open(LEAGUES_PATH, encoding="utf-8") as f:
        leagues = json.load(f)["leagues"]
    return [lg["odds_api_key"] for lg in leagues if lg.get("active")]


def minutos_ate_kickoff(start_time: str) -> float:
    from datetime import datetime, timezone
    try:
        dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        delta = dt - datetime.now(timezone.utc)
        return delta.total_seconds() / 60
    except Exception:
        return 9999


def main() -> None:
    logger.info("=== Trading Bot iniciando ===")

    banca_inicial = float(os.getenv("BANCA_INICIAL", "1000"))

    db = Database()
    db.initialize()

    bankroll = Bankroll(db=db, banca_inicial=banca_inicial)
    odds_client = OddsClient()
    telegram = TelegramBot(db=db)
    monitor = LiveMonitor(db=db, bankroll=bankroll, telegram=telegram)

    # Dashboard em thread separada na porta 5000
    start_in_thread(db=db, bankroll=bankroll, port=5000)
    logger.info("Dashboard disponivel em http://localhost:5000")

    telegram.test_connection()
    monitor.start()

    sport_keys = load_sport_keys()
    logger.info(f"Monitorando {len(sport_keys)} ligas")

    alertas_enviados: set[str] = set()

    while True:
        try:
            if bankroll.check_stop_diario():
                logger.warning("Stop diario ativo — aguardando proximo ciclo sem buscar mercados")
                time.sleep(SCAN_INTERVAL)
                continue

            logger.info("--- Nova varredura ---")
            markets = odds_client.get_active_markets(sport_keys)

            for market in markets:
                market_id = market.get("market_id", "")
                if market_id in alertas_enviados:
                    continue

                minutos = minutos_ate_kickoff(market.get("start_time", ""))
                if not (0 < minutos <= KICKOFF_WINDOW):
                    continue

                if bankroll.check_max_trades_simultaneos():
                    logger.info("Limite de trades simultaneos atingido — pulando novos alertas")
                    break

                # Busca odd anterior para checar veto de queda brusca
                ultimo = db.get_last_odds(market_id)
                prev_odd = ultimo.get("odd_favorito") if ultimo else None

                # Salva snapshot de odds antes da analise
                db.insert_odds_snapshot(market)

                result = analyze_match(market, prev_odd=prev_odd)

                if result["status"] != "APROVADO":
                    logger.info(f"Reprovado: {market.get('event')} — {result['motivo']}")
                    continue

                stake = bankroll.calcular_stake()

                from alerts import alert_formatter
                msg = alert_formatter.entrada(market, result, stake)
                telegram.send_entrada(msg, market_id=market_id)

                db.insert_trade(
                    liga=market.get("competition", ""),
                    jogo=market.get("event", ""),
                    favorito=market.get("favorito", ""),
                    odd_entrada=market.get("odd_favorito", 0.0),
                    stake_reais=stake,
                    motivo_entrada=result["motivo"],
                    score_entrada=result["score"],
                    market_id=market_id,
                )
                alertas_enviados.add(market_id)
                logger.info(f"Alerta enviado: {market.get('event')} | Score {result['score']}")

        except KeyboardInterrupt:
            logger.info("Interrompido pelo usuario")
            monitor.stop()
            break
        except Exception as e:
            logger.error(f"Erro no loop principal: {e}", exc_info=True)

        time.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    main()
