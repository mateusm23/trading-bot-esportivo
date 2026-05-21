"""
Backtest historico — valida se favoritismo 1.35-1.60 tem edge real.

Fonte: football-data.co.uk (gratuito, sem API key).
Cobre: Premier League, La Liga, Serie A, Bundesliga, Ligue 1.
Periodos: 3 temporadas (2022/23, 2023/24, 2024/25) — ~3.000 partidas.

O que testa:
  Estrategia BASE = apostar no favorito sempre que odd_favorito in [1.35, 1.60].
  (Sem filtro de forma — forma so sera validada quando tivermos dados proprios.)

Metricas calculadas:
  - Taxa de acerto, ROI, P&L, max drawdown por: geral / liga / faixa de odd
  - Comparativo: nossa faixa vs apostar tudo na Premier League vs faixa mais estreita

Saida:
  data/backtest_results.json   (lido pelo dashboard)
  Impressao no terminal
  Mensagem Telegram (opcional, roda com --telegram)
"""

import csv
import io
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:
    logger.error("requests nao instalado: pip install requests")
    sys.exit(1)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
BACKTEST_PATH = os.path.join(DATA_DIR, "backtest_results.json")

ODD_MIN = 1.35
ODD_MAX = 1.60
STAKE_UNIT = 1.0   # 1 unidade por aposta (ROI em %)

SUBRANGES = [
    (1.35, 1.40),
    (1.40, 1.45),
    (1.45, 1.50),
    (1.50, 1.55),
    (1.55, 1.61),   # inclui 1.60
]

# Ligas e temporadas
SEASONS = ["2223", "2324", "2425"]
LEAGUES = {
    "Premier League": "E0",
    "La Liga":        "SP1",
    "Serie A":        "I1",
    "Bundesliga":     "D1",
    "Ligue 1":        "F1",
}
BASE_URL = "https://www.football-data.co.uk/mmz4281"

# Colunas de odds preferidas (em ordem de prioridade)
ODD_COLS = [
    ("AvgH", "AvgD", "AvgA"),    # media de todas as casas
    ("B365H", "B365D", "B365A"), # Bet365
    ("PSH",  "PSD",  "PSA"),     # Pinnacle (mais eficiente)
    ("WHH",  "WHD",  "WHA"),     # William Hill
]


# ------------------------------------------------------------------
# Download + parse
# ------------------------------------------------------------------

def _download_csv(league_code: str, season: str) -> list[dict]:
    url = f"{BASE_URL}/{season}/{league_code}.csv"
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        # football-data.co.uk usa latin-1
        text = r.content.decode("latin-1")
        reader = csv.DictReader(io.StringIO(text))
        rows = [row for row in reader if row.get("FTR") in ("H", "D", "A")]
        logger.info(f"{league_code}/{season}: {len(rows)} partidas")
        return rows
    except Exception as e:
        logger.warning(f"{league_code}/{season}: falha ({e})")
        return []


def _get_odds(row: dict) -> tuple[float, float, float] | None:
    """Retorna (odd_home, odd_draw, odd_away) usando a melhor fonte disponivel."""
    for h_col, d_col, a_col in ODD_COLS:
        try:
            h = float(row.get(h_col, "") or 0)
            d = float(row.get(d_col, "") or 0)
            a = float(row.get(a_col, "") or 0)
            if h > 1.0 and d > 1.0 and a > 1.0:
                return h, d, a
        except ValueError:
            continue
    return None


def _parse_date(row: dict) -> str:
    raw = row.get("Date", "")
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return raw


# ------------------------------------------------------------------
# Motor do backtest
# ------------------------------------------------------------------

class Bet:
    __slots__ = ("date", "league", "home", "away", "fav", "odd_fav", "won")

    def __init__(self, date, league, home, away, fav, odd_fav, won):
        self.date = date
        self.league = league
        self.home = home
        self.away = away
        self.fav = fav
        self.odd_fav = odd_fav
        self.won = won


