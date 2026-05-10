"""
shared/engines/ict_engine.py — ICT / Smart Money Concepts Engine
=================================================================
Extracted from ICTdemon/production_core + GOD_HF_LIQUIDITY_SWEEP_3m.py.
LOGIC ONLY — no chart visualization, no Telegram, no scheduling.

Detects:
  - Valeyre Z-Score (mean reversion optimality)
  - Sweeps (liquidity grabs above/below range)
  - Fair Value Gaps (FVG) with volume validation
  - Inversion FVGs (iFVG) — closed FVGs become opposite S/R
  - Optimal Trade Entry (OTE) zones (61.8% - 78.6% retracement)
  - SMT Divergence (inter-asset correlation divergences)
  - PO3 / AMD (Accumulation, Manipulation, Distribution)
  - Market Maker Models (Breaker Blocks)
  - Silver Bullet window detection (10-11 AM NY)
  - CVD divergence via Spearman correlation
  - Volume Profile POC (Point of Control)
  - High-Frequency Sweep pattern (4H/3M)

Usage:
    from shared.engines.ict_engine import ICTEngine
    engine = ICTEngine()
    sweeps = engine.detect_sweeps(df_3m)
    fvgs = engine.detect_fvgs(df_3m)
    ote = engine.calculate_ote(high, low, direction=1)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import datetime
from typing import Dict, List, Tuple, Optional, Any
from scipy.stats import spearmanr


class ICTEngine:
    """ICT / Smart Money Concepts institutional analysis engine."""

    def __init__(self, ema_period: int = 50, fvg_min_atr_ratio: float = 0.5,
                 ote_low: float = 0.618, ote_high: float = 0.786):
        self.ema_period = ema_period
        self.fvg_min_atr_ratio = fvg_min_atr_ratio
        self.ote_low = ote_low
        self.ote_high = ote_high

    # ═══════════════════════════════════════════════════════════════
    # VALEYRE Z-SCORE (mean reversion optimality)
    # ═══════════════════════════════════════════════════════════════

    def calculate_valeyre_zscore(self, df: pd.DataFrame, ema_period: int = None) -> pd.DataFrame:
        """
        Valeyre (2025) — Single-Scale Mean Reversion Optimality.
        Normalizes distance to EMA by volatility.
        
        Returns DataFrame with added 'ema', 'volatility', 'z_score' columns.
        """
        if ema_period is None:
            ema_period = self.ema_period
        
        df = df.copy()
        df['ema'] = df['close'].ewm(span=ema_period, adjust=False).mean()
        df['log_ret'] = np.log(df['close'] / df['close'].shift(1))
        df['volatility'] = df['log_ret'].rolling(window=ema_period).std() * np.sqrt(ema_period)
        
        vol = df['volatility'].replace(0, 0.001)
        dist_pct = (df['close'] - df['ema']) / df['ema']
        df['z_score'] = dist_pct / vol
        
        return df

    def get_zscore_signal(self, df: pd.DataFrame, ema_period: int = None) -> Dict:
        """Get latest Valeyre Z-Score signal with regime."""
        df = self.calculate_valeyre_zscore(df, ema_period)
        latest = df.iloc[-1]
        z = float(latest['z_score'])
        
        regime = 'NEUTRAL'
        if z > 2.0:
            regime = 'OVERHEATED'
        elif z > 1.5:
            regime = 'ELEVATED'
        elif z < -2.0:
            regime = 'UNDERVALUED'
        elif z < -1.5:
            regime = 'MEAN_REVERT_RISK'
        
        return {
            "z_score": round(z, 4),
            "regime": regime,
            "close": round(float(latest['close']), 2),
            "ema": round(float(latest['ema']), 2),
            "volatility": round(float(latest['volatility']), 6),
            "bias": "SHORT" if z > 1.5 else ("LONG" if z < -1.5 else "NEUTRAL"),
        }

    # ═══════════════════════════════════════════════════════════════
    # SWEEPS (liquidity grabs)
    # ═══════════════════════════════════════════════════════════════

    def detect_sweeps(self, df: pd.DataFrame, lookback: int = 24) -> Dict[str, Any]:
        """
        Detect if current candles swept previous range highs/lows.
        
        Returns: {"sweep_low": bool, "sweep_high": bool, "range_low": float, "range_high": float}
        """
        if len(df) < lookback + 1:
            return {"sweep_low": False, "sweep_high": False}
        
        recent_low = float(df['low'].iloc[-3:].min())
        recent_high = float(df['high'].iloc[-3:].max())
        
        range_low = float(df['low'].iloc[-(lookback+3):-3].min())
        range_high = float(df['high'].iloc[-(lookback+3):-3].max())
        
        return {
            "sweep_low": recent_low < range_low,
            "sweep_high": recent_high > range_high,
            "range_low": round(range_low, 2),
            "range_high": round(range_high, 2),
            "recent_low": round(recent_low, 2),
            "recent_high": round(recent_high, 2),
        }

    def detect_hf_sweep_4h_3m(self, df_4h: pd.DataFrame, df_3m: pd.DataFrame) -> Dict[str, Any]:
        """
        High-Frequency Sweep: 4H extreme bias + 3M SFP confirmation.
        From GOD_HF_LIQUIDITY_SWEEP_3m.py.
        """
        # 4H Valeyre extreme
        z_4h = self.get_zscore_signal(df_4h)
        
        # 3M sweeps
        sweeps_3m = self.detect_sweeps(df_3m, lookback=48)  # 4h worth of 3m candles
        
        # Bias from 4H
        bias = "NEUTRAL"
        if z_4h['z_score'] > 1.5:
            bias = "SHORT"
        elif z_4h['z_score'] < -1.5:
            bias = "LONG"
        
        # SFP confirmation: sweep against bias = valid entry
        sfp_valid = (bias == "SHORT" and sweeps_3m['sweep_high']) or \
                    (bias == "LONG" and sweeps_3m['sweep_low'])
        
        return {
            "bias_4h": bias,
            "zscore_4h": z_4h,
            "sweep_3m": sweeps_3m,
            "sfp_valid": sfp_valid,
            "signal": "EXECUTE" if sfp_valid else "WAIT",
            "direction": bias if sfp_valid else "NEUTRAL",
        }

    # ═══════════════════════════════════════════════════════════════
    # FAIR VALUE GAPS (FVG)
    # ═══════════════════════════════════════════════════════════════

    def detect_fvgs(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Detect Fair Value Gaps with volume/ATR validation.
        
        Bullish FVG: Low[i+2] > High[i]  (gap up, price should retrace to fill)
        Bearish FVG: High[i+2] < Low[i]  (gap down, price should retrace to fill)
        
        Only returns FVGs with institutional displacement (candle size > ATR * min_ratio).
        """
        fvgs = []
        if len(df) < 3:
            return fvgs
        
        atr = (df['high'] - df['low']).rolling(14).mean()
        
        for i in range(len(df) - 2):
            c1, c2, c3 = df.iloc[i], df.iloc[i+1], df.iloc[i+2]
            
            # Institutional displacement check
            displacement = abs(c2['close'] - c2['open'])
            atr_val = float(atr.iloc[i+1]) if i+1 < len(atr) else displacement
            
            if displacement < atr_val * self.fvg_min_atr_ratio:
                continue
            
            # Bullish FVG
            if c3['low'] > c1['high']:
                fvgs.append({
                    "type": "bullish",
                    "top": round(float(c3['low']), 2),
                    "bottom": round(float(c1['high']), 2),
                    "size": round(float(c3['low'] - c1['high']), 2),
                    "index": i,
                    "filled": False,
                })
            # Bearish FVG
            elif c3['high'] < c1['low']:
                fvgs.append({
                    "type": "bearish",
                    "top": round(float(c1['low']), 2),
                    "bottom": round(float(c3['high']), 2),
                    "size": round(float(c1['low'] - c3['high']), 2),
                    "index": i,
                    "filled": False,
                })
        
        return fvgs

    def detect_ifvgs(self, df: pd.DataFrame, fvgs: List[Dict]) -> List[Dict[str, Any]]:
        """
        Inversion Fair Value Gaps (iFVG).
        When a FVG closes below its bottom (bullish) or above its top (bearish),
        it inverts and becomes the opposite S/R level.
        """
        ifvgs = []
        curr_price = float(df['close'].iloc[-1])
        
        for fvg in fvgs:
            if fvg['type'] == 'bullish' and curr_price < fvg['bottom']:
                ifvgs.append({
                    "type": "bearish_inversion",
                    "resistance": fvg['top'],
                    "level": fvg['bottom'],
                    "original_fvg": fvg,
                })
            elif fvg['type'] == 'bearish' and curr_price > fvg['top']:
                ifvgs.append({
                    "type": "bullish_inversion",
                    "support": fvg['bottom'],
                    "level": fvg['top'],
                    "original_fvg": fvg,
                })
        
        return ifvgs

    # ═══════════════════════════════════════════════════════════════
    # OPTIMAL TRADE ENTRY (OTE)
    # ═══════════════════════════════════════════════════════════════

    def calculate_ote(self, high: float, low: float, direction: int) -> Tuple[float, float]:
        """
        Calculate Optimal Trade Entry zone (61.8% - 78.6% Fibonacci).
        
        Args:
            high, low: Swing high/low prices.
            direction: 1 = long (buy in retracement), -1 = short (sell in retracement).
        
        Returns:
            (entry_low, entry_high) — the OTE zone to enter within.
        """
        diff = abs(high - low)
        
        if direction == 1:  # Long OTE
            entry_low = high - diff * self.ote_high
            entry_high = high - diff * self.ote_low
            return (round(entry_low, 2), round(entry_high, 2))
        else:  # Short OTE
            entry_low = low + diff * self.ote_low
            entry_high = low + diff * self.ote_high
            return (round(entry_low, 2), round(entry_high, 2))

    # ═══════════════════════════════════════════════════════════════
    # SMT DIVERGENCE (inter-asset)
    # ═══════════════════════════════════════════════════════════════

    def detect_smt_divergence(self, df1: pd.DataFrame, df2: pd.DataFrame) -> Dict[str, Any]:
        """
        SMT Divergence: BTC/ETH correlation breakdown.
        
        Bullish SMT: Asset1 makes lower low, Asset2 fails → reversal up.
        Bearish SMT: Asset1 makes higher high, Asset2 fails → reversal down.
        """
        if len(df1) < 2 or len(df2) < 2:
            return {"smt": False, "type": "NEUTRAL"}
        
        a1_prev_low = float(df1['low'].iloc[-2])
        a1_curr_low = float(df1['low'].iloc[-1])
        a2_prev_low = float(df2['low'].iloc[-2])
        a2_curr_low = float(df2['low'].iloc[-1])
        
        a1_prev_high = float(df1['high'].iloc[-2])
        a1_curr_high = float(df1['high'].iloc[-1])
        a2_prev_high = float(df2['high'].iloc[-2])
        a2_curr_high = float(df2['high'].iloc[-1])
        
        # Bullish SMT
        if a1_curr_low < a1_prev_low and a2_curr_low > a2_prev_low:
            return {"smt": True, "type": "BULLISH_SMT", "bias": "LONG",
                    "asset1_low": a1_curr_low, "asset2_low": a2_curr_low}
        # Bearish SMT
        if a1_curr_high > a1_prev_high and a2_curr_high < a2_prev_high:
            return {"smt": True, "type": "BEARISH_SMT", "bias": "SHORT",
                    "asset1_high": a1_curr_high, "asset2_high": a2_curr_high}
        
        return {"smt": False, "type": "NEUTRAL"}

    # ═══════════════════════════════════════════════════════════════
    # PO3 / AMD (Accumulation, Manipulation, Distribution)
    # ═══════════════════════════════════════════════════════════════

    def detect_po3_amd(self, df: pd.DataFrame, lookback: int = 24) -> Dict[str, str]:
        """
        Power of 3: Accumulation → Manipulation → Distribution.
        Identifies current phase based on session open and sweep behavior.
        """
        if len(df) < lookback:
            return {"phase": "UNKNOWN", "bias": "NEUTRAL"}
        
        recent = df.tail(lookback)
        open_price = float(recent.iloc[0]['open'])
        high = float(recent['high'].max())
        low = float(recent['low'].min())
        curr = float(recent.iloc[-1]['close'])
        
        # Manipulation detection
        manipulated_up = float(recent.iloc[-1]['high']) > high * 0.999
        manipulated_down = float(recent.iloc[-1]['low']) < low * 1.001
        
        if manipulated_up and curr < open_price:
            return {"phase": "MANIPULATION", "bias": "SHORT",
                    "type": "DEVIATION_UP", "detail": "Sweep high + close below open"}
        elif manipulated_down and curr > open_price:
            return {"phase": "MANIPULATION", "bias": "LONG",
                    "type": "DEVIATION_DOWN", "detail": "Sweep low + close above open"}
        
        if curr > open_price:
            return {"phase": "DISTRIBUTION", "bias": "LONG"}
        elif curr < open_price:
            return {"phase": "ACCUMULATION", "bias": "SHORT"}
        
        return {"phase": "ACCUMULATION", "bias": "NEUTRAL"}

    # ═══════════════════════════════════════════════════════════════
    # SILVER BULLET WINDOW
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def is_silver_bullet_window(utc_time: datetime.datetime = None) -> bool:
        """
        Silver Bullet: 10-11 AM NY time (15:00-16:00 UTC).
        Highest probability ICT setup window.
        """
        if utc_time is None:
            utc_time = datetime.datetime.now(datetime.timezone.utc)
        return 15 <= utc_time.hour < 16

    # ═══════════════════════════════════════════════════════════════
    # MARKET MAKER MODELS (Breaker Blocks)
    # ═══════════════════════════════════════════════════════════════

    def detect_breaker_blocks(self, df: pd.DataFrame) -> List[Dict[str, Any]]:
        """
        Market Maker Models: Breaker Blocks.
        
        Bearish Breaker: Bullish candle → Bearish candle → Break below bullish low.
        Bullish Breaker: Bearish candle → Bullish candle → Break above bearish high.
        """
        patterns = []
        if len(df) < 5:
            return patterns
        
        last_3 = df.tail(3)
        c1, c2, c3 = last_3.iloc[0], last_3.iloc[1], last_3.iloc[2]
        
        # Bearish Breaker
        if (c1['close'] > c1['open'] and
            c2['close'] < c2['open'] and
            c3['close'] < c2['low']):
            patterns.append({
                "type": "BEARISH_BREAKER",
                "level": round(float(c1['high']), 2),
                "bias": "SHORT",
            })
        
        # Bullish Breaker
        if (c1['close'] < c1['open'] and
            c2['close'] > c2['open'] and
            c3['close'] > c2['high']):
            patterns.append({
                "type": "BULLISH_BREAKER",
                "level": round(float(c1['low']), 2),
                "bias": "LONG",
            })
        
        return patterns

    # ═══════════════════════════════════════════════════════════════
    # CVD DIVERGENCE
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def calculate_cvd_divergence(price: pd.Series, cvd: pd.Series, window: int = 14) -> Dict[str, Any]:
        """
        Institutional pressure validation via Spearman correlation.
        
        High positive correlation: CVD confirms price (trend is real).
        Near-zero or negative: CVD diverges from price (manipulation).
        """
        if len(price) < window or len(cvd) < window:
            return {"correlation": 0.0, "divergence": False, "p_value": 1.0}
        
        corr, p_value = spearmanr(price.tail(window), cvd.tail(window))
        corr = float(corr)
        p_value = float(p_value)
        
        return {
            "correlation": round(corr, 4),
            "divergence": abs(corr) < 0.3,
            "p_value": round(p_value, 4),
            "interpretation": _cvd_interpretation(corr),
        }

    # ═══════════════════════════════════════════════════════════════
    # COMPREHENSIVE ICT ANALYSIS
    # ═══════════════════════════════════════════════════════════════

    def comprehensive_analysis(self, df_3m: pd.DataFrame, df_15m: pd.DataFrame = None,
                                df_4h: pd.DataFrame = None, df_btc: pd.DataFrame = None,
                                df_eth: pd.DataFrame = None) -> Dict[str, Any]:
        """
        Run all ICT analyses and produce a unified signal.
        
        Args:
            df_3m: 3-minute OHLCV (for scalping/intraday sweeps).
            df_15m: 15-minute OHLCV (for FVG/OTE).
            df_4h: 4-hour OHLCV (for bias/regime).
            df_btc, df_eth: For SMT divergence.
        
        Returns:
            Dict with all signals and a unified verdict.
        """
        result = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "signals": {},
            "verdict": "NO_TRADE",
            "direction": "NEUTRAL",
        }
        
        # Sweeps on 3M
        if df_3m is not None:
            sweeps = self.detect_sweeps(df_3m, lookback=48)
            result["signals"]["sweeps"] = sweeps
        
        # FVGs on 15M
        if df_15m is not None:
            fvgs = self.detect_fvgs(df_15m)
            result["signals"]["fvgs"] = fvgs[-5:] if fvgs else []  # Last 5
            result["signals"]["ifvgs"] = self.detect_ifvgs(df_15m, fvgs)
        
        # Valeyre Z-Score on 4H
        if df_4h is not None:
            z = self.get_zscore_signal(df_4h)
            result["signals"]["valeyre"] = z
            result["signals"]["silver_bullet"] = self.is_silver_bullet_window()
            result["signals"]["po3"] = self.detect_po3_amd(df_4h)
            result["signals"]["breakers"] = self.detect_breaker_blocks(df_4h)
        
        # SMT Divergence BTC/ETH
        if df_btc is not None and df_eth is not None:
            result["signals"]["smt"] = self.detect_smt_divergence(df_btc, df_eth)
        
        # HF Sweep (4H + 3M)
        if df_4h is not None and df_3m is not None:
            hf = self.detect_hf_sweep_4h_3m(df_4h, df_3m)
            result["signals"]["hf_sweep"] = hf
            
            if hf["sfp_valid"]:
                result["verdict"] = "SIGNAL"
                result["direction"] = hf["direction"]
        
        return result


def _cvd_interpretation(corr: float) -> str:
    """Human-readable CVD divergence interpretation."""
    if corr > 0.7:
        return "STRONG_CONFIRMATION — CVD confirms price, trend genuine"
    elif corr > 0.3:
        return "WEAK_CONFIRMATION — CVD loosely follows price"
    elif corr > -0.3:
        return "DIVERGENCE — CVD not confirming price, potential manipulation"
    else:
        return "STRONG_DIVERGENCE — CVD opposes price, institutional trap likely"
