import csv
import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, render_template, request

from core.database import Database
from core.bankroll import Bankroll
from core.result_fetcher import buscar_resultado, favorito_ganhou, calcular_clv, get_league_codes

app = Flask(__name__)

_db: Database = None
_bankroll: Bankroll = None
_banca_inicial: float = 1000.0
_scan_running = False
_scan_lock = threading.Lock()

DATA_DIR      = os.path.join(os.path.dirname(__file__), "..", "data")
SELECOES_PATH = os.path.join(DATA_DIR, "selecoes_hoje.json")
HISTORICO_PATH = os.path.join(DATA_DIR, "historico.csv")
GRADE_PATH    = os.path.join(DATA_DIR, "grade_completa.json")
QUOTA_PATH    = os.path.join(DATA_DIR, "api_quota.json")


def init_dashboard(db: Database, bankroll: Bankroll, banca_inicial: float = 1000.0) -> None:
    global _db, _bankroll, _banca_inicial
    _db = db
    _bankroll = bankroll
    _banca_inicial = banca_inicial


def start_in_thread(db: Database, bankroll: Bankroll, port: int = 5000,
                    banca_inicial: float = 1000.0) -> threading.Thread:
    init_dashboard(db, bankroll, banca_inicial)
    t = threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False),
        daemon=True,
    )
    t.start()
    return t


def _hoje_brt() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")


# ------------------------------------------------------------------
# Pagina principal
# ------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ------------------------------------------------------------------
# Dados do dia / grade
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
        return jsonify({"status": "UNKNOWN"})
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
            return jsonify({"status": "already_running", "message": "Scan ja em execucao."})
        _scan_running = True

    def _run():
        global _scan_running
        try:
            script = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts", "morning_scan.py"))
            root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
            subprocess.run([sys.executable, script], cwd=root)
        finally:
            _scan_running = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started", "message": "Scan iniciado. Aguarde ~60 segundos."})


@app.route("/api/scan_status")
def api_scan_status():
    return jsonify({"running": _scan_running})


# ------------------------------------------------------------------
# Gestão de Banca — Apostas
# ------------------------------------------------------------------

@app.route("/api/bets", methods=["GET"])
def api_get_bets():
    if not _db:
        return jsonify([])
    data = request.args.get("data")
    bets = _db.get_bets(data_jogo=data)
    return jsonify(bets)


@app.route("/api/bets", methods=["POST"])
def api_post_bet():
    if not _db:
        return jsonify({"erro": "DB nao inicializado"}), 500
    body = request.json or {}
    try:
        trade_id = _db.registrar_aposta(body)
        return jsonify({"id": trade_id, "status": "ok"})
    except Exception as e:
        return jsonify({"erro": str(e)}), 400


@app.route("/api/bets/<int:bet_id>", methods=["PUT"])
def api_put_bet(bet_id: int):
    if not _db:
        return jsonify({"erro": "DB nao inicializado"}), 500
    body = request.json or {}
    resultado = body.get("resultado")
    if resultado not in ("WIN", "LOSS", "VOID"):
        return jsonify({"erro": "resultado invalido"}), 400
    result = _db.atualizar_resultado_aposta(bet_id, resultado)
    return jsonify(result)


@app.route("/api/bets/<int:bet_id>", methods=["DELETE"])
def api_delete_bet(bet_id: int):
    if not _db:
        return jsonify({"erro": "DB nao inicializado"}), 500
    _db.deletar_aposta(bet_id)
    return jsonify({"status": "ok"})


@app.route("/api/bets/registrados_hoje")
def api_registrados_hoje():
    if not _db:
        return jsonify([])
    hoje = _hoje_brt()
    market_ids = list(_db.get_market_ids_registrados(hoje))
    return jsonify(market_ids)


@app.route("/api/bets/auto_results", methods=["POST"])
def api_auto_results():
    """Busca resultados automaticamente para apostas PENDENTES cujo horario ja passou."""
    if not _db:
        return jsonify({"erro": "DB nao inicializado"}), 500

    now_utc = datetime.now(timezone.utc)
    # Pega apostas pendentes de qualquer data
    todas = _db.get_bets()
    pendentes = [
        b for b in todas
        if b.get("resultado") == "PENDENTE" and b.get("start_time") and b.get("home_team")
    ]

    # Filtra as que ja deveriam ter terminado (start_time + 2h no passado)
    para_verificar = []
    for bet in pendentes:
        try:
            st = datetime.fromisoformat(bet["start_time"].replace("Z", "+00:00"))
            if st + timedelta(hours=2) < now_utc:
                para_verificar.append(bet)
        except Exception:
            pass

    if not para_verificar:
        return jsonify({"updated": 0, "message": "Nenhuma aposta pendente para verificar"})

    updated = 0
    skipped = 0
    for bet in para_verificar:
        liga = bet.get("liga", "")
        home = bet.get("home_team", "")
        away = bet.get("away_team", "")
        data_jogo = bet.get("data_jogo", "")
        favorito = bet.get("favorito", "")
        market_id = bet.get("market_id", "")

        fd_code, af_id = get_league_codes(liga)
        resultado = buscar_resultado(home, away, fd_code, data_jogo, af_id)

        ganhou = favorito_ganhou(favorito, home, resultado)
        if ganhou is True:
            db_resultado = "WIN"
        elif ganhou is False:
            db_resultado = "LOSS"
        else:
            skipped += 1
            continue

        clv = calcular_clv(market_id, bet.get("odd_entrada", 0))
        _db.atualizar_resultado_aposta(bet["id"], db_resultado, clv_percent=clv)
        updated += 1

    return jsonify({
        "updated": updated,
        "skipped": skipped,
        "message": f"{updated} apostas atualizadas automaticamente",
    })


# ------------------------------------------------------------------
# Gestão de Banca — Estatísticas
# ------------------------------------------------------------------

@app.route("/api/bankroll")
def api_bankroll():
    if not _db:
        return jsonify({})
    return jsonify(_db.get_bankroll_stats(_banca_inicial))


@app.route("/api/bankroll/curve")
def api_bankroll_curve():
    if not _db:
        return jsonify([])
    return jsonify(_db.get_banca_curve(_banca_inicial))


@app.route("/api/bankroll/por_liga")
def api_bankroll_por_liga():
    if not _db:
        return jsonify([])
    return jsonify(_db.get_stats_por_liga())


# ------------------------------------------------------------------
# Legado
# ------------------------------------------------------------------

@app.route("/api/resumo")
def api_resumo():
    if not _bankroll:
        return jsonify({})
    return jsonify(_bankroll.exportar_resumo_dia())
