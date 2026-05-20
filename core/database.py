import sqlite3
import logging
import os
import threading
from datetime import date, datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "trading.db")

_CREATE_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    data_jogo       TEXT,
    start_time      TEXT,
    liga            TEXT NOT NULL,
    jogo            TEXT NOT NULL,
    home_team       TEXT,
    away_team       TEXT,
    favorito        TEXT NOT NULL,
    odd_entrada     REAL NOT NULL,
    odd_saida       REAL,
    stake_reais     REAL NOT NULL,
    resultado       TEXT,
    pnl_reais       REAL,
    motivo_entrada  TEXT,
    motivo_saida    TEXT,
    score_entrada   INTEGER,
    market_id       TEXT,
    apostado        INTEGER DEFAULT 1
)
"""

_CREATE_ODDS_HISTORY = """
CREATE TABLE IF NOT EXISTS odds_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id       TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    odd_favorito    REAL,
    odd_empate      REAL,
    odd_zebra       REAL,
    num_bookmakers  INTEGER
)
"""

_CREATE_ALERTS = """
CREATE TABLE IF NOT EXISTS alerts_sent (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id   TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    tipo_alerta TEXT NOT NULL,
    mensagem    TEXT NOT NULL
)
"""

_CREATE_DAILY_STATS = """
CREATE TABLE IF NOT EXISTS daily_stats (
    data            TEXT PRIMARY KEY,
    trades_total    INTEGER DEFAULT 0,
    trades_win      INTEGER DEFAULT 0,
    trades_loss     INTEGER DEFAULT 0,
    roi_percent     REAL DEFAULT 0,
    banca_inicio    REAL DEFAULT 0,
    banca_fim       REAL DEFAULT 0,
    drawdown_max    REAL DEFAULT 0
)
"""

_MIGRATION_COLUMNS = [
    ("data_jogo",  "TEXT"),
    ("start_time", "TEXT"),
    ("home_team",  "TEXT"),
    ("away_team",  "TEXT"),
    ("apostado",   "INTEGER DEFAULT 1"),
]


class Database:
    def __init__(self, path: str = DB_PATH) -> None:
        self._path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(_CREATE_TRADES)
            conn.execute(_CREATE_ODDS_HISTORY)
            conn.execute(_CREATE_ALERTS)
            conn.execute(_CREATE_DAILY_STATS)
        self._migrate()
        logger.info(f"Banco de dados inicializado em {self._path}")

    def _migrate(self) -> None:
        """Adiciona colunas novas sem apagar dados existentes."""
        with self._lock, self._connect() as conn:
            existing = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
            for col, typ in _MIGRATION_COLUMNS:
                if col not in existing:
                    conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {typ}")
                    logger.info(f"Migração: coluna '{col}' adicionada em trades")

    # ------------------------------------------------------------------
    # Apostas (bet journal)
    # ------------------------------------------------------------------

    def registrar_aposta(self, aposta: dict) -> int:
        """Registra uma aposta feita ou nao feita pelo usuario.
        apostado=1 → aposta realizada (afeta banca)
        apostado=0 → seleção ignorada (tracking only)
        """
        apostado = int(aposta.get("apostado", 1))
        resultado = "PENDENTE" if apostado == 1 else "NAO_APOSTADO"

        sql = """
            INSERT INTO trades (
                created_at, data_jogo, start_time,
                liga, jogo, home_team, away_team,
                favorito, odd_entrada, stake_reais,
                motivo_entrada, score_entrada, market_id,
                apostado, resultado
            ) VALUES (
                :created_at, :data_jogo, :start_time,
                :liga, :jogo, :home_team, :away_team,
                :favorito, :odd_entrada, :stake_reais,
                :motivo_entrada, :score_entrada, :market_id,
                :apostado, :resultado
            )
        """
        now = datetime.now(timezone.utc).isoformat()
        params = {
            "created_at": now,
            "data_jogo": aposta.get("data_jogo", ""),
            "start_time": aposta.get("start_time", ""),
            "liga": aposta.get("liga", ""),
            "jogo": aposta.get("jogo", ""),
            "home_team": aposta.get("home_team", ""),
            "away_team": aposta.get("away_team", ""),
            "favorito": aposta.get("favorito", ""),
            "odd_entrada": aposta.get("odd_entrada", 0.0),
            "stake_reais": aposta.get("stake_reais", 0.0),
            "motivo_entrada": aposta.get("motivo_entrada", ""),
            "score_entrada": aposta.get("score_entrada", 0),
            "market_id": aposta.get("market_id", ""),
            "apostado": apostado,
            "resultado": resultado,
        }
        with self._lock, self._connect() as conn:
            cur = conn.execute(sql, params)
            trade_id = cur.lastrowid
        logger.info(f"Aposta #{trade_id} registrada: {params['jogo']} | apostado={apostado}")
        return trade_id

    def atualizar_resultado_aposta(self, trade_id: int, resultado: str) -> dict:
        """Atualiza WIN/LOSS/VOID e calcula P&L automaticamente."""
        trade = self.get_trade(trade_id)
        if not trade:
            return {"erro": "Aposta nao encontrada"}

        stake = trade.get("stake_reais", 0) or 0
        odd = trade.get("odd_entrada", 1) or 1

        if resultado == "WIN":
            pnl = round(stake * (odd - 1), 2)
        elif resultado == "LOSS":
            pnl = round(-stake, 2)
        else:  # VOID / cancelada
            pnl = 0.0

        sql = "UPDATE trades SET resultado=?, pnl_reais=? WHERE id=?"
        with self._lock, self._connect() as conn:
            conn.execute(sql, (resultado, pnl, trade_id))
        logger.info(f"Aposta #{trade_id} encerrada: {resultado} | P&L R${pnl:+.2f}")
        return {"id": trade_id, "resultado": resultado, "pnl_reais": pnl}

    def deletar_aposta(self, trade_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM trades WHERE id=?", (trade_id,))

    def get_bets(self, data_jogo: Optional[str] = None, limit: int = 200) -> list[dict]:
        if data_jogo:
            sql = "SELECT * FROM trades WHERE data_jogo=? ORDER BY start_time ASC"
            with self._lock, self._connect() as conn:
                rows = conn.execute(sql, (data_jogo,)).fetchall()
        else:
            sql = "SELECT * FROM trades ORDER BY data_jogo DESC, start_time ASC LIMIT ?"
            with self._lock, self._connect() as conn:
                rows = conn.execute(sql, (limit,)).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_market_ids_registrados(self, data_jogo: str) -> set[str]:
        """Retorna market_ids já registrados para evitar duplicatas."""
        sql = "SELECT market_id FROM trades WHERE data_jogo=? AND market_id != ''"
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, (data_jogo,)).fetchall()
        return {r["market_id"] for r in [_row_to_dict(r) for r in rows]}

    # ------------------------------------------------------------------
    # Estatísticas de banca
    # ------------------------------------------------------------------

    def get_bankroll_stats(self, banca_inicial: float) -> dict:
        sql = """
            SELECT
                COUNT(CASE WHEN apostado=1 AND resultado NOT IN ('NAO_APOSTADO') THEN 1 END) as total,
                COUNT(CASE WHEN apostado=1 AND resultado='WIN'      THEN 1 END) as wins,
                COUNT(CASE WHEN apostado=1 AND resultado='LOSS'     THEN 1 END) as losses,
                COUNT(CASE WHEN apostado=1 AND resultado='PENDENTE' THEN 1 END) as pendentes,
                COALESCE(SUM(CASE WHEN apostado=1 AND pnl_reais IS NOT NULL THEN pnl_reais ELSE 0 END), 0) as lucro_total,
                COALESCE(SUM(CASE WHEN apostado=1 THEN stake_reais ELSE 0 END), 0) as total_apostado
            FROM trades
        """
        with self._lock, self._connect() as conn:
            row = dict(conn.execute(sql).fetchone())

        wins = row["wins"] or 0
        losses = row["losses"] or 0
        lucro = row["lucro_total"] or 0
        banca_atual = round(banca_inicial + lucro, 2)
        taxa = round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0
        roi = round(lucro / banca_inicial * 100, 1) if banca_inicial > 0 else 0
        stake_sugerido = round(banca_atual * 0.025, 2)

        # Sequência atual (streak)
        sql_streak = """
            SELECT resultado FROM trades
            WHERE apostado=1 AND resultado IN ('WIN','LOSS')
            ORDER BY data_jogo DESC, start_time DESC
            LIMIT 20
        """
        with self._lock, self._connect() as conn:
            recentes = [r["resultado"] for r in conn.execute(sql_streak).fetchall()]
        streak = 0
        if recentes:
            tipo = recentes[0]
            for r in recentes:
                if r == tipo:
                    streak += 1
                else:
                    break

        # Stop diário de hoje
        hoje = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime("%Y-%m-%d")
        sql_hoje = """
            SELECT COALESCE(SUM(pnl_reais), 0) as pnl_hoje
            FROM trades
            WHERE apostado=1 AND data_jogo=? AND pnl_reais IS NOT NULL
        """
        with self._lock, self._connect() as conn:
            pnl_hoje = dict(conn.execute(sql_hoje, (hoje,)).fetchone())["pnl_hoje"] or 0
        stop_limite = round(-banca_inicial * 0.05, 2)

        return {
            "banca_inicial": banca_inicial,
            "banca_atual": banca_atual,
            "lucro_total": round(lucro, 2),
            "roi_percent": roi,
            "total_apostas": row["total"] or 0,
            "wins": wins,
            "losses": losses,
            "pendentes": row["pendentes"] or 0,
            "taxa_acerto": taxa,
            "total_apostado": round(row["total_apostado"] or 0, 2),
            "stake_sugerido": stake_sugerido,
            "streak": streak,
            "streak_tipo": recentes[0] if recentes else None,
            "pnl_hoje": round(pnl_hoje, 2),
            "stop_limite": stop_limite,
            "stop_atingido": pnl_hoje <= stop_limite,
        }

    def get_banca_curve(self, banca_inicial: float) -> list[dict]:
        sql = """
            SELECT data_jogo, SUM(pnl_reais) as pnl_dia
            FROM trades
            WHERE apostado=1 AND pnl_reais IS NOT NULL AND data_jogo IS NOT NULL AND data_jogo != ''
            GROUP BY data_jogo
            ORDER BY data_jogo
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql).fetchall()

        curve = [{"data": "inicio", "banca": banca_inicial}]
        banca = banca_inicial
        for row in rows:
            banca = round(banca + (row["pnl_dia"] or 0), 2)
            curve.append({"data": row["data_jogo"], "banca": banca})
        return curve

    def get_stats_por_liga(self) -> list[dict]:
        sql = """
            SELECT liga,
                COUNT(CASE WHEN resultado='WIN'  THEN 1 END) as wins,
                COUNT(CASE WHEN resultado='LOSS' THEN 1 END) as losses,
                COALESCE(SUM(pnl_reais), 0) as pnl
            FROM trades
            WHERE apostado=1 AND resultado IN ('WIN','LOSS')
            GROUP BY liga
            ORDER BY (wins + losses) DESC
        """
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        result = []
        for r in rows:
            d = _row_to_dict(r)
            total = (d["wins"] or 0) + (d["losses"] or 0)
            d["taxa"] = round(d["wins"] / total * 100, 1) if total > 0 else 0
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # Trades legados (compatibilidade)
    # ------------------------------------------------------------------

    def insert_trade(self, liga, jogo, favorito, odd_entrada, stake_reais,
                     motivo_entrada="", score_entrada=0, market_id="") -> int:
        return self.registrar_aposta({
            "liga": liga, "jogo": jogo, "favorito": favorito,
            "odd_entrada": odd_entrada, "stake_reais": stake_reais,
            "motivo_entrada": motivo_entrada, "score_entrada": score_entrada,
            "market_id": market_id, "apostado": 1,
        })

    def update_trade(self, trade_id, resultado, odd_saida, pnl_reais, motivo_saida="") -> None:
        sql = "UPDATE trades SET resultado=?, odd_saida=?, pnl_reais=?, motivo_saida=? WHERE id=?"
        with self._lock, self._connect() as conn:
            conn.execute(sql, (resultado, odd_saida, pnl_reais, motivo_saida, trade_id))

    def get_active_trades(self) -> list[dict]:
        sql = "SELECT * FROM trades WHERE resultado='PENDENTE' AND apostado=1 ORDER BY created_at"
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [_row_to_dict(r) for r in rows]

    def get_trade(self, trade_id: int) -> Optional[dict]:
        sql = "SELECT * FROM trades WHERE id=?"
        with self._lock, self._connect() as conn:
            row = conn.execute(sql, (trade_id,)).fetchone()
        return _row_to_dict(row) if row else None

    def get_recent_trades(self, limit: int = 20) -> list[dict]:
        sql = "SELECT * FROM trades ORDER BY created_at DESC LIMIT ?"
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, (limit,)).fetchall()
        return [_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Odds history
    # ------------------------------------------------------------------

    def insert_odds_snapshot(self, market: dict) -> None:
        sql = """
            INSERT INTO odds_history (market_id, timestamp, odd_favorito, odd_empate, odd_zebra, num_bookmakers)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(sql, (
                market.get("market_id", ""), now,
                market.get("odd_favorito"), market.get("odd_empate"),
                market.get("odd_zebra"), market.get("num_bookmakers"),
            ))

    def get_last_odds(self, market_id: str) -> Optional[dict]:
        sql = "SELECT * FROM odds_history WHERE market_id=? ORDER BY timestamp DESC LIMIT 1"
        with self._lock, self._connect() as conn:
            row = conn.execute(sql, (market_id,)).fetchone()
        return _row_to_dict(row) if row else None

    # ------------------------------------------------------------------
    # Alertas e stats diários
    # ------------------------------------------------------------------

    def insert_alert(self, market_id: str, tipo_alerta: str, mensagem: str) -> None:
        sql = "INSERT INTO alerts_sent (market_id, timestamp, tipo_alerta, mensagem) VALUES (?, ?, ?, ?)"
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(sql, (market_id, now, tipo_alerta, mensagem))

    def upsert_daily_stats(self, stats: dict) -> None:
        sql = """
            INSERT INTO daily_stats (data, trades_total, trades_win, trades_loss,
                roi_percent, banca_inicio, banca_fim, drawdown_max)
            VALUES (:data, :trades_total, :trades_win, :trades_loss,
                    :roi_percent, :banca_inicio, :banca_fim, :drawdown_max)
            ON CONFLICT(data) DO UPDATE SET
                trades_total=excluded.trades_total, trades_win=excluded.trades_win,
                trades_loss=excluded.trades_loss, roi_percent=excluded.roi_percent,
                banca_fim=excluded.banca_fim, drawdown_max=excluded.drawdown_max
        """
        with self._lock, self._connect() as conn:
            conn.execute(sql, stats)

    def get_daily_stats(self, dt: Optional[date] = None) -> Optional[dict]:
        key = (dt or date.today()).isoformat()
        sql = "SELECT * FROM daily_stats WHERE data=?"
        with self._lock, self._connect() as conn:
            row = conn.execute(sql, (key,)).fetchone()
        return _row_to_dict(row) if row else None

    def get_daily_stats_history(self, days: int = 30) -> list[dict]:
        sql = "SELECT * FROM daily_stats ORDER BY data DESC LIMIT ?"
        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, (days,)).fetchall()
        return [_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)
