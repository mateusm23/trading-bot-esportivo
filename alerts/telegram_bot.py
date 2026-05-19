import requests
import logging
import os
import time
from dotenv import load_dotenv

from core.database import Database

load_dotenv()

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_RETRIES = 3
RETRY_DELAY = 2  # segundos entre tentativas


class TelegramBot:
    def __init__(self, db: Database) -> None:
        self._token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
        self._db = db

    def send(self, mensagem: str, tipo_alerta: str = "INFO", market_id: str = "") -> bool:
        """Envia mensagem ao Telegram com retry automático (3x)."""
        if not self._token or not self._chat_id:
            logger.warning("Telegram não configurado — mensagem ignorada")
            return False

        url = TELEGRAM_API.format(token=self._token)
        payload = {
            "chat_id": self._chat_id,
            "text": mensagem,
            "parse_mode": "HTML",
        }

        for tentativa in range(1, MAX_RETRIES + 1):
            try:
                r = requests.post(url, json=payload, timeout=10)
                r.raise_for_status()
                logger.info(f"Telegram OK [{tipo_alerta}]: {mensagem[:60]}...")
                if market_id:
                    self._db.insert_alert(market_id, tipo_alerta, mensagem)
                return True
            except requests.RequestException as e:
                logger.warning(f"Telegram falhou (tentativa {tentativa}/{MAX_RETRIES}): {e}")
                if tentativa < MAX_RETRIES:
                    time.sleep(RETRY_DELAY)

        logger.error(f"Telegram: falha após {MAX_RETRIES} tentativas — [{tipo_alerta}]")
        return False

    def send_entrada(self, mensagem: str, market_id: str = "") -> bool:
        return self.send(mensagem, tipo_alerta="ENTRADA", market_id=market_id)

    def send_saida_lucro(self, mensagem: str, market_id: str = "") -> bool:
        return self.send(mensagem, tipo_alerta="SAIDA_LUCRO", market_id=market_id)

    def send_stop(self, mensagem: str, market_id: str = "") -> bool:
        return self.send(mensagem, tipo_alerta="STOP_LOSS", market_id=market_id)

    def send_revisao(self, mensagem: str, market_id: str = "") -> bool:
        return self.send(mensagem, tipo_alerta="REVISAO", market_id=market_id)

    def test_connection(self) -> bool:
        """Envia mensagem de teste para confirmar que o bot está funcionando."""
        return self.send("Bot de trading iniciado.", tipo_alerta="SISTEMA")
