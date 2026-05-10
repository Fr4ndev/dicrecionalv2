#!/usr/bin/env python3
"""
core/execution/shadow_tester.py — Live Performance & Shadow Tester
═══════════════════════════════════════════════════════════════════
CCXTV2 Extension (no core modifications). Compares discretionary signals
against ML VetoSystem verdicts, tracks virtual P&L, and maintains a
live statistical edge measurement.

Golden Rules (reaffirmed via hub_reader):
  - VPIN Gate: > 0.62 for execution
  - Basis: < -0.05% for accumulation
  - OBI Ignition: > 0.40 to confirm pressure

Usage:
    tester = ShadowTester()
    await tester.initialize()

    signal_id = tester.submit_signal(
        direction="LONG", symbol="BTC/USDT:USDT",
        entry_price=81000.0, alpha_half_life=7,
        features_df=features, timesfm_sign=+1
    )
    # ... after alpha half-life ...
    await tester.close_track(signal_id, exit_price=81200.0)
    print(tester.contingency_table())
"""

from __future__ import annotations

import json
import logging
import os
import asyncio
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from collections import deque

import numpy as np

logger = logging.getLogger("ShadowTester")

# ═══════════════════════════════════════════════════════════════
# Constants
# ═══════════════════════════════════════════════════════════════
DEFAULT_ALPHA_HALF_LIFE = 7       # candles (5-15 range)
DEFAULT_STATS_INTERVAL_HOURS = 24
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
LIVE_STATS_PATH = os.path.join(LOG_DIR, "live_stats.json")
SHADOW_LOG_PATH = os.path.join(LOG_DIR, "shadow_tracks.jsonl")


@dataclass
class ShadowSignal:
    """A single discretionary signal submitted for shadow testing."""
    signal_id: str
    timestamp: str
    direction: str           # "LONG" | "SHORT"
    symbol: str
    entry_price: float
    alpha_half_life: int     # candles to track
    veto_result: dict = field(default_factory=dict)
    tracked: bool = False
    closed: bool = False
    exit_price: float = 0.0
    outcome: str = ""        # "WIN" | "LOSS" | "BREAKEVEN"
    pnl_pct: float = 0.0
    bars_observed: int = 0


@dataclass
class ContingencyTable:
    """2x2 contingency table for ML veto vs. actual outcome."""
    go_winner: int = 0       # ML said GO, actual WIN
    go_loser: int = 0        # ML said GO, actual LOSS
    veto_winner: int = 0     # ML said VETO, actual WIN  (Type I Error)
    veto_loser: int = 0      # ML said VETO, actual LOSS (System Success)
    reduce_winner: int = 0   # ML said REDUCE, actual WIN
    reduce_loser: int = 0    # ML said REDUCE, actual LOSS

    @property
    def total(self) -> int:
        return self.go_winner + self.go_loser + self.veto_winner + self.veto_loser + self.reduce_winner + self.reduce_loser

    @property
    def veto_accuracy(self) -> float:
        denom = self.veto_winner + self.veto_loser
        return self.veto_loser / max(denom, 1)

    @property
    def type_i_rate(self) -> float:
        denom = self.veto_winner + self.veto_loser
        return self.veto_winner / max(denom, 1)

    @property
    def go_win_rate(self) -> float:
        denom = self.go_winner + self.go_loser
        return self.go_winner / max(denom, 1)

    def to_dict(self) -> dict:
        return {
            "go_winner": self.go_winner,
            "go_loser": self.go_loser,
            "veto_winner": self.veto_winner,
            "veto_loser": self.veto_loser,
            "reduce_winner": self.reduce_winner,
            "reduce_loser": self.reduce_loser,
            "total": self.total,
            "veto_accuracy": round(self.veto_accuracy, 4),
            "type_i_error_rate": round(self.type_i_rate, 4),
            "go_win_rate": round(self.go_win_rate, 4),
        }


