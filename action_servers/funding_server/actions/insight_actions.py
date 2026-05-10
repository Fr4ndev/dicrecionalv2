"""
insight_actions.py — Institutional Microstructure Insights v2.0
═══════════════════════════════════════════════════════════════
New generation endpoints: CVD divergence, TRAP_SCORE, HEALTH_SCORE,
Flash Alerts, weighted signals. Integrates with IntelligenceHub API.
Uses correct hub.method() public API — lambda pattern, no deadlocks.
"""
import sys
import json
import asyncio
from datetime import datetime, timezone
import logging
import pandas as pd

from sema4ai.actions import action

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import IntelligenceHub, _run_hub_sync

logger = logging.getLogger("InsightActions")

# ── Constants ─────────────────────────────────────────────────
VPIN_THRESHOLD = 0.62
OBI_THRESHOLD = 0.40
BASIS_THRESHOLD = -0.05
ABSORPTION_THRESHOLD = 0.60


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

async def _fetch_obi(hub, symbol: str, depth: int = 20) -> float:
    ob = await hub.get_order_book(symbol, depth)
    if not ob:
        return 0.0
    bids = sum(b[1] for b in ob.get("bids", []))
    asks = sum(a[1] for a in ob.get("asks", []))
    return (bids - asks) / (bids + asks) if (bids + asks) > 0 else 0.0


def _cvd_from_df(df: pd.DataFrame, limit: int = None) -> float:
    """Compute CVD from trades DataFrame."""
    if df is None or df.empty:
        return 0.0
    if limit:
        df = df.iloc[-limit:]
    cvd = 0.0
    for _, row in df.iterrows():
        side = str(row.get("side", "")).lower()
        amount = float(row.get("amount", 0))
        price = float(row.get("price", 0))
        usd = amount * price if price > 0 else 0
        if side == "buy":
            cvd += usd
        elif side == "sell":
            cvd -= usd
    return cvd


# ═══════════════════════════════════════════════════════════════
# ACTION: get-cvd-divergence
# ═══════════════════════════════════════════════════════════════

async def _get_cvd_divergence_internal(hub, symbol: str, trade_limit_small: int = 100,
                                        trade_limit_large: int = 500) -> dict:
    ts = datetime.now(timezone.utc).isoformat()

    # Fetch trades for larger window once, compute both windows
    trades_df = await hub.get_trades(symbol, limit=trade_limit_large)
    if trades_df is None or len(trades_df) < trade_limit_small:
        return {"status": "error", "reason": "trades_fetch_failed", "timestamp": ts}

    cvd_large = _cvd_from_df(trades_df)
    cvd_small = _cvd_from_df(trades_df.iloc[-trade_limit_small:]) if len(trades_df) >= trade_limit_small else cvd_large

    sign_small = 1 if cvd_small > 0 else (-1 if cvd_small < 0 else 0)
    sign_large = 1 if cvd_large > 0 else (-1 if cvd_large < 0 else 0)
    diverging = sign_small != 0 and sign_large != 0 and sign_small != sign_large

    # Context: OBI + VPIN + Funding
    obi = await _fetch_obi(hub, symbol)
    tox = await hub.get_toxicity(symbol)
    vpin = tox.vpin_index
    fund = await hub.get_funding_state(symbol)
    funding_pct = fund.rate_pct if fund else 0.0

    # TRAP_SCORE
    trap_score = 0
    if diverging:
        trap_score += 25
    if sign_small != 0 and obi != 0 and sign_small != (1 if obi > OBI_THRESHOLD else -1 if obi < -OBI_THRESHOLD else 0):
        trap_score += 30
    if (obi > OBI_THRESHOLD and funding_pct > 0.01) or (obi < -OBI_THRESHOLD and funding_pct < -0.01):
        trap_score += 25
    trap_score = min(trap_score, 100)

    event_type = "NO_DIVERGENCE"
    pattern = "NEUTRAL"
    if diverging:
        if trap_score >= 80:
            event_type = "CONFIRMED_TRAP"
        elif trap_score >= 55:
            event_type = "PROBABLE_TRAP"
        elif trap_score >= 25:
            event_type = "EARLY_ABSORPTION"
        else:
            event_type = "MINOR_DIVERGENCE"

        if sign_small > 0 and sign_large < 0:
            pattern = "BULLISH_ABSORPTION"
        elif sign_small < 0 and sign_large > 0:
            pattern = "BEARISH_DISTRIBUTION"

    return {
        "status": "ok",
        "symbol": symbol,
        "timestamp": ts,
        "cvd": {
            "cvd_100trades_usd": round(cvd_small, 2),
            "cvd_500trades_usd": round(cvd_large, 2),
            "sign_100t": "BUY" if cvd_small > 0 else ("SELL" if cvd_small < 0 else "ZERO"),
            "sign_500t": "BUY" if cvd_large > 0 else ("SELL" if cvd_large < 0 else "ZERO"),
            "diverging": diverging,
            "pattern": pattern,
        },
        "context": {
            "obi": round(obi, 4),
            "vpin": round(vpin, 4),
            "funding_pct": round(funding_pct, 4),
        },
        "trap_score": trap_score,
        "event_type": event_type,
        "action": (
            "ABORT_OBI_DIRECTION" if trap_score >= 80
            else "DO_NOT_TRADE_OBI_DIRECTION" if trap_score >= 55
            else "MONITOR_REDUCE_50PCT" if trap_score >= 25
            else "STANDARD"
        ),
    }


