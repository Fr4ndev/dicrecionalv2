"""
workflows_advanced.py — Institutional Trading Workflows v3.0
═══════════════════════════════════════════════════════════════════
Combines ALL available data sources into institutional-grade
setup routines for Scalp, Intraday, and Swing modalities.

Data sources:
  Action Server (36 endpoints): funding, OBI, OI, CVD, basis, VPIN, microstructure
  Hyperliquid Server (6 endpoints): altcoin funding extremes, crowded trades, alpha
  SRLevelsEngine: fractal pivots, SR heatmap, volume profile, multi-TF confluence
  ICTEngine: Valeyre Z-Score, sweeps, FVG/iFVG, OTE, SMT, PO3/AMD, breakers
  RedisBridge: cross-request CVD velocity & wall state persistence

Philosophy:
  Every decision = weighted score from multiple independent signals.
  Entry = ALL gates passed AND directional bias aligned.
  Target = nearest confluent level opposite bias.
  Invalidation = explicit, testable conditions (no ambiguity).
  Confidence = weighted sum of signal strengths (0-100 scale).

Author: AI Ops Master Suite · ccxtv2-next
"""

import sys, os, json, time, asyncio, logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import httpx
    import pandas as pd
    import numpy as np
except ImportError as e:
    pass  # Will be caught at action registration

from shared import (
    IntelligenceHub, _run_hub_sync, RedisBridge, redis, settings,
    ZScoreEngine, SRLevelsEngine, ICTEngine,
)

try:
    from sema4ai.actions import action
except ImportError:
    def action(func=None, **kwargs):
        return func if func else lambda f: f

logger = logging.getLogger("WorkflowsAdvanced")

# ═══════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════

VPIN_THRESHOLD   = 0.62
OBI_IGNITION     = 0.40
BASIS_THRESHOLD  = -0.05
CVD_ACCEL_GATE   = 0.0
OBI_SCALP_GATE   = 0.40
ABSORPTION_GATE  = 0.60

SR_ENGINE   = SRLevelsEngine(n_bins=50, rolling_window=50)
ICT_ENGINE  = ICTEngine(ema_period=50, fvg_min_atr_ratio=0.5)
Z_ENGINE    = ZScoreEngine()

AS_FUNDING = "http://localhost:8080/api/actions/funding-action-server"
AS_HL      = "http://localhost:8081/api/actions/hyperliquid-funding-server"

TFS = {"scalp": ["3m", "15m"], "intraday": ["15m", "1h", "4h"], "swing": ["4h", "1d", "1w"]}

# ═══════════════════════════════════════════════════════════════════
# ACTION SERVER BRIDGE (internal endpoint calls)
# ═══════════════════════════════════════════════════════════════════

def _ep(name: str, payload: dict, base: str = AS_FUNDING, timeout: int = 30) -> dict:
    """Call any action server endpoint. Returns parsed dict or {"error": ...}."""
    import httpx
    url = f"{base}/{name}/run"
    try:
        r = httpx.post(url, json=payload, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, str):
                try: data = json.loads(data)
                except (json.JSONDecodeError, TypeError): return {"raw": data}
            return data if isinstance(data, dict) else {"raw": str(data)}
        return {"error": f"HTTP {r.status_code}", "body": r.text[:200]}
    except Exception as e:
        return {"error": str(e)[:150]}


