import os, time, logging, requests
from dotenv import load_dotenv
load_dotenv()
logger = logging.getLogger(__name__)
BASE_URL = "https://api.telegram.org/bot{token}/{method}"

class TelegramBot:
    def __init__(self):
        self._token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    def configurado(self): return bool(self._token and self._chat_id)
    def _url(self, m): return BASE_URL.format(token=self._token, method=m)
    def send(self, texto, chat_id=None, markup=None):
        payload = {"chat_id": chat_id or self._chat_id, "text": texto, "parse_mode": "HTML"}
        if markup: payload["reply_markup"] = markup
        for i in range(1, 4):
            try:
                r = requests.post(self._url("sendMessage"), json=payload, timeout=10)
                r.raise_for_status(); return True
            except Exception as e:
                logger.warning(f"Telegram falhou ({i}/3): {e}")
                if i < 3: time.sleep(2)
        return False
    def answer_callback(self, cb_id, texto=""):
        try: requests.post(self._url("answerCallbackQuery"), json={"callback_query_id": cb_id, "text": texto}, timeout=5)
        except Exception: pass
    def get_updates(self, offset=None):
        try:
            r = requests.get(self._url("getUpdates"), params={"timeout": 25, "offset": offset, "allowed_updates": ["message","callback_query"]}, timeout=30)
            return r.json().get("result", [])
        except Exception: return []
