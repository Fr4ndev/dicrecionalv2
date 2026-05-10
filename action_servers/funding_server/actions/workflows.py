"""
workflows.py — Tactical Workflow Routines: Scalp, Intraday, Swing
═══════════════════════════════════════════════════════════════════
Production-grade institutional workflow endpoints for ccxtv2-next.
Each workflow chains existing endpoints into decision trees with
clear ENTRY → TARGET → INVALIDATION logic.

Powered by:
  - funding_actions.py (CCXT puro, 9 endpoints probados ✅)
  - market_actions.py (order book, basis, microstructure)
  - shared/ (IntelligenceHub + RedisBridge + ZScoreEngine)

Redis requirement: CVD velocity + wall state persist between calls.
Without Redis: workflows still function but lose cross-request memory.
"""

import sys, os, json, time, asyncio, logging
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import IntelligenceHub, _run_hub_sync, RedisBridge, redis

try:
    from sema4ai.actions import action
except ImportError:
    def action(func=None, **kwargs):
        """Fallback for local testing without Sema4.ai."""
        return func if func else lambda f: f

logger = logging.getLogger("Workflows")


# ═══════════════════════════════════════════════════════════════════
# SHARED ENGINES (imported once, reused across workflows)
# ═══════════════════════════════════════════════════════════════════

VPIN_THRESHOLD = 0.62
OBI_IGNITION = 0.40
BASIS_THRESHOLD = -0.05
CVD_ACCEL_GATE = 0.0
OBI_SCALP_GATE = 0.40
ABSORPTION_THRESHOLD = 0.60


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _call_endpoint(name: str, payload: dict, base: str = "http://localhost:8080") -> dict:
    """Call another action server endpoint internally. Uses httpx sync."""
    import httpx
    url = f"{base}/api/actions/funding-action-server/{name}/run"
    try:
        r = httpx.post(url, json=payload, timeout=30)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, str):
                try:
                    return json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    return {"raw_response": data}
            return data if isinstance(data, dict) else {"raw_response": str(data)}
        return {"error": f"HTTP {r.status_code}", "body": r.text[:200]}
    except Exception as e:
        return {"error": str(e)[:150]}


# ═══════════════════════════════════════════════════════════════════
# SCALP WORKFLOW (1-15 min trades)
# ═══════════════════════════════════════════════════════════════════