def _simulate(rows: list[dict], league: str) -> list[Bet]:
    bets = []
    for row in rows:
        odds = _get_odds(row)
        if not odds:
            continue
        oh, od, oa = odds
        ftr = row.get("FTR", "")

        # Favorito = menor odd entre casa e visitante (exclui empate)
        if oh <= oa:
            fav = "HOME"
            odd_fav = oh
            fav_won = (ftr == "H")
        else:
            fav = "AWAY"
            odd_fav = oa
            fav_won = (ftr == "A")

        if not (ODD_MIN <= odd_fav <= ODD_MAX):
            continue

        bets.append(Bet(
            date=_parse_date(row),
            league=league,
            home=row.get("HomeTeam", ""),
            away=row.get("AwayTeam", ""),
            fav=fav,
            odd_fav=round(odd_fav, 3),
            won=fav_won,
        ))
    return bets


# ------------------------------------------------------------------
# Estatísticas
# ------------------------------------------------------------------

def _stats(bets: list[Bet]) -> dict:
    if not bets:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "pnl_units": 0, "roi": 0, "max_drawdown": 0}
    wins = sum(1 for b in bets if b.won)
    losses = len(bets) - wins
    pnl_units = sum((b.odd_fav - 1) if b.won else -1 for b in bets)
    roi = round(pnl_units / len(bets) * 100, 2)

    # Max drawdown
    peak = 0.0
    equity = 0.0
    max_dd = 0.0
    for b in bets:
        equity += (b.odd_fav - 1) if b.won else -1
        if equity > peak:
            peak = equity
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd

    return {
        "total": len(bets),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(bets) * 100, 1),
        "pnl_units": round(pnl_units, 2),
        "roi": roi,
        "max_drawdown": round(max_dd, 2),
    }


def _stats_por_subrange(bets: list[Bet]) -> list[dict]:
    result = []
    for lo, hi in SUBRANGES:
        sub = [b for b in bets if lo <= b.odd_fav < hi]
        s = _stats(sub)
        label = f"{lo:.2f}–{hi-0.01:.2f}" if hi < 1.61 else f"{lo:.2f}–1.60"
        result.append({"faixa": label, **s})
    return result


def _equity_curve(bets: list[Bet]) -> list[dict]:
    """Curva de P&L cumulativa por data (para o grafico)."""
    por_data: dict[str, float] = {}
    for b in sorted(bets, key=lambda x: x.date):
        pnl = (b.odd_fav - 1) if b.won else -1.0
        por_data[b.date] = por_data.get(b.date, 0) + pnl

    curve = []
    cumul = 0.0
    for data in sorted(por_data):
        cumul = round(cumul + por_data[data], 3)
        curve.append({"data": data, "pnl_cumul": cumul})
    return curve


def _stats_por_liga(bets: list[Bet]) -> list[dict]:
    por_liga: dict[str, list[Bet]] = defaultdict(list)
    for b in bets:
        por_liga[b.league].append(b)
    result = []
    for liga, lb in sorted(por_liga.items()):
        s = _stats(lb)
        result.append({"liga": liga, **s})
    return sorted(result, key=lambda x: x["total"], reverse=True)


def _melhor_faixa(por_subrange: list[dict]) -> dict | None:
    candidatos = [s for s in por_subrange if s["total"] >= 30]
    if not candidatos:
        return None
    return max(candidatos, key=lambda s: s["roi"])


def _formatar_terminal(resultado: dict) -> str:
    g = resultado["geral"]
    linhas = [
        "=" * 60,
        f"BACKTEST HISTORICO — {resultado['num_temporadas']} temporadas",
        f"Ligas: {', '.join(resultado['ligas'])}",
        f"Periodo: {resultado.get('data_inicio','')} ate {resultado.get('data_fim','')}",
        "=" * 60,
        "",
        "RESULTADO GERAL (odd favorito 1.35-1.60):",
        f"  Apostas: {g['total']}  |  Wins: {g['wins']}  |  Losses: {g['losses']}",
        f"  Taxa de acerto: {g['win_rate']}%",
        f"  P&L: {g['pnl_units']:+.2f} u  |  ROI: {g['roi']:+.2f}%",
        f"  Max drawdown: {g['max_drawdown']:.1f} u",
        "",
        "POR FAIXA DE ODD:",
    ]
    for s in resultado["por_faixa"]:
        roi_str = f"{s['roi']:+.2f}%"
        mark = " <<< MELHOR" if s.get("melhor") else ""
        linhas.append(f"  {s['faixa']}  {s['total']:>4} apostas  {s['win_rate']:>5}%  ROI {roi_str:>8}{mark}")

    linhas += ["", "POR LIGA:"]
    for s in resultado["por_liga"]:
        linhas.append(
            f"  {s['liga']:<20} {s['total']:>4} apostas  {s['win_rate']:>5}%  ROI {s['roi']:>+8.2f}%"
        )

    m = resultado.get("melhor_faixa")
    if m:
        linhas += [
            "",
            f"INSIGHT: melhor faixa = {m['faixa']} | ROI {m['roi']:+.2f}% | {m['total']} apostas",
        ]

    if g["roi"] >= 0:
        linhas.append("\nESTRATEGIA BASE: LUCRATIVA historicamente.")
        linhas.append("Adicionar filtros de forma tende a melhorar o ROI.")
    else:
        linhas.append(f"\nESTRATEGIA BASE: NEGATIVA ({g['roi']:+.2f}% ROI).")
        linhas.append("Os filtros de forma precisam recuperar essa margem.")
        if m and m["roi"] >= 0:
            linhas.append(f"  Mas a faixa {m['faixa']} e lucrativa — considere estreitar o range.")

    linhas.append("=" * 60)
    return "\n".join(linhas)


