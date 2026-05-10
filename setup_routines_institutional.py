"""
╔══════════════════════════════════════════════════════════════════════════════╗
║   INSTITUTIONAL-GRADE SETUP ROUTINES — Sema4.ai Action Server              ║
║   Modalities: SCALP (1-15M) | INTRADAY (1-8H) | SWING (1-7D)              ║
║   Production-ready. No placeholders. Real thresholds from codebase.        ║
╚══════════════════════════════════════════════════════════════════════════════╝

Gate Architecture:
  Every decision references WHICH signal triggered it (full audit trail).
  Entry  = weighted score from 6+ independent gates. score >= threshold → EXECUTE.
  Target = specific price level calculated from live data (OB walls / SR confluence).
  Invalidation = explicit, testable price or signal condition.

Data Sources (ALL four used per modality where relevant):
  • Action Server  (port 8080)  — microstructure, OBI, funding, OB walls, ICT, SR
  • Hyperliquid    (port 8081)  — HL funding rates, cross-exchange validation
  • Local Engines  (Python)     — SRLevelsEngine, ICTEngine (called via AS snapshot)
  • Redis Bridge                — CVD velocity persistence, wall state, setup results
"""

from sema4ai.actions import action
import httpx
import json
import redis as redis_client
from typing import Optional
import time
import math

# ═══════════════════════════════════════════════════════════════════════════════
# INFRASTRUCTURE LAYER
# ═══════════════════════════════════════════════════════════════════════════════

ACTION_SERVER = "http://localhost:8080"
HL_SERVER     = "http://localhost:8081"
REDIS_HOST    = "localhost"
REDIS_PORT    = 6379

# Execute thresholds — calibrated to gate architectures below
SCALP_THRESHOLD    = 65.0   # /100  — fast setups, lower bar, microstructure-driven
INTRADAY_THRESHOLD = 70.0   # /100  — needs regime + structure alignment
SWING_THRESHOLD    = 72.0   # /100  — highest conviction, multi-TF mandatory


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _as(endpoint: str, params: dict | None = None) -> dict:
    """Call Action Server (port 8080) endpoint. Raises on non-2xx."""
    resp = httpx.get(
        f"{ACTION_SERVER}/{endpoint}",
        params=params or {},
        timeout=12,
    )
    resp.raise_for_status()
    return resp.json()


def _hl(endpoint: str, params: dict | None = None) -> dict:
    """Call Hyperliquid Server (port 8081) endpoint. Raises on non-2xx."""
    resp = httpx.get(
        f"{HL_SERVER}/{endpoint}",
        params=params or {},
        timeout=12,
    )
    resp.raise_for_status()
    return resp.json()


# ── Redis helpers ─────────────────────────────────────────────────────────────

def _r() -> redis_client.Redis:
    return redis_client.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def _r_get(key: str) -> Optional[str]:
    """Redis GET — returns None on any failure (non-critical path)."""
    try:
        return _r().get(key)
    except Exception:
        return None


def _r_set(key: str, value: str, ex: int = 300) -> None:
    """Redis SET — silent failure (non-critical path)."""
    try:
        _r().set(key, value, ex=ex)
    except Exception:
        pass


def _r_lpush_capped(key: str, value: str, maxlen: int = 50) -> None:
    """Push to Redis list and cap length — used for velocity history."""
    try:
        pipe = _r().pipeline()
        pipe.lpush(key, value)
        pipe.ltrim(key, 0, maxlen - 1)
        pipe.execute()
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
# ██████████████████  SCALP SETUP  (1-15 min)  ████████████████████████████████
# ═══════════════════════════════════════════════════════════════════════════════