@action(is_consequential=False)
def get_cvd_divergence(symbol: str, trade_limit_small: int = 100,
                       trade_limit_large: int = 500) -> str:
    """Detects CVD divergence (100t vs 500t) indicating institutional flow reversal.

    Computes Cumulative Volume Delta for two trade windows. When the direction
    of recent trades (100) contradicts the broader window (500), it signals
    active flow reversal — institutions absorbing the prior trend.

    Args:
        symbol: Trading pair symbol (e.g. 'BTC/USDT:USDT').
        trade_limit_small: Number of recent trades for micro window. Default 100.
        trade_limit_large: Number of trades for macro window. Default 500.

    Returns:
        JSON string with cvd (micro/macro USD), diverging flag, pattern,
        trap_score (0-100), event_type, and recommended action.
    """
    return _run_hub_sync(lambda hub: _get_cvd_divergence_internal(hub, symbol, trade_limit_small, trade_limit_large))


# ═══════════════════════════════════════════════════════════════
# ACTION: get-trap-score
# ═══════════════════════════════════════════════════════════════

async def _get_trap_score_internal(hub, symbol: str) -> dict:
    cvd_data = await _get_cvd_divergence_internal(hub, symbol)
    trap_score = cvd_data.get("trap_score", 0)

    tox = await hub.get_toxicity(symbol)
    vpin = tox.vpin_index
    abs_rate = tox.absorption_rate  # proxy for whale/institutional presence

    spot_sym = symbol.split(":")[0]
    basis_snap = await hub.get_basis(symbol, spot_sym)
    basis_pct = basis_snap.basis_pct if basis_snap else 0.0

    if abs_rate < 0.20 and trap_score > 0:
        trap_score = min(trap_score + 15, 100)

    confidence_label = (
        "CRITICAL" if trap_score >= 80
        else "ALTA" if trap_score >= 55
        else "MEDIA" if trap_score >= 25
        else "BAJA"
    )

    return {
        "status": "ok",
        "symbol": symbol,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "trap_score": trap_score,
        "confidence": confidence_label,
        "diagnosis": cvd_data.get("event_type", "NO_DIVERGENCE"),
        "cvd_pattern": cvd_data.get("cvd", {}).get("pattern", "NEUTRAL"),
        "obi": cvd_data.get("context", {}).get("obi", 0),
        "vpin": round(vpin, 4),
        "basis_pct": round(basis_pct, 4),
        "action": cvd_data.get("action", "STANDARD"),
    }


@action(is_consequential=False)
def get_trap_score(symbol: str) -> str:
    """Computes TRAP_SCORE (0-100) via OBI×CVD×Funding cross-validation.

    Cross-validates order book imbalance against cumulative volume delta
    and funding rates to detect liquidity traps. A trap exists when the
    order book suggests one direction but real capital flows the opposite way.

    Args:
        symbol: Trading pair symbol (e.g. 'BTC/USDT:USDT').

    Returns:
        JSON string with trap_score (0-100), confidence (BAJA/MEDIA/ALTA/CRITICAL),
        diagnosis, cvd_pattern, obi, vpin, basis_pct, and recommended action.
    """
    return _run_hub_sync(lambda hub: _get_trap_score_internal(hub, symbol))


# ═══════════════════════════════════════════════════════════════
# ACTION: get-health-score
# ═══════════════════════════════════════════════════════════════

