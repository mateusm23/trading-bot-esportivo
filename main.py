"""
Momentum Bet Bot V2 — Bot Telegram para monitoramento de pressão em jogos ao vivo.
Selecione jogos diretamente pelo Telegram; dados coletados pela API do Momentum Bet local.
"""

import logging
import os
import sys
import threading
import time

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

from alerts.telegram_bot import TelegramBot
from core import momentum_client as api
from core import sinais

bot = TelegramBot()

# ── Handlers de comando ───────────────────────────────────────────────────────

def cmd_start(chat_id):
    bot.send(
        "<b>Momentum Bet Bot V2</b>\n\n"
        "/jogos — Grade do dia com botões para monitorar\n"
        "/status — Jogos sendo monitorados agora\n"
        "/parar — Para o monitoramento de um jogo\n\n"
        "O Momentum Bet deve estar rodando localmente (INICIAR.bat).",
        chat_id=chat_id,
    )


def cmd_jogos(chat_id):
    if not api.api_online():
        bot.send(
            "Momentum Bet offline.\nAbra <b>INICIAR.bat</b> no computador e tente novamente.",
            chat_id=chat_id,
        )
        return

    try:
        data = api.buscar_grade()
    except Exception as e:
        bot.send(f"Erro ao buscar grade: {e}", chat_id=chat_id)
        return

    jogos = data.get("jogos", [])
    if not jogos:
        bot.send("Nenhum jogo disponível na grade.", chat_id=chat_id)
        return

    texto = "<b>Grade do dia</b>\n\n"
    botoes = []

    for j in jogos:
        status_icon = "🔴" if j.get("ao_vivo") else "🕐"
        mon_icon = "📡" if j.get("monitorando") else ""
        pressao = j.get("pressao_atual")
        p_str = f"  <b>{pressao:+}</b>" if pressao is not None else ""
        texto += f"{status_icon}{mon_icon} {j['nome']}  {j['inicio']}{p_str}\n"

        label = "■ Parar" if j.get("monitorando") else "▶ Monitorar"
        cb_data = f"par:{j['event_id']}" if j.get("monitorando") else f"mon:{j['event_id']}:{j['market_id']}"
        botoes.append([{"text": f"{label} — {j['nome']}", "callback_data": cb_data}])

    markup = {"inline_keyboard": botoes}
    bot.send(texto, chat_id=chat_id, markup=markup)


def cmd_status(chat_id):
    if not api.api_online():
        bot.send("Momentum Bet offline.", chat_id=chat_id)
        return

    try:
        statuses = api.buscar_status()
    except Exception as e:
        bot.send(f"Erro ao buscar status: {e}", chat_id=chat_id)
        return

    if not statuses:
        bot.send("Nenhum jogo sendo monitorado no momento.", chat_id=chat_id)
        return

    linhas = ["<b>Jogos monitorados</b>\n"]
    for eid, st in statuses.items():
        casa = st.get("time_casa", "?")
        fora = st.get("time_fora", "?")
        placar = st.get("placar", "?-?")
        pressao = st.get("pressao_atual")
        p_str = f"  pressão {pressao:+}" if pressao is not None else ""
        ciclos = st.get("ciclos", 0)
        erro = st.get("erro")
        erro_str = f"\n  ⚠️ {erro}" if erro else ""
        linhas.append(f"<b>{casa} vs {fora}</b>  {placar}{p_str}\n  {ciclos} ciclos{erro_str}")

    bot.send("\n\n".join(linhas), chat_id=chat_id)


def cmd_parar(chat_id):
    if not api.api_online():
        bot.send("Momentum Bet offline.", chat_id=chat_id)
        return

    try:
        statuses = api.buscar_status()
    except Exception as e:
        bot.send(f"Erro: {e}", chat_id=chat_id)
        return

    if not statuses:
        bot.send("Nenhum jogo monitorado para parar.", chat_id=chat_id)
        return

    botoes = []
    for eid, st in statuses.items():
        casa = st.get("time_casa", "?")
        fora = st.get("time_fora", "?")
        botoes.append([{"text": f"■ Parar — {casa} vs {fora}", "callback_data": f"par:{eid}"}])

    markup = {"inline_keyboard": botoes}
    bot.send("Qual jogo parar?", chat_id=chat_id, markup=markup)


# ── Callback handler ──────────────────────────────────────────────────────────

def handle_callback(cb):
    cb_id = cb["id"]
    chat_id = cb["message"]["chat"]["id"]
    data = cb.get("data", "")

    if data.startswith("mon:"):
        partes = data.split(":")
        event_id, market_id = partes[1], partes[2]
        try:
            api.iniciar_jogo(event_id, market_id)
            bot.answer_callback(cb_id, "Monitoramento iniciado!")
            bot.send(f"📡 Monitoramento iniciado.\nUse /status para acompanhar.", chat_id=chat_id)
        except Exception as e:
            bot.answer_callback(cb_id, "Erro ao iniciar")
            bot.send(f"Erro: {e}", chat_id=chat_id)

    elif data.startswith("par:"):
        event_id = data.split(":")[1]
        try:
            api.parar_jogo(event_id)
            bot.answer_callback(cb_id, "Monitoramento parado.")
            bot.send("■ Monitoramento parado.", chat_id=chat_id)
        except Exception as e:
            bot.answer_callback(cb_id, "Erro ao parar")
            bot.send(f"Erro: {e}", chat_id=chat_id)

    else:
        bot.answer_callback(cb_id)


# ── Loop de alertas ───────────────────────────────────────────────────────────

def _loop_alertas():
    while True:
        time.sleep(60)
        if not bot.configurado() or not api.api_online():
            continue
        try:
            statuses = api.buscar_status()
            for eid, st in statuses.items():
                try:
                    dados = api.buscar_dados(eid)
                    msgs = sinais.verificar(eid, st, dados)
                    for m in msgs:
                        bot.send(m)
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Loop alertas erro: {e}")


# ── Loop principal (long polling) ─────────────────────────────────────────────

def _processar(update):
    if "message" in update:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        texto = msg.get("text", "").strip()
        if texto.startswith("/start"):
            cmd_start(chat_id)
        elif texto.startswith("/jogos"):
            cmd_jogos(chat_id)
        elif texto.startswith("/status"):
            cmd_status(chat_id)
        elif texto.startswith("/parar"):
            cmd_parar(chat_id)

    elif "callback_query" in update:
        handle_callback(update["callback_query"])


def main():
    if not bot.configurado():
        logger.error("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID não configurado no .env")
        sys.exit(1)

    logger.info("=== Momentum Bet Bot V2 iniciando ===")
    bot.send("<b>Bot online!</b>\nUse /jogos para ver a grade do dia.")

    threading.Thread(target=_loop_alertas, daemon=True).start()

    offset = None
    while True:
        updates = bot.get_updates(offset=offset)
        for u in updates:
            offset = u["update_id"] + 1
            try:
                _processar(u)
            except Exception as e:
                logger.error(f"Erro ao processar update {u.get('update_id')}: {e}", exc_info=True)


if __name__ == "__main__":
    main()