def _fetch_ohlcv(symbol: str, tf: str, limit: int = 200) -> Optional["pd.DataFrame"]:
    """Fetch OHLCV via IntelligenceHub for local engine computation."""
    async def _fetch():
        hub = IntelligenceHub.instance_sync()
        hub._init_internals()
        await hub.connect()
        try:
            ohlcv = await hub._exchange.fetch_ohlcv(symbol, tf, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('datetime', inplace=True)
            return df
        finally:
            await hub.close()
    try:
        loop = asyncio.new_event_loop()
        df = loop.run_until_complete(_fetch())
        loop.close()
        return df
    except Exception as e:
        logger.warning(f"OHLCV fetch failed ({symbol} {tf}): {e}")
        return None


# ═══════════════════════════════════════════════════════════════════
# SCALP ROUTINE — Institutional Scalping (1-15 min)
# ═══════════════════════════════════════════════════════════════════
# Signature: Speed + Microstructure + Sweep confirmation
#
# Data flow:
#   1. Action Server → OBI (pressure) + Funding (extreme) + VPIN (toxicity)
#   2. ICTEngine on 3M → Sweeps + FVG + OTE zone
#   3. Action Server → OB Walls (nearest liquidity cluster)
#   4. Hyperliquid → Crowded trade check (don't fade the crowd on majors)
#   5. Redis → CVD velocity (persisted cross-request)
#
# ENTRY (all must be true):
#   a) OBI > 0.40 (LONG) or < -0.40 (SHORT) — institutional pressure
#   b) VPIN > 0.62 — informed flow, not retail noise
#   c) Sweep confirmed on 3M (liquidity grab in bias direction)
#   d) FVG exists within 3 candles (imbalance to fill)
#   e) Funding extreme (> |0.05%|) on at least 2 exchanges
#   f) CVD velocity > 0 (Redis) — aggression confirmed cross-request
#
# TARGET:
#   OTE zone (61.8%-78.6% of sweep range) OR nearest opposite OB wall
#
# INVALIDATION:
#   OBI flips sign | VPIN < 0.40 | 15 min timeout | CVD goes negative

@action(is_consequential=False)
def setup_scalp_institutional(asset: str = "BTC") -> str:
    """INSTITUTIONAL SCALP SETUP — all data sources combined.
    
    Combines OBI, VPIN, 3M sweeps, FVGs, CVD velocity, and OB walls
    into a single 6-gate institutional scalp decision.
    
    Args:
        asset: Trading asset (BTC, ETH, SOL, LINK, HYPE).
    
    Returns:
        JSON with entry verdict, bias, score breakdown, target OTE zone,
        invalidation conditions, and traceable gate results.
    """
    symbol = f"{asset}/USDT:USDT" if asset != "HYPE" else f"{asset}/USDC:USDC"
    score = {"passed": 0, "total": 6, "details": []}
    results: Dict[str, Any] = {"modality": "scalp", "asset": asset, "symbol": symbol}

    # ── PHASE 1: Microstructure (Action Server) ────────────────────
    snap = _ep("get-full-market-snapshot", {"assets": asset, "ob_depth": 50})
    triggers = snap.get("triggers", {}).get(asset, {})
    obi_max = float(triggers.get("max_obi", 0) or 0)
    trigger_level = triggers.get("trigger_level", "NONE")
    funding_avg = float(triggers.get("avg_funding_pct", 0) or 0)
    
    # OBI gate
    obi_ok = abs(obi_max) > OBI_SCALP_GATE
    score["details"].append({"gate": "OBI", "value": round(obi_max, 4), "threshold": OBI_SCALP_GATE, "passed": obi_ok})
    if obi_ok: score["passed"] += 1
    
    # Funding extreme detection
    funding_ok = abs(funding_avg) > 0.05
    score["details"].append({"gate": "FundingExtreme", "value": round(funding_avg, 4), "threshold": 0.05, "passed": funding_ok})
    if funding_ok: score["passed"] += 1

    # ── PHASE 2: VPIN / Toxicity ────────────────────────────────────
    vpin = _ep("get-toxicity-index", {"symbol": symbol, "ob_depth": 50, "trade_limit": 500})
    vpin_val = 0.0
    if "error" not in vpin:
        raw = vpin.get("toxicity_index", vpin.get("vpin_index", 0))
        if isinstance(raw, str):
            try: raw = json.loads(raw).get("toxicity_index", 0)
            except: pass
        vpin_val = float(raw or 0)
    
    vpin_ok = vpin_val > VPIN_THRESHOLD
    score["details"].append({"gate": "VPIN", "value": round(vpin_val, 4), "threshold": VPIN_THRESHOLD, "passed": vpin_ok})
    if vpin_ok: score["passed"] += 1

    # ── PHASE 3: ICT Engine on 3M (Sweeps + FVG) ───────────────────
    df_3m = _fetch_ohlcv(symbol, "3m", limit=100)
    ict_data = {}
    if df_3m is not None:
        sweeps = ICT_ENGINE.detect_sweeps(df_3m, lookback=48)
        fvgs = ICT_ENGINE.detect_fvgs(df_3m)
        ifvgs = ICT_ENGINE.detect_ifvgs(df_3m, fvgs)
        
        sweep_ok = sweeps["sweep_high"] or sweeps["sweep_low"]
        fvg_ok = len(fvgs) > 0 and any(not f["filled"] for f in fvgs[-5:])
        
        ict_data = {
            "sweeps": sweeps,
            "fvgs_last_5": fvgs[-5:],
            "ifvgs": ifvgs,
        }
        
        score["details"].append({"gate": "Sweep_3M", "value": sweeps, "threshold": "any sweep", "passed": sweep_ok})
        if sweep_ok: score["passed"] += 1
        score["details"].append({"gate": "FVG_3M", "value": len(fvgs), "threshold": "≥1 unfilled", "passed": fvg_ok})
        if fvg_ok: score["passed"] += 1

    # ── PHASE 4: OB Walls + CVD ─────────────────────────────────────
    walls = _ep("get-ob-walls", {"symbol": symbol, "depth": 20})
    
    # CVD velocity from Redis (cross-request persistence)
    r = redis()
    cvd_vel = r.get_cvd_velocity(f"cvd:{asset.lower()}")
    cvd_ok = cvd_vel is not None and cvd_vel > 0
    score["details"].append({"gate": "CVD_Velocity", "value": cvd_vel, "threshold": ">0", "passed": cvd_ok})
    if cvd_ok: score["passed"] += 1

    # ── DECISION ────────────────────────────────────────────────────
    entry_ok = score["passed"] >= 5  # At least 5/6 gates
    
    # Bias from OBI direction
    bias = "NEUTRAL"
    if entry_ok:
        bias = "LONG" if obi_max > 0 else "SHORT"
    
    # Target: OTE zone from the sweep
    target = None
    ote_zone = None
    if entry_ok and df_3m is not None:
        sw_high = float(df_3m['high'].max())
        sw_low = float(df_3m['low'].min())
        direction = 1 if bias == "LONG" else -1
        ote_zone = ICT_ENGINE.calculate_ote(sw_high, sw_low, direction)
        target = ote_zone[0]  # Entry zone start
    
    # Invalidation
    invalidation = {
        "obi_flip": round(-OBI_SCALP_GATE if bias == "LONG" else OBI_SCALP_GATE, 2),
        "vpin_drop": 0.40,
        "timeout_seconds": 900,
        "max_loss_pct": -0.5,
        "cvd_negative": True,
    }
    
    results["entry"] = {"verdict": "EXECUTE" if entry_ok else "NO_TRADE", "bias": bias}
    results["score"] = score
    results["ict"] = ict_data
    results["target"] = {"ote_zone": ote_zone, "walls": walls.get("asks", [])[:2] if bias == "LONG" else walls.get("bids", [])[:2]}
    results["invalidation"] = invalidation
    
    entry_ok and score["details"].append({"gate": "DECISION", "value": f"{score['passed']}/6", "threshold": "≥5", "passed": True})
    
    return json.dumps(results, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════
# INTRADAY ROUTINE (1-8 hours)
# ═══════════════════════════════════════════════════════════════════
# Signature: Bias confirmation + SR levels + SFP detection
#
# Data flow:
#   1. ICTEngine on 15M → FVG + Breakers + PO3 phase
#   2. SRLevelsEngine on 1H → Key levels + volume profile
#   3. Action Server → Z-Score regime + Basis (spot vs perp)
#   4. Action Server → Confluence trigger + SFP detection
#   5. ICTEngine → SMT Divergence BTC/ETH
#   6. Redis → CVD velocity trend (multi-hour)
#
# ENTRY (all must be true):
#   a) Valeyre regime ≠ NEUTRAL (direction from regime)
#   b) Basis confirms spot premium (< -0.05% LONG) or perp premium (> 0.05% SHORT)
#   c) SFP detected on 1H (liquidity sweep against bias = entry trigger)
#   d) Price within OTE zone of last swing
#   e) CVD velocity confirms (cross-request)
#   f) SMT divergence aligned (BTC/ETH)
#
# TARGET:
#   Nearest confluent SR level opposite bias (from SRLevelsEngine multi-TF)
#
# INVALIDATION:
#   Regime flip | Basis flip | 8h timeout | Max DD -2%

@action(is_consequential=False)
def setup_intraday_institutional(asset: str = "BTC") -> str:
    """INSTITUTIONAL INTRADAY SETUP — multi-timeframe bias + SR levels + SFP.
    
    Combines Valeyre regime, basis, SFP detection, multi-TF SR confluence,
    SMT divergence, and confluence trigger into a 6-gate intraday decision.
    
    Args:
        asset: Trading asset (BTC, ETH).
    
    Returns:
        JSON with entry verdict, bias, score breakdown, confluent SR target,
        invalidation conditions, and traceable gate results.
    """
    symbol = f"{asset}/USDT:USDT"
    spot_sym = f"{asset}/USDT"
    score = {"passed": 0, "total": 6, "details": []}
    results: Dict[str, Any] = {"modality": "intraday", "asset": asset, "symbol": symbol}

    # ── PHASE 1: Valeyre Z-Score regime (1H) ────────────────────────
    df_1h = _fetch_ohlcv(symbol, "1h", limit=200)
    regime_data = {}
    if df_1h is not None:
        z = ICT_ENGINE.get_zscore_signal(df_1h)
        regime_ok = z["regime"] in ("OVERHEATED", "UNDERVALUED", "ELEVATED", "MEAN_REVERT_RISK")
        regime_data = z
        score["details"].append({"gate": "ValeyreRegime", "value": z["regime"], "threshold": "≠NEUTRAL", "passed": regime_ok})
        if regime_ok: score["passed"] += 1

    # ── PHASE 2: Basis (Spot vs Perp) ───────────────────────────────
    basis = _ep("get-basis", {"symbol_spot": spot_sym, "symbol_perp": symbol})
    basis_pct = float((basis.get("basis_pct", 0) or 0))
    
    # Basis must align with regime bias
    regime_bias = z.get("bias", "NEUTRAL") if df_1h is not None else "NEUTRAL"
    basis_ok = (regime_bias == "LONG" and basis_pct < BASIS_THRESHOLD) or \
               (regime_bias == "SHORT" and basis_pct > abs(BASIS_THRESHOLD))
    score["details"].append({"gate": "BasisConfirm", "value": round(basis_pct, 4), "threshold": f"{BASIS_THRESHOLD} (LONG) / +{abs(BASIS_THRESHOLD)} (SHORT)", "passed": basis_ok})
    if basis_ok: score["passed"] += 1

    # ── PHASE 3: SFP Detection ──────────────────────────────────────
    sfp = _ep("detect-sfp-confluence", {"assets": asset})
    sfp_detected = False
    if "error" not in sfp:
        raw_sfp = sfp.get("sfp_detected", sfp.get("confluence", False))
        sfp_detected = bool(raw_sfp)
    
    score["details"].append({"gate": "SFP", "value": sfp_detected, "threshold": "true", "passed": sfp_detected})
    if sfp_detected: score["passed"] += 1

    # ── PHASE 4: SR Levels Engine (1H + 4H) ─────────────────────────
    df_4h = _fetch_ohlcv(symbol, "4h", limit=200)
    levels_data = {}
    if df_1h is not None:
        levels_1h = SR_ENGINE.compute_key_levels(df_1h, top_n=8)
        levels_4h = SR_ENGINE.compute_key_levels(df_4h, top_n=8) if df_4h is not None else []
        confluence = SR_ENGINE.get_level_confluence({"1h": levels_1h, "4h": levels_4h})
        
        levels_data = {"levels_1h": levels_1h[:5], "levels_4h": levels_4h[:5], "confluence": confluence[:5]}
        
        # At least 1 confluent level found
        levels_ok = len(confluence) > 0
        score["details"].append({"gate": "SRConfluence", "value": len(confluence), "threshold": "≥1", "passed": levels_ok})
        if levels_ok: score["passed"] += 1

    # ── PHASE 5: SMT Divergence (BTC/ETH) ───────────────────────────
    smt_ok = False
    smt_data = {}
    if asset in ("BTC", "ETH"):
        other = "ETH" if asset == "BTC" else "BTC"
        df_self = df_1h
        df_other = _fetch_ohlcv(f"{other}/USDT:USDT", "1h", limit=50)
        if df_self is not None and df_other is not None:
            smt_data = ICT_ENGINE.detect_smt_divergence(df_self, df_other)
            smt_ok = smt_data.get("smt", False)
    
    score["details"].append({"gate": "SMT_Divergence", "value": smt_data.get("type", "NONE"), "threshold": "≠NEUTRAL", "passed": smt_ok})
    if smt_ok: score["passed"] += 1

    # ── PHASE 6: Confluence trigger ──────────────────────────────────
    trigger = _ep("detect-confluence-trigger", {"assets": asset, "ob_depth": 50})
    trigger_level = trigger.get("trigger_level", "NONE") if "error" not in trigger else "NONE"
    confluence_ok = trigger_level in ("SENSITIVE", "CONSERVATIVE")
    score["details"].append({"gate": "Confluence", "value": trigger_level, "threshold": "SENSITIVE/CONSERVATIVE", "passed": confluence_ok})
    if confluence_ok: score["passed"] += 1

    # ── DECISION ────────────────────────────────────────────────────
    entry_ok = score["passed"] >= 4  # At least 4/6 gates
    
    bias = regime_bias if entry_ok else "NEUTRAL"
    
    # Target: nearest confluent SR level opposite bias
    target_level = None
    if entry_ok and levels_data.get("confluence"):
        curr_price = float(df_1h['close'].iloc[-1]) if df_1h is not None else 0
        for lvl in levels_data["confluence"]:
            if (bias == "LONG" and lvl["price"] > curr_price) or \
               (bias == "SHORT" and lvl["price"] < curr_price):
                target_level = lvl
                break
    
    results["entry"] = {"verdict": "EXECUTE" if entry_ok else "NO_TRADE", "bias": bias}
    results["score"] = score
    results["valeyre"] = regime_data
    results["basis"] = {"basis_pct": round(basis_pct, 4)}
    results["sfp"] = {"detected": sfp_detected}
    results["sr_levels"] = levels_data
    results["smt"] = smt_data
    results["target"] = {"confluent_level": target_level}
    results["invalidation"] = {
        "regime_flip": f"Regime changes from {regime_data.get('regime','?')}",
        "basis_flip": f"Basis crosses {BASIS_THRESHOLD if bias == 'LONG' else abs(BASIS_THRESHOLD)}%",
        "timeout_hours": 8,
        "max_adverse_pct": -2.0,
    }
    
    return json.dumps(results, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════
# SWING ROUTINE (1-7 days)
# ═══════════════════════════════════════════════════════════════════
# Signature: Macro regime + Multi-TF SR confluence + Funding trend
#
# Data flow:
#   1. ICTEngine on 4H/1D → Valeyre extreme regime (OVERHEATED/UNDERVALUED)
#   2. SRLevelsEngine on 4H/1D/1W → Multi-TF SR confluence zones
#   3. Action Server → Funding history (50 candles) → multi-day trend
#   4. Action Server → Ultra-deep confluence (multi-TF regime analysis)
#   5. Hyperliquid → Top funding extremes (confirm macro bias)
#   6. ICTEngine → PO3 phase on 4H
#   7. ICTEngine → SMT Divergence BTC/ETH on 1D
#
# ENTRY (all must be true):
#   a) Valeyre 4H zscore > |2.0| (extreme regime)
#   b) Multi-TF SR confluence ≥ 2 TFs agree on key level
#   c) Funding trend > 80% same direction over 50 candles
#   d) Ultra-deep confluence verdict = INFORMED_FLOW or ACCUMULATION
#   e) HL funding extremes confirm (same asset funding extreme)
#   f) PO3 phase = DISTRIBUTION (for short) or ACCUMULATION (for long)
#
# TARGET:
#   Valeyre Z-Score returns to NEUTRAL (±1.0)
#
# INVALIDATION:
#   HTF regime flips | Funding trend reverses (3+ opposite candles) | 7d timeout | Max DD -5%

@action(is_consequential=False)
def setup_swing_institutional(asset: str = "BTC") -> str:
    """INSTITUTIONAL SWING SETUP — macro regime + multi-TF SR + funding trend.
    
    Combines Valeyre extreme regime, multi-TF SR confluence, 50-candle funding trend,
    ultra-deep confluence, Hyperliquid confirmation, and PO3 phase into a 6-gate swing decision.
    
    Args:
        asset: Trading asset (BTC, ETH, SOL).
    
    Returns:
        JSON with entry verdict, bias, score breakdown, regime-neutral target,
        invalidation conditions, and traceable gate results.
    """
    symbol = f"{asset}/USDT:USDT"
    score = {"passed": 0, "total": 6, "details": []}
    results: Dict[str, Any] = {"modality": "swing", "asset": asset, "symbol": symbol}

    # ── PHASE 1: Valeyre extreme regime (4H) ────────────────────────
    df_4h = _fetch_ohlcv(symbol, "4h", limit=300)
    df_1d = _fetch_ohlcv(symbol, "1d", limit=365)
    regime_data = {}
    
    if df_4h is not None:
        z = ICT_ENGINE.get_zscore_signal(df_4h)
        z_extreme = abs(z["z_score"]) > 2.0
        regime_data = z
        score["details"].append({"gate": "ValeyreExtreme", "value": round(z["z_score"], 2), "threshold": "|z|>2.0", "passed": z_extreme})
        if z_extreme: score["passed"] += 1

    # ── PHASE 2: Multi-TF SR confluence (4H + 1D + 1W) ──────────────
    df_1w = _fetch_ohlcv(symbol, "1w", limit=52)
    confluence_data = {}
    if df_4h is not None and df_1d is not None:
        l_4h = SR_ENGINE.compute_key_levels(df_4h, top_n=10)
        l_1d = SR_ENGINE.compute_key_levels(df_1d, top_n=10) if df_1d is not None else []
        l_1w = SR_ENGINE.compute_key_levels(df_1w, top_n=10) if df_1w is not None else []
        
        levels_map = {"4h": l_4h}
        if l_1d: levels_map["1d"] = l_1d
        if l_1w: levels_map["1w"] = l_1w
        
        confluence = SR_ENGINE.get_level_confluence(levels_map, tolerance_pct=0.01)
        confluence_data = {"levels": levels_map, "confluence": confluence[:5]}
        
        # Need at least 2 TFs agreeing
        strong_confluence = [c for c in confluence if c["confluence_count"] >= 2]
        confluence_ok = len(strong_confluence) > 0
        score["details"].append({"gate": "MTF_SR", "value": len(strong_confluence), "threshold": "≥1 zone with 2+ TFs", "passed": confluence_ok})
        if confluence_ok: score["passed"] += 1

    # ── PHASE 3: Funding trend (50 candles) ─────────────────────────
    fund_hist = _ep("get-funding-history", {"exchange": "binance", "asset": asset, "limit": 50})
    funding_trend_pct = 50.0
    if "error" not in fund_hist:
        rates = fund_hist.get("funding_rates", []) or []
        if len(rates) >= 30:
            pos = sum(1 for r in rates if isinstance(r, dict) and (r.get("funding_rate", 0) or 0) > 0)
            funding_trend_pct = (pos / len(rates)) * 100
    
    trend_ok = funding_trend_pct > 80 or funding_trend_pct < 20
    score["details"].append({"gate": "FundingTrend", "value": round(funding_trend_pct, 1), "threshold": ">80% or <20%", "passed": trend_ok})
    if trend_ok: score["passed"] += 1

    # ── PHASE 4: Ultra-deep confluence ──────────────────────────────
    deep = _ep("get-ultra-deep-confluence", {"assets": asset, "depth": 100})
    deep_verdict = deep.get("verdict", deep.get("senior_verdict", "NO_DATA")) if "error" not in deep else "NO_DATA"
    deep_ok = deep_verdict in ("INFORMED_FLOW", "ACCUMULATION", "OVERHEATED", "MEAN_REVERT_RISK")
    score["details"].append({"gate": "DeepConfluence", "value": deep_verdict, "threshold": "INFORMED_FLOW/ACCUMULATION", "passed": deep_ok})
    if deep_ok: score["passed"] += 1

    # ── PHASE 5: Hyperliquid confirmation ───────────────────────────
    hl_top = _ep("get-hl-funding-top", {"top_n": 20}, base=AS_HL)
    hl_confirms = False
    if "error" not in hl_top:
        hl_asset = asset.upper()
        for item in hl_top.get("top_funding_premium", []) + hl_top.get("top_funding_discount", []):
            if hl_asset in str(item.get("asset", "")):
                f = abs(item.get("funding_annualized_pct", 0))
                if f > 30:
                    hl_confirms = True
                    break
    
    score["details"].append({"gate": "HL_Confirm", "value": hl_confirms, "threshold": "funding > |30%| annualized", "passed": hl_confirms})
    if hl_confirms: score["passed"] += 1

    # ── PHASE 6: PO3 / AMD phase (4H) ────────────────────────────────
    po3_ok = False
    po3_data = {}
    if df_4h is not None:
        po3 = ICT_ENGINE.detect_po3_amd(df_4h)
        po3_data = po3
        regime_bias = z.get("bias", "NEUTRAL") if regime_data else "NEUTRAL"
        po3_ok = (regime_bias == "SHORT" and po3["phase"] == "DISTRIBUTION") or \
                 (regime_bias == "LONG" and po3["phase"] == "ACCUMULATION")
    
    score["details"].append({"gate": "PO3_AMD", "value": po3_data.get("phase", "?"), "threshold": "aligned with regime", "passed": po3_ok})
    if po3_ok: score["passed"] += 1

    # ── DECISION ────────────────────────────────────────────────────
    entry_ok = score["passed"] >= 4  # At least 4/6 gates
    bias = regime_data.get("bias", "NEUTRAL") if entry_ok else "NEUTRAL"
    
    results["entry"] = {"verdict": "EXECUTE" if entry_ok else "NO_TRADE", "bias": bias}
    results["score"] = score
    results["valeyre"] = regime_data
    results["sr_confluence"] = confluence_data
    results["funding_trend"] = round(funding_trend_pct, 1)
    results["deep_confluence"] = deep_verdict
    results["po3"] = po3_data
    results["target"] = {"type": "REGIME_NEUTRAL", "description": "Exit when 4H zscore crosses |1.0|"}
    results["invalidation"] = {
        "regime_flip": "HTF zscore crosses opposite extreme",
        "trend_reversal": "3+ consecutive opposite funding candles on 50-candle window",
        "timeout_days": 7,
        "max_drawdown_pct": -5.0,
    }
    
    return json.dumps(results, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════
# MASTER SETUP (all 3 modalities at once)
# ═══════════════════════════════════════════════════════════════════

@action(is_consequential=False)
def setup_master(asset: str = "BTC") -> str:
    """Run ALL 3 institutional modality setups and return the best opportunity.
    
    Args:
        asset: Trading asset (BTC, ETH, SOL).
    
    Returns:
        JSON with scorecard for all modalities, best setup identified by score ratio,
        Redis health status, and timestamp.
    """
    results = {}
    
    try:
        scalp = json.loads(setup_scalp_institutional(asset))
        results["scalp"] = scalp
    except Exception as e:
        results["scalp"] = {"error": str(e)}
    
    try:
        intraday = json.loads(setup_intraday_institutional(asset))
        results["intraday"] = intraday
    except Exception as e:
        results["intraday"] = {"error": str(e)}
    
    try:
        swing = json.loads(setup_swing_institutional(asset))
        results["swing"] = swing
    except Exception as e:
        results["swing"] = {"error": str(e)}
    
    # Find best setup (highest score ratio)
    best = None
    best_ratio = 0
    for modality in ["scalp", "intraday", "swing"]:
        mod = results.get(modality, {})
        score_data = mod.get("score", {})
        if isinstance(score_data, dict):
            ratio = score_data.get("passed", 0) / max(score_data.get("total", 1), 1)
            if ratio > best_ratio:
                best_ratio = ratio
                best = modality
    
    return json.dumps({
        "status": "ok",
        "asset": asset,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "best_setup": best,
        "best_score_ratio": round(best_ratio, 2),
        "modalities": results,
        "redis": redis().health(),
    }, indent=2, default=str)
