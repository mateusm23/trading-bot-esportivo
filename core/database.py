import sqlite3
import logging
import os
import threading
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "trading.db")

_CREATE_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT NOT NULL,
    liga            TEXT NOT NULL,
    jogo            TEXT NOT NULL,
    favorito        TEXT NOT NULL,
    odd_entrada     REAL NOT NULL,
    odd_saida       REAL,
    stake_reais     REAL NOT NULL,
    resultado       TEXT,          -- WIN | LOSS | CANCELADO | NULL (aberto)
    pnl_reais       REAL,
    motivo_entrada  TEXT,
    motivo_saida    TEXT,
    score_entrada   INTEGER,
    market_id       TEXT
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


class Database:
    def __init__(self, path: str = DB_PATH) -> None:
        self._path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def initialize(self) -> None:
        """Cria todas as tabelas se não existirem."""
        with self._connect() as conn:
            conn.execute(_CREATE_TRADES)
            conn.execute(_CREATE_ODDS_HISTORY)
            conn.execute(_CREATE_ALERTS)
            conn.execute(_CREATE_DAILY_STATS)
        logger.info(f"Banco de dados inicializado em {self._path}")

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    def insert_trade(
        self,
        liga: str,
        jogo: str,
        favorito: str,
        odd_entrada: float,
        stake_reais: float,
        motivo_entrada: str = "",
        score_entrada: int = 0,
        market_id: str = "",
    ) -> int:
        sql = """
            INSERT INTO trades
                (created_at, liga, jogo, favorito, odd_entrada,
                 stake_reais, motivo_entrada, score_entrada, market_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        now = datetime.utcnow().isoformat()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                sql,
                (now, liga, jogo, favorito, odd_entrada,
                 stake_reais, motivo_entrada, score_entrada, market_id),
            )
            trade_id = cur.lastrowid
        logger.info(f"Trade #{trade_id} registrado: {jogo} | {favorito} @ {odd_entrada}")
        return trade_id

    def update_trade(
        self,
        trade_id: int,
        resultado: str,
        odd_saida: Optional[float],
        pnl_reais: float,
        motivo_saida: str = "",
    ) -> None:
        sql = """
            UPDATE trades
            SET resultado=?, odd_saida=?, pnl_reais=?, motivo_saida=?
            WHERE id=?
        """
        with self._lock, self._connect() as conn:
            conn.execute(sql, (resultado, odd_saida, pnl_reais, motivo_saida, trade_id))
        logger.info(f"Trade #{trade_id} encerrado: {resultado} | P&L R${pnl_reais:+.2f}")

    def get_active_trades(self) -> list[dict]:
        sql = "SELECT * FROM trades WHERE resultado IS NULL ORDER BY created_at"
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
            INSERT INTO odds_history
                (market_id, timestamp, odd_favorito, odd_empate, odd_zebra, num_bookmakers)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        now = datetime.utcnow().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(
                sql,
                (
                    market.get("market_id", ""),
                    now,
                    market.get("odd_favorito"),
                    market.get("odd_empate"),
                    market.get("odd_zebra"),
                    market.get("num_bookmakers"),
                ),
            )

    def get_last_odds(self, market_id: str) -> Optional[dict]:
        """Retorna o snapshot de odds mais recente para um market_id."""
        sql = """
            SELECT * FROM odds_history
            WHERE market_id=?
            ORDER BY timestamp DESC LIMIT 1
        """
        with self._lock, self._connect() as conn:
            row = conn.execute(sql, (market_id,)).fetchone()
        return _row_to_dict(row) if row else None

    # ------------------------------------------------------------------
    # Alertas
    # ------------------------------------------------------------------

    def insert_alert(self, market_id: str, tipo_alerta: str, mensagem: str) -> None:
        sql = """
            INSERT INTO alerts_sent (market_id, timestamp, tipo_alerta, mensagem)
            VALUES (?, ?, ?, ?)
        """
        now = datetime.utcnow().isoformat()
        with self._lock, self._connect() as conn:
            conn.execute(sql, (market_id, now, tipo_alerta, mensagem))
        logger.debug(f"Alerta registrado: {tipo_alerta} | {market_id}")

    # ------------------------------------------------------------------
    # Estatísticas diárias
    # ------------------------------------------------------------------

    def upsert_daily_stats(self, stats: dict) -> None:
        sql = """
            INSERT INTO daily_stats
                (data, trades_total, trades_win, trades_loss,
                 roi_percent, banca_inicio, banca_fim, drawdown_max)
            VALUES (:data, :trades_total, :trades_win, :trades_loss,
                    :roi_percent, :banca_inicio, :banca_fim, :drawdown_max)
            ON CONFLICT(data) DO UPDATE SET
                trades_total = excluded.trades_total,
                trades_win   = excluded.trades_win,
                trades_loss  = excluded.trades_loss,
                roi_percent  = excluded.roi_percent,
                banca_fim    = excluded.banca_fim,
                drawdown_max = excluded.drawdown_max
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
