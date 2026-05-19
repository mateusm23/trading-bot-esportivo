import requests
import logging
import os
import time
import threading
from typing import Optional
from dotenv import load_dotenv

from core.database import Database
from core.bankroll import Bankroll
from alerts.telegram_bot import TelegramBot
from alerts import alert_formatter

load_dotenv()

logger = logging.getLogger(__name__)

API_FOOTBALL_URL = "https://v3.football.api-sports.io"
POLL_INTERVAL = 180        # segundos entre verificações (3 min → ~30 req/jogo de 90min)
SEGUNDO_TEMPO_MIN = 50     # minutos a partir dos quais alerta revisão manual
ODD_SAIDA_QUEDA = 0.15    # queda de 15% na odd → sair com lucro
ODD_STOP_ABSOLUTA = 1.80  # odd acima disso → stop loss imediato
ODD_STOP_SUBIDA = 0.40    # subida de 40% → gol da zebra
ODD_GOL_FAVORITO = 0.50   # queda de 50% → gol do favorito


class LiveMonitor:
    def __init__(self, db: Database, bankroll: Bankroll, telegram: TelegramBot) -> None:
        self._db = db
        self._bankroll = bankroll
        self._telegram = telegram
        self._headers = {"x-apisports-key": os.getenv("API_FOOTBALL_KEY", "")}
        self._running = False
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # Controle do loop
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("LiveMonitor iniciado")

    def stop(self) -> None:
        self._running = False
        logger.info("LiveMonitor parado")

    # ------------------------------------------------------------------
    # Loop principal
    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while self._running:
            trades_ativos = self._db.get_active_trades()
            if trades_ativos:
                logger.info(f"LiveMonitor: verificando {len(trades_ativos)} trade(s) ativo(s)")
                for trade in trades_ativos:
                    try:
                        self._verificar_trade(trade)
                    except Exception as e:
                        logger.error(f"Erro ao verificar trade #{trade['id']}: {e}")
            time.sleep(POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Verificação por trade
    # ------------------------------------------------------------------

    def _verificar_trade(self, trade: dict) -> None:
        market_id = trade.get("market_id", "")
        odd_entrada = trade.get("odd_entrada", 0.0)
        trade_id = trade["id"]

        # Busca fixture ao vivo via API-Football
        fixture = self._get_fixture(trade.get("jogo", ""))
        if not fixture:
            logger.debug(f"Trade #{trade_id}: jogo ainda não ao vivo ou não encontrado")
            return

        minuto = fixture.get("minuto", 0)
        eventos = fixture.get("eventos", [])
        placar = fixture.get("placar", {})

        # Alerta de revisão manual no 2º tempo sem saída
        if minuto >= SEGUNDO_TEMPO_MIN:
            msg = alert_formatter.revisao_manual(trade)
            self._telegram.send_revisao(msg, market_id=market_id)
            logger.info(f"Trade #{trade_id}: alerta de revisão manual (min {minuto})")

        # Checar eventos relevantes
        for evento in eventos:
            tipo = evento.get("type", "").lower()
            time_marcou = evento.get("team", "")
            favorito = trade.get("favorito", "")

            if tipo == "goal":
                if _nome_corresponde(time_marcou, favorito):
                    # Gol do favorito → sair com lucro
                    mercado_resumo = {"event": trade.get("jogo", ""), "market_id": market_id}
                    msg = alert_formatter.saida_lucro(
                        mercado_resumo, odd_entrada,
                        motivo=f"GOL do favorito ({time_marcou}) no min {evento.get('minute', '?')}"
                    )
                    self._telegram.send_saida_lucro(msg, market_id=market_id)
                    self._encerrar_trade(trade_id, "WIN", odd_entrada * 0.75, msg)
                    return
                else:
                    # Gol da zebra → stop loss
                    mercado_resumo = {"event": trade.get("jogo", ""), "market_id": market_id}
                    msg = alert_formatter.stop_loss(
                        mercado_resumo, odd_entrada,
                        motivo=f"GOL da ZEBRA ({time_marcou}) no min {evento.get('minute', '?')}"
                    )
                    self._telegram.send_stop(msg, market_id=market_id)
                    self._encerrar_trade(trade_id, "LOSS", odd_entrada * 1.50, msg)
                    return

            if tipo in ("card", "red card") and _nome_corresponde(time_marcou, favorito):
                mercado_resumo = {"event": trade.get("jogo", ""), "market_id": market_id}
                msg = alert_formatter.stop_loss(
                    mercado_resumo, odd_entrada,
                    motivo=f"CARTAO VERMELHO do favorito ({time_marcou}) no min {evento.get('minute', '?')}"
                )
                self._telegram.send_stop(msg, market_id=market_id)
                self._encerrar_trade(trade_id, "LOSS", odd_entrada * 1.50, msg)
                return

    # ------------------------------------------------------------------
    # API-Football
    # ------------------------------------------------------------------

    def _get_fixture(self, jogo: str) -> Optional[dict]:
        """Busca fixture ao vivo. Retorna {minuto, eventos, placar} ou None."""
        try:
            r = requests.get(
                f"{API_FOOTBALL_URL}/fixtures",
                headers=self._headers,
                params={"live": "all"},
                timeout=10,
            )
            r.raise_for_status()
            fixtures = r.json().get("response", [])
        except requests.RequestException as e:
            logger.error(f"API-Football falhou: {e}")
            return None

        for f in fixtures:
            teams = f.get("teams", {})
            home = teams.get("home", {}).get("name", "")
            away = teams.get("away", {}).get("name", "")
            nome_fixture = f"{home} vs {away}"

            if not _jogo_corresponde(jogo, nome_fixture):
                continue

            elapsed = f.get("fixture", {}).get("status", {}).get("elapsed") or 0
            eventos_raw = f.get("events", [])

            eventos: list[dict] = []
            for ev in eventos_raw:
                eventos.append({
                    "type": ev.get("type", ""),
                    "detail": ev.get("detail", ""),
                    "team": ev.get("team", {}).get("name", ""),
                    "minute": ev.get("time", {}).get("elapsed"),
                })

            return {
                "minuto": elapsed,
                "eventos": eventos,
                "placar": {
                    "home": f.get("goals", {}).get("home"),
                    "away": f.get("goals", {}).get("away"),
                },
            }

        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _encerrar_trade(
        self, trade_id: int, resultado: str, odd_estimada: float, motivo: str
    ) -> None:
        trade = self._db.get_trade(trade_id)
        if not trade:
            return
        stake = trade.get("stake_reais", 0.0)
        odd_entrada = trade.get("odd_entrada", 1.0)

        if resultado == "WIN":
            pnl = round(stake * (odd_entrada - odd_estimada), 2)
        else:
            pnl = round(-stake, 2)

        self._bankroll.registrar_resultado(
            trade_id=trade_id,
            resultado=resultado,
            pnl=pnl,
            odd_saida=odd_estimada,
            motivo_saida=motivo,
        )


def _nome_corresponde(nome_api: str, nome_trade: str) -> bool:
    a = nome_api.lower().strip()
    b = nome_trade.lower().strip()
    return a in b or b in a or any(
        parte in b for parte in a.split() if len(parte) > 3
    )


def _jogo_corresponde(jogo_trade: str, jogo_fixture: str) -> bool:
    partes = [p.strip().lower() for p in jogo_trade.replace(" vs ", "|").split("|")]
    fixture_lower = jogo_fixture.lower()
    return all(any(p in nome for nome in fixture_lower.split(" vs ")) for p in partes)
