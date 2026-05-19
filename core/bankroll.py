import logging
import os
from datetime import date
from typing import Optional

from core.database import Database

logger = logging.getLogger(__name__)

STAKE_PERCENT_DEFAULT = float(os.getenv("STAKE_PERCENT", "2.5"))
STOP_DIARIO_PERCENT = float(os.getenv("STOP_DIARIO_PERCENT", "5.0"))
MAX_TRADES_SIMULTANEOS = 2


class Bankroll:
    def __init__(self, db: Database, banca_inicial: float) -> None:
        self._db = db
        self._banca_inicial_dia: float = banca_inicial
        self._banca_atual: float = banca_inicial
        self._pnl_dia: float = 0.0
        self._drawdown_max: float = 0.0
        self._pico_dia: float = banca_inicial

    # ------------------------------------------------------------------
    # Stake
    # ------------------------------------------------------------------

    def calcular_stake(self, banca_atual: Optional[float] = None) -> float:
        """Retorna o valor em reais a arriscar (STAKE_PERCENT% da banca)."""
        banca = banca_atual or self._banca_atual
        stake = round(banca * STAKE_PERCENT_DEFAULT / 100, 2)
        logger.info(f"Stake calculada: R${stake:.2f} ({STAKE_PERCENT_DEFAULT}% de R${banca:.2f})")
        return stake

    # ------------------------------------------------------------------
    # Controles de risco
    # ------------------------------------------------------------------

    def check_stop_diario(self) -> bool:
        """True se a perda acumulada no dia atingiu ou superou o limite."""
        if self._banca_inicial_dia == 0:
            return False
        perda_pct = (-self._pnl_dia / self._banca_inicial_dia) * 100
        if perda_pct >= STOP_DIARIO_PERCENT:
            logger.warning(
                f"STOP DIARIO ativado: perda de {perda_pct:.1f}% "
                f"(limite: {STOP_DIARIO_PERCENT}%)"
            )
            return True
        return False

    def check_max_trades_simultaneos(self) -> bool:
        """True se o número de trades abertos atingiu o limite."""
        ativos = len(self._db.get_active_trades())
        if ativos >= MAX_TRADES_SIMULTANEOS:
            logger.warning(f"Limite de trades simultâneos atingido: {ativos}/{MAX_TRADES_SIMULTANEOS}")
            return True
        return False

    # ------------------------------------------------------------------
    # Registro de resultado
    # ------------------------------------------------------------------

    def registrar_resultado(
        self,
        trade_id: int,
        resultado: str,
        pnl: float,
        odd_saida: Optional[float] = None,
        motivo_saida: str = "",
    ) -> None:
        """Atualiza o banco, ajusta banca e recalcula métricas do dia."""
        self._db.update_trade(
            trade_id=trade_id,
            resultado=resultado,
            odd_saida=odd_saida,
            pnl_reais=pnl,
            motivo_saida=motivo_saida,
        )
        self._banca_atual += pnl
        self._pnl_dia += pnl

        # Atualiza drawdown máximo
        if self._banca_atual > self._pico_dia:
            self._pico_dia = self._banca_atual
        queda = self._pico_dia - self._banca_atual
        if queda > self._drawdown_max:
            self._drawdown_max = queda

        logger.info(
            f"Resultado registrado — Trade #{trade_id}: {resultado} | "
            f"P&L R${pnl:+.2f} | Banca: R${self._banca_atual:.2f}"
        )
        self._salvar_stats_dia()

    # ------------------------------------------------------------------
    # Resumo do dia
    # ------------------------------------------------------------------

    def exportar_resumo_dia(self) -> dict:
        """Retorna métricas consolidadas do dia atual."""
        trades_hoje = self._trades_do_dia()
        total = len(trades_hoje)
        wins = sum(1 for t in trades_hoje if t.get("resultado") == "WIN")
        losses = sum(1 for t in trades_hoje if t.get("resultado") == "LOSS")
        win_rate = round(wins / total * 100, 1) if total > 0 else 0.0
        roi = round(self._pnl_dia / self._banca_inicial_dia * 100, 2) if self._banca_inicial_dia else 0.0

        return {
            "data": date.today().isoformat(),
            "banca_inicio": self._banca_inicial_dia,
            "banca_atual": self._banca_atual,
            "pnl_dia": round(self._pnl_dia, 2),
            "roi_percent": roi,
            "trades_total": total,
            "trades_win": wins,
            "trades_loss": losses,
            "win_rate": win_rate,
            "drawdown_max": round(self._drawdown_max, 2),
            "stop_ativo": self.check_stop_diario(),
            "trades_ativos": len(self._db.get_active_trades()),
        }

    @property
    def banca_atual(self) -> float:
        return self._banca_atual

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _trades_do_dia(self) -> list[dict]:
        hoje = date.today().isoformat()
        return [
            t for t in self._db.get_recent_trades(limit=200)
            if t.get("created_at", "").startswith(hoje)
        ]

    def _salvar_stats_dia(self) -> None:
        resumo = self.exportar_resumo_dia()
        self._db.upsert_daily_stats(
            {
                "data": resumo["data"],
                "trades_total": resumo["trades_total"],
                "trades_win": resumo["trades_win"],
                "trades_loss": resumo["trades_loss"],
                "roi_percent": resumo["roi_percent"],
                "banca_inicio": resumo["banca_inicio"],
                "banca_fim": resumo["banca_atual"],
                "drawdown_max": resumo["drawdown_max"],
            }
        )
