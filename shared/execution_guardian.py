#!/usr/bin/env python3
"""
core/execution/guardian.py — Execution Guardian (Confluence Gate + Time-Decay + Drawdown Guard)
══════════════════════════════════════════════════════════════════════════════════════════════
CHANGE: 2026-05-05 — P8: final gate before order dispatch. Enforces:
  - Confluence: all 3 models (ML + Meta + TimesFM) must agree
  - Time-Decay Exit: if trade lives >70% of label_horizon without hitting barrier → force close
  - Drawdown Guard: 3 consecutive losses in high-entropy regime → 4h cooldown
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("Guardian")

COOLDOWN_HOURS = 4
MAX_CONSECUTIVE_LOSSES = 3
TIME_DECAY_RATIO = 0.70  # 70% of label_horizon


@dataclass
class GuardianDecision:
    symbol: str
    decision: str        # "EXECUTE" | "COOLDOWN" | "TIME_EXIT" | "REJECT"
    reason: str
    bet_size_pct: float = 0.0
    signal: str = "NO_TRADE"
    cooldown_until: Optional[datetime] = None
    active_trades: int = 0
    consecutive_losses: int = 0
    entropy_warning: bool = False


class ExecutionGuardian:
    """Pre-execution filter with risk management state machine."""

    def __init__(self):
        self.cooldown_until: Optional[datetime] = None
        self.consecutive_losses: int = 0
        self.active_trades: Dict[str, dict] = {}  # symbol → {entry_time, entry_price, tp, sl, max_holding}
        self.trade_history: List[dict] = []        # list of completed trade results

    def evaluate(
        self,
        symbol: str,
        bet_result,   # BetSizeResult
        entry_price: float,
        tp_level: float,
        sl_level: float,
        label_horizon: int = 6,
    ) -> GuardianDecision:
        """Evaluate whether to execute a trade given current state.

        Args:
            symbol: Trading symbol (BTC, ETH).
            bet_result: BetSizeResult from bet_sizer.
            entry_price: Proposed entry price.
            tp_level: Take-profit level.
            sl_level: Stop-loss level.
            label_horizon: Triple Barrier max_holding (bars).

        Returns:
            GuardianDecision with final verdict.
        """
        now = datetime.now(timezone.utc)

        # ── Cooldown check ──────────────────────────────
        if self.cooldown_until and now < self.cooldown_until:
            remaining = (self.cooldown_until - now).total_seconds() / 3600
            return GuardianDecision(
                symbol=symbol,
                decision="COOLDOWN",
                reason=f"Cooldown active: {remaining:.1f}h remaining after {self.consecutive_losses} consecutive losses",
                cooldown_until=self.cooldown_until,
                consecutive_losses=self.consecutive_losses,
            )

        # ── Time-decay check on active trades ────────────
        if symbol in self.active_trades:
            trade = self.active_trades[symbol]
            trade_age_bars = trade.get("bars_elapsed", 0)
            if trade_age_bars / trade.get("max_holding", 1) >= TIME_DECAY_RATIO:
                logger.warning(f"[TimeDecay] {symbol}: {trade_age_bars}/{trade['max_holding']} bars — forcing exit")
                return GuardianDecision(
                    symbol=symbol,
                    decision="TIME_EXIT",
                    reason=f"Alpha decay: {trade_age_bars}/{trade['max_holding']} bars ({trade_age_bars/trade['max_holding']:.0%})",
                )

        # ── Entropy warning ─────────────────────────────
        entropy_warning = bet_result.regime_entropy > 0.6

        # ── Confluence gate check ───────────────────────
        if not bet_result.confluence:
            return GuardianDecision(
                symbol=symbol,
                decision="REJECT",
                reason=f"Confluence failed: P={bet_result.raw_probability:.1%} Meta={bet_result.meta_verdict} Entropy={bet_result.regime_entropy:.2f}",
                consecutive_losses=self.consecutive_losses,
                entropy_warning=entropy_warning,
            )

        # ── All gates passed → EXECUTE ──────────────────
        decision = GuardianDecision(
            symbol=symbol,
            decision="EXECUTE",
            reason="All confluence gates passed",
            bet_size_pct=bet_result.position_size_pct,
            signal=bet_result.signal,
            consecutive_losses=self.consecutive_losses,
            entropy_warning=entropy_warning,
            active_trades=len(self.active_trades),
        )

        return decision

    def record_trade_open(
        self, symbol: str, entry_price: float, tp: float, sl: float, max_holding: int
    ) -> None:
        """Record a new trade entry."""
        self.active_trades[symbol] = {
            "entry_time": datetime.now(timezone.utc),
            "entry_price": entry_price,
            "tp": tp,
            "sl": sl,
            "max_holding": max_holding,
            "bars_elapsed": 0,
        }

    def record_trade_close(self, symbol: str, exit_price: float, outcome: str) -> None:
        """Record trade exit and update drawdown state."""
        if symbol in self.active_trades:
            trade = self.active_trades.pop(symbol)
            self.trade_history.append({
                **trade,
                "exit_time": datetime.now(timezone.utc),
                "exit_price": exit_price,
                "outcome": outcome,
            })

        if outcome in ("SL_HIT", "TIME_EXIT", "REJECTED"):
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0

        # ── Drawdown guard: 3 consecutive losses → cooldown ──
        if self.consecutive_losses >= MAX_CONSECUTIVE_LOSSES:
            self.cooldown_until = datetime.now(timezone.utc) + timedelta(hours=COOLDOWN_HOURS)
            logger.warning(
                f"[DrawdownGuard] {MAX_CONSECUTIVE_LOSSES} consecutive losses → "
                f"cooldown until {self.cooldown_until.strftime('%H:%M UTC')}"
            )

    def tick_bars(self, bars: int = 1) -> None:
        """Increment bar counter for all active trades (called each candle)."""
        for symbol in list(self.active_trades):
            self.active_trades[symbol]["bars_elapsed"] += bars

    def health_check(self) -> dict:
        """Return current state for monitoring."""
        return {
            "cooldown_active": bool(self.cooldown_until and datetime.now(timezone.utc) < self.cooldown_until),
            "consecutive_losses": self.consecutive_losses,
            "active_trades": len(self.active_trades),
            "total_trades_history": len(self.trade_history),
            "win_rate": sum(1 for t in self.trade_history if t.get("outcome") == "TP_HIT") / max(len(self.trade_history), 1),
        }

    # CHANGE: 2026-05-05 — P9 Model Drift Detection
    # Source: Shadow-Running & Variance Control skill
    def check_model_drift(self, brier_scores: list, window_hours: int = 24) -> dict:
        """Detect sustained Brier score degradation over window_hours.

        If Brier has risen monotonically for the window period, triggers
        retrain recommendation.

        Args:
            brier_scores: List of (timestamp, brier) tuples for recent predictions.
            window_hours: Lookback window for drift detection.

        Returns:
            dict with drift_detected, trend_pct, recommendation.
        """
        if len(brier_scores) < 6:
            return {"drift_detected": False, "trend_pct": 0.0, "recommendation": "insufficient_data"}

        recent = brier_scores[-window_hours:] if len(brier_scores) > window_hours else brier_scores
        values = [v for _, v in recent]

        # Linear trend
        x = np.arange(len(values))
        A = np.vstack([x, np.ones(len(x))]).T
        slope, _ = np.linalg.lstsq(A, values, rcond=None)[0]

        drift_detected = slope > 0 and values[-1] > 1.2 * values[0]  # 20% increase

        if drift_detected:
            logger.warning(
                f"[ModelDrift] Brier score rising: {values[0]:.4f} → {values[-1]:.4f} "
                f"(+{(values[-1]/max(values[0],0.01)-1)*100:.0f}%). "
                f"Recommend: retrain model."
            )

        return {
            "drift_detected": drift_detected,
            "trend_pct": float(slope * 100),
            "brier_first": float(values[0]),
            "brier_last": float(values[-1]),
            "recommendation": "RETRAIN" if drift_detected else "OK",
        }
