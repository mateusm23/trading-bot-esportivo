import csv
import json
import os
import threading
from flask import Flask, jsonify, render_template

from core.database import Database
from core.bankroll import Bankroll

app = Flask(__name__)

_db: Database = None
_bankroll: Bankroll = None

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SELECOES_PATH = os.path.join(DATA_DIR, "selecoes_hoje.json")
HISTORICO_PATH = os.path.join(DATA_DIR, "historico.csv")


def init_dashboard(db: Database, bankroll: Bankroll) -> None:
    global _db, _bankroll
    _db = db
    _bankroll = bankroll


def start_in_thread(db: Database, bankroll: Bankroll, port: int = 5000) -> threading.Thread:
    init_dashboard(db, bankroll)
    t = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    t.start()
    return t


# ------------------------------------------------------------------
# Rotas
# ------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/selecoes_hoje")
def api_selecoes_hoje():
    if not os.path.exists(SELECOES_PATH):
        return jsonify({"data": "", "total_jogos": 0, "selecoes": []})
    try:
        with open(SELECOES_PATH, encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({"data": "", "total_jogos": 0, "selecoes": []})


@app.route("/api/historico")
def api_historico():
    if not os.path.exists(HISTORICO_PATH):
        return jsonify([])
    try:
        with open(HISTORICO_PATH, encoding="utf-8", newline="") as f:
            return jsonify(list(csv.DictReader(f)))
    except Exception:
        return jsonify([])


@app.route("/api/resumo")
def api_resumo():
    if not _bankroll:
        return jsonify({})
    return jsonify(_bankroll.exportar_resumo_dia())


@app.route("/api/trades/ativos")
def api_trades_ativos():
    if not _db:
        return jsonify([])
    return jsonify(_db.get_active_trades())


@app.route("/api/trades/recentes")
def api_trades_recentes():
    if not _db:
        return jsonify([])
    return jsonify(_db.get_recent_trades(limit=20))