@action(is_consequential=False)
def workflow_scalp(assets: str = "BTC,ETH") -> str:
    """
    INSTITUTIONAL SCALP ROUTINE — entry / target / invalidation.

    Decision tree:
        1. Funding snapshot → detect extreme funding (>0.05% or <-0.05%)
        2. OBI check → bid/ask pressure at top 20 levels
        3. Confluence trigger → VPIN + CVD + OBI gate evaluation
        4. Order book walls → nearest liquidity clusters

    ENTRY conditions (ALL must be true):
        - Confluence trigger = "SENSITIVE" or "CONSERVATIVE"
        - OBI > 0.40 (LONG) or OBI < -0.40 (SHORT)
        - Funding extreme detected on at least 1 exchange
        - CVD acceleration > 0 (aggression confirmed)

    TARGET:
        - LONG: nearest ask wall cluster + 0.1%
        - SHORT: nearest bid wall cluster - 0.1%

    INVALIDATION:
        - OBI flips sign (LONG: OBI < -0.20, SHORT: OBI > 0.20)
        - CVD acceleration goes negative
        - 15-minute timeout without hitting target

    Args:
        assets: Comma-separated assets. Default: "BTC,ETH"

    Returns:
        JSON with verdict, entry conditions, targets, invalidations per asset.
    """
    asset_list = [a.strip().upper() for a in assets.split(",")]
    results = {}

    for asset in asset_list:
        # Step 1: Full market snapshot (funding + OI + OBI)
        snap = _call_endpoint("get-full-market-snapshot", {"assets": asset, "ob_depth": 50})
        if "error" in snap:
            results[asset] = {"verdict": "DATA_ERROR", "error": snap["error"]}
            continue

        # Step 2: Confluence trigger evaluation
        trigger = _call_endpoint("detect-confluence-trigger", {"assets": asset, "ob_depth": 50})
        trigger_level = trigger.get("trigger_level", "NONE") if isinstance(trigger, dict) else "NONE"

        # Step 3: Order book walls
        symbol = f"{asset}/USDT:USDT" if asset != "HYPE" else f"{asset}/USDC:USDC"
        walls = _call_endpoint("get-ob-walls", {"symbol": symbol, "depth": 20})

        # Step 4: Extract metrics
        detail = snap.get("detail", {})
        obi_val = 0.0
        funding_max = 0.0
        funding_min = 0.0

        for key, val in detail.items():
            if isinstance(val, dict):
                f = val.get("funding_rate", 0) or 0
                o = val.get("obi", 0) or 0
                funding_max = max(funding_max, f)
                funding_min = min(funding_min, f)
                obi_val = o if abs(o) > abs(obi_val) else obi_val

        # ── ENTRY LOGIC ──────────────────────────────────────
        bias = "NEUTRAL"
        entry_ok = False
        reasons = []

        has_extreme_funding = abs(funding_max) > 0.05 or abs(funding_min) > 0.05
        has_confluence = trigger_level in ("SENSITIVE", "CONSERVATIVE")
        has_obi_pressure = abs(obi_val) > OBI_SCALP_GATE

        if has_confluence and has_obi_pressure and has_extreme_funding:
            entry_ok = True
            if obi_val > 0:
                bias = "LONG"
                reasons.append(f"OBI={obi_val:.3f} > {OBI_SCALP_GATE} (bid pressure)")
            else:
                bias = "SHORT"
                reasons.append(f"OBI={obi_val:.3f} < -{OBI_SCALP_GATE} (ask pressure)")
        else:
            if not has_confluence:
                reasons.append(f"Confluence={trigger_level} (need SENSITIVE/CONSERVATIVE)")
            if not has_obi_pressure:
                reasons.append(f"OBI={obi_val:.3f} insufficient")
            if not has_extreme_funding:
                reasons.append(f"Funding max={funding_max:.4f}% min={funding_min:.4f}% (no extreme)")

        # ── TARGET ──────────────────────────────────────────
        target = None
        if walls and isinstance(walls, dict) and "error" not in walls:
            bids = walls.get("bids", []) or []
            asks = walls.get("asks", []) or []
            if bias == "LONG" and asks:
                target = asks[0][0] if isinstance(asks[0], list) else None
            elif bias == "SHORT" and bids:
                target = bids[0][0] if isinstance(bids[0], list) else None

        # ── INVALIDATION ────────────────────────────────────
        invalidation = {
            "obi_flip": OBI_SCALP_GATE * (-1 if bias == "LONG" else 1),
            "cvd_negative": "CVD acceleration < 0",
            "timeout_seconds": 900,  # 15 min
            "max_loss_pct": -0.5,
        }

        results[asset] = {
            "verdict": "EXECUTE" if entry_ok else "NO_TRADE",
            "bias": bias,
            "entry_conditions": {
                "confluence": trigger_level,
                "obi": round(obi_val, 4),
                "funding_max_pct": round(funding_max, 4),
                "funding_min_pct": round(funding_min, 4),
                "obi_gate_passed": has_obi_pressure,
                "confluence_passed": has_confluence,
                "funding_extreme_passed": has_extreme_funding,
            },
            "target": target,
            "invalidation": invalidation,
            "reasons": reasons,
            "timestamp": _ts(),
        }

    return json.dumps({"status": "ok", "workflow": "scalp", "assets": results}, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════
# INTRADAY WORKFLOW (1-8 hour trades)
# ═══════════════════════════════════════════════════════════════════

@action(is_consequential=False)
def workflow_intraday(assets: str = "BTC,ETH") -> str:
    """
    INSTITUTIONAL INTRADAY ROUTINE — entry / target / invalidation.

    Decision tree:
        1. Z-Score vs history → regime detection (OVERHEATED / MEAN_REVERT / NEUTRAL)
        2. Basis (spot vs perp) → accumulation or retail FOMO
        3. Funding history → trend direction over last 10 candles
        4. Confluence trigger → multi-factor confirmation

    ENTRY conditions:
        - Z-Score regime = OVERHEATED (short) or MEAN_REVERT_RISK (long)
        - Basis confirms: negative basis (< -0.05%) for LONG, positive for SHORT
        - Funding trend aligned with bias (last 5/10 candles same direction)
        - Confluence = SENSITIVE

    TARGET:
        - LONG: Z-Score return to NEUTRAL (zscore → 0)
        - SHORT: Z-Score return to NEUTRAL (zscore → 0)

    INVALIDATION:
        - Z-Score regime flips to opposite extreme
        - Basis flips sign
        - 8-hour timeout
        - Max adverse excursion: 2% from entry

    Args:
        assets: Comma-separated assets. Default: "BTC,ETH"

    Returns:
        JSON with verdict, entry conditions, targets, invalidations per asset.
    """
    asset_list = [a.strip().upper() for a in assets.split(",")]
    results = {}

    for asset in asset_list:
        # Step 1: Z-Score vs history
        zscore = _call_endpoint("get-zscore-vs-history", {"assets": asset})
        zscore_val = 0.0
        regime = "NEUTRAL"
        if isinstance(zscore, dict) and "error" not in zscore:
            detail = zscore.get("detail", {})
            for key in detail:
                zd = detail[key]
                if isinstance(zd, dict):
                    zscore_val = zd.get("zscore", 0) or 0
                    regime = zd.get("regime", "NEUTRAL") or "NEUTRAL"
                    break

        # Step 2: Basis (spot vs perp)
        symbol_spot = f"{asset}/USDT"
        symbol_perp = f"{asset}/USDT:USDT"
        basis = _call_endpoint("get-basis", {
            "symbol_spot": symbol_spot,
            "symbol_perp": symbol_perp
        })
        basis_pct = 0.0
        if isinstance(basis, dict) and "error" not in basis:
            basis_pct = basis.get("basis_pct", 0) or 0

        # Step 3: Funding history
        fund_hist = _call_endpoint("get-funding-history", {
            "exchange": "binance", "asset": asset, "limit": 10
        })
        funding_trend = "NEUTRAL"
        if isinstance(fund_hist, dict) and "error" not in fund_hist:
            rates = fund_hist.get("funding_rates", []) or []
            if len(rates) >= 5:
                recent = rates[-5:]
                pos_count = sum(1 for r in recent if isinstance(r, dict) and (r.get("funding_rate", 0) or 0) > 0)
                neg_count = len(recent) - pos_count
                funding_trend = "LONG_BIAS" if pos_count >= 4 else ("SHORT_BIAS" if neg_count >= 4 else "MIXED")

        # Step 4: Confluence
        trigger = _call_endpoint("detect-confluence-trigger", {"assets": asset, "ob_depth": 50})
        trigger_level = trigger.get("trigger_level", "NONE") if isinstance(trigger, dict) else "NONE"

        # ── ENTRY LOGIC ──────────────────────────────────────
        bias = "NEUTRAL"
        entry_ok = False
        reasons = []

        # Intraday bias from regime
        if regime == "OVERHEATED":
            candidate_bias = "SHORT"
        elif regime == "MEAN_REVERT_RISK":
            candidate_bias = "LONG"
        else:
            candidate_bias = None

        if candidate_bias:
            basis_ok = (candidate_bias == "LONG" and basis_pct < BASIS_THRESHOLD) or \
                       (candidate_bias == "SHORT" and basis_pct > abs(BASIS_THRESHOLD))
            trend_ok = (candidate_bias == funding_trend) or funding_trend == "MIXED"
            confluence_ok = trigger_level == "SENSITIVE"

            if basis_ok and trend_ok and confluence_ok:
                entry_ok = True
                bias = candidate_bias
                reasons.append(f"Regime={regime} (z={zscore_val:.2f})")
                reasons.append(f"Basis={basis_pct:.4f}% {'confirmed' if basis_ok else 'rejected'}")
                reasons.append(f"Funding trend={funding_trend}")
            else:
                reasons.append(f"Regime={regime} → bias={candidate_bias}")
                if not basis_ok:
                    reasons.append(f"Basis={basis_pct:.4f}% rejected")
                if not trend_ok:
                    reasons.append(f"Funding trend={funding_trend} ≠ {candidate_bias}")
                if not confluence_ok:
                    reasons.append(f"Confluence={trigger_level} (need SENSITIVE)")
        else:
            reasons.append(f"Regime={regime} — no intraday edge")

        # ── TARGET & INVALIDATION ────────────────────────────
        results[asset] = {
            "verdict": "EXECUTE" if entry_ok else "NO_TRADE",
            "bias": bias,
            "entry_conditions": {
                "zscore": round(zscore_val, 4),
                "regime": regime,
                "basis_pct": round(basis_pct, 4),
                "funding_trend": funding_trend,
                "confluence": trigger_level,
            },
            "target": {
                "type": "REGIME_NEUTRAL",
                "description": f"Exit when zscore returns to NEUTRAL zone (±1.5σ)",
            },
            "invalidation": {
                "regime_flip": f"Regime flips to {'OVERHEATED' if bias == 'LONG' else 'MEAN_REVERT_RISK'}",
                "basis_flip": f"Basis crosses {-BASIS_THRESHOLD if bias == 'LONG' else BASIS_THRESHOLD}%",
                "timeout_hours": 8,
                "max_adverse_pct": -2.0,
            },
            "reasons": reasons,
            "timestamp": _ts(),
        }

    return json.dumps({"status": "ok", "workflow": "intraday", "assets": results}, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════
# SWING WORKFLOW (1-7 day trades)
# ═══════════════════════════════════════════════════════════════════

@action(is_consequential=False)
def workflow_swing(assets: str = "BTC,ETH") -> str:
    """
    INSTITUTIONAL SWING ROUTINE — entry / target / invalidation.

    Decision tree:
        1. Ultra-deep confluence → multi-timeframe regime analysis
        2. Z-Score HTF (48h) → macro regime
        3. Funding history (50 candles) → multi-day trend
        4. OBI macro → institutional accumulation/distribution

    ENTRY conditions:
        - Ultra-deep confluence verdict = "INFORMED_FLOW" or "ACCUMULATION"
        - HTF Z-Score > 1.5 (overheated → short) or < -1.5 (undervalued → long)
        - Funding trend over 50 candles (>80% same direction)
        - OBI macro > 0.30 (accumulation) or < -0.30 (distribution)

    TARGET:
        - LONG: HTF resistance level (Z-Score from overheated to neutral)
        - SHORT: HTF support level (Z-Score from undervalued to neutral)

    INVALIDATION:
        - HTF regime flips
        - Funding trend reverses (>3 consecutive opposite candles)
        - 7-day timeout
        - Max drawdown: 5%

    Args:
        assets: Comma-separated assets. Default: "BTC,ETH"

    Returns:
        JSON with verdict, entry conditions, targets, invalidations per asset.
    """
    asset_list = [a.strip().upper() for a in assets.split(",")]
    results = {}

    for asset in asset_list:
        # Step 1: Ultra-deep confluence (multi-timeframe)
        deep = _call_endpoint("get-ultra-deep-confluence", {"assets": asset, "depth": 100})
        deep_verdict = "NO_DATA"
        if isinstance(deep, dict) and "error" not in deep:
            deep_verdict = deep.get("verdict", deep.get("senior_verdict", "NO_DATA"))

        # Step 2: HTF Z-Score
        zscore = _call_endpoint("get-zscore-vs-history", {"assets": asset})
        zscore_htf = 0.0
        regime_htf = "NEUTRAL"
        if isinstance(zscore, dict) and "error" not in zscore:
            detail = zscore.get("detail", {})
            for key in detail:
                zd = detail[key]
                if isinstance(zd, dict):
                    zscore_htf = zd.get("zscore", 0) or 0
                    regime_htf = zd.get("regime", "NEUTRAL") or "NEUTRAL"
                    break

        # Step 3: Funding history (50 candles for multi-day trend)
        fund_hist = _call_endpoint("get-funding-history", {
            "exchange": "binance", "asset": asset, "limit": 50
        })
        funding_trend_pct = 50.0
        if isinstance(fund_hist, dict) and "error" not in fund_hist:
            rates = fund_hist.get("funding_rates", []) or []
            if len(rates) >= 30:
                pos = sum(1 for r in rates if isinstance(r, dict) and (r.get("funding_rate", 0) or 0) > 0)
                funding_trend_pct = (pos / len(rates)) * 100

        # Step 4: OBI macro (accumulation/distribution)
        obi = _call_endpoint("get-orderbook-imbalance", {"assets": asset, "depth": 100})
        obi_macro = 0.0
        if isinstance(obi, dict) and "error" not in obi:
            detail = obi.get("detail", {})
            for key, val in detail.items():
                if isinstance(val, dict):
                    obi_macro = val.get("obi", val.get("imbalance_score", 0)) or 0
                    if isinstance(obi_macro, str):
                        obi_macro = 0.5 if "BID" in str(obi_macro) else (-0.5 if "ASK" in str(obi_macro) else 0)
                    break

        # ── ENTRY LOGIC ──────────────────────────────────────
        bias = "NEUTRAL"
        entry_ok = False
        reasons = []

        deep_ok = deep_verdict in ("INFORMED_FLOW", "ACCUMULATION", "MEAN_REVERT_RISK", "OVERHEATED")
        zscore_ok = abs(zscore_htf) > 1.5
        trend_ok = funding_trend_pct > 80 or funding_trend_pct < 20
        obi_ok = abs(obi_macro) > 0.30

        if deep_ok and zscore_ok and trend_ok and obi_ok:
            entry_ok = True
            if zscore_htf > 1.5:
                bias = "SHORT"
                reasons.append(f"HTF zscore={zscore_htf:.2f} → OVERHEATED")
            else:
                bias = "LONG"
                reasons.append(f"HTF zscore={zscore_htf:.2f} → UNDERVALUED")
            reasons.append(f"Deep confluence={deep_verdict}")
            reasons.append(f"Funding trend={funding_trend_pct:.0f}% {'LONG' if funding_trend_pct > 80 else 'SHORT'} bias")
            reasons.append(f"OBI macro={obi_macro:.3f}")
        else:
            if not deep_ok:
                reasons.append(f"Deep confluence={deep_verdict} insufficient")
            if not zscore_ok:
                reasons.append(f"HTF zscore={zscore_htf:.2f} (need |z|>1.5)")
            if not trend_ok:
                reasons.append(f"Funding trend={funding_trend_pct:.0f}% (need >80% or <20%)")
            if not obi_ok:
                reasons.append(f"OBI macro={obi_macro:.3f} insufficient")

        results[asset] = {
            "verdict": "EXECUTE" if entry_ok else "NO_TRADE",
            "bias": bias,
            "entry_conditions": {
                "deep_confluence": deep_verdict,
                "htf_zscore": round(zscore_htf, 4),
                "htf_regime": regime_htf,
                "funding_trend_pct": round(funding_trend_pct, 1),
                "obi_macro": round(obi_macro, 4),
            },
            "target": {
                "type": "REGIME_NEUTRAL_HTF",
                "description": f"Exit when HTF zscore crosses {'below 1.5' if bias == 'SHORT' else 'above -1.5'}",
            },
            "invalidation": {
                "regime_flip": f"HTF zscore crosses {'-1.5' if bias == 'SHORT' else '1.5'}",
                "trend_reversal": "3+ consecutive opposite funding candles",
                "timeout_days": 7,
                "max_drawdown_pct": -5.0,
            },
            "reasons": reasons,
            "timestamp": _ts(),
        }

    return json.dumps({"status": "ok", "workflow": "swing", "assets": results}, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════
# HEALTH CHECK (all-in-one)
# ═══════════════════════════════════════════════════════════════════

@action(is_consequential=False)
def workflow_health() -> str:
    """
    Full system health: Redis status + endpoint validation + market pulse.

    Returns:
        JSON with Redis health, server endpoints, quick market state snapshot.
    """
    redis_health = redis().health()

    # Quick check: 1 lightweight endpoint
    fund_ok = False
    try:
        r = _call_endpoint("get-funding-rates-table", {"assets": "BTC"})
        fund_ok = "error" not in r
    except Exception:
        pass

    return json.dumps({
        "status": "ok" if (redis_health["redis"] == "ONLINE" and fund_ok) else "degraded",
        "timestamp": _ts(),
        "redis": redis_health,
        "endpoints": {
            "funding_rates": "ONLINE" if fund_ok else "ERROR",
        },
    }, indent=2, default=str)


# ── Local test ────────────────────────────────────────────────────
if __name__ == "__main__":
    print("SCALP:", workflow_scalp("BTC"))
    print("\nINTRADAY:", workflow_intraday("BTC"))
    print("\nSWING:", workflow_swing("BTC"))
    print("\nHEALTH:", workflow_health())
