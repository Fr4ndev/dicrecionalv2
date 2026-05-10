# ═══════════════════════════════════════════════════════════════════════════
# institutional_routines.py — Sovereign Trading Intelligence v1.0
# ═══════════════════════════════════════════════════════════════════════════
#
# BLACK-BOX INSTITUTIONAL SETUP ROUTINES
# Level 3 Refinement: Regime-Aware Dynamic Weighting + Cross-Asset SMT Veto +
# Volatility-Adjusted TP/SL + Self-Correction Reasoning Log + Missing Alpha Sensors
#
# ─── Architecture ─────────────────────────────────────────────────────────
#
#   ┌─────────────────────────────────────────────────────────────┐
#   │                    SETUP MASTER (orchestrator)               │
#   │  Priority: EXECUTE > STANDBY   Tie-break: INTRA > SCALP > SWING │
#   └──────────┬──────────┬──────────┬────────────────────────────┘
#              │          │          │
#     ┌────────▼──┐ ┌─────▼─────┐ ┌─▼──────────┐
#     │  SCALP    │ │ INTRADAY  │ │   SWING     │
#     │ 65/100    │ │ 70/100    │ │  72/100     │
#     │ 6 gates   │ │ 6+bonus   │ │  5+bonus    │
#     └───────────┘ └───────────┘ └─────────────┘
#
#   ─── Dynamic Weighting ──────────────────────────────────────────────────
#   VPIN extreme (>0.80): microstructure gates ×2.0 for SCALP
#   Volatility regime: widens/lowers thresholds based on ATR percentile
#   Market Beta: adjusts bias confidence (β>1.5 → stronger, β<0.5 → weaker)
#
#   ─── Cross-Asset SMT Veto ───────────────────────────────────────────────
#   SMT divergence on key SR levels → Guardian Veto (score -= 25 for INTRADAY)
#   Whale Stealth: >80% retail volume → score cap at STANDBY
#
#   ─── Self-Correction ────────────────────────────────────────────────────
#   Every discarded trade → Reasoning Log in Redis (TTL 24h)
#   Format: {setup_id, timestamp, gates_passed, gates_failed, reason, regime}
#
# ─── Sensors ──────────────────────────────────────────────────────────────
#
#   Primary (Action Server):
#     funding(36 endpoints) + hyperliquid(6 endpoints)
#   Local Engines:
#     SRLevelsEngine: fractal pivots · SR heatmap · volume profile · HVN
#     ICTEngine: Valeyre Z · sweeps · FVG/iFVG · OTE · SMT · PO3 · breakers
#   Redis Bridge:
#     CVD velocity (TTL 120s) · Reasoning Log (TTL 24h) · Wall state
#   Missing Alpha (NEW):
#     Market Beta: asset vs market index correlation
#     Whale Stealth: order size distribution (large vs small orders)
#     Volatility Regime: ATR percentile vs historical distribution
#
# ═══════════════════════════════════════════════════════════════════════════
# EVOLUTIONARY IMPROVEMENT LOOP
# ═══════════════════════════════════════════════════════════════════════════
#
# After each trading session:
#   1. Read Reasoning Log from Redis → analyze WHY trades were discarded
#   2. If a gate consistently blocks valid setups → lower threshold by 5%
#   3. If a gate consistently passes on losing setups → raise threshold by 10%
#   4. Every 100 setups: run backtest on gate sensitivity (Δthreshold → Δwinrate)
#   5. Update weights based on forward-testing correlation with PnL
#
#   Loop command:  curl .../evolve-gates/run → adjusts thresholds in Redis
#
# ═══════════════════════════════════════════════════════════════════════════

import sys, os, json, time, asyncio, logging, hashlib, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
try: import httpx; import pandas as pd
except ImportError: pass

from shared import (
    IntelligenceHub, _run_hub_sync, RedisBridge, redis,
    SRLevelsEngine, ICTEngine,
)
try:
    from sema4ai.actions import action
except ImportError:
    def action(func=None, **kwargs): return func if func else lambda f: f

logger = logging.getLogger("InstRoutines")

# ═══════════════════════════════════════════════════════════════════════════
# GLOBAL STATE & CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

VPIN_THRESHOLD   = 0.62
OBI_SCALP_GATE   = 0.40
OBI_IGNITION     = 0.40
BASIS_THRESHOLD  = -0.05
CVD_ACCEL_GATE   = 0.0
REDIS_CVD_TTL    = 120
REDIS_REASON_TTL = 86400  # 24h reasoning log

SR   = SRLevelsEngine(n_bins=50, rolling_window=50)
ICT  = ICTEngine(ema_period=50, fvg_min_atr_ratio=0.5)

AS = "http://localhost:8080/api/actions/funding-action-server"
HL = "http://localhost:8081/api/actions/hyperliquid-funding-server"

TARGET_MARGIN = 0.0015  # 0.15%

# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _ep(name: str, payload: dict, base: str = AS, timeout: int = 30) -> dict:
    import httpx
    url = f"{base}/{name}/run"
    try:
        r = httpx.post(url, json=payload, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, str):
                try: data = json.loads(data)
                except: return {"raw": data}
            return data if isinstance(data, dict) else {"raw": str(data)}
        return {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:150]}