# ------------------------------------------------------------------
# Main
# ------------------------------------------------------------------

def main(enviar_telegram: bool = False) -> None:
    logger.info("=== Backtest iniciado ===")
    os.makedirs(DATA_DIR, exist_ok=True)

    todos_bets: list[Bet] = []

    for liga_nome, codigo in LEAGUES.items():
        for season in SEASONS:
            rows = _download_csv(codigo, season)
            bets = _simulate(rows, liga_nome)
            todos_bets.extend(bets)
            logger.info(f"{liga_nome} {season}: {len(bets)} apostas simuladas")

    if not todos_bets:
        logger.error("Nenhuma aposta simulada — verifique conectividade")
        return

    todos_bets.sort(key=lambda b: b.date)

    geral = _stats(todos_bets)
    por_faixa = _stats_por_subrange(todos_bets)
    por_liga = _stats_por_liga(todos_bets)
    curva = _equity_curve(todos_bets)
    melhor = _melhor_faixa(por_faixa)

    if melhor:
        for s in por_faixa:
            s["melhor"] = (s["faixa"] == melhor["faixa"])

    resultado = {
        "gerado_em": datetime.utcnow().isoformat(),
        "num_temporadas": len(SEASONS),
        "temporadas": SEASONS,
        "ligas": list(LEAGUES.keys()),
        "data_inicio": todos_bets[0].date if todos_bets else "",
        "data_fim": todos_bets[-1].date if todos_bets else "",
        "odd_range": [ODD_MIN, ODD_MAX],
        "geral": geral,
        "por_faixa": por_faixa,
        "por_liga": por_liga,
        "melhor_faixa": melhor,
        "equity_curve": curva,
    }

    with open(BACKTEST_PATH, "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    logger.info(f"Resultado salvo em {BACKTEST_PATH}")

    resumo = _formatar_terminal(resultado)
    print(resumo.encode("ascii", "replace").decode("ascii"))

    if enviar_telegram:
        try:
            from core.database import Database
            from alerts.telegram_bot import TelegramBot
            db = Database()
            db.initialize()
            t = TelegramBot(db=db)
            # Telegram tem limite de 4096 chars — enviamos resumo compacto
            msg_tg = f"BACKTEST ({len(todos_bets)} apostas, {len(SEASONS)} temp.)\n\n"
            msg_tg += f"ROI geral: {geral['roi']:+.2f}%\n"
            msg_tg += f"Taxa: {geral['win_rate']}% | P&L: {geral['pnl_units']:+.2f}u\n\n"
            if melhor:
                msg_tg += f"Melhor faixa: {melhor['faixa']} → ROI {melhor['roi']:+.2f}%\n"
            for s in por_liga:
                msg_tg += f"{s['liga']}: {s['win_rate']}% | ROI {s['roi']:+.2f}%\n"
            t.send(msg_tg, tipo_alerta="BACKTEST")
        except Exception as e:
            logger.warning(f"Telegram nao enviado: {e}")

    logger.info("=== Backtest concluido ===")


if __name__ == "__main__":
    enviar = "--telegram" in sys.argv
    main(enviar_telegram=enviar)