class ShadowTester:
    """Tracks discretionary signals vs. ML VetoSystem verdicts.

    Builds a statistical edge measurement over time by comparing
    what the trader "feels" vs. what the ML system "recommends".
    Updates live_stats.json every 24h (or on demand).
    """

    def __init__(self):
        self._veto = None
        self._active_signals: Dict[str, ShadowSignal] = {}
        self._history: deque = deque(maxlen=5000)
        self._table = ContingencyTable()
        self._last_stats_update: Optional[datetime] = None
        self._p_value: float = 1.0
        self._signals_since_update: int = 0
        os.makedirs(LOG_DIR, exist_ok=True)
        self._load_state()

    async def initialize(self) -> None:
        """Lazy-load the VetoSystem (avoids import at module level)."""
        from shared.veto_system import MLVetoSystem
        self._veto = MLVetoSystem()
        self._veto.load_models("BTC_USDT_USDT")
        logger.info("[ShadowTester] Initialized — VetoSystem loaded")

    def _load_state(self) -> None:
        """Restore contingency table from live_stats.json."""
        if os.path.exists(LIVE_STATS_PATH):
            try:
                with open(LIVE_STATS_PATH) as f:
                    state = json.load(f)
                t = state.get("contingency", {})
                self._table = ContingencyTable(
                    go_winner=t.get("go_winner", 0),
                    go_loser=t.get("go_loser", 0),
                    veto_winner=t.get("veto_winner", 0),
                    veto_loser=t.get("veto_loser", 0),
                    reduce_winner=t.get("reduce_winner", 0),
                    reduce_loser=t.get("reduce_loser", 0),
                )
                self._p_value = state.get("p_value", 1.0)
                self._last_stats_update = datetime.fromisoformat(
                    state["updated_at"]
                ) if "updated_at" in state else None
                logger.info(f"[ShadowTester] Loaded state: {self._table.total} tracked signals")
            except Exception as e:
                logger.warning(f"[ShadowTester] Could not load state: {e}")

    def _save_state(self) -> None:
        """Persist live_stats.json."""
        try:
            state = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "contingency": self._table.to_dict(),
                "p_value": round(self._p_value, 6),
                "is_significant": self._p_value < 0.05,
                "active_signals": len(self._active_signals),
                "total_observed": self._table.total,
                "golden_rules": {
                    "vpin_gate": 0.62,
                    "basis_threshold_pct": -0.05,
                    "obi_ignition": 0.40,
                },
            }
            with open(LIVE_STATS_PATH, "w") as f:
                json.dump(state, f, indent=2)
            self._last_stats_update = datetime.now(timezone.utc)
        except Exception as e:
            logger.error(f"[ShadowTester] Failed to save state: {e}")

    # ══════════════════════════════════════════════════════════
    # INPUT CAPTURE — Receive a discretionary signal
    # ══════════════════════════════════════════════════════════
    async def submit_signal(
        self,
        direction: str,
        symbol: str = "BTC/USDT:USDT",
        entry_price: Optional[float] = None,
        alpha_half_life: int = DEFAULT_ALPHA_HALF_LIFE,
        features_df=None,
        timesfm_sign: int = 0,
        timesfm_align: float = 0.0,
        source: str = "discretionary",
    ) -> str:
        """Submit a manual signal for shadow comparison against VetoSystem.

        Returns a signal_id for later closing.
        """
        if self._veto is None:
            await self.initialize()

        # ── Fetch live metrics via hub_reader (single source of truth) ──
        from shared.hub_reader import get_live_metrics
        try:
            metrics = await get_live_metrics(symbol)
        except Exception:
            metrics = None

        vpin = float(metrics.vpin) if metrics else 0.5
        obi = float(metrics.obi) if metrics else 0.0
        basis = float(metrics.basis) if metrics else 0.0

        if entry_price is None:
            try:
                from shared.hub import IntelligenceHub
                hub = await IntelligenceHub.instance()
                ticker = await hub.fetch_ticker(symbol)
                entry_price = float(ticker["last"]) if ticker else 0.0
            except Exception:
                entry_price = 0.0

        # ── Consult VetoSystem ──
        veto_result = self._veto.evaluate_signal(
            direction=direction,
            symbol="BTC" if "BTC" in symbol else "ETH",
            source=source,
            features_df=features_df,
            vpin=vpin,
            obi=obi,
            basis=basis,
            entry_price=entry_price,
            timesfm_sign=timesfm_sign,
            timesfm_align=timesfm_align,
        )

        signal_id = f"shadow_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S_%f')}_{direction}"
        alpha_half_life = max(5, min(15, alpha_half_life))

        shadow = ShadowSignal(
            signal_id=signal_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            direction=direction.upper(),
            symbol=symbol,
            entry_price=entry_price,
            alpha_half_life=alpha_half_life,
            veto_result={
                "final_verdict": veto_result.final_verdict,
                "kelly_multiplier": veto_result.kelly_multiplier,
                "max_size_pct": veto_result.max_size_pct,
                "reason": veto_result.reason,
                "ml_probability": veto_result.ml_probability,
                "vpin": veto_result.vpin,
                "obi": veto_result.obi,
                "basis": veto_result.basis,
                "hmm_entropy": veto_result.hmm_entropy,
                "anti_hallucination": veto_result.anti_hallucination,
            },
        )

        # ── Also record in VetoSystem's own shadow log ──
        self._veto.shadow_log_signal(direction=direction, ml_prob=veto_result.ml_probability)

        self._active_signals[signal_id] = shadow
        self._log_to_jsonl(shadow)

        logger.info(
            f"[ShadowTester] {signal_id}: {direction} @ {entry_price} → "
            f"VETO={veto_result.final_verdict} kelly={veto_result.kelly_multiplier:.0%}"
        )
        return signal_id

    # ══════════════════════════════════════════════════════════
    # TRACKING — Follow price through Alpha Half-Life
    # ══════════════════════════════════════════════════════════
    async def close_track(self, signal_id: str, exit_price: float) -> ShadowSignal:
        """Close a shadow signal after Alpha Half-Life expires.

        Records outcome and updates contingency table.
        """
        shadow = self._active_signals.pop(signal_id, None)
        if shadow is None:
            raise KeyError(f"Signal {signal_id} not found in active tracks")

        shadow.closed = True
        shadow.exit_price = exit_price

        if shadow.direction == "LONG":
            pnl_pct = (exit_price - shadow.entry_price) / shadow.entry_price
        else:
            pnl_pct = (shadow.entry_price - exit_price) / shadow.entry_price

        shadow.pnl_pct = pnl_pct

        if pnl_pct > 0.001:
            shadow.outcome = "WIN"
        elif pnl_pct < -0.001:
            shadow.outcome = "LOSS"
        else:
            shadow.outcome = "BREAKEVEN"

        # ── Update contingency table ──
        verdict = shadow.veto_result["final_verdict"]
        if shadow.outcome == "WIN":
            if verdict == "GO":
                self._table.go_winner += 1
            elif verdict == "VETO":
                self._table.veto_winner += 1  # Type I Error — vetoed a winner
            elif "REDUCE" in verdict:
                self._table.reduce_winner += 1
        elif shadow.outcome == "LOSS":
            if verdict == "GO":
                self._table.go_loser += 1
            elif verdict == "VETO":
                self._table.veto_loser += 1  # System Success — correctly vetoed a loser
            elif "REDUCE" in verdict:
                self._table.reduce_loser += 1

        # ── Regret logging into VetoSystem ──
        actual = 1 if shadow.outcome == "WIN" else 0
        self._veto.log_regret(
            direction=shadow.direction,
            verdict=verdict,
            ml_prob=shadow.veto_result["ml_probability"],
            actual_outcome=actual,
            reason=shadow.veto_result["reason"],
        )

        self._history.append(shadow)
        self._signals_since_update += 1

        # Auto-update stats every 24h or every 50 signals
        if (
            self._signals_since_update >= 50
            or (
                self._last_stats_update
                and (datetime.now(timezone.utc) - self._last_stats_update) > timedelta(hours=DEFAULT_STATS_INTERVAL_HOURS)
            )
        ):
            self._recompute_p_value()
            self._save_state()
            self._signals_since_update = 0

        logger.info(
            f"[ShadowTester] {signal_id} closed: {shadow.outcome} "
            f"({shadow.pnl_pct:+.2%}) | veto_verdict={verdict}"
        )
        return shadow

    def close_track_sync(self, signal_id: str, exit_price: float) -> ShadowSignal:
        """Synchronous version of close_track for non-async contexts."""
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Already in event loop, call async directly
            import concurrent.futures
            future = asyncio.run_coroutine_threadsafe(
                self.close_track(signal_id, exit_price), loop
            )
            return future.result(timeout=30)
        return loop.run_until_complete(self.close_track(signal_id, exit_price))

    # ══════════════════════════════════════════════════════════
    # CONTINGENCY TABLE
    # ══════════════════════════════════════════════════════════
    def contingency_table(self) -> dict:
        """Return the full 2x2 contingency analysis."""
        return self._table.to_dict()

    # ══════════════════════════════════════════════════════════
    # STATISTICAL EDGE — P-value computation
    # ══════════════════════════════════════════════════════════
    def _recompute_p_value(self) -> None:
        """Recompute p-value using binomial test on GO signals."""
        go_total = self._table.go_winner + self._table.go_loser
        if go_total < 10:
            self._p_value = 1.0
            return
        try:
            from scipy.stats import binomtest
            result = binomtest(
                self._table.go_winner, go_total,
                p=0.5, alternative="greater"
            )
            self._p_value = result.pvalue
        except ImportError:
            self._p_value = 1.0
            logger.warning("[ShadowTester] scipy not available for p-value calc")

    @property
    def p_value(self) -> float:
        return self._p_value

    @property
    def is_statistically_significant(self) -> bool:
        return self._p_value < 0.05

    # ══════════════════════════════════════════════════════════
    # LIVE STATS SUMMARY
    # ══════════════════════════════════════════════════════════
    def live_summary(self) -> dict:
        """Return current live performance snapshot."""
        veto = self._veto.shadow_status() if self._veto else {}
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "contingency": self._table.to_dict(),
            "p_value": round(self._p_value, 6),
            "is_significant": self.is_statistically_significant,
            "active_tracks": len(self._active_signals),
            "total_closed": self._table.total,
            "veto_system_shadow": veto,
            "golden_rules": {
                "vpin_gate": 0.62,
                "basis_threshold_pct": -0.05,
                "obi_ignition": 0.40,
            },
        }

    # ══════════════════════════════════════════════════════════
    # PERSISTENCE — JSONL append log
    # ══════════════════════════════════════════════════════════
    def _log_to_jsonl(self, shadow: ShadowSignal) -> None:
        """Append a shadow signal to the JSONL log."""
        record = {
            "signal_id": shadow.signal_id,
            "timestamp": shadow.timestamp,
            "direction": shadow.direction,
            "symbol": shadow.symbol,
            "entry_price": shadow.entry_price,
            "alpha_half_life": shadow.alpha_half_life,
            "veto_result": shadow.veto_result,
        }
        try:
            with open(SHADOW_LOG_PATH, "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.error(f"[ShadowTester] JSONL write failed: {e}")

    def force_update_stats(self) -> None:
        """Manually trigger a p-value recompute and stats save."""
        self._recompute_p_value()
        self._save_state()
        self._signals_since_update = 0


# ═══════════════════════════════════════════════════════════════
# Singleton accessor (compatible with IntelligenceHub pattern)
# ═══════════════════════════════════════════════════════════════
_shadow_instance: Optional[ShadowTester] = None


async def get_shadow_tester() -> ShadowTester:
    """Get or create the singleton ShadowTester instance."""
    global _shadow_instance
    if _shadow_instance is None:
        _shadow_instance = ShadowTester()
        await _shadow_instance.initialize()
    return _shadow_instance


def get_shadow_tester_sync() -> ShadowTester:
    """Synchronous singleton accessor."""
    global _shadow_instance
    if _shadow_instance is None:
        _shadow_instance = ShadowTester()
    return _shadow_instance
