import json
import threading
from datetime import date
from flask import Flask, jsonify, render_template

from core.database import Database
from core.bankroll import Bankroll

app = Flask(__name__)

_db: Database = None
_bankroll: Bankroll = None


def init_dashboard(db: Database, bankroll: Bankroll) -> None:
    global _db, _bankroll
    _db = db
    _bankroll = bankroll


def start_in_thread(db: Database, bankroll: Bankroll, port: int = 5000) -> threading.Thread:
    init_dashboard(db, bankroll)
    t = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False),
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


@app.route("/api/grafico")
def api_grafico():
    if not _db:
        return jsonify([])
    historico = _db.get_daily_stats_history(days=30)
    return jsonify(list(reversed(historico)))