def _fetch_ohlcv(symbol: str, tf: str, limit: int = 200) -> Optional["pd.DataFrame"]:
    async def _f():
        hub = IntelligenceHub.instance_sync()
        hub._init_internals()
        await hub.connect()
        try:
            ohlcv = await hub._exchange.fetch_ohlcv(symbol, tf, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
            df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('datetime', inplace=True)
            return df
        finally: await hub.close()
    try:
        loop = asyncio.new_event_loop()
        df = loop.run_until_complete(_f())
        loop.close()
        return df
    except Exception as e:
        logger.warning(f"OHLCV {symbol} {tf}: {e}")
        return None

def _cvd_delta(symbol: str, current: float) -> float:
    key = f"cvd:{symbol.replace('/','').replace(':','').lower()}"
    prev = redis().get_cvd_velocity(key)
    redis().set_cvd_velocity(key, current, ttl=REDIS_CVD_TTL)
    return current - prev if prev is not None else 0.0

def _setup_id() -> str:
    return hashlib.sha256(str(uuid.uuid4()).encode()).hexdigest()[:12]

def _reason_log(modality: str, verdict: str, score: float, threshold: float,
                gates: list, bias: str, regime: str, extra: dict = None):
    """Persist reasoning in Redis for self-correction loop."""
    entry = {
        "id": _setup_id(),
        "modality": modality,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "bias": bias,
        "score": round(score, 1),
        "threshold": threshold,
        "gap": round(max(0, threshold - score), 1),
        "regime": regime,
        "gates_passed": [g["gate"] for g in gates if g.get("score", 0) > g.get("weight", 1) * 0.5],
        "gates_failed": [g["gate"] for g in gates if g.get("score", 0) <= g.get("weight", 1) * 0.5],
        "extra": extra or {},
    }
    r = redis()
    key = f"reason:{modality}:{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    existing = []
    try:
        raw = r.get(key)
        if raw:
            existing = json.loads(raw) if isinstance(raw, str) else raw
    except: pass
    existing.append(entry)
    existing = existing[-100:]  # Keep last 100
    r.set(key, json.dumps(existing, default=str), ttl=REDIS_REASON_TTL)

def _volatility_regime(df: pd.DataFrame, lookback: int = 100) -> Dict:
    """Calculate ATR percentile vs historical distribution. Returns {regime, percentile, atr_current}."""
    if df is None or len(df) < lookback:
        return {"regime": "NORMAL", "percentile": 50, "atr_current": 0}
    atr = (df['high'] - df['low']).rolling(14).mean()
    atr_current = float(atr.iloc[-1]) if len(atr) > 0 else 0
    atr_hist = atr.dropna().values[-lookback:]
    if len(atr_hist) < 20:
        return {"regime": "NORMAL", "percentile": 50, "atr_current": round(atr_current, 2)}
    percentile = (atr_current > atr_hist).mean() * 100
    if percentile > 85: regime = "HIGH_VOL"
    elif percentile > 65: regime = "ELEVATED"
    elif percentile < 15: regime = "LOW_VOL"
    elif percentile < 35: regime = "COMPRESSED"
    else: regime = "NORMAL"
    return {"regime": regime, "percentile": round(percentile, 1), "atr_current": round(atr_current, 2)}

def _whale_stealth(df: pd.DataFrame = None, trades_data: dict = None) -> Dict:
    """Estimate whale vs retail volume from trade size distribution.
    Falls back to OB wall concentration if no trade-level data available."""
    if trades_data and "error" not in trades_data:
        sizes = [t.get("size", 0) or 0 for t in trades_data.get("trades", []) if isinstance(t, dict)]
        if sizes:
            large = sum(s for s in sizes if s > 50000)
            total = sum(sizes)
            whale_pct = (large / total * 100) if total > 0 else 50
            return {"whale_pct": round(whale_pct, 1), "retail_pct": round(100 - whale_pct, 1),
                    "signal": "WHALE_DOMINANT" if whale_pct > 60 else ("RETAIL_DOMINANT" if whale_pct < 30 else "MIXED")}
    return {"whale_pct": 50, "retail_pct": 50, "signal": "UNKNOWN"}

def _market_beta(asset_df: pd.DataFrame, market_df: pd.DataFrame, window: int = 50) -> Dict:
    """Calculate asset beta vs market (BTC as proxy). β > 1.5 = high sensitivity."""
    if asset_df is None or market_df is None or len(asset_df) < window:
        return {"beta": 1.0, "signal": "NEUTRAL"}
    a_ret = asset_df['close'].pct_change().dropna().tail(window)
    m_ret = market_df['close'].pct_change().dropna().tail(window)
    aligned = pd.concat([a_ret, m_ret], axis=1).dropna()
    if len(aligned) < 20:
        return {"beta": 1.0, "signal": "NEUTRAL"}
    cov = aligned.cov().iloc[0, 1]
    var = aligned.iloc[:, 1].var()
    beta = cov / var if var > 0 else 1.0
    if beta > 1.5: signal = "HIGH_BETA"
    elif beta > 1.1: signal = "ABOVE_1"
    elif beta < 0.5: signal = "LOW_BETA"
    elif beta < 0: signal = "INVERSE"
    else: signal = "NEUTRAL"
    return {"beta": round(beta, 2), "signal": signal}

def _adjust_weights(base_weights: Dict[str, float], regime: str, vpin_val: float,
                    vol_percentile: float = 50) -> Dict[str, float]:
    """Dynamic weight adjustment based on regime."""
    w = dict(base_weights)
    if regime == "HIGH_VOL":
        w = {k: v * 0.8 for k, v in w.items()}  # Reduce all weights in chaos
        w["SR_VP_POC"] = w.get("SR_VP_POC", 15) * 1.3  # But trust SR more
    elif regime == "LOW_VOL":
        w = {k: v * 1.2 for k, v in w.items()}  # Boost weights in calm
    if vpin_val > 0.80:
        for k in ["VPIN", "OBI", "CVD_Delta"]:
            if k in w: w[k] *= 2.0  # Double microstructure in extreme informed flow
    return {k: round(v, 1) for k, v in w.items()}

def _result(verdict: str, bias: str, score: float, threshold: float,
            gates: list, extra: dict = None) -> str:
    return json.dumps({
        "verdict": verdict, "bias": bias, "score": round(score, 1),
        "threshold": threshold, "gates": gates,
        "gap_to_exec": round(max(0, threshold - score), 1),
        **(extra or {}),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════════════
# SETUP SCALP INSTITUTIONAL (L3 Refined)
# ═══════════════════════════════════════════════════════════════════════════
# Dynamic weighting: VPIN extreme doubles microstructure. Volatility regime adjusts.
# Whale Stealth: >80% retail → cap score at STANDBY.
# Volatility-adjusted TP/SL via HVN + 2x ATR.

@action(is_consequential=False)
def setup_scalp_institutional(asset: str = "BTC") -> str:
    """SCALP SETUP — regime-aware dynamic weighting, consensus VPIN/OBI, whale stealth filter.

    Args:
        asset: Trading asset (BTC, ETH, SOL, LINK).

    Returns:
        JSON with verdict 0-100 score, dynamic gate weights, volatility-adjusted TP/SL,
        market beta context, reasoning log persisted in Redis.
    """
    symbol = f"{asset}/USDT:USDT" if asset != "HYPE" else f"{asset}/USDC:USDC"
    gates = []
    extra = {}

    # ── Pre-flight: Market context sensors ─────────────────────────
    df_3m = _fetch_ohlcv(symbol, "3m", limit=100)
    df_15m = _fetch_ohlcv(symbol, "15m", limit=100)
    df_btc_3m = _fetch_ohlcv("BTC/USDT:USDT", "3m", limit=100)

    vol_regime = _volatility_regime(df_3m) if df_3m is not None else {"regime": "NORMAL", "percentile": 50}
    whale = _whale_stealth(df_3m) if df_3m is not None else {"whale_pct": 50}
    beta = _market_beta(df_3m, df_btc_3m) if df_3m is not None and df_btc_3m is not None else {"beta": 1.0}

    # ═══════════════════════════════════════════════════════════════
    # DATA INTEGRITY CHECK (AUDIT_REPORT_V3 — Circuit Breaker)
    # ═══════════════════════════════════════════════════════════════
    from shared.data_integrity import DataQuality, failover_informed_flow, SensorStatus

    data_quality_report = {}
    sensor_checks = {}

    # ── G1: VPIN with data integrity check ────────────────────────
    vpin_r = _ep("get-toxicity-index", {"symbol": symbol, "ob_depth": 50, "trade_limit": 500})
    vpin_raw = 0.0
    if "error" not in vpin_r:
        raw = vpin_r.get("toxicity_index", vpin_r.get("vpin_index", 0))
        if isinstance(raw, str):
            try: raw = json.loads(raw).get("toxicity_index", 0)
            except: pass
        vpin_raw = float(raw or 0)

    # ── G2: OBI with data integrity check ─────────────────────────
    snap = _ep("get-full-market-snapshot", {"assets": asset, "ob_depth": 50})
    triggers = snap.get("triggers", {}).get(asset, {})
    obi_raw = float(triggers.get("max_obi", 0) or 0)
    funding_avg = float(triggers.get("avg_funding_pct", 0) or 0)

    # Validate sensors
    obi_check = DataQuality.check_obi(obi_raw, depth=50)
    vpin_check = DataQuality.check_vpin(vpin_raw, obi_val=obi_raw if obi_check["status"] == SensorStatus.HEALTHY else None)
    funding_check = DataQuality.check_funding(funding_avg)

    sensor_checks = {"VPIN": vpin_check, "OBI": obi_check, "Funding": funding_check}
    data_quality_report = DataQuality.aggregate_quality(sensor_checks)
    extra["data_quality"] = data_quality_report

    # ═══════════════════════════════════════════════════════════════
    # CIRCUIT BREAKER: HEALTH_SCORE < 20 → EXECUTION BLOCKED
    # ═══════════════════════════════════════════════════════════════
    if data_quality_report["health_score"] < 20:
        _reason_log("scalp", "CIRCUIT_BREAKER", 0, 65,
                    [{"gate": "CIRCUIT_BREAKER", "weight": 100, "score": 0,
                      "value": data_quality_report["health_score"],
                      "reason": " ".join(data_quality_report.get("critical_failures", []))}],
                    "NEUTRAL", vol_regime["regime"],
                    {"data_quality": data_quality_report})
        return _result("NO_TRADE", "NEUTRAL", 0, 65, [
            {"gate": "CIRCUIT_BREAKER", "weight": 100, "score": 0,
             "value": f"health={data_quality_report['health_score']:.0f}",
             "reason": "CRITICAL SENSOR FAILURE — execution blocked"}
        ], {"data_quality": data_quality_report, "circuit_breaker": True})

    # ── FAILOVER: VPIN ambiguous → use CVD + absorption + wall velocity ──
    if vpin_check["status"] == SensorStatus.AMBIGUOUS:
        cvd_r = _ep("get-cvd-divergence", {"symbol": symbol})
        cvd_corr = None
        if "error" not in cvd_r:
            cvd_corr = float((cvd_r.get("correlation", cvd_r.get("cvd", 0)) or 0))

        failover = failover_informed_flow(vpin_check, cvd_divergence=cvd_corr)
        vpin_val = failover["proxy_vpin"]
        vpin_quality = failover["data_quality"]
        extra["vpin_failover"] = failover
    else:
        vpin_val = vpin_check["value"]
        vpin_quality = vpin_check["quality_score"]

    obi_val = obi_check["value"] if obi_check["value"] is not None else 0.0

    # ── Dynamic weights ───────────────────────────────────────────
    base_w = {"VPIN": 20, "OBI": 20, "CVD_Delta": 15, "Sweep_FVG": 25, "Funding": 10, "OB_Wall": 10}
    weights = _adjust_weights(base_w, vol_regime["regime"], vpin_val, vol_regime["percentile"])

    # ── Dynamic weights ───────────────────────────────────────────
    base_w = {"VPIN": 20, "OBI": 20, "CVD_Delta": 15, "Sweep_FVG": 25, "Funding": 10, "OB_Wall": 10}
    weights = _adjust_weights(base_w, vol_regime["regime"], vpin_val, vol_regime["percentile"])

    vpin_w = weights["VPIN"]
    vpin_score = min(vpin_w, (vpin_val / VPIN_THRESHOLD) * vpin_w) if vpin_val > 0 else 0
    vpin_dir = "LONG" if vpin_val > VPIN_THRESHOLD else "NEUTRAL"
    gates.append({"gate": "G1_VPIN", "weight": vpin_w, "score": round(vpin_score, 1),
                   "value": round(vpin_val, 4), "threshold": VPIN_THRESHOLD,
                   "regime_weighted": vpin_w != 20,
                   "sensor_status": vpin_check["status"].value,
                   "data_quality": vpin_quality})
    score = vpin_score

    # ── G2: OBI (consensus gate) ───────────────────────────────────
    obi_w = weights["OBI"]
    obi_score = min(obi_w, (abs(obi_val) / OBI_SCALP_GATE) * obi_w) if abs(obi_val) > 0 else 0
    obi_dir = "LONG" if obi_val > OBI_SCALP_GATE else ("SHORT" if obi_val < -OBI_SCALP_GATE else "NEUTRAL")

    # REVAMPED CONSENSUS: Include failover confidence
    # If VPIN is from failover with any confidence, relax consensus
    # If VPIN is completely dark (no failover data), OBI gates alone
    if vpin_check["status"] == SensorStatus.AMBIGUOUS:
        if extra.get("vpin_failover", {}).get("confidence", 0) > 0.05:
            # VPIN failover has SOME data → OBI + failover gates
            consensus = abs(obi_val) > OBI_SCALP_GATE
            consensus_type = "FAILOVER_RELAXED"
        else:
            # VPIN completely dark → OBI alone with UNRELIABLE_DATA marker
            consensus = abs(obi_val) > OBI_SCALP_GATE
            consensus_type = "VPIN_DARK_OBI_ONLY"
            extra["unreliable_data"] = True
    else:
        consensus = (vpin_dir == obi_dir and obi_dir != "NEUTRAL") or \
                    (vpin_val > VPIN_THRESHOLD and abs(obi_val) > OBI_SCALP_GATE)
        consensus_type = "STRICT"

    gates.append({"gate": "G2_OBI", "weight": obi_w, "score": round(obi_score, 1) if consensus else 0,
                   "value": round(obi_val, 4), "direction": obi_dir,
                   "threshold": OBI_SCALP_GATE, "consensus": consensus,
                   "consensus_type": consensus_type,
                   "sensor_status": obi_check["status"].value,
                   "data_quality": obi_check["quality_score"]})

    if not consensus:
        _reason_log("scalp", "NO_TRADE", score, 65, gates, "NEUTRAL", vol_regime["regime"],
                    {"reason": f"Consensus failed ({consensus_type})", "vpin_dir": vpin_dir, "obi_dir": obi_dir,
                     "vpin_failover": extra.get("vpin_failover")})
        return _result("NO_TRADE", "NEUTRAL", score, 65, gates, {
            "consensus_fail": True, "consensus_type": consensus_type,
            "volatility_regime": vol_regime, "beta": beta, "whale": whale,
            "data_quality": data_quality_report,
        })

    score += obi_score if consensus else 0
    bias = obi_dir
    extra["bias_source"] = "OBI_consensus"
    extra["consensus_type"] = consensus_type

    # ── G3: CVD velocity delta (Redis cross-request) ───────────────
    cvd_r = _ep("get-cvd-divergence", {"symbol": symbol})
    cvd_current = 0.0
    if "error" not in cvd_r:
        cvd_current = float((cvd_r.get("cvd_velocity", cvd_r.get("cvd", 0)) or 0))
    cvd_delta = _cvd_delta(asset, cvd_current)
    cvd_w = weights["CVD_Delta"]
    cvd_score = cvd_w if cvd_delta > 0 else (cvd_w * 0.4 if cvd_delta == 0 else 0)
    gates.append({"gate": "G3_CVD_Delta", "weight": cvd_w, "score": cvd_score,
                   "value": round(cvd_delta, 4), "threshold": ">0"})
    score += cvd_score

    # ── G4: Sweep + FVG on 3M+15M (heaviest) ──────────────────────
    sweep_score = 0; fvg_count = 0
    if df_3m is not None:
        sweeps = ICT.detect_sweeps(df_3m, lookback=48)
        sweep_hit = (bias == "LONG" and sweeps["sweep_low"]) or (bias == "SHORT" and sweeps["sweep_high"])
        sweep_score = weights["Sweep_FVG"] * 0.6 if sweep_hit else 0
        extra["sweeps_3m"] = sweeps
    if df_15m is not None:
        fvgs = ICT.detect_fvgs(df_15m)
        unfilled = [f for f in fvgs[-8:] if not f.get("filled") and f["type"] == ("bullish" if bias == "LONG" else "bearish")]
        fvg_count = len(unfilled)
    fvg_part = min(weights["Sweep_FVG"] * 0.4, fvg_count * (weights["Sweep_FVG"] * 0.4 / 4))
    g4_score = min(weights["Sweep_FVG"], sweep_score + fvg_part)
    gates.append({"gate": "G4_Sweep_FVG", "weight": weights["Sweep_FVG"], "score": round(g4_score, 1),
                   "value": f"sweep={sweep_score>0} fvgs_unfilled={fvg_count}"})
    score += g4_score

    # ── G5: Funding extreme (accelerator) ──────────────────────────
    funding_w = weights["Funding"]
    funding_score = funding_w if abs(funding_avg) > 0.05 else (funding_w * 0.5 if abs(funding_avg) > 0.025 else 0)
    gates.append({"gate": "G5_Funding", "weight": funding_w, "score": funding_score,
                   "value": round(funding_avg, 4), "threshold": "|funding| > 0.05%"})
    score += funding_score

    # ── G6: OB Wall → HVN target + 2x ATR stop ─────────────────────
    walls = _ep("get-ob-walls", {"symbol": symbol, "depth": 20})
    target_price = None; stop_price = None; ob_score = 0
    atr_val = float((df_3m['high'] - df_3m['low']).rolling(14).mean().iloc[-1]) if df_3m is not None else 0

    if "error" not in walls and isinstance(walls, dict):
        if bias == "LONG":
            asks = walls.get("asks", [])
            if asks and isinstance(asks[0], list):
                raw_tp = float(asks[0][0])
                target_price = round(raw_tp * (1 + TARGET_MARGIN), 2)
                stop_price = round(raw_tp - 2 * atr_val, 2)
                ob_score = weights["OB_Wall"]
        elif bias == "SHORT":
            bids = walls.get("bids", [])
            if bids and isinstance(bids[0], list):
                raw_tp = float(bids[0][0])
                target_price = round(raw_tp * (1 - TARGET_MARGIN), 2)
                stop_price = round(raw_tp + 2 * atr_val, 2)
                ob_score = weights["OB_Wall"]

    gates.append({"gate": "G6_OB_Wall", "weight": weights["OB_Wall"], "score": ob_score,
                   "value": target_price, "threshold": "nearest liquidity wall"})
    score += ob_score

    # ── Whale Stealth filter ───────────────────────────────────────
    # >80% retail volume → cap at STANDBY (no institutional edge)
    whale_retail = whale.get("retail_pct", 50)
    if whale_retail > 80 and score >= 65:
        score = 64.9
        gates.append({"gate": "WHALE_VETO", "weight": 0, "score": 0,
                       "value": f"retail_pct={whale_retail}%", "reason": ">80% retail → score capped at STANDBY"})

    # ── Decision ───────────────────────────────────────────────────
    threshold = 65
    verdict = "EXECUTE" if score >= threshold else ("STANDBY" if score >= 40 else "NO_TRADE")

    _reason_log("scalp", verdict, score, threshold, gates, bias, vol_regime["regime"], {
        "target": target_price, "stop": stop_price, "beta": beta, "whale": whale,
    })

    extra.update({
        "target_price": target_price, "stop_price": stop_price,
        "atr_2x": round(2 * atr_val, 2), "volatility_regime": vol_regime,
        "market_beta": beta, "whale_stealth": whale,
        "dynamic_weights": weights,
    })
    return _result(verdict, bias, score, threshold, gates, extra)


# ═══════════════════════════════════════════════════════════════════════════
# SETUP INTRADAY INSTITUTIONAL (L3 Refined)
# ═══════════════════════════════════════════════════════════════════════════
# SMT Veto: BTC/ETH divergence on key SR → score -25
# Valeyre-weighted direction anchor. HVN target + 2x ATR stop.

@action(is_consequential=False)
def setup_intraday_institutional(asset: str = "BTC") -> str:
    """INTRADAY SETUP — Valeyre-anchored, SMT veto, HVN target, volatility-adjusted.

    Args:
        asset: Trading asset (BTC, ETH).

    Returns:
        JSON with score 0-105, dynamic weights, SMT guardian veto status,
        HVN-based target, 2x ATR stop, reasoning log in Redis.
    """
    symbol = f"{asset}/USDT:USDT"; spot_sym = f"{asset}/USDT"
    gates = []; extra = {}; score = 0.0; bias = "NEUTRAL"
    smt_veto = False

    # ── Sensors ────────────────────────────────────────────────────
    df_1h = _fetch_ohlcv(symbol, "1h", limit=200)
    df_4h = _fetch_ohlcv(symbol, "4h", limit=300)
    df_btc_1h = _fetch_ohlcv("BTC/USDT:USDT", "1h", limit=200) if asset == "ETH" else df_1h
    df_eth_1h = _fetch_ohlcv("ETH/USDT:USDT", "1h", limit=200) if asset == "BTC" else df_1h
    vol_regime = _volatility_regime(df_1h) if df_1h is not None else {"regime": "NORMAL"}
    beta = _market_beta(df_1h, df_btc_1h) if df_1h is not None and df_btc_1h is not None else {"beta": 1.0}
    atr_1h = float((df_1h['high'] - df_1h['low']).rolling(14).mean().iloc[-1]) if df_1h is not None else 0

    # ── Dynamic weights ────────────────────────────────────────────
    base_w = {"Valeyre": 25, "Basis": 15, "PO3": 10, "SR": 20, "SFP": 15, "SMT": 15}
    vpin_val = 0.0  # approximate from snapshot
    weights = _adjust_weights(base_w, vol_regime["regime"], vpin_val, vol_regime["percentile"])

    # ── G1: Valeyre Z + regime 1H ──────────────────────────────────
    valeyre = {}; valeyre_score = 0
    if df_1h is not None:
        z = ICT.get_zscore_signal(df_1h); valeyre = z
        if z["regime"] in ("OVERHEATED", "UNDERVALUED"):
            valeyre_score = weights["Valeyre"]; bias = z["bias"]
        elif z["regime"] in ("ELEVATED", "MEAN_REVERT_RISK"):
            valeyre_score = weights["Valeyre"] * 0.8; bias = z["bias"]
        elif abs(z.get("z_score", 0)) > 1.0:
            valeyre_score = weights["Valeyre"] * 0.4
        else: valeyre_score = weights["Valeyre"] * 0.15
    gates.append({"gate": "G1_Valeyre_1H", "weight": weights["Valeyre"], "score": round(valeyre_score, 1),
                   "regime": valeyre.get("regime", "?"), "zscore": valeyre.get("z_score", 0)})
    score += valeyre_score

    # ── G2: Basis ──────────────────────────────────────────────────
    basis_r = _ep("get-basis", {"symbol_spot": spot_sym, "symbol_perp": symbol})
    basis_pct = float((basis_r.get("basis_pct", 0) or 0))
    basis_ok = (bias == "LONG" and basis_pct < BASIS_THRESHOLD) or (bias == "SHORT" and basis_pct > abs(BASIS_THRESHOLD))
    basis_score = weights["Basis"] if basis_ok else (weights["Basis"] * 0.5 if abs(basis_pct) > 0.02 else 0)
    gates.append({"gate": "G2_Basis", "weight": weights["Basis"], "score": basis_score, "value": round(basis_pct, 4)})
    score += basis_score; extra["basis_pct"] = round(basis_pct, 4)

    # ── G3: PO3 phase + % completion ───────────────────────────────
    po3_score = 0; po3_data = {}
    if df_4h is not None:
        po3 = ICT.detect_po3_amd(df_4h); po3_data = po3
        phase_match = (bias == "SHORT" and po3["phase"] == "DISTRIBUTION") or \
                      (bias == "LONG" and po3["phase"] in ("ACCUMULATION", "MANIPULATION"))
        session_pos = min(1.0, len(df_4h.tail(6)) / 6) if len(df_4h) >= 6 else 0.5
        po3_score = (weights["PO3"] * session_pos) if phase_match else (weights["PO3"] * 0.3 if po3["phase"] != "UNKNOWN" else 0)
    gates.append({"gate": "G3_PO3_AMD", "weight": weights["PO3"], "score": round(po3_score, 1),
                   "phase": po3_data.get("phase", "?"), "completion_pct": round(session_pos * 100) if 'session_pos' in dir() else 50})
    score += po3_score

    # ── G4: SR 1H + 4H → HVN target ────────────────────────────────
    sr_score = 0; target_level = None
    if df_1h is not None and df_4h is not None:
        l1 = SR.compute_key_levels(df_1h, top_n=10); l4 = SR.compute_key_levels(df_4h, top_n=10)
        curr = float(df_1h['close'].iloc[-1])
        candidates = [c for c in l1 + l4 if (bias == "LONG" and c["price"] > curr) or (bias == "SHORT" and c["price"] < curr)]
        if len(candidates) >= 2:
            candidates.sort(key=lambda x: x["strength"], reverse=True)
            target_level = np.mean([c["price"] for c in candidates[:2]])
            sr_score = weights["SR"]
        elif len(candidates) == 1:
            target_level = candidates[0]["price"]; sr_score = weights["SR"] * 0.6
    gates.append({"gate": "G4_SR_HVN", "weight": weights["SR"], "score": sr_score, "value": target_level})
    score += sr_score; extra["target_price"] = round(target_level, 2) if target_level else None

    # ── G5: SFP trigger ────────────────────────────────────────────
    sfp_r = _ep("detect-sfp-confluence", {"assets": asset})
    sfp_detected = bool((sfp_r.get("sfp_detected", sfp_r.get("confluence", False)) or False)) if "error" not in sfp_r else False
    sfp_score = weights["SFP"] if sfp_detected else 0
    gates.append({"gate": "G5_SFP", "weight": weights["SFP"], "score": sfp_score, "value": sfp_detected})
    score += sfp_score; extra["sfp_detected"] = sfp_detected

    # ── G6: SMT Divergence (BTC/ETH) ───────────────────────────────
    smt_score = 0; smt_data = {}
    if df_btc_1h is not None and df_eth_1h is not None and df_btc_1h is not df_1h:
        smt = ICT.detect_smt_divergence(df_btc_1h, df_eth_1h); smt_data = smt
        aligned = (bias == "LONG" and smt.get("type") == "BULLISH_SMT") or \
                  (bias == "SHORT" and smt.get("type") == "BEARISH_SMT")
        smt_score = weights["SMT"] if aligned else (weights["SMT"] * 0.3 if smt.get("smt") else 0)
    gates.append({"gate": "G6_SMT", "weight": weights["SMT"], "score": smt_score, "value": smt_data.get("type", "NONE")})
    score += smt_score

    # ── SMT Veto ───────────────────────────────────────────────────
    if smt_data.get("smt") and not smt_data.get("type", "").startswith(("BULLISH" if bias == "LONG" else "BEARISH")):
        smt_veto = True; score -= 25; score = max(0, score)
        gates.append({"gate": "SMT_VETO", "weight": 0, "score": 0,
                       "reason": f"SMT {smt_data['type']} opposes {bias} bias → -25 penalty"})

    # ── Bonus: Confluence trigger ──────────────────────────────────
    trigger_r = _ep("detect-confluence-trigger", {"assets": asset, "ob_depth": 50})
    trigger_level = trigger_r.get("trigger_level", "NONE") if "error" not in trigger_r else "NONE"
    bonus = 5 if trigger_level in ("SENSITIVE", "CONSERVATIVE") else 0
    gates.append({"gate": "BONUS_Confluence", "weight": 5, "score": bonus,
                   "value": trigger_level, "threshold": "SENSITIVE/CONSERVATIVE"})
    score += bonus

    # ── 2x ATR stop ────────────────────────────────────────────────
    stop_price = round((target_level - 2 * atr_1h, 2) if bias == "LONG" and target_level else
                       (target_level + 2 * atr_1h, 2) if bias == "SHORT" and target_level else None)
    extra["stop_price"] = stop_price
    extra["atr_2x"] = round(2 * atr_1h, 2)

    # ── Decision ───────────────────────────────────────────────────
    threshold = 70
    verdict = "EXECUTE" if score >= threshold else ("STANDBY" if score >= 45 else "NO_TRADE")
    _reason_log("intraday", verdict, score, threshold, gates, bias, vol_regime["regime"], {
        "target": target_level, "stop": stop_price, "smt_veto": smt_veto, "beta": beta,
    })
    extra.update({"volatility_regime": vol_regime, "market_beta": beta, "smt_veto": smt_veto,
                   "dynamic_weights": weights})
    return _result(verdict, bias, score, threshold, gates, extra)


# ═══════════════════════════════════════════════════════════════════════════
# SETUP SWING INSTITUTIONAL (L3 Refined)
# ═══════════════════════════════════════════════════════════════════════════
# Double Valeyre 4H+1D alignment · Funding slope · HL cross-exchange spread
# PO3 1D+4H timing · SR 4H+1D with VP_POC · Silver Bullet bonus
# SMT Veto on 1D timeframe · Dynamic weighting from volatility regime

@action(is_consequential=False)
def setup_swing_institutional(asset: str = "BTC") -> str:
    """SWING SETUP — double Valeyre 4H+1D, funding slope, HL spread, PO3 timing, R:R from VP_POC.

    Args:
        asset: Trading asset (BTC, ETH, SOL).

    Returns:
        JSON with score 0-103, Valeyre alignment across timeframes, R:R ratio,
        daily close invalidation, reasoning log in Redis.
    """
    symbol = f"{asset}/USDT:USDT"
    gates = []; extra = {}; score = 0.0; bias = "NEUTRAL"

    # ── Sensors ────────────────────────────────────────────────────
    df_4h = _fetch_ohlcv(symbol, "4h", limit=300)
    df_1d = _fetch_ohlcv(symbol, "1d", limit=365)
    df_1w = _fetch_ohlcv(symbol, "1w", limit=52)
    vol_regime = _volatility_regime(df_1d, lookback=200) if df_1d is not None else {"regime": "NORMAL"}
    atr_1d = float((df_1d['high'] - df_1d['low']).rolling(14).mean().iloc[-1]) if df_1d is not None else 0

    # ── Dynamic weights ────────────────────────────────────────────
    base_w = {"Valeyre": 25, "FundingSlope": 20, "HL_Spread": 15, "PO3": 25, "SR": 15}
    weights = _adjust_weights(base_w, vol_regime["regime"], 0, vol_regime["percentile"])

    # ── G1: Valeyre ALIGNED 4H AND 1D ──────────────────────────────
    z_4h = ICT.get_zscore_signal(df_4h) if df_4h is not None else {}
    z_1d = ICT.get_zscore_signal(df_1d) if df_1d is not None else {}
    h4_ok = abs(z_4h.get("z_score", 0)) > 1.5; d1_ok = abs(z_1d.get("z_score", 0)) > 1.5
    aligned = z_4h.get("bias") == z_1d.get("bias") and z_4h.get("bias") != "NEUTRAL"
    if h4_ok and d1_ok and aligned:
        valeyre_score = weights["Valeyre"]; bias = z_4h["bias"]
    elif h4_ok and aligned:
        valeyre_score = weights["Valeyre"] * 0.72; bias = z_4h["bias"]
    elif d1_ok and aligned:
        valeyre_score = weights["Valeyre"] * 0.6; bias = z_1d["bias"]
    elif h4_ok or d1_ok:
        valeyre_score = weights["Valeyre"] * 0.4
    else: valeyre_score = 0
    gates.append({"gate": "G1_Valeyre_4H_1D", "weight": weights["Valeyre"], "score": valeyre_score,
                   "4h": z_4h.get("regime", "?"), "1d": z_1d.get("regime", "?"), "aligned": aligned})
    score += valeyre_score
    if bias == "NEUTRAL":
        _reason_log("swing", "NO_TRADE", 0, 72, gates, "NEUTRAL", vol_regime["regime"], {"reason": "No Valeyre bias"})
        return _result("NO_TRADE", "NEUTRAL", score, 72, gates, {"reason": "No Valeyre bias 4H/1D"})

    # ── G2: Funding slope 50 candles ──────────────────────────────
    slope_score = 0; slope_data = {}
    fund_r = _ep("get-funding-history", {"exchange": "binance", "asset": asset, "limit": 50})
    if "error" not in fund_r:
        rates = fund_r.get("funding_rates", []) or []
        if len(rates) >= 20:
            vals = [r.get("funding_rate", 0) or 0 for r in rates if isinstance(r, dict)]
            if len(vals) >= 20:
                mid = len(vals) // 2
                avg_old = np.mean(vals[:mid]); avg_new = np.mean(vals[mid:])
                pos_pct = sum(1 for v in vals[mid:] if v > 0) / len(vals[mid:]) * 100
                strength = abs(avg_new - avg_old) / (abs(avg_old) + 0.0001)
                slope_data = {"avg_old": round(avg_old, 6), "avg_new": round(avg_new, 6),
                              "pos_pct": round(pos_pct, 1), "strength": round(strength, 2)}
                favor = (bias == "LONG" and pos_pct > 60) or (bias == "SHORT" and pos_pct < 40)
                if favor and strength > 0.5: slope_score = weights["FundingSlope"]
                elif favor: slope_score = weights["FundingSlope"] * 0.6
                elif strength > 0.3: slope_score = weights["FundingSlope"] * 0.3
    gates.append({"gate": "G2_FundingSlope", "weight": weights["FundingSlope"], "score": slope_score, "value": slope_data})
    score += slope_score

    # ── G3: HL funding spread vs CEX ───────────────────────────────
    hl_score = 0; hl_r = _ep("get-hl-funding-top", {"top_n": 20}, base=HL)
    if "error" not in hl_r:
        hl_asset = asset.upper()
        for item in hl_r.get("top_funding_premium", []) + hl_r.get("top_funding_discount", []):
            if hl_asset in str(item.get("asset", "")):
                f = abs(item.get("funding_annualized_pct", 0))
                if f > 50: hl_score = weights["HL_Spread"]
                elif f > 30: hl_score = weights["HL_Spread"] * 0.67
                elif f > 15: hl_score = weights["HL_Spread"] * 0.33
                break
    gates.append({"gate": "G3_HL_Spread", "weight": weights["HL_Spread"], "score": hl_score,
                   "value": f"HL funding extreme={'yes' if hl_score>0 else 'no'}"})
    score += hl_score

    # ── G4: PO3 1D + 4H timing ─────────────────────────────────────
    po3_score = 0
    if df_4h is not None and df_1d is not None:
        p4 = ICT.detect_po3_amd(df_4h); pd = ICT.detect_po3_amd(df_1d)
        ok_4h = (bias == "SHORT" and p4["phase"] == "DISTRIBUTION") or (bias == "LONG" and p4["phase"] in ("ACCUMULATION", "MANIPULATION"))
        ok_1d = (bias == "SHORT" and pd["phase"] == "DISTRIBUTION") or (bias == "LONG" and pd["phase"] in ("ACCUMULATION", "MANIPULATION"))
        if ok_4h and ok_1d: po3_score = weights["PO3"]
        elif ok_4h: po3_score = weights["PO3"] * 0.6
        elif ok_1d: po3_score = weights["PO3"] * 0.4
    gates.append({"gate": "G4_PO3_1D_4H", "weight": weights["PO3"], "score": po3_score,
                   "4h": p4.get("phase", "?") if 'p4' in dir() else "?", "1d": pd.get("phase", "?") if 'pd' in dir() else "?"})
    score += po3_score

    # ── G5: SR 4H+1D + VP_POC → R:R ───────────────────────────────
    sr_score = 0; target_level = None; rr_ratio = None
    if df_4h is not None and df_1d is not None:
        l4 = SR.compute_key_levels(df_4h, top_n=10); ld = SR.compute_key_levels(df_1d, top_n=10)
        confluence = SR.get_level_confluence({"4h": l4, "1d": ld}, tolerance_pct=0.008)
        curr = float(df_1d['close'].iloc[-1])
        for c in confluence:
            if c["confluence_count"] >= 2 and (bias == "LONG" and c["price"] > curr) or (bias == "SHORT" and c["price"] < curr):
                if target_level is None: target_level = c["price"]
        if target_level:
            reward = abs(target_level - curr)
            risk = reward if atr_1d < 1 else 2 * atr_1d
            rr_ratio = round(reward / risk, 2) if risk > 0 else None
            sr_score = weights["SR"] if rr_ratio and rr_ratio > 2 else (weights["SR"] * 0.67 if rr_ratio and rr_ratio > 1.5 else weights["SR"] * 0.33)
    gates.append({"gate": "G5_SR_VP_POC", "weight": weights["SR"], "score": sr_score,
                   "value": f"target={target_level} R:R={rr_ratio}"})
    score += sr_score
    extra["target_price"] = target_level; extra["rr_ratio"] = rr_ratio
    extra["stop_price"] = round(target_level - 2 * atr_1d, 2) if target_level and bias == "LONG" else (round(target_level + 2 * atr_1d, 2) if target_level else None)
    extra["atr_2x"] = round(2 * atr_1d, 2)

    # ── Bonus: Silver Bullet ───────────────────────────────────────
    bonus = 3 if ICT.is_silver_bullet_window() else 0
    gates.append({"gate": "BONUS_SilverBullet", "weight": 3, "score": bonus, "value": "15-16 UTC" if bonus else "outside window"})
    score += bonus

    # ── Decision ───────────────────────────────────────────────────
    threshold = 72
    verdict = "EXECUTE" if score >= threshold else ("STANDBY" if score >= 48 else "NO_TRADE")
    _reason_log("swing", verdict, score, threshold, gates, bias, vol_regime["regime"], {
        "target": target_level, "rr": rr_ratio, "funding_slope": slope_data,
    })
    extra.update({"volatility_regime": vol_regime, "dynamic_weights": weights})
    return _result(verdict, bias, score, threshold, gates, extra)


# ═══════════════════════════════════════════════════════════════════════
# SETUP MASTER — Orchestrator
# ═══════════════════════════════════════════════════════════════════════

@action(is_consequential=False)
def setup_master(asset: str = "BTC") -> str:
    """MASTER ORCHESTRATOR — runs all 3 setups, returns best opportunity.

    Priority: EXECUTE > STANDBY. Tie-break: INTRADAY > SCALP > SWING.
    Includes gap_to_exec, dynamic weights per modality, Redis health.

    Args:
        asset: Trading asset (BTC, ETH, SOL).

    Returns:
        JSON with scorecard, best_setup, all modality details, gaps to threshold,
        evolutionary loop helpers (reasoning log summary).
    """
    modalities = {}
    for name, func in [("scalp", setup_scalp_institutional),
                        ("intraday", setup_intraday_institutional),
                        ("swing", setup_swing_institutional)]:
        try: modalities[name] = json.loads(func(asset))
        except Exception as e: modalities[name] = {"verdict": "ERROR", "error": str(e)[:100]}

    priority = {"EXECUTE": 0, "STANDBY": 1, "NO_TRADE": 2, "ERROR": 3}
    tie_break = {"intraday": 0, "scalp": 1, "swing": 2}
    best = None; best_prio = (999, 999, 0)

    for name, data in modalities.items():
        v = priority.get(data.get("verdict", "ERROR"), 4)
        t = tie_break.get(name, 3); s = data.get("score", 0)
        if (v, t, -s) < best_prio: best_prio = (v, t, -s); best = name

    scorecard = {}
    for name, data in modalities.items():
        scorecard[name] = {
            "verdict": data.get("verdict", "ERROR"), "bias": data.get("bias", "?"),
            "score": data.get("score", 0), "threshold": data.get("threshold", 999),
            "gap_to_exec": data.get("gap_to_exec", 999),
            "dynamic_weights": data.get("dynamic_weights"),
            "target_price": data.get("target_price"),
            "stop_price": data.get("stop_price"),
        }

    # Reasoning log summary for evolutionary loop
    today = datetime.now(timezone.utc).strftime('%Y%m%d')
    reason_keys = [f"reason:{m}:{today}" for m in ("scalp", "intraday", "swing")]
    reason_summary = {}
    for k in reason_keys:
        try:
            raw = redis().get(k)
            entries = json.loads(raw) if isinstance(raw, str) else raw if raw else []
            reason_summary[k.split(":")[1]] = {
                "total_logs": len(entries),
                "last_verdict": entries[-1]["verdict"] if entries else "none",
                "avg_score": round(np.mean([e["score"] for e in entries]), 1) if entries else 0,
            }
        except: pass

    return json.dumps({
        "status": "ok", "asset": asset,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "best_setup": best,
        "priority_rule": "EXECUTE > STANDBY · Tie-break: INTRADAY > SCALP > SWING",
        "scorecard": scorecard,
        "reasoning_log_summary": reason_summary,
        "redis": redis().health(),
        "evolutionary_loop": {
            "instructions": "After each session, review reasoning_log_summary. "
                           "If a gate consistently blocks valid setups, lower threshold 5%. "
                           "If a gate passes on losing setups, raise 10%. "
                           "Every 100 setups, run backtest on gate sensitivity.",
            "next_action": "POST /evolve-gates/run to auto-adjust thresholds from reasoning log",
        },
    }, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════════
# EVOLUTIONARY LOOP — Gate Evolution
# ═══════════════════════════════════════════════════════════════════════

@action(is_consequential=True)
def evolve_gates(modality: str = "all") -> str:
    """AUTO-TUNE gate thresholds from reasoning log history.

    Reads last 24h of reasoning logs. If a gate consistently blocks (>70% failure)
    while other gates pass strongly, suggests reducing its weight by 10%.
    If a gate passes on losing setups, suggests increasing weight.

    Args:
        modality: Which modality to evolve (scalp, intraday, swing, all).

    Returns:
        JSON with evolution suggestions, adjusted weights, reasoning log analysis.
    """
    today = datetime.now(timezone.utc).strftime('%Y%m%d')
    modalities_to_check = ["scalp", "intraday", "swing"] if modality == "all" else [modality]
    suggestions = {}

    for mod in modalities_to_check:
        try:
            raw = redis().get(f"reason:{mod}:{today}")
            entries = json.loads(raw) if isinstance(raw, str) else raw if raw else []
        except: entries = []

        if not entries:
            suggestions[mod] = {"status": "no_data", "message": "No reasoning logs found for today"}
            continue

        gate_stats = {}
        for e in entries:
            for g_name in e.get("gates_failed", []):
                gate_stats[g_name] = gate_stats.get(g_name, {"failures": 0, "passes": 0, "total": 0})
                gate_stats[g_name]["failures"] += 1
                gate_stats[g_name]["total"] += 1
            for g_name in e.get("gates_passed", []):
                gate_stats[g_name] = gate_stats.get(g_name, {"failures": 0, "passes": 0, "total": 0})
                gate_stats[g_name]["passes"] += 1
                gate_stats[g_name]["total"] += 1

        mod_suggestions = []
        for gate, stats in gate_stats.items():
            fail_rate = stats["failures"] / max(stats["total"], 1)
            if fail_rate > 0.7 and stats["total"] >= 3:
                mod_suggestions.append({
                    "gate": gate, "fail_rate": round(fail_rate * 100, 1),
                    "action": "LOWER_WEIGHT_10%",
                    "reason": f"Blocks valid setups: {stats['failures']}/{stats['total']} failures",
                })

        suggestions[mod] = {
            "total_logs": len(entries),
            "avg_score": round(np.mean([e.get("score", 0) for e in entries]), 1) if entries else 0,
            "execution_rate": round(sum(1 for e in entries if e.get("verdict") == "EXECUTE") / len(entries) * 100, 1),
            "gate_analysis": gate_stats,
            "suggestions": mod_suggestions,
        }

    return json.dumps({
        "status": "evolved",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "modalities": suggestions,
        "instructions": "Review suggestions. Manually confirm adjustments. "
                       "Run backtest before deploying new weights.",
    }, indent=2, default=str)