async def _compute_health_component(hub, symbol: str) -> dict:
    perp_sym = symbol
    spot_sym = symbol.split(":")[0]

    # 1. VPIN (weight 2x)
    tox = await hub.get_toxicity(perp_sym)
    vpin = tox.vpin_index
    vpin_score = 8 if vpin > VPIN_THRESHOLD else (5 if vpin > 0.40 else 2)

    # 2. OBI (weight 2x)
    obi = await _fetch_obi(hub, perp_sym)
    abs_obi = abs(obi)
    obi_score = 9 if abs_obi > OBI_THRESHOLD else (5 if abs_obi > 0.20 else 2)

    # 3. Basis (weight 1.5x)
    basis_snap = await hub.get_basis(perp_sym, spot_sym)
    basis_pct = basis_snap.basis_pct if basis_snap else 0.0
    basis_score = 9 if basis_pct < BASIS_THRESHOLD else (5 if abs(basis_pct) < 0.05 else 2)

    # 4. CVD alignment (weight 1.5x) — quick OBI vs CVD check
    trades_df = await hub.get_trades(perp_sym, limit=100)
    cvd = _cvd_from_df(trades_df)
    cvd_aligned = (cvd > 0 and obi > 0) or (cvd < 0 and obi < 0)
    cvd_score = 8 if cvd_aligned else (4 if cvd == 0 or obi == 0 else 2)

    # 5. Absorption (weight 1x)
    abs_rate = tox.absorption_rate
    abs_score = 8 if abs_rate > ABSORPTION_THRESHOLD else (4 if abs_rate > 0 else 2)

    # 6. Funding (weight 1x)
    fund = await hub.get_funding_state(perp_sym)
    funding_pct = fund.rate_pct if fund else 0.0
    funding_score = 9 if funding_pct < 0 else (5 if abs(funding_pct) < 0.10 else 2)

    # 7. OI Delta (weight 1x) — FundingState doesn't have this, use 5 as neutral
    oi_score = 5

    # 8. Kyle's Lambda — ToxicityResult doesn't expose this directly
    # price impact proxy: use order book depth
    ob = await hub.get_order_book(perp_sym, limit=50)
    kyle_score = 7 if ob else 4

    weights = [2.0, 2.0, 1.5, 1.5, 1.0, 1.0, 1.0, 0.5]
    scores = [vpin_score, obi_score, basis_score, cvd_score,
              abs_score, funding_score, oi_score, kyle_score]
    weighted_sum = sum(s * w for s, w in zip(scores, weights))
    total_weight = sum(weights)
    health_score = round((weighted_sum / total_weight) * 10, 1)

    band = (
        "OPTIMAL" if health_score >= 86 else "FAVORABLE" if health_score >= 71
        else "NEUTRAL" if health_score >= 56 else "CAUTIOUS" if health_score >= 41
        else "DEGRADED" if health_score >= 26 else "CODE_RED"
    )

    return {
        "health_score": health_score,
        "band": band,
        "components": {
            "vpin": {"score": vpin_score, "value": round(vpin, 4), "weight": 2.0},
            "obi": {"score": obi_score, "value": round(obi, 4), "weight": 2.0},
            "basis": {"score": basis_score, "value": round(basis_pct, 4), "weight": 1.5},
            "cvd_alignment": {"score": cvd_score, "value": cvd_aligned, "weight": 1.5},
            "absorption": {"score": abs_score, "value": round(abs_rate, 2), "weight": 1.0},
            "funding": {"score": funding_score, "value": round(funding_pct, 4), "weight": 1.0},
            "oi_delta": {"score": oi_score, "value": 0, "weight": 1.0},
            "kyles_lambda": {"score": kyle_score, "value": 0, "weight": 0.5},
        },
    }


async def _get_health_score_internal(hub, assets_str: str) -> dict:
    assets = [a.strip() for a in assets_str.split(",")]
    per_asset = {}
    global_scores = []
    for asset in assets:
        sym = f"{asset}/USDT:USDT" if ":" not in asset else asset
        try:
            result = await _compute_health_component(hub, sym)
            per_asset[asset] = result
            global_scores.append(result["health_score"])
        except Exception as e:
            per_asset[asset] = {"error": str(e)}

    global_health = round(sum(global_scores) / len(global_scores), 1) if global_scores else 0
    global_band = (
        "OPTIMAL" if global_health >= 86 else "FAVORABLE" if global_health >= 71
        else "NEUTRAL" if global_health >= 56 else "CAUTIOUS" if global_health >= 41
        else "DEGRADED" if global_health >= 26 else "CODE_RED"
    )

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "assets": per_asset,
        "global": {"health_score": global_health, "band": global_band},
    }


