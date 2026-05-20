import csv
import json
import os
import subprocess
import sys
import threading
from flask import Flask, jsonify, render_template, request

from core.database import Database
from core.bankroll import Bankroll

app = Flask(__name__)

_db: Database = None
_bankroll: Bankroll = None
_scan_running = False
_scan_lock = threading.Lock()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
SELECOES_PATH = os.path.join(DATA_DIR, "selecoes_hoje.json")
HISTORICO_PATH = os.path.join(DATA_DIR, "historico.csv")
GRADE_PATH     = os.path.join(DATA_DIR, "grade_completa.json")
QUOTA_PATH     = os.path.join(DATA_DIR, "api_quota.json")


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
# Pagina principal
# ------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ------------------------------------------------------------------
# Dados do dia
# ------------------------------------------------------------------

@app.route("/api/selecoes_hoje")
def api_selecoes_hoje():
    if not os.path.exists(SELECOES_PATH):
        return jsonify({"data": "", "total_jogos": 0, "selecoes": []})
    try:
        with open(SELECOES_PATH, encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({"data": "", "total_jogos": 0, "selecoes": []})


@app.route("/api/grade_completa")
def api_grade_completa():
    if not os.path.exists(GRADE_PATH):
        return jsonify({"total_jogos": 0, "total_selecionados": 0, "jogos": []})
    try:
        with open(GRADE_PATH, encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({"total_jogos": 0, "total_selecionados": 0, "jogos": []})


@app.route("/api/historico")
def api_historico():
    if not os.path.exists(HISTORICO_PATH):
        return jsonify([])
    try:
        with open(HISTORICO_PATH, encoding="utf-8", newline="") as f:
            return jsonify(list(csv.DictReader(f)))
    except Exception:
        return jsonify([])


# ------------------------------------------------------------------
# Quota da API
# ------------------------------------------------------------------

@app.route("/api/quota")
def api_quota():
    if not os.path.exists(QUOTA_PATH):
        return jsonify({"status": "UNKNOWN", "requests_remaining": None})
    try:
        with open(QUOTA_PATH, encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception:
        return jsonify({"status": "UNKNOWN"})


# ------------------------------------------------------------------
# Scan manual
# ------------------------------------------------------------------

@app.route("/api/scan", methods=["POST"])
def api_scan():
    global _scan_running
    with _scan_lock:
        if _scan_running:
            return jsonify({"status": "already_running", "message": "Scan ja em execucao. Aguarde."})
        _scan_running = True

    def _run():
        global _scan_running
        try:
            script = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "scripts", "morning_scan.py")
            )
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            subprocess.run([sys.executable, script], cwd=root)
        finally:
            _scan_running = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "message": "Scan iniciado. Aguarde ~60 segundos e atualize a pagina."})


@app.route("/api/scan_status")
def api_scan_status():
    return jsonify({"running": _scan_running})


# ------------------------------------------------------------------
# Trades (banco de dados local)
# ------------------------------------------------------------------

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