@action(is_consequential=False)
def setup_scalp_institutional(asset: str = "BTC") -> str:
    """
    Institutional SCALP setup routine — 1 to 15 minute holding period.

    GATE ARCHITECTURE (max 100 pts → EXECUTE if score >= 65):
      G1  VPIN toxicity index             weight = 20
      G2  Order Book Imbalance (OBI)      weight = 20
      G3  CVD velocity delta (Redis)      weight = 15
      G4  FVG + Sweep confirmation 3M/15M weight = 25
      G5  Funding extreme (accelerator)   weight = 10
      G6  OB wall target confirmation     weight = 10

    Direction  = consensus of G1 (CVD divergence) + G2 (OBI sign).
                 CONFLICT between G1/G2 → immediate NO_TRADE.
    Target     = nearest OB liquidity wall in setup direction.
    Invalidation:
      LONG:  OBI flips below -0.35 OR CVD velocity turns negative > 200k
      SHORT: OBI flips above +0.35 OR CVD velocity turns positive > 200k
      Both:  VPIN drops below 0.30 (flow dries up)
    """
    audit: list[str] = []
    score: float = 0.0
    direction: Optional[str] = None      # "LONG" | "SHORT"
    target_price: Optional[float] = None
    invalidation_price: Optional[float] = None
    fvg_mid: Optional[float] = None

    # ── G1: VPIN Toxicity Index (microstructure) ──────────────────────────────
    g1_bias: Optional[str] = None
    try:
        tox = _as("get-toxicity-index", {"asset": asset})
        vpin: float = float(tox.get("vpin", 0.0))
        # cvd_divergence: positive = buy pressure, negative = sell pressure
        cvd_div: float = float(tox.get("cvd_divergence", 0.0))

        if vpin >= 0.65:
            g1_score = 20.0
            g1_bias = "LONG" if cvd_div > 0 else "SHORT"
            audit.append(
                f"G1_VPIN=FULL(vpin={vpin:.3f}≥0.65,"
                f"cvd_div={cvd_div:+.0f},bias={g1_bias})"
            )
        elif vpin >= 0.45:
            g1_score = 10.0
            g1_bias = "LONG" if cvd_div > 0 else "SHORT"
            audit.append(
                f"G1_VPIN=PARTIAL(vpin={vpin:.3f}∈[0.45,0.65),"
                f"bias={g1_bias})"
            )
        else:
            g1_score = 0.0
            audit.append(f"G1_VPIN=MISS(vpin={vpin:.3f}<0.45,toxic_flow_absent)")
        score += g1_score
    except Exception as e:
        audit.append(f"G1_VPIN=ERROR({e})")

    # ── G2: Order Book Imbalance ──────────────────────────────────────────────
    ob_bias: Optional[str] = None
    try:
        obi_data = _as("get-orderbook-imbalance", {"asset": asset})
        obi: float = float(obi_data.get("imbalance", 0.0))   # range -1.0 to +1.0
        ob_bias = "LONG" if obi > 0 else "SHORT"

        if abs(obi) >= 0.60:
            g2_score = 20.0
            audit.append(f"G2_OBI=FULL(obi={obi:+.3f}≥|0.60|,bias={ob_bias})")
        elif abs(obi) >= 0.35:
            g2_score = 10.0
            audit.append(f"G2_OBI=PARTIAL(obi={obi:+.3f}∈|[0.35,0.60)|,bias={ob_bias})")
        else:
            g2_score = 0.0
            ob_bias = None
            audit.append(f"G2_OBI=MISS(obi={obi:+.3f}<|0.35|,no_clear_imbalance)")
        score += g2_score

        # Direction consensus — G1 and G2 must agree (or one be None)
        if g1_bias and ob_bias:
            direction = g1_bias if g1_bias == ob_bias else "CONFLICT"
        else:
            direction = ob_bias or g1_bias

    except Exception as e:
        audit.append(f"G2_OBI=ERROR({e})")
        direction = g1_bias

    # Conflict kills the trade immediately — microstructure is the core signal
    if direction == "CONFLICT":
        return json.dumps({
            "asset": asset,
            "modality": "SCALP",
            "decision": "NO_TRADE",
            "reason": "MICROSTRUCTURE_CONFLICT: VPIN bias ≠ OBI bias — flow not confirmed",
            "score": round(score, 1),
            "audit_trail": audit,
        }, indent=2)

    # ── G3: CVD Velocity via Redis (cross-request persistence) ───────────────
    try:
        snap = _as("get-full-market-snapshot", {"asset": asset})
        cvd_now: float = float(snap.get("cvd", 0.0))

        cvd_prev_raw = _r_get(f"cvd_last:{asset}")
        cvd_prev = float(cvd_prev_raw) if cvd_prev_raw else cvd_now
        cvd_velocity = cvd_now - cvd_prev   # USD delta since last call

        # Persist for next call
        _r_set(f"cvd_last:{asset}", str(cvd_now), ex=120)
        _r_lpush_capped(f"cvd_hist:{asset}", str(cvd_velocity))

        cvd_aligned = (cvd_velocity > 0 and direction == "LONG") or \
                      (cvd_velocity < 0 and direction == "SHORT")
        mag = abs(cvd_velocity)

        if mag >= 500_000 and cvd_aligned:
            g3_score = 15.0
            audit.append(
                f"G3_CVD=FULL(Δ={cvd_velocity:+,.0f},ACCELERATING_{direction},"
                f"threshold=500k)"
            )
        elif mag >= 200_000 and cvd_aligned:
            g3_score = 8.0
            audit.append(
                f"G3_CVD=PARTIAL(Δ={cvd_velocity:+,.0f},moderate,"
                f"threshold=200k)"
            )
        elif not cvd_aligned and mag >= 200_000:
            g3_score = 0.0
            audit.append(
                f"G3_CVD=ADVERSE(Δ={cvd_velocity:+,.0f},against_{direction})"
            )
        else:
            g3_score = 0.0
            audit.append(f"G3_CVD=FLAT(Δ={cvd_velocity:+,.0f},<200k_threshold)")
        score += g3_score
    except Exception as e:
        audit.append(f"G3_CVD=ERROR({e})")

    # ── G4: FVG + Sweep Confirmation (ICTEngine, 3M/15M) ─────────────────────
    try:
        ict = _as("get-full-market-snapshot", {
            "asset": asset,
            "engine": "ict",
            "features": "sweep,fvg",
            "timeframes": "3M,15M",
        })
        sweep_active: bool = bool(ict.get("sweep_active", False))
        sweep_dir: Optional[str] = ict.get("sweep_direction")   # "HIGH" | "LOW"
        fvg_active: bool = bool(ict.get("fvg_active", False))
        fvg_filled: bool = bool(ict.get("fvg_filled", True))
        fvg_mid_raw = ict.get("fvg_mid_price")
        fvg_mid = float(fvg_mid_raw) if fvg_mid_raw else None
        fvg_tf: str = ict.get("fvg_timeframe", "3M")

        # Sweep of LOW = bullish (smart money raided longs → LONG reversal)
        # Sweep of HIGH = bearish (smart money raided shorts → SHORT reversal)
        sweep_bias: Optional[str] = None
        if sweep_active and sweep_dir == "LOW":
            sweep_bias = "LONG"
        elif sweep_active and sweep_dir == "HIGH":
            sweep_bias = "SHORT"

        fvg_valid = fvg_active and not fvg_filled

        if sweep_bias == direction and fvg_valid:
            g4_score = 25.0
            audit.append(
                f"G4_ICT=FULL(sweep_{sweep_dir}+FVG_{fvg_tf}@{fvg_mid},"
                f"aligned_{direction})"
            )
        elif sweep_bias == direction:
            g4_score = 12.0
            audit.append(
                f"G4_ICT=PARTIAL(sweep_{sweep_dir}_OK,FVG_missing_or_filled)"
            )
        elif fvg_valid:
            g4_score = 8.0
            audit.append(
                f"G4_ICT=PARTIAL(FVG_{fvg_tf}@{fvg_mid}_valid,sweep_absent)"
            )
        else:
            g4_score = 0.0
            audit.append(
                f"G4_ICT=MISS(sweep={sweep_active},sweep_dir={sweep_dir},"
                f"fvg_valid={fvg_valid})"
            )
        score += g4_score
    except Exception as e:
        audit.append(f"G4_ICT=ERROR({e})")

    # ── G5: Funding Extreme — Accelerator gate (not a blocker) ───────────────
    try:
        funding = _as("get-funding-rates-table", {"asset": asset})
        rate_1h: float = float(funding.get("rate_1h", 0.0))   # raw hourly rate

        # Extreme funding in opposite direction = fuel for reversal scalp
        if direction == "SHORT" and rate_1h > 0.0005:      # > 0.05%/hr longs paying
            g5_score = 10.0
            audit.append(
                f"G5_FUNDING=ACCELERATOR(rate={rate_1h*100:.4f}%/hr,"
                f"extreme_long_overpay,SHORT_fuel)"
            )
        elif direction == "LONG" and rate_1h < -0.0003:    # < -0.03%/hr shorts paying
            g5_score = 10.0
            audit.append(
                f"G5_FUNDING=ACCELERATOR(rate={rate_1h*100:.4f}%/hr,"
                f"extreme_short_overpay,LONG_fuel)"
            )
        elif abs(rate_1h) < 0.0001:                         # < 0.01%/hr = neutral
            g5_score = 5.0
            audit.append(f"G5_FUNDING=NEUTRAL(rate={rate_1h*100:.4f}%/hr)")
        else:
            g5_score = 3.0
            audit.append(f"G5_FUNDING=MILD(rate={rate_1h*100:.4f}%/hr)")
        score += g5_score
    except Exception as e:
        audit.append(f"G5_FUNDING=ERROR({e})")

    # ── G6: OB Walls → Target Price + Invalidation Level ─────────────────────
    try:
        walls = _as("get-ob-walls", {"asset": asset})
        mid_price: float = float(walls.get("mid_price", 0.0))
        bid_walls: list = walls.get("bid_walls", [])   # [{price, size_usd}, ...]
        ask_walls: list = walls.get("ask_walls", [])

        if direction == "LONG" and ask_walls:
            # Target = nearest ask wall (liquidity cluster above = magnet)
            nearest_ask = min(ask_walls, key=lambda w: float(w["price"]))
            target_price = float(nearest_ask["price"])
            wall_usd = float(nearest_ask.get("size_usd", 0))
            g6_score = 10.0
            audit.append(
                f"G6_WALL=TARGET(ask_wall@{target_price:,.2f},"
                f"size=${wall_usd:,.0f},LONG_magnet)"
            )
        elif direction == "SHORT" and bid_walls:
            # Target = nearest bid wall (liquidity cluster below = magnet)
            nearest_bid = max(bid_walls, key=lambda w: float(w["price"]))
            target_price = float(nearest_bid["price"])
            wall_usd = float(nearest_bid.get("size_usd", 0))
            g6_score = 10.0
            audit.append(
                f"G6_WALL=TARGET(bid_wall@{target_price:,.2f},"
                f"size=${wall_usd:,.0f},SHORT_magnet)"
            )
        else:
            g6_score = 0.0
            audit.append(f"G6_WALL=MISS(no_{direction}_wall_found)")
        score += g6_score

        # Invalidation = stop behind opposite wall (stop-hunt zone)
        if direction == "LONG" and bid_walls:
            opp_wall = max(bid_walls, key=lambda w: float(w["price"]))
            invalidation_price = round(float(opp_wall["price"]) * 0.9985, 2)
            audit.append(
                f"INVALIDATION_LONG=OBI_flip<-0.35 OR CVD_Δ<-200k OR "
                f"price_below_{invalidation_price:,.2f}(bid_wall_undercut)"
            )
        elif direction == "SHORT" and ask_walls:
            opp_wall = min(ask_walls, key=lambda w: float(w["price"]))
            invalidation_price = round(float(opp_wall["price"]) * 1.0015, 2)
            audit.append(
                f"INVALIDATION_SHORT=OBI_flip>+0.35 OR CVD_Δ>+200k OR "
                f"price_above_{invalidation_price:,.2f}(ask_wall_broken)"
            )

    except Exception as e:
        audit.append(f"G6_WALL=ERROR({e})")

    # ── DECISION ──────────────────────────────────────────────────────────────
    decision = "EXECUTE" if score >= SCALP_THRESHOLD else "STANDBY"

    # Persist to Redis for downstream cross-request state
    _r_set(
        f"scalp_setup:{asset}",
        json.dumps({"score": score, "direction": direction, "ts": time.time()}),
        ex=90,  # 90s TTL — scalp setup stales fast
    )

    return json.dumps({
        "asset": asset,
        "modality": "SCALP",
        "timeframe": "1M-15M",
        "decision": decision,
        "direction": direction,
        "score": round(score, 1),
        "threshold": SCALP_THRESHOLD,
        "target_price": target_price,
        "invalidation_price": invalidation_price,
        "entry_zone": f"FVG_MID@{fvg_mid:.2f}" if fvg_mid else "MARKET_ORDER",
        "audit_trail": audit,
        "triggered_by": [a for a in audit if "=FULL" in a],
    }, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# ████████████████  INTRADAY SETUP  (1-8h)  ███████████████████████████████████
# ═══════════════════════════════════════════════════════════════════════════════

@action(is_consequential=False)
def setup_intraday_institutional(asset: str = "BTC") -> str:
    """
    Institutional INTRADAY setup routine — 1 to 8 hour holding period.

    GATE ARCHITECTURE (max 100 pts + 5 bonus → EXECUTE if score >= 70):
      G1  Valeyre Z-Score + regime/bias   weight = 25  (regime heaviest)
      G2  Basis — spot vs perp premium    weight = 15
      G3  PO3 phase alignment (AMD)       weight = 20
      G4  SR confluence 1H + 4H           weight = 20
      G5  SFP entry trigger               weight = 10
      G6  SMT divergence confirmation     weight = 10
      [B] Confluence trigger gate bonus   +  5  (if fires)

    Direction  = Valeyre Z-Score sign (primary) confirmed by basis/PO3.
    Target     = SR confluence level (1H/4H intersection price).
    Invalidation:
      LONG:  Valeyre 1H Z crosses back above -0.80 OR price closes below nearest 1H support
      SHORT: Valeyre 1H Z crosses back below +0.80 OR price closes above nearest 1H resistance
    """
    audit: list[str] = []
    score: float = 0.0
    direction: Optional[str] = None
    target_price: Optional[float] = None
    invalidation_price: Optional[float] = None

    # ── G1: Valeyre Z-Score + Regime (ICTEngine via Action Server) ────────────
    valeyre_z: float = 0.0
    try:
        ict_1h = _as("get-full-market-snapshot", {
            "asset": asset, "engine": "ict", "tf": "1H",
        })
        valeyre_z = float(ict_1h.get("valeyre_z", 0.0))
        regime: str = ict_1h.get("regime", "NEUTRAL")   # BULL | BEAR | RANGING | NEUTRAL

        # Primary direction from Valeyre mean-reversion signal
        raw_dir = "LONG" if valeyre_z < 0 else "SHORT"

        if abs(valeyre_z) >= 2.0:
            regime_ok = (
                (raw_dir == "LONG" and regime in ("BULL", "NEUTRAL", "RANGING")) or
                (raw_dir == "SHORT" and regime in ("BEAR", "NEUTRAL", "RANGING"))
            )
            if regime_ok:
                g1_score = 25.0
                direction = raw_dir
                audit.append(
                    f"G1_VALEYRE=FULL(Z={valeyre_z:.2f},regime={regime},"
                    f"EXTREME_{direction}_MR)"
                )
            else:
                # Z extreme but regime conflicts (e.g. oversold in BEAR)
                g1_score = 12.0
                direction = raw_dir
                audit.append(
                    f"G1_VALEYRE=PARTIAL(Z={valeyre_z:.2f},regime_conflict={regime},"
                    f"half_weight)"
                )
        elif abs(valeyre_z) >= 1.20:
            g1_score = 14.0
            direction = raw_dir
            audit.append(
                f"G1_VALEYRE=MODERATE(Z={valeyre_z:.2f},regime={regime},"
                f"dir={direction})"
            )
        else:
            g1_score = 0.0
            audit.append(
                f"G1_VALEYRE=MISS(Z={valeyre_z:.2f}<|1.20|,no_MR_signal)"
            )
        score += g1_score
    except Exception as e:
        audit.append(f"G1_VALEYRE=ERROR({e})")

    # ── G2: Basis — Spot vs Perp premium/discount ─────────────────────────────
    try:
        basis_data = _as("get-basis", {"asset": asset})
        basis_pct: float = float(basis_data.get("basis_pct", 0.0))   # annualized %
        basis_trend: str = basis_data.get("trend", "FLAT")            # WIDENING | COMPRESSING | FLAT

        if direction == "LONG":
            if basis_pct > 8.0 and basis_trend == "WIDENING":
                g2_score = 15.0
                audit.append(
                    f"G2_BASIS=FULL(basis={basis_pct:.1f}%ann,"
                    f"trend=WIDENING,strong_carry_LONG_aligned)"
                )
            elif basis_pct > 3.0:
                g2_score = 8.0
                audit.append(
                    f"G2_BASIS=PARTIAL(basis={basis_pct:.1f}%ann,positive_carry)"
                )
            elif basis_pct < 0:
                g2_score = 0.0
                audit.append(
                    f"G2_BASIS=ADVERSE(basis={basis_pct:.1f}%ann,"
                    f"negative_carry_vs_LONG)"
                )
            else:
                g2_score = 4.0
                audit.append(f"G2_BASIS=FLAT(basis={basis_pct:.1f}%ann)")

        elif direction == "SHORT":
            if basis_pct < -3.0 or basis_trend == "COMPRESSING":
                g2_score = 15.0
                audit.append(
                    f"G2_BASIS=FULL(basis={basis_pct:.1f}%ann,"
                    f"trend={basis_trend},carry_collapse_SHORT_aligned)"
                )
            elif basis_pct < 2.0:
                g2_score = 8.0
                audit.append(
                    f"G2_BASIS=PARTIAL(basis={basis_pct:.1f}%ann,low_carry)"
                )
            else:
                g2_score = 0.0
                audit.append(
                    f"G2_BASIS=ADVERSE(basis={basis_pct:.1f}%ann,"
                    f"strong_carry_vs_SHORT)"
                )
        else:
            g2_score = 0.0
            audit.append("G2_BASIS=SKIP(no_direction_from_G1)")
        score += g2_score
    except Exception as e:
        audit.append(f"G2_BASIS=ERROR({e})")

    # ── G3: PO3 Phase — Power of Three (Accumulation / Distribution) ──────────
    try:
        po3 = _as("get-full-market-snapshot", {
            "asset": asset, "engine": "ict",
            "feature": "po3", "tf": "1H",
        })
        po3_phase: str = po3.get("po3_phase", "UNKNOWN")   # ACCUMULATION | MANIPULATION | DISTRIBUTION
        po3_completion: float = float(po3.get("po3_completion_pct", 0.0))

        # Intraday: ideal to enter at ACCUMULATION end (for LONG) or DISTRIBUTION end (for SHORT)
        phase_dir_map = {"ACCUMULATION": "LONG", "DISTRIBUTION": "SHORT"}
        ideal_dir = phase_dir_map.get(po3_phase)

        if ideal_dir == direction and po3_completion >= 70.0:
            g3_score = 20.0
            audit.append(
                f"G3_PO3=FULL(phase={po3_phase},{po3_completion:.0f}%_complete,"
                f"aligned_{direction})"
            )
        elif ideal_dir == direction and po3_completion >= 40.0:
            g3_score = 10.0
            audit.append(
                f"G3_PO3=PARTIAL(phase={po3_phase},{po3_completion:.0f}%,early_entry)"
            )
        elif po3_phase == "MANIPULATION":
            # In manipulation phase — uncertain, but note it
            g3_score = 5.0
            audit.append(
                f"G3_PO3=CAUTION(phase=MANIPULATION,{po3_completion:.0f}%,"
                f"wait_for_distribution_confirmation)"
            )
        else:
            g3_score = 0.0
            audit.append(
                f"G3_PO3=MISS(phase={po3_phase},ideal={ideal_dir}≠dir={direction})"
            )
        score += g3_score
    except Exception as e:
        audit.append(f"G3_PO3=ERROR({e})")

    # ── G4: SR Confluence 1H + 4H → Target Price ──────────────────────────────
    current_price: float = 0.0
    try:
        sr_data = _as("get-full-market-snapshot", {
            "asset": asset, "engine": "sr",
            "timeframes": "1H,4H",
        })
        current_price = float(sr_data.get("price", 0.0))
        sr_1h: list = sr_data.get("sr_levels_1h", [])
        sr_4h: list = sr_data.get("sr_levels_4h", [])

        tol_1h = current_price * 0.003   # 0.30% for 1H SR
        tol_4h = current_price * 0.006   # 0.60% for 4H SR

        def _nearest_target_sr(levels: list, dir_: str, price: float, tol: float):
            """
            LONG → find nearest resistance above price (within tolerance or above).
            SHORT → find nearest support below price (within tolerance or below).
            Returns the SR dict or None.
            """
            candidates = []
            for lvl in levels:
                lp = float(lvl.get("price", 0))
                lt = lvl.get("type", "")
                if dir_ == "LONG" and lt in ("RESISTANCE", "KEY_RESISTANCE") and lp > price - tol:
                    candidates.append(lvl)
                elif dir_ == "SHORT" and lt in ("SUPPORT", "KEY_SUPPORT") and lp < price + tol:
                    candidates.append(lvl)
            if not candidates:
                return None
            # Closest to current price
            return min(candidates, key=lambda x: abs(float(x["price"]) - price))

        sr1 = _nearest_target_sr(sr_1h, direction, current_price, tol_1h)
        sr4 = _nearest_target_sr(sr_4h, direction, current_price, tol_4h)

        if sr1 and sr4:
            target_price = round(
                (float(sr1["price"]) + float(sr4["price"])) / 2, 2
            )
            g4_score = 20.0
            audit.append(
                f"G4_SR=FULL(1H@{float(sr1['price']):.2f},"
                f"4H@{float(sr4['price']):.2f},"
                f"confluence_target={target_price:.2f})"
            )
        elif sr4:
            target_price = round(float(sr4["price"]), 2)
            g4_score = 12.0
            audit.append(
                f"G4_SR=PARTIAL(4H_only@{target_price:.2f},1H_absent)"
            )
        elif sr1:
            target_price = round(float(sr1["price"]), 2)
            g4_score = 8.0
            audit.append(
                f"G4_SR=PARTIAL(1H_only@{target_price:.2f},4H_absent)"
            )
        else:
            g4_score = 0.0
            audit.append(f"G4_SR=MISS(no_{direction}_SR_within_range)")
        score += g4_score
    except Exception as e:
        audit.append(f"G4_SR=ERROR({e})")

    # ── G5: SFP Entry Trigger (Swing Failure Pattern) ─────────────────────────
    sfp_level: Optional[float] = None
    try:
        sfp = _as("get-full-market-snapshot", {
            "asset": asset, "engine": "ict",
            "feature": "sfp", "tf": "1H",
        })
        sfp_active: bool = bool(sfp.get("sfp_active", False))
        sfp_dir: Optional[str] = sfp.get("sfp_direction")   # "BULLISH" | "BEARISH"
        sfp_lvl_raw = sfp.get("sfp_level")
        sfp_level = float(sfp_lvl_raw) if sfp_lvl_raw else None
        sfp_tf: str = sfp.get("sfp_timeframe", "1H")

        sfp_bias: Optional[str] = None
        if sfp_active and sfp_dir == "BULLISH":
            sfp_bias = "LONG"
        elif sfp_active and sfp_dir == "BEARISH":
            sfp_bias = "SHORT"

        if sfp_bias == direction:
            g5_score = 10.0
            audit.append(
                f"G5_SFP=TRIGGER(SFP_{sfp_dir}@{sfp_level},tf={sfp_tf},"
                f"precision_entry_triggered)"
            )
            # SFP level defines the invalidation (stop just beyond it)
            if sfp_level:
                mult = 0.998 if direction == "LONG" else 1.002
                invalidation_price = round(sfp_level * mult, 2)
                audit.append(
                    f"INVALIDATION={'below' if direction == 'LONG' else 'above'}_"
                    f"SFP_level@{invalidation_price:.2f}"
                )
        else:
            g5_score = 0.0
            audit.append(
                f"G5_SFP=MISS(active={sfp_active},sfp_dir={sfp_dir},"
                f"required={direction})"
            )
        score += g5_score
    except Exception as e:
        audit.append(f"G5_SFP=ERROR({e})")

    # ── G6: SMT Divergence Confirmation (ICTEngine) ───────────────────────────
    try:
        smt = _as("get-full-market-snapshot", {
            "asset": asset, "engine": "ict", "feature": "smt",
        })
        smt_active: bool = bool(smt.get("smt_active", False))
        smt_type: Optional[str] = smt.get("smt_type")      # "BULLISH_SMT" | "BEARISH_SMT"
        smt_pair: Optional[str] = smt.get("correlated_asset")  # e.g. "ETH"

        smt_aligned = (
            (smt_type == "BULLISH_SMT" and direction == "LONG") or
            (smt_type == "BEARISH_SMT" and direction == "SHORT")
        )

        if smt_active and smt_aligned:
            g6_score = 10.0
            audit.append(
                f"G6_SMT=CONFIRM({smt_type},pair={smt_pair},"
                f"corr_asset_diverging_confirms_{direction})"
            )
        elif smt_active and not smt_aligned:
            g6_score = 0.0
            audit.append(
                f"G6_SMT=ADVERSE({smt_type}_vs_required_{direction})"
            )
        else:
            g6_score = 0.0
            audit.append(f"G6_SMT=MISS(smt_active={smt_active},type={smt_type})")
        score += g6_score
    except Exception as e:
        audit.append(f"G6_SMT=ERROR({e})")

    # ── BONUS: Confluence Trigger Gate ────────────────────────────────────────
    try:
        conf = _as("detect-confluence-trigger", {
            "asset": asset, "timeframe": "1H",
        })
        conf_fired: bool = conf.get("triggered", False)
        conf_dir: Optional[str] = conf.get("direction")
        conf_pattern: Optional[str] = conf.get("pattern")

        if conf_fired and conf_dir == direction:
            score = min(score + 5.0, 100.0)
            audit.append(
                f"BONUS_CONFLUENCE=+5(pattern={conf_pattern},"
                f"direction={conf_dir},gate_fired)"
            )
        else:
            audit.append(
                f"BONUS_CONFLUENCE=MISS(fired={conf_fired},dir={conf_dir})"
            )
    except Exception as e:
        audit.append(f"BONUS_CONFLUENCE=ERROR({e})")

    # ── DECISION ──────────────────────────────────────────────────────────────
    decision = "EXECUTE" if score >= INTRADAY_THRESHOLD else "STANDBY"

    _r_set(
        f"intraday_setup:{asset}",
        json.dumps({"score": score, "direction": direction, "ts": time.time()}),
        ex=3_600,  # 1h TTL
    )

    # Fallback invalidation if SFP level was not set
    if not invalidation_price and current_price > 0:
        pct = 0.995 if direction == "LONG" else 1.005
        invalidation_price = round(current_price * pct, 2)
        audit.append(
            f"INVALIDATION_FALLBACK={'below' if direction == 'LONG' else 'above'}_"
            f"{invalidation_price:.2f}(0.5%_default,no_SFP_level)"
        )

    return json.dumps({
        "asset": asset,
        "modality": "INTRADAY",
        "timeframe": "1H-8H",
        "decision": decision,
        "direction": direction,
        "score": round(score, 1),
        "threshold": INTRADAY_THRESHOLD,
        "target_price": target_price,
        "invalidation_price": invalidation_price,
        "valeyre_z_1h": round(valeyre_z, 3),
        "audit_trail": audit,
        "triggered_by": [
            a for a in audit
            if any(tag in a for tag in ("=FULL", "=TRIGGER", "=CONFIRM", "=ACCELERATOR"))
        ],
    }, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# ████████████████  SWING SETUP  (1-7 days)  ██████████████████████████████████
# ═══════════════════════════════════════════════════════════════════════════════

@action(is_consequential=False)
def setup_swing_institutional(asset: str = "BTC") -> str:
    """
    Institutional SWING setup routine — 1 to 7 day holding period.

    GATE ARCHITECTURE (max 100 pts + 5 bonus → EXECUTE if score >= 72):
      G1  Valeyre extreme 4H + 1D aligned weight = 25  (macro regime, heaviest)
      G2  Funding trend — 50 candle slope  weight = 20
      G3  HL funding cross-exchange valid   weight = 15
      G4  PO3 phase timing ACCUM / DIST     weight = 25  (timing gate, critical)
      G5  SR confluence 4H + 1D → target   weight = 15
      [B] Silver Bullet window bonus        +  5 (time-of-day bonus)

    Direction  = Valeyre extreme alignment (4H AND 1D must agree).
    Target     = HTF SR confluence level (4H/1D intersection).
    Invalidation:
      LONG:  Daily close below key 1D support  OR  Valeyre 1D Z-Score crosses -0.50
      SHORT: Daily close above key 1D resistance OR  Valeyre 1D Z-Score crosses +0.50
    """
    audit: list[str] = []
    score: float = 0.0
    direction: Optional[str] = None
    target_price: Optional[float] = None
    invalidation_price: Optional[float] = None
    vz_1d: float = 0.0

    # ── G1: Valeyre Extreme — Macro Regime (4H + 1D alignment) ───────────────
    try:
        ict_4h = _as("get-full-market-snapshot", {
            "asset": asset, "engine": "ict", "tf": "4H",
        })
        ict_1d = _as("get-full-market-snapshot", {
            "asset": asset, "engine": "ict", "tf": "1D",
        })

        vz_4h: float = float(ict_4h.get("valeyre_z", 0.0))
        vz_1d = float(ict_1d.get("valeyre_z", 0.0))
        regime_1d: str = ict_1d.get("regime", "NEUTRAL")

        both_long = vz_4h < -1.5 and vz_1d < -1.0
        both_short = vz_4h > 1.5 and vz_1d > 1.0
        aligned = both_long or both_short

        if aligned:
            direction = "LONG" if both_long else "SHORT"
            extreme = max(abs(vz_4h), abs(vz_1d))
            if extreme >= 2.5:
                g1_score = 25.0
                audit.append(
                    f"G1_VALEYRE=FULL(1D_Z={vz_1d:.2f},4H_Z={vz_4h:.2f},"
                    f"EXTREME_{direction},regime_1d={regime_1d})"
                )
            elif extreme >= 1.8:
                g1_score = 18.0
                audit.append(
                    f"G1_VALEYRE=STRONG(1D_Z={vz_1d:.2f},4H_Z={vz_4h:.2f},"
                    f"dir={direction})"
                )
            else:
                g1_score = 12.0
                audit.append(
                    f"G1_VALEYRE=MODERATE(1D_Z={vz_1d:.2f},4H_Z={vz_4h:.2f})"
                )

            # Invalidation signal based on Valeyre reversion threshold
            iv_z = -0.50 if direction == "LONG" else +0.50
            audit.append(
                f"INVALIDATION_SIGNAL=Valeyre_1D_Z_crosses_{iv_z:+.2f}"
                f"(current={vz_1d:.2f})"
            )
        else:
            # Check if at least one TF is extreme
            max_z = max(abs(vz_4h), abs(vz_1d))
            if max_z >= 2.0:
                g1_score = 8.0
                direction = "LONG" if vz_1d < 0 else "SHORT"
                audit.append(
                    f"G1_VALEYRE=PARTIAL(4H/1D_misaligned,"
                    f"1D_Z={vz_1d:.2f},4H_Z={vz_4h:.2f},max={max_z:.2f})"
                )
            else:
                g1_score = 0.0
                audit.append(
                    f"G1_VALEYRE=MISS(1D_Z={vz_1d:.2f},4H_Z={vz_4h:.2f},"
                    f"no_extreme,swing_not_ready)"
                )
        score += g1_score
    except Exception as e:
        audit.append(f"G1_VALEYRE=ERROR({e})")

    # ── G2: Funding Trend — 50-Candle Slope Analysis ──────────────────────────
    try:
        hist = _as("get-funding-history", {"asset": asset, "limit": "100"})
        raw_rates = hist.get("history", [])[:50]
        rates: list[float] = [float(r.get("rate", 0.0)) for r in raw_rates]

        if len(rates) < 10:
            raise ValueError(f"Only {len(rates)} candles — need ≥10")

        mid = len(rates) // 2
        avg_old = sum(rates[mid:]) / len(rates[mid:])   # older half
        avg_new = sum(rates[:mid]) / len(rates[:mid])   # recent half
        trend_delta = avg_new - avg_old                 # positive = funding rising

        pct_positive = sum(1 for r in rates if r > 0) / len(rates)
        avg_current = avg_new

        if direction == "SHORT":
            # Want: longs paying heavy and building (squeeze fuel accumulating)
            if pct_positive >= 0.70 and trend_delta > 0:
                g2_score = 20.0
                audit.append(
                    f"G2_FUNDING_TREND=FULL(pct_pos={pct_positive:.0%}≥70%,"
                    f"trend_delta=+{trend_delta*1e4:.2f}bps,LONG_crowd_building,"
                    f"SHORT_fuel)"
                )
            elif pct_positive >= 0.55:
                g2_score = 12.0
                audit.append(
                    f"G2_FUNDING_TREND=PARTIAL(pct_pos={pct_positive:.0%},"
                    f"trend_delta={trend_delta*1e4:+.2f}bps)"
                )
            else:
                g2_score = 4.0
                audit.append(
                    f"G2_FUNDING_TREND=WEAK(pct_pos={pct_positive:.0%},"
                    f"funding_not_extreme)"
                )

        elif direction == "LONG":
            # Want: shorts paying heavy (capitulation) and funding falling
            if pct_positive <= 0.30 and trend_delta < 0:
                g2_score = 20.0
                audit.append(
                    f"G2_FUNDING_TREND=FULL(pct_pos={pct_positive:.0%}≤30%,"
                    f"trend_delta={trend_delta*1e4:.2f}bps,SHORT_crowd_capitulating,"
                    f"LONG_fuel)"
                )
            elif pct_positive <= 0.45 or trend_delta < 0:
                g2_score = 12.0
                audit.append(
                    f"G2_FUNDING_TREND=PARTIAL(pct_pos={pct_positive:.0%},"
                    f"easing_toward_neutral)"
                )
            else:
                g2_score = 4.0
                audit.append(
                    f"G2_FUNDING_TREND=WEAK(pct_pos={pct_positive:.0%},"
                    f"funding_elevated_vs_LONG)"
                )
        else:
            g2_score = 0.0
            audit.append("G2_FUNDING_TREND=SKIP(no_direction)")
        score += g2_score
    except Exception as e:
        audit.append(f"G2_FUNDING_TREND=ERROR({e})")

    # ── G3: Hyperliquid Cross-Exchange Funding Validation ─────────────────────
    try:
        hl_data = _hl("get-hl-funding-single", {"asset": asset})
        hl_rate_8h: float = float(hl_data.get("rate_8h", 0.0))

        # Compare vs CEX (Binance/OKX) from Action Server
        cex_data = _as("get-funding-rates-table", {"asset": asset})
        cex_rate_8h: float = float(cex_data.get("rate_8h", 0.0))

        spread = hl_rate_8h - cex_rate_8h   # positive = HL more bullish than CEX

        if direction == "SHORT":
            # HL longs paying more → HL-native FOMO → cross-exchange squeeze potential
            if hl_rate_8h > 0.0008 and hl_rate_8h >= cex_rate_8h:
                g3_score = 15.0
                audit.append(
                    f"G3_HL_FUNDING=FULL(HL_8h={hl_rate_8h*1e4:.2f}bps,"
                    f"CEX_8h={cex_rate_8h*1e4:.2f}bps,"
                    f"spread=+{spread*1e4:.2f}bps,HL_FOMO_SHORT_fuel)"
                )
            elif hl_rate_8h > 0.0003:
                g3_score = 8.0
                audit.append(
                    f"G3_HL_FUNDING=PARTIAL(HL_8h={hl_rate_8h*1e4:.2f}bps,"
                    f"elevated_but_not_extreme)"
                )
            else:
                g3_score = 3.0
                audit.append(f"G3_HL_FUNDING=FLAT(HL_8h={hl_rate_8h*1e4:.2f}bps)")

        elif direction == "LONG":
            # HL funding negative → extreme short bias on HL → capitulation fuel
            if hl_rate_8h < -0.0003:
                g3_score = 15.0
                audit.append(
                    f"G3_HL_FUNDING=FULL(HL_8h={hl_rate_8h*1e4:.2f}bps,"
                    f"NEGATIVE_extreme,HL_short_capitulation_LONG_fuel)"
                )
            elif hl_rate_8h < 0:
                g3_score = 8.0
                audit.append(
                    f"G3_HL_FUNDING=PARTIAL(HL_8h={hl_rate_8h*1e4:.2f}bps,negative_mild)"
                )
            elif hl_rate_8h < 0.0002:
                g3_score = 5.0
                audit.append(f"G3_HL_FUNDING=NEUTRAL(HL_8h={hl_rate_8h*1e4:.2f}bps)")
            else:
                g3_score = 0.0
                audit.append(
                    f"G3_HL_FUNDING=ADVERSE(HL_8h={hl_rate_8h*1e4:.2f}bps,"
                    f"positive_funding_vs_LONG)"
                )
        else:
            g3_score = 0.0
            audit.append("G3_HL_FUNDING=SKIP(no_direction)")
        score += g3_score
    except Exception as e:
        audit.append(f"G3_HL_FUNDING=ERROR({e})")

    # ── G4: PO3 Phase Timing — 1D + 4H (ACCUMULATION / DISTRIBUTION) ──────────
    try:
        po3_1d = _as("get-full-market-snapshot", {
            "asset": asset, "engine": "ict",
            "feature": "po3", "tf": "1D",
        })
        po3_4h = _as("get-full-market-snapshot", {
            "asset": asset, "engine": "ict",
            "feature": "po3", "tf": "4H",
        })

        phase_1d: str = po3_1d.get("po3_phase", "UNKNOWN")
        phase_4h: str = po3_4h.get("po3_phase", "UNKNOWN")
        completion_1d: float = float(po3_1d.get("po3_completion_pct", 0.0))

        # Ideal swing timing:
        # LONG: 1D in ACCUMULATION (≥60%), 4H in ACCUMULATION or early MANIPULATION
        # SHORT: 1D in DISTRIBUTION (≥60%), 4H in DISTRIBUTION or early MANIPULATION
        ideal_long = phase_1d == "ACCUMULATION"
        ideal_short = phase_1d == "DISTRIBUTION"

        if direction == "LONG" and ideal_long:
            if phase_4h in ("ACCUMULATION", "MANIPULATION") and completion_1d >= 60.0:
                g4_score = 25.0
                audit.append(
                    f"G4_PO3=FULL(1D={phase_1d}_{completion_1d:.0f}%,"
                    f"4H={phase_4h},OPTIMAL_LONG_timing)"
                )
            elif completion_1d >= 40.0:
                g4_score = 15.0
                audit.append(
                    f"G4_PO3=PARTIAL(1D={phase_1d}_{completion_1d:.0f}%,"
                    f"4H={phase_4h},early_but_aligned)"
                )
            else:
                g4_score = 8.0
                audit.append(
                    f"G4_PO3=EARLY(1D={phase_1d}_{completion_1d:.0f}%,too_early)"
                )

        elif direction == "SHORT" and ideal_short:
            if phase_4h in ("DISTRIBUTION", "MANIPULATION") and completion_1d >= 60.0:
                g4_score = 25.0
                audit.append(
                    f"G4_PO3=FULL(1D={phase_1d}_{completion_1d:.0f}%,"
                    f"4H={phase_4h},OPTIMAL_SHORT_timing)"
                )
            elif completion_1d >= 40.0:
                g4_score = 15.0
                audit.append(
                    f"G4_PO3=PARTIAL(1D={phase_1d}_{completion_1d:.0f}%)"
                )
            else:
                g4_score = 8.0
                audit.append(f"G4_PO3=EARLY(1D={phase_1d}_{completion_1d:.0f}%)")

        elif phase_1d == "MANIPULATION":
            g4_score = 5.0
            audit.append(
                f"G4_PO3=CAUTION(1D=MANIPULATION,{completion_1d:.0f}%,"
                f"4H={phase_4h},wait_for_distribution)"
            )
        else:
            g4_score = 0.0
            audit.append(
                f"G4_PO3=MISS(1D={phase_1d},required="
                f"{'ACCUM' if direction == 'LONG' else 'DIST'})"
            )
        score += g4_score
    except Exception as e:
        audit.append(f"G4_PO3=ERROR({e})")

    # ── G5: Multi-TF SR Confluence (4H + 1D) → Target Price ──────────────────
    current_price: float = 0.0
    try:
        sr = _as("get-full-market-snapshot", {
            "asset": asset, "engine": "sr",
            "timeframes": "4H,1D",
        })
        current_price = float(sr.get("price", 0.0))
        sr_4h_levels: list = sr.get("sr_levels_4h", [])
        sr_1d_levels: list = sr.get("sr_levels_1d", [])
        vp_poc: float = float(sr.get("volume_profile_poc", 0.0))

        tol = current_price * 0.010   # 1.0% for swing

        def _swing_target(levels_4h, levels_1d, vp_poc_, dir_, price, tol_):
            """
            Find highest-quality target level:
            Priority: 1D KEY level > 1D level > 4H level > VP_POC
            LONG → resistance above; SHORT → support below.
            """
            pool: list[dict] = []
            for lv in levels_1d:
                pool.append({
                    "price": float(lv["price"]),
                    "tf": "1D",
                    "strength": int(lv.get("strength", 1)) + 2,  # 1D bonus
                    "type": lv.get("type", ""),
                })
            for lv in levels_4h:
                pool.append({
                    "price": float(lv["price"]),
                    "tf": "4H",
                    "strength": int(lv.get("strength", 1)),
                    "type": lv.get("type", ""),
                })
            if vp_poc_ > 0:
                pool.append({
                    "price": vp_poc_,
                    "tf": "VP_POC",
                    "strength": 4,
                    "type": "RESISTANCE" if dir_ == "LONG" else "SUPPORT",
                })

            if dir_ == "LONG":
                candidates = [l for l in pool if l["price"] > price + tol_]
            else:
                candidates = [l for l in pool if l["price"] < price - tol_]

            if not candidates:
                return None
            # Best = closest in price (nearest magnet)
            return min(candidates, key=lambda x: abs(x["price"] - price))

        best = _swing_target(
            sr_4h_levels, sr_1d_levels, vp_poc, direction, current_price, tol
        )

        if best:
            target_price = round(best["price"], 2)
            distance_pct = abs(target_price - current_price) / current_price * 100
            rr_hint = distance_pct / 1.0   # assuming ~1% stop as placeholder

            if best["tf"] in ("1D", "VP_POC") and distance_pct >= 5.0:
                g5_score = 15.0
                audit.append(
                    f"G5_SR=FULL(target@{target_price:.2f},{best['tf']},"
                    f"dist={distance_pct:.1f}%,quality_target)"
                )
            elif distance_pct >= 3.0:
                g5_score = 10.0
                audit.append(
                    f"G5_SR=PARTIAL(target@{target_price:.2f},{best['tf']},"
                    f"dist={distance_pct:.1f}%)"
                )
            else:
                g5_score = 5.0
                audit.append(
                    f"G5_SR=WEAK(target@{target_price:.2f},"
                    f"dist={distance_pct:.1f}%,too_close)"
                )

            # Invalidation: 1D support/resistance breakdown level
            if direction == "LONG":
                supports = [
                    l for l in sr_1d_levels
                    if l.get("type") in ("SUPPORT", "KEY_SUPPORT")
                    and float(l["price"]) < current_price
                ]
                if supports:
                    nearest_sup = max(supports, key=lambda x: float(x["price"]))
                    invalidation_price = round(float(nearest_sup["price"]) * 0.997, 2)
                    audit.append(
                        f"INVALIDATION_LONG=daily_close_below_{invalidation_price:.2f}"
                        f"(1D_support_breakdown)"
                    )
            elif direction == "SHORT":
                resistances = [
                    l for l in sr_1d_levels
                    if l.get("type") in ("RESISTANCE", "KEY_RESISTANCE")
                    and float(l["price"]) > current_price
                ]
                if resistances:
                    nearest_res = min(resistances, key=lambda x: float(x["price"]))
                    invalidation_price = round(float(nearest_res["price"]) * 1.003, 2)
                    audit.append(
                        f"INVALIDATION_SHORT=daily_close_above_{invalidation_price:.2f}"
                        f"(1D_resistance_breach)"
                    )
        else:
            g5_score = 0.0
            audit.append(f"G5_SR=MISS(no_{direction}_SR_found_beyond_1%)")
        score += g5_score
    except Exception as e:
        audit.append(f"G5_SR=ERROR({e})")

    # ── BONUS: Silver Bullet Window (ICT timing) ──────────────────────────────
    try:
        sb = _as("get-full-market-snapshot", {
            "asset": asset, "engine": "ict", "feature": "silver_bullet",
        })
        sb_active: bool = bool(sb.get("silver_bullet_active", False))
        sb_window: Optional[str] = sb.get("silver_bullet_window")  # "10:00-11:00" etc.

        if sb_active:
            score = min(score + 5.0, 100.0)
            audit.append(
                f"BONUS_SILVER_BULLET=+5(window={sb_window},"
                f"ICT_high_probability_time_confirmed)"
            )
        else:
            audit.append(
                f"BONUS_SILVER_BULLET=INACTIVE(current_window={sb_window})"
            )
    except Exception as e:
        audit.append(f"BONUS_SILVER_BULLET=ERROR({e})")

    # ── DECISION ──────────────────────────────────────────────────────────────
    decision = "EXECUTE" if score >= SWING_THRESHOLD else "STANDBY"

    _r_set(
        f"swing_setup:{asset}",
        json.dumps({"score": score, "direction": direction, "ts": time.time()}),
        ex=86_400,  # 24h TTL
    )

    return json.dumps({
        "asset": asset,
        "modality": "SWING",
        "timeframe": "1D-7D",
        "decision": decision,
        "direction": direction,
        "score": round(score, 1),
        "threshold": SWING_THRESHOLD,
        "target_price": target_price,
        "invalidation_price": invalidation_price,
        "valeyre_z_1d": round(vz_1d, 3),
        "audit_trail": audit,
        "triggered_by": [
            a for a in audit
            if any(tag in a for tag in ("=FULL", "=TRIGGER", "=CONFIRM"))
        ],
    }, indent=2)


# ═══════════════════════════════════════════════════════════════════════════════
# ████████████████  MASTER ORCHESTRATOR  ██████████████████████████████████████
# ═══════════════════════════════════════════════════════════════════════════════

@action(is_consequential=False)
def setup_master(asset: str = "BTC") -> str:
    """
    Run all three setup routines and return the best opportunity.

    Priority logic:
      1. EXECUTE beats STANDBY regardless of score.
      2. Among EXECUTEs: highest score wins.
      3. Tie-break: INTRADAY > SCALP > SWING (best risk/reward per time unit).
      4. If no EXECUTE: return the closest STANDBY with gap analysis.
    """
    results: dict = {}
    errors: dict = {}

    runners = [
        ("SCALP",    setup_scalp_institutional),
        ("INTRADAY", setup_intraday_institutional),
        ("SWING",    setup_swing_institutional),
    ]

    for name, fn in runners:
        try:
            raw = fn(asset=asset)
            results[name] = json.loads(raw)
        except Exception as e:
            errors[name] = str(e)

    executes = {k: v for k, v in results.items() if v.get("decision") == "EXECUTE"}
    standbys = {k: v for k, v in results.items() if v.get("decision") == "STANDBY"}

    best_modality: Optional[str] = None
    best_setup: Optional[dict] = None

    priority_order = ["INTRADAY", "SCALP", "SWING"]

    if executes:
        max_score = max(v.get("score", 0) for v in executes.values())
        # Among max-score ties → pick by priority
        top = {k: v for k, v in executes.items() if v.get("score", 0) == max_score}
        for p in priority_order:
            if p in top:
                best_modality = p
                break
        if not best_modality:
            best_modality = list(top.keys())[0]
        best_setup = executes[best_modality]
    elif standbys:
        max_score = max(v.get("score", 0) for v in standbys.values())
        top = {k: v for k, v in standbys.items() if v.get("score", 0) == max_score}
        for p in priority_order:
            if p in top:
                best_modality = p
                break
        if not best_modality:
            best_modality = list(top.keys())[0]
        best_setup = standbys[best_modality]

    summary = {
        k: {
            "decision":   v.get("decision"),
            "score":      v.get("score"),
            "direction":  v.get("direction"),
            "target":     v.get("target_price"),
            "invalidate": v.get("invalidation_price"),
            "gap_to_exec": round(
                {
                    "SCALP":    SCALP_THRESHOLD,
                    "INTRADAY": INTRADAY_THRESHOLD,
                    "SWING":    SWING_THRESHOLD,
                }.get(k, 70) - v.get("score", 0),
                1,
            ),
        }
        for k, v in results.items()
    }

    return json.dumps({
        "asset": asset,
        "best_modality": best_modality,
        "best_setup": best_setup,
        "summary": summary,
        "errors": errors,
    }, indent=2)