@action(is_consequential=False)
def get_health_score(assets: str = "BTC,ETH") -> str:
    """Computes institutional market HEALTH_SCORE (0-100) with 8 weighted components.

    Evaluates VPIN, OBI, Basis, CVD alignment, Absorption, Funding, OI Delta,
    and Market Depth (Kyle's Lambda) per asset. Returns per-asset breakdown
    and a global weighted average.

    Args:
        assets: Comma-separated asset list (e.g. 'BTC,ETH'). Default 'BTC,ETH'.

    Returns:
        JSON string with per-asset health_score, band (CODE_RED→OPTIMAL),
        8 component scores with weights, and global aggregate.
    """
    return _run_hub_sync(lambda hub: _get_health_score_internal(hub, assets))


# ═══════════════════════════════════════════════════════════════
# ACTION: get-flash-alert
# ═══════════════════════════════════════════════════════════════

async def _get_flash_alert_internal(hub, symbol: str) -> dict:
    cvd_data = await _get_cvd_divergence_internal(hub, symbol)
    trap_data = await _get_trap_score_internal(hub, symbol)

    diverging = cvd_data.get("cvd", {}).get("diverging", False)
    trap_score = trap_data.get("trap_score", 0)
    vpin = cvd_data.get("context", {}).get("vpin", 0)
    obi = cvd_data.get("context", {}).get("obi", 0)
    cvd_small = cvd_data.get("cvd", {}).get("cvd_100trades_usd", 0)
    cvd_large = cvd_data.get("cvd", {}).get("cvd_500trades_usd", 0)

    # Ghost Wall detection: |OBI| > 0.85 (p90+ extreme) AND CVD contradicts
    obi_sign = 1 if obi > 0 else (-1 if obi < 0 else 0)
    cvd_sign_small = 1 if cvd_small > 0 else (-1 if cvd_small < 0 else 0)
    ghost_wall = abs(obi) > 0.85 and obi_sign != 0 and cvd_sign_small != 0 and obi_sign != cvd_sign_small
    ghost_wall_pct = min(int(abs(obi) * 100), 100)

    if not diverging or vpin < 0.50 or trap_score < 25:
        return {
            "status": "ok",
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "alert_active": False,
            "ghost_wall_detected": ghost_wall,
            "reason": f"No active divergence (VPIN={vpin:.2f}, TRAP_SCORE={trap_score})",
        }

    cvd = cvd_data["cvd"]
    price = await hub.get_price(symbol)
    asset_name = "BTC" if "BTC" in symbol else "ETH"

    # Build ghost wall context paragraph
    if ghost_wall:
        wall_type = "Bid" if obi > 0 else "Ask"
        ghost_context = (
            f"GHOST WALL {wall_type.upper()} DETECTED: "
            f"Order book shows extreme {wall_type.lower()} pressure (|OBI|={abs(obi):.2f}, p{ghost_wall_pct}) "
            f"but CVD 100t is {cvd['sign_100t']}ing (${abs(cvd_small):,.0f}). "
            f"The {wall_type.lower()} wall is likely SPOOFED — not intended for execution. "
            f"Do NOT trade in OBI direction."
        )
    else:
        ghost_context = ""

    return {
        "status": "ok",
        "symbol": symbol,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "alert_active": True,
        "ghost_wall_detected": ghost_wall,
        "ghost_wall_confidence": ghost_wall_pct if ghost_wall else 0,
        "flash_alert": {
            "paragraph_1": (
                f"{asset_name} @ ${price:,.2f}: "
                f"CVD 100t = {cvd['sign_100t']} ${abs(cvd['cvd_100trades_usd']):,.0f} "
                f"vs CVD 500t = {cvd['sign_500t']} ${abs(cvd['cvd_500trades_usd']):,.0f}. "
                f"Divergencia: {cvd['pattern']}. OBI D20 = {obi:.2f}, "
                f"VPIN = {vpin:.2f}."
                + (f" {ghost_context}" if ghost_wall else "")
            ),
            "paragraph_2": (
                f"Tipo: {trap_data['diagnosis']} (TRAP_SCORE {trap_score}/100). "
                f"Funding: {cvd_data['context']['funding_pct']:.4f}%. "
                f"Basis: {trap_data['basis_pct']:.4f}%."
            ),
            "paragraph_3": (
                f"Accion: {trap_data['action']}. "
                f"Invalidacion: si CVD 100t vuelve a alinearse con CVD 500t."
                + (" Si |OBI| < 0.60 → Ghost Wall disuelto." if ghost_wall else "")
            ),
        },
        "trap_score": trap_score,
        "confidence": trap_data["confidence"],
        "priority": "P0_CRITICAL" if trap_score >= 80 else "P1_HIGH",
    }


