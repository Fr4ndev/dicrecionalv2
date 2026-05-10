#!/usr/bin/env python3
"""
core/execution/veto_system.py — P12 ML Veto System + Adaptive Intelligence Suite
══════════════════════════════════════════════════════════════════════════════
CHANGE: 2026-05-05 — P12: pivot from autonomous generator to institutional
confluence filter. Integrates 3 adaptive skills:

SKILL 1 — Regime-Specific Thresholds: VPIN/ML/entropy gates adapt to HMM regime.
SKILL 2 — Anti-Hallucination: ML vs TimesFM divergence forces REDUCE_SIZE.
SKILL 3 — Regret Analysis: logs vetoed winners, auto-recalibrates thresholds.

You bring direction. The system brings mathematical rigor.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger("VetoSystem")

# ── Default thresholds (adjusted by SKILL 1 per regime) ─────
DEFAULT_VPIN_GATE = 0.62
DEFAULT_ML_GATE = 0.48
DEFAULT_ENTROPY_GATE = 0.70

HIGH_VOL_VPIN = 0.75
HIGH_VOL_ML = 0.55
HIGH_VOL_ENTROPY = 0.60

REGRET_RECAL_THRESHOLD = 10  # consecutive vetoed winners → recalibrate


@dataclass
class VetoResult:
    symbol: str; direction: str; source: str; timestamp: str = ""
    ml_probability: float = 0.0; ml_verdict: str = "UNKNOWN"
    hmm_entropy: float = 0.0; hmm_verdict: str = "OK"
    vpin: float = 0.0; obi: float = 0.0; basis: float = 0.0
    micro_verdict: str = "UNKNOWN"
    market_entropy: float = 0.0; entropy_blocked: bool = False
    anti_hallucination: bool = False; anti_hallucination_action: str = ""
    tfm_sign: int = 0; tfm_align: float = 0.0
    final_verdict: str = "UNKNOWN"; kelly_multiplier: float = 1.0
    max_size_pct: float = 0.20; reason: str = ""; telegram_message: str = ""


class MLVetoSystem:
    """Discretionary copilot — ML/HMM/microstructure gates on your signals.

    Usage:
        veto = MLVetoSystem(); veto.load_models("BTC_USDT_USDT")
        r = veto.evaluate_signal("LONG", vpin=0.72, obi=-0.58, basis=-0.06,
                                  features_df=features, timesfm_sign=+1)
        print(r.telegram_message)
    """

    def __init__(self, models_dir: str = None):
        base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.models_dir = models_dir or os.path.join(base, "models")
        self.ml_model = None; self._hmm_entropy = 0.5
        self.regret_log: List[dict] = []; self.regret_count: int = 0
        self.vpin_gate = DEFAULT_VPIN_GATE
        self.ml_gate = DEFAULT_ML_GATE
        self.entropy_gate = DEFAULT_ENTROPY_GATE
        self.shadow_signals = 0; self.shadow_tp = 0; self._shadow_pvalue = 1.0

    def load_models(self, symbol: str = "BTC_USDT_USDT") -> bool:
        from shared.hub import IntelligenceHub
        self.ml_model = ReversalModel()
        if not self.ml_model.load(symbol=symbol): return False
        hmm_path = os.path.join(self.models_dir, f"{symbol}_hmm.json")
        if os.path.exists(hmm_path):
            with open(hmm_path) as f:
                self._hmm_entropy = json.load(f).get("entropy", 0.5)
        logger.info(f"VetoSystem loaded: {symbol}")
        return True

    def evaluate_signal(self, direction: str, symbol: str = "BTC",
                        source: str = "discretionary", features_df=None,
                        vpin: float = 0.5, obi: float = 0.0, basis: float = 0.0,
                        entry_price: float = None, timesfm_sign: int = 0,
                        timesfm_align: float = 0.0) -> VetoResult:
        """Full 4-gate evaluation with adaptive thresholds."""

        r = VetoResult(symbol=symbol, direction=direction.upper(), source=source,
                       timestamp=datetime.now(timezone.utc).isoformat(),
                       vpin=vpin, obi=obi, basis=basis, tfm_sign=timesfm_sign,
                       tfm_align=timesfm_align)
        r.hmm_entropy = self._hmm_entropy
        veto, warn = [], []

        # ── SKILL 1: adapt thresholds to regime ──────────────
        self._adapt_thresholds(r.hmm_entropy)

        # ── Gate 1: ML ──────────────────────────────────────
        if self.ml_model and features_df is not None and not features_df.empty:
            try:
                X = features_df[self.ml_model.feature_names].fillna(0)
                r.ml_probability = float(self.ml_model.predict(X.iloc[[-1]])[0])
                if direction == "LONG" and r.ml_probability < self.ml_gate:
                    r.ml_verdict = "VETO"; veto.append(f"ML={r.ml_probability:.1%}<{self.ml_gate:.0%}")
                elif direction == "SHORT" and r.ml_probability > (1 - self.ml_gate):
                    r.ml_verdict = "VETO"; veto.append(f"ML reversal risk for SHORT")
                elif direction == "LONG" and r.ml_probability < 0.55:
                    r.ml_verdict = "WARNING"; warn.append(f"ML weak: {r.ml_probability:.1%}")
                else:
                    r.ml_verdict = "CONFIRM"
            except Exception as e:
                logger.warning(f"ML check: {e}")

        # ── Gate 2: HMM ─────────────────────────────────────
        if r.hmm_entropy > self.entropy_gate:
            r.hmm_verdict = "VETO"; veto.append(f"HMM entropy={r.hmm_entropy:.2f}>{self.entropy_gate}")
        elif r.hmm_entropy > 0.60:
            r.hmm_verdict = "WARNING"; warn.append(f"HMM elevated: {r.hmm_entropy:.2f}")
        else:
            r.hmm_verdict = "OK"

        # ── Gate 3: Microstructure ──────────────────────────
        if vpin < self.vpin_gate:
            veto.append(f"VPIN={vpin:.3f}<{self.vpin_gate}")
        if direction == "LONG" and basis > -0.03:
            warn.append(f"Basis={basis:.3f}% — weak accumulation")
        if abs(obi) < 0.30:
            warn.append(f"OBI={obi:.3f} — weak imbalance")
        r.micro_verdict = "VETO" if vpin < self.vpin_gate else ("WARNING" if len(warn) > 1 else "CONFIRM")

        # ── Gate 4: Market Entropy ──────────────────────────
        if features_df is not None and not features_df.empty:
            r.market_entropy = self._compute_entropy(features_df)
            if r.market_entropy > 0.75:
                r.entropy_blocked = True; veto.append(f"Mkt entropy={r.market_entropy:.2f} — block all")
            elif r.market_entropy > 0.60:
                warn.append(f"Mkt entropy: {r.market_entropy:.2f}")

        # ── SKILL 2: Anti-Hallucination ─────────────────────
        if timesfm_sign != 0 and r.ml_verdict == "CONFIRM":
            ml_reversal = r.ml_probability > 0.55
            tfm_continuation = (direction == "LONG" and timesfm_sign < 0) or \
                               (direction == "SHORT" and timesfm_sign > 0)
            if ml_reversal and tfm_continuation:
                r.anti_hallucination = True
                r.anti_hallucination_action = "REDUCE_SIZE — TimesFM confirms trend, ML reversal is likely noise"
                if r.final_verdict != "VETO":
                    warn.append("anti-hallucination: ML vs TimesFM diverge")

        # ── Final verdict ───────────────────────────────────
        if veto:
            r.final_verdict = "VETO"; r.kelly_multiplier = 0.0; r.max_size_pct = 0.0
            r.reason = " | ".join(veto)
        elif len(warn) >= 2:
            r.final_verdict = "REDUCE_SIZE"; r.kelly_multiplier = 0.25; r.max_size_pct = 0.05
            r.reason = " | ".join(warn)
        elif warn:
            r.final_verdict = "REDUCE_SIZE"; r.kelly_multiplier = 0.50; r.max_size_pct = 0.10
            r.reason = " | ".join(warn)
        else:
            r.final_verdict = "GO"; r.kelly_multiplier = 1.0; r.max_size_pct = 0.20
            r.reason = "All gates passed"

        r.telegram_message = self._fmt_tg(r, entry_price)
        return r

    # ══════════════════════════════════════════════════════════
    # SKILL 1: Regime-Specific Adaptive Thresholds
    # ══════════════════════════════════════════════════════════
    def _adapt_thresholds(self, hmm_entropy: float) -> None:
        """Adjust VPIN/ML/entropy gates based on HMM regime entropy."""
        if hmm_entropy > 0.65:  # HIGH_VOL / chaotic
            self.vpin_gate = HIGH_VOL_VPIN
            self.ml_gate = HIGH_VOL_ML
            self.entropy_gate = HIGH_VOL_ENTROPY
        else:  # LOW_VOL / trending
            self.vpin_gate = DEFAULT_VPIN_GATE
            self.ml_gate = DEFAULT_ML_GATE
            self.entropy_gate = DEFAULT_ENTROPY_GATE

    # ══════════════════════════════════════════════════════════
    # SKILL 2: Anti-Hallucination
    # ══════════════════════════════════════════════════════════
    def check_ml_tfm_divergence(self, direction: str, ml_prob: float,
                                 timesfm_sign: int) -> dict:
        """Detect ML vs TimesFM contradiction."""
        ml_says_reversal = ml_prob > 0.55
        tfm_agrees = (direction == "LONG" and timesfm_sign > 0) or \
                     (direction == "SHORT" and timesfm_sign < 0)
        if ml_says_reversal and not tfm_agrees and timesfm_sign != 0:
            return {"divergence": True, "action": "REDUCE_SIZE",
                    "reason": "ML reversal vs TimesFM continuation"}
        return {"divergence": False, "action": "OK", "reason": ""}

    # ══════════════════════════════════════════════════════════
    # SKILL 3: Regret Analysis (Cognitive Self-Awareness)
    # ══════════════════════════════════════════════════════════
    def log_regret(self, direction: str, verdict: str, ml_prob: float,
                   actual_outcome: int, reason: str = "") -> None:
        """Log vetoed trades that would have won. Triggers recalibration."""
        if verdict != "VETO":
            return
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "direction": direction,
                 "verdict": verdict, "ml_prob": ml_prob, "actual": actual_outcome, "reason": reason}
        self.regret_log.append(entry)

        if actual_outcome == 1:  # vetoed a winner
            self.regret_count += 1
            if self.regret_count >= REGRET_RECAL_THRESHOLD:
                self._recalibrate_from_regret()
        else:  # good veto
            self.regret_count = max(0, self.regret_count - 1)

    def _recalibrate_from_regret(self) -> None:
        """Loosen thresholds when too many winners are being vetoed."""
        old_vpin = self.vpin_gate
        old_ml = self.ml_gate
        self.vpin_gate = max(0.50, self.vpin_gate - 0.05)
        self.ml_gate = max(0.35, self.ml_gate - 0.03)
        self.regret_count = 0
        logger.warning(
            f"[RegretRecal] {REGRET_RECAL_THRESHOLD} vetoed winners → "
            f"loosening gates: VPIN {old_vpin:.2f}→{self.vpin_gate:.2f}, "
            f"ML {old_ml:.2f}→{self.ml_gate:.2f}"
        )

    # ══════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════
    def _compute_entropy(self, features_df) -> float:
        expanded = ["rsi_14", "rsi_6", "price_position_50", "price_position_100",
                     "hl_ratio_20", "hl_ratio_100", "bb_width_20", "volume_trend_20", "cvd_accel"]
        avail = [f for f in expanded if f in features_df.columns]
        if not avail: return 0.5
        recent = features_df[avail].iloc[-20:]
        if len(recent) < 5: return 0.5
        stds = recent.std() / (recent.mean().abs() + 1e-9)
        return float(np.clip(stds.mean() / 5.0, 0.0, 1.0))

    def _fmt_tg(self, r: VetoResult, entry_price: float = None) -> str:
        emoji = "🛑" if r.final_verdict == "VETO" else ("⚠️" if "REDUCE" in r.final_verdict else "✅")
        header = {"VETO": "TRADE VETOED", "REDUCE_SIZE": "APPROVED — REDUCED SIZE"}.get(r.final_verdict, "APPROVED")
        price = f"   Entry: ${entry_price:,.2f}\n" if entry_price else ""
        return (
            f"{emoji} {r.symbol} — {r.direction} — {header}\n"
            f"   Source: {r.source}\n{price}"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"   ML:    {r.ml_probability:.1%} ({r.ml_verdict})\n"
            f"   HMM:   entropy={r.hmm_entropy:.2f} ({r.hmm_verdict})\n"
            f"   VPIN:  {r.vpin:.3f}  OBI: {r.obi:+.3f}  Basis: {r.basis:+.3f}%\n"
            f"   TF:    {'🟢' if r.tfm_sign > 0 else '🔴' if r.tfm_sign < 0 else '⚪'} "
            f"align={r.tfm_align:.2f}\n"
            f"   MktEnt: {r.market_entropy:.2f}\n"
            f"{'   ⚡ Anti-Hallucination: ' + r.anti_hallucination_action + chr(10) if r.anti_hallucination else ''}"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"   Kelly: {r.kelly_multiplier:.0%} | Size: {r.max_size_pct:.0%}\n"
            f"   {r.reason}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )

    def shadow_log_signal(self, direction: str, ml_prob: float, outcome: int = None):
        self.shadow_signals += 1
        if outcome == 1: self.shadow_tp += 1
        if self.shadow_signals % 200 == 0:
            from scipy.stats import binomtest
            self._shadow_pvalue = binomtest(self.shadow_tp, self.shadow_signals, p=0.5, alternative="greater").pvalue
            logger.info(f"[SHADOW] {self.shadow_signals} signals, p={self._shadow_pvalue:.4f}")

    def shadow_status(self) -> dict:
        return {"signals": self.shadow_signals, "tp": self.shadow_tp,
                "win_rate": self.shadow_tp / max(self.shadow_signals, 1),
                "p_value": self._shadow_pvalue, "edge": self._shadow_pvalue < 0.05}