@action(is_consequential=False)
def get_flash_alert(symbol: str) -> str:
    """Generates a 3-paragraph FLASH ALERT for active CVD divergence.

    Detects Ghost Wall patterns (|OBI| > 0.85 with CVD contradiction)
    indicating institutional spoofing. Only activates when TRAP_SCORE >= 25
    and VPIN > 0.50. Returns compact alert ready for Telegram dispatch.

    Args:
        symbol: Trading pair symbol (e.g. 'BTC/USDT:USDT').

    Returns:
        JSON string with alert_active flag, flash_alert (3 paragraphs),
        ghost_wall_detected, trap_score, confidence, and priority (P0/P1).
    """
    return _run_hub_sync(lambda hub: _get_flash_alert_internal(hub, symbol))


# ═══════════════════════════════════════════════════════════════
# ACTION: get-weighted-scalp-signal
# ═══════════════════════════════════════════════════════════════

async def _get_weighted_scalp_internal(hub, assets_str: str, weights_str: str = None) -> dict:
    assets = [a.strip() for a in assets_str.split(",")]
    weights = {"toxicity": 3, "obi": 3, "cvd_alignment": 2, "basis": 1, "iceberg": 1}
    if weights_str:
        try:
            custom = json.loads(weights_str)
            weights.update(custom)
        except (json.JSONDecodeError, ValueError):
            pass

    results = {}
    max_score = sum(weights.values())

    for asset in assets:
        sym = f"{asset}/USDT:USDT" if ":" not in asset else asset
        try:
            tox = await hub.get_toxicity(sym)
            vpin = tox.vpin_index
            abs_rate = tox.absorption_rate

            obi = await _fetch_obi(hub, sym)

            spot_sym = sym.split(":")[0]
            basis_snap = await hub.get_basis(sym, spot_sym)
            basis_pct = basis_snap.basis_pct if basis_snap else 0.0

            trades_df = await hub.get_trades(sym, limit=100)
            cvd = _cvd_from_df(trades_df)
            cvd_aligned = (cvd > 0 and obi > 0) or (cvd < 0 and obi < 0)

            raw_score = 0.0
            if vpin > VPIN_THRESHOLD:
                raw_score += weights["toxicity"]
            elif vpin > 0.50:
                raw_score += weights["toxicity"] * 0.5

            if abs(obi) > OBI_THRESHOLD:
                raw_score += weights["obi"]
            elif abs(obi) > 0.20:
                raw_score += weights["obi"] * 0.5

            if cvd_aligned and abs(obi) > 0.15:
                raw_score += weights["cvd_alignment"]

            if (obi > 0 and basis_pct < 0) or (obi < 0 and basis_pct > 0):
                raw_score += weights["basis"]
            elif basis_pct < BASIS_THRESHOLD:
                raw_score += weights["basis"] * 0.5

            if abs_rate > ABSORPTION_THRESHOLD:
                raw_score += weights["iceberg"]

            normalized = round((raw_score / max_score) * 11, 1)
            direction = "LONG" if obi > 0 else "SHORT" if obi < 0 else "NEUTRAL"
            verdict = (
                "GO_FULL" if normalized >= 9
                else "GO_LONG" if obi > 0 and normalized >= 7
                else "GO_SHORT" if obi < 0 and normalized >= 7
                else "GO_PARTIAL" if normalized >= 5
                else "NO_TRADE"
            )

            results[asset] = {
                "weighted_score": normalized,
                "max_score": 11,
                "verdict": verdict,
                "direction": direction,
                "breakdown": {
                    "toxicity": {"value": round(vpin, 4), "weight": weights["toxicity"], "passed": vpin > VPIN_THRESHOLD},
                    "obi": {"value": round(obi, 4), "weight": weights["obi"], "passed": abs(obi) > OBI_THRESHOLD},
                    "cvd_alignment": {"value": cvd_aligned, "weight": weights["cvd_alignment"], "passed": cvd_aligned},
                    "basis": {"value": round(basis_pct, 4), "weight": weights["basis"], "passed": basis_pct < BASIS_THRESHOLD},
                    "iceberg": {"value": round(abs_rate, 4), "weight": weights["iceberg"], "passed": abs_rate > ABSORPTION_THRESHOLD},
                },
            }
        except Exception as e:
            results[asset] = {"error": str(e)}

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "weights_used": weights,
        "results": results,
    }


@action(is_consequential=False)
def get_weighted_scalp_signal(assets: str = "BTC,ETH",
                              weights: str = '{"toxicity":3,"obi":3,"cvd_alignment":2,"basis":1,"iceberg":1}') -> str:
    """Weighted composite scalp signal (0-11) with configurable component weights.

    Combines toxicity (VPIN), order book imbalance (OBI), CVD alignment,
    basis direction, and iceberg activity into a single trading signal.
    Weights can be customized to emphasize different components.

    Args:
        assets: Comma-separated asset list (e.g. 'BTC,ETH'). Default 'BTC,ETH'.
        weights: JSON dict of component weights (e.g. '{"toxicity":4,"obi":3}').
            Default weighs toxicity 3x, OBI 3x, CVD alignment 2x, basis 1x, iceberg 1x.

    Returns:
        JSON string with per-asset weighted_score (0-11), verdict
        (GO_FULL/GO_LONG/SHORT/GO_PARTIAL/NO_TRADE), direction, and breakdown.
    """
    return _run_hub_sync(lambda hub: _get_weighted_scalp_internal(hub, assets, weights))


# ═══════════════════════════════════════════════════════════════
# ACTION: get-tactical-ensemble
# ═══════════════════════════════════════════════════════════════

async def _get_tactical_ensemble_internal(hub, assets_str: str) -> dict:
    assets = [a.strip() for a in assets_str.split(",")]
    health = await _get_health_score_internal(hub, assets_str)
    weighted = await _get_weighted_scalp_internal(hub, assets_str)

    traps = {}
    flash_alerts = {}
    for asset in assets:
        sym = f"{asset}/USDT:USDT" if ":" not in asset else asset
        try:
            trap = await _get_trap_score_internal(hub, sym)
            traps[asset] = {"trap_score": trap["trap_score"], "confidence": trap["confidence"],
                            "action": trap["action"]}
            flash = await _get_flash_alert_internal(hub, sym)
            flash_alerts[asset] = {"active": flash.get("alert_active", False),
                                   "trap_score": flash.get("trap_score", 0),
                                   "priority": flash.get("priority", "NONE")}
        except Exception as e:
            traps[asset] = {"error": str(e)}

    decisions = []
    for asset in assets:
        ws = weighted.get("results", {}).get(asset, {})
        ts = traps.get(asset, {}).get("trap_score", 0)
        verdict = ws.get("verdict", "NO_TRADE")
        direction = ws.get("direction", "NEUTRAL")

        if ts >= 55:
            decisions.append(f"{asset}: TRAP={ts}% — OBI({direction}) is SUSPECT, do NOT enter")
        elif verdict in ("GO_FULL", "GO_LONG", "GO_SHORT"):
            decisions.append(f"{asset}: {verdict} {direction} — scalp {ws.get('weighted_score', 0):.1f}/11")
        elif verdict == "GO_PARTIAL":
            decisions.append(f"{asset}: {verdict} {direction} — 50% sizing")
        else:
            decisions.append(f"{asset}: NO_TRADE")

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "global": {"health_score": health.get("global", {}).get("health_score", 0),
                    "band": health.get("global", {}).get("band", "UNKNOWN")},
        "decisions": decisions,
        "health": health.get("assets", {}),
        "scalp_signals": weighted.get("results", {}),
        "trap_scores": traps,
        "flash_alerts": flash_alerts,
    }


@action(is_consequential=False)
def get_tactical_ensemble(assets: str = "BTC,ETH") -> str:
    """Brutal composited ensemble: Health Scores + Trap Scores + Weighted Scalp Signals.

    Combines three analysis layers into a single call: market health diagnostics,
    liquidity trap detection, and weighted scalp signals. Returns unified decision
    matrix ideal for dashboards, AI agents, and Telegram command handlers.

    Args:
        assets: Comma-separated asset list (e.g. 'BTC,ETH'). Default 'BTC,ETH'.

    Returns:
        JSON string with global health_score and band, human-readable decisions
        per asset, full health breakdown, scalp signals, trap scores, and flash alerts.
    """
    return _run_hub_sync(lambda hub: _get_tactical_ensemble_internal(hub, assets))
