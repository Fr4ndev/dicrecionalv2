"""
hl_scanner.py — Hyperliquid Opportunity Scanner
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Detects shortable and longeable altcoin opportunities on Hyperliquid.
Analyzes funding extremes, OI anomalies, and volume patterns.

Scoring criteria:
  1. Funding Premium > +100% annualized → LONG PAYERS → SHORT opportunity
  2. Funding Discount < -100% annualized → SHORT PAYERS → LONG opportunity
  3. High OI + extreme funding → crowded trade, squeeze potential
  4. High volume + extreme funding → momentum confirmation
  5. Low OI + extreme funding → early opportunity, less crowded
"""
import json
import logging
from datetime import datetime, timezone
import ccxt
from sema4ai.actions import action

logger = logging.getLogger("HLScanner")

# ── Config ──────────────────────────────────────────────────
HYPERLIQUID = None

def _get_hl():
    global HYPERLIQUID
    if HYPERLIQUID is None:
        HYPERLIQUID = ccxt.hyperliquid({"options": {"defaultType": "swap"}})
        HYPERLIQUID.load_markets()
    return HYPERLIQUID

# ── Thresholds ──────────────────────────────────────────────
FUNDING_EXTREME_LONG  = -100.0   # annualized % — shorts paying heavily → LONG
FUNDING_EXTREME_SHORT = +100.0   # annualized % — longs paying heavily → SHORT
FUNDING_HIGH_LONG     = -50.0
FUNDING_HIGH_SHORT    = +50.0
OI_MIN_USD            = 100_000   # min OI to be tradeable
VOL_MIN_USD           = 50_000    # min 24h volume


def _fetch_and_score() -> dict:
    """Fetch all HL data and compute opportunity scores."""
    hl = _get_hl()
    tickers = hl.fetch_tickers()
    
    opportunities = {
        "long_setups":  [],  # short payers → go LONG
        "short_setups": [],  # long payers → go SHORT
        "watchlist":    [],  # borderline
        "market_stats": {},
    }
    
    for symbol, ticker in tickers.items():
        if not symbol.endswith("USDC:USDC"):
            continue
        
        asset = symbol.split("/")[0]
        funding_raw = ticker.get("info", {}).get("funding", 0)
        funding_pct = float(funding_raw) * 100  # to %
        funding_annual = funding_pct * 3 * 365  # annualized
        
        mark = float(ticker.get("info", {}).get("markPx", 0) or ticker.get("mark", 0))
        oracle = float(ticker.get("info", {}).get("oraclePx", 0))
        premium = (mark - oracle) / oracle * 100 if oracle > 0 else 0
        
        oi = float(ticker.get("info", {}).get("openInterest", 0))
        vol_24h = float(ticker.get("info", {}).get("dayNtlVlm", 0) or ticker.get("baseVolume", 0))
        
        if oi < OI_MIN_USD or vol_24h < VOL_MIN_USD:
            continue
        
        # ── Scoring ──────────────────────────────────────
        score = 0
        direction = "NEUTRAL"
        
        # Funding direction score
        if funding_annual < FUNDING_EXTREME_LONG:
            score += 50  # shorts paying heavily → LONG
            direction = "LONG"
        elif funding_annual < FUNDING_HIGH_LONG:
            score += 30
            direction = "LONG"
        elif funding_annual > FUNDING_EXTREME_SHORT:
            score += 50  # longs paying heavily → SHORT
            direction = "SHORT"
        elif funding_annual > FUNDING_HIGH_SHORT:
            score += 30
            direction = "SHORT"
        
        # Premium confirmation (mark vs oracle)
        if direction == "LONG" and premium < -0.5:
            score += 20  # discount = accumulation
        elif direction == "SHORT" and premium > 0.5:
            score += 20  # premium = distribution
        
        # OI / volume ratio (crowded vs liquid)
        if oi > 1_000_000 and funding_annual != 0:
            score += 15  # high OI + extreme funding = crowded trade
        
        # Market cap proxy (OI is close to market cap in perps)
        if oi > 10_000_000:
            score += 10  # whale territory
        
        confidence = "ALTA" if score >= 70 else "MEDIA" if score >= 50 else "BAJA"
        
        entry = {
            "asset": asset,
            "symbol": symbol,
            "funding_annualized_pct": round(funding_annual, 2),
            "funding_rate_pct": round(funding_pct, 6),
            "premium_pct": round(premium, 4),
            "oi_usd": round(oi, 2),
            "volume_24h_usd": round(vol_24h, 2),
            "mark_price": mark,
            "score": score,
            "confidence": confidence,
            "direction": direction,
        }
        
        if score >= 50 and direction == "LONG":
            opportunities["long_setups"].append(entry)
        elif score >= 50 and direction == "SHORT":
            opportunities["short_setups"].append(entry)
        elif score >= 30:
            opportunities["watchlist"].append(entry)
    
    # Sort by score
    opportunities["long_setups"].sort(key=lambda x: x["score"], reverse=True)
    opportunities["short_setups"].sort(key=lambda x: x["score"], reverse=True)
    opportunities["watchlist"].sort(key=lambda x: x["score"], reverse=True)
    
    # Market stats
    all_fundings = []
    for symbol, ticker in tickers.items():
        if not symbol.endswith("USDC:USDC"):
            continue
        f = float(ticker.get("info", {}).get("funding", 0)) * 100 * 3 * 365
        all_fundings.append(f)
    
    if all_fundings:
        import statistics
        opportunities["market_stats"] = {
            "total_assets": len(all_fundings),
            "funding_mean": round(statistics.mean(all_fundings), 2),
            "funding_median": round(statistics.median(all_fundings), 2),
            "funding_std": round(statistics.stdev(all_fundings) if len(all_fundings) > 1 else 0, 2),
            "positive_count": sum(1 for f in all_fundings if f > 0),
            "negative_count": sum(1 for f in all_fundings if f < 0),
            "extreme_positive": sum(1 for f in all_fundings if f > FUNDING_EXTREME_SHORT),
            "extreme_negative": sum(1 for f in all_fundings if f < FUNDING_EXTREME_LONG),
        }
    
    opportunities["summary"] = {
        "long_opportunities": len(opportunities["long_setups"]),
        "short_opportunities": len(opportunities["short_setups"]),
        "watchlist": len(opportunities["watchlist"]),
        "total_scanned": len(all_fundings),
    }
    
    return opportunities


# ══════════════════════════════════════════════════════════════
# ACTION: scan-hl-opportunities
# ══════════════════════════════════════════════════════════════

@action(is_consequential=False)
def scan_hl_opportunities() -> str:
    """Scans ALL Hyperliquid perpetuals for shortable and longeable opportunities.

    Detects tokens where funding is extremely positive (longs paying = SHORT it)
    or extremely negative (shorts paying = LONG it). Cross-references with OI,
    volume, and mark-vs-oracle premium to score opportunities 0-100.

    Returns:
        JSON with long_setups, short_setups, watchlist, market_stats, and summary.
    """
    ts = datetime.now(timezone.utc).isoformat()
    try:
        data = _fetch_and_score()
        return json.dumps({
            "status": "ok",
            "timestamp": ts,
            "exchange": "hyperliquid",
            **data,
        })
    except Exception as e:
        logger.error(f"scan_hl_opportunities failed: {e}")
        return json.dumps({"status": "error", "timestamp": ts, "error": str(e)})


# ══════════════════════════════════════════════════════════════
# ACTION: get-hl-token-deepdive
# ══════════════════════════════════════════════════════════════

@action(is_consequential=False)
def get_hl_token_deepdive(asset: str) -> str:
    """Deep-dive analysis for a single Hyperliquid token.

    Returns full funding context, mark vs oracle spread,
    OI delta (if historical data available), and oppportunity verdict.

    Args:
        asset: Token ticker (e.g. 'HYPE', 'SOL', 'BTC').

    Returns:
        JSON with full token analysis and trading verdict.
    """
    ts = datetime.now(timezone.utc).isoformat()
    try:
        data = _fetch_and_score()
        
        # Find the token in any list
        token_data = None
        for lst in ["long_setups", "short_setups", "watchlist"]:
            for entry in data.get(lst, []):
                if entry["asset"] == asset.upper():
                    token_data = entry
                    break
            if token_data:
                break
        
        # If not found in scored lists, do a raw fetch
        if not token_data:
            hl = _get_hl()
            try:
                ticker = hl.fetch_ticker(f"{asset.upper()}/USDC:USDC")
                funding_raw = ticker.get("info", {}).get("funding", 0)
                mark = float(ticker.get("info", {}).get("markPx", 0))
                oracle = float(ticker.get("info", {}).get("oraclePx", 0))
                oi = float(ticker.get("info", {}).get("openInterest", 0))
                vol_24h = float(ticker.get("info", {}).get("dayNtlVlm", 0) or 0)
                
                token_data = {
                    "asset": asset.upper(),
                    "funding_annualized_pct": round(float(funding_raw) * 100 * 3 * 365, 2),
                    "funding_rate_pct": round(float(funding_raw) * 100, 6),
                    "premium_pct": round((mark - oracle) / oracle * 100, 4) if oracle > 0 else 0,
                    "oi_usd": round(oi, 2),
                    "volume_24h_usd": round(vol_24h, 2),
                    "mark_price": mark,
                    "score": 0,
                    "confidence": "BAJA",
                    "direction": "NEUTRAL",
                }
            except Exception:
                return json.dumps({"status": "error", "timestamp": ts,
                                  "error": f"Could not fetch data for {asset}"})
        
        # Verdict
        annual = token_data.get("funding_annualized_pct", 0)
        premium = token_data.get("premium_pct", 0)
        
        if annual < -100:
            verdict = "STRONG_LONG — shorts paying {:.0f}% annual, discount {:.2f}%".format(abs(annual), premium)
        elif annual < -50:
            verdict = "LONG_SETUP — shorts paying {:.0f}% annual".format(abs(annual))
        elif annual > 100:
            verdict = "STRONG_SHORT — longs paying {:.0f}% annual, premium {:.2f}%".format(annual, premium)
        elif annual > 50:
            verdict = "SHORT_SETUP — longs paying {:.0f}% annual".format(annual)
        else:
            verdict = "NEUTRAL — funding within normal range ({:.0f}% annual)".format(annual)
        
        return json.dumps({
            "status": "ok",
            "timestamp": ts,
            "asset": asset.upper(),
            "token_data": token_data,
            "verdict": verdict,
        })
    except Exception as e:
        return json.dumps({"status": "error", "timestamp": ts, "error": str(e)})


# ══════════════════════════════════════════════════════════════
# ACTION: scan-hl-alpha — Advanced multi-signal scanner
# ══════════════════════════════════════════════════════════════

def _scan_alpha() -> dict:
    """Advanced scanner: funding + premium + Vol/OI + BTC/ETH majors."""
    data = _fetch_and_score()
    
    alpha = {
        "double_confirm": [],     # funding extreme + premium divergence agree
        "crowded_trades": [],     # high OI + extreme funding = squeeze setup
        "momentum_anomalies": [], # extreme Vol/OI ratio
        "majors_only": {},        # BTC/ETH/SOL isolated from HL
    }
    
    hl = _get_hl()
    tickers = hl.fetch_tickers()
    
    for symbol, ticker in tickers.items():
        if not symbol.endswith("USDC:USDC"):
            continue
        
        asset = symbol.split("/")[0]
        funding_raw = ticker.get("info", {}).get("funding", 0)
        annual = float(funding_raw) * 100 * 3 * 365
        
        mark = float(ticker.get("info", {}).get("markPx", 0))
        oracle = float(ticker.get("info", {}).get("oraclePx", 0))
        premium = (mark - oracle) / oracle * 100 if oracle > 0 else 0
        
        oi = float(ticker.get("info", {}).get("openInterest", 0))
        vol = float(ticker.get("info", {}).get("dayNtlVlm", 0) or 0)
        
        if oi < 50_000:  # filter noise
            continue
        
        entry = {
            "asset": asset,
            "funding_annual_pct": round(annual, 2),
            "premium_pct": round(premium, 2),
            "oi_usd": round(oi, 2),
            "vol_24h_usd": round(vol, 2),
            "vol_oi_ratio": round(vol / oi, 1) if oi > 0 else 0,
            "mark_price": mark,
            "oracle_price": oracle,
        }
        
        # ── Double confirmation: funding + premium agree ───
        if annual < -50 and premium < -1.0:
            entry["signal"] = "LONG_DOUBLE_CONFIRM"
            entry["confidence"] = "ALTA"
            entry["why"] = f"Shorts pay {abs(annual):.0f}%/yr AND mark ${mark:.4f} below oracle ${oracle:.4f} ({premium:+.2f}% discount = accumulation)"
            entry["score"] = min(100, int(abs(annual) * 0.3 + abs(premium) * 10))
            alpha["double_confirm"].append(entry)
        elif annual > 50 and premium > 1.0:
            entry["signal"] = "SHORT_DOUBLE_CONFIRM"
            entry["confidence"] = "ALTA"
            entry["why"] = f"Longs pay {annual:.0f}%/yr AND mark ${mark:.4f} above oracle ${oracle:.4f} ({premium:+.2f}% premium = distribution)"
            entry["score"] = min(100, int(annual * 0.3 + premium * 10))
            alpha["double_confirm"].append(entry)
        
        # ── Crowded trades: massive OI + extreme funding ───
        if oi > 5_000_000 and abs(annual) > 30:
            entry["signal"] = "CROWDED_" + ("LONG" if annual < -30 else "SHORT")
            entry["confidence"] = "MEDIA"
            entry["why"] = f"${oi:,.0f} OI with {annual:+.0f}%/yr funding — crowded trade, squeeze potential"
            entry["score"] = min(100, int(oi / 500_000))
            alpha["crowded_trades"].append(entry)
        
        # ── Momentum: extreme Vol/OI turnover ───
        if oi > 500_000 and vol > 0:
            turnover = vol / oi
            if turnover > 30:
                entry["signal"] = "MOMENTUM"
                entry["confidence"] = "MEDIA"
                entry["why"] = f"{turnover:.0f}x daily turnover — {asset} is moving FAST"
                entry["score"] = min(100, int(turnover))
                alpha["momentum_anomalies"].append(entry)
        
        # ── Majors isolated: BTC/ETH/SOL pure HL data ───
        if asset in ("BTC", "ETH", "SOL"):
            alpha["majors_only"][asset] = entry
    
    # Sort by score
    for key in ["double_confirm", "crowded_trades", "momentum_anomalies"]:
        alpha[key].sort(key=lambda x: x.get("score", 0), reverse=True)
    
    alpha["summary"] = {
        "double_confirm": len(alpha["double_confirm"]),
        "crowded_trades": len(alpha["crowded_trades"]),
        "momentum_anomalies": len(alpha["momentum_anomalies"]),
        "majors_available": list(alpha["majors_only"].keys()),
    }
    
    return alpha


@action(is_consequential=False)
def scan_hl_alpha() -> str:
    """Advanced multi-signal Hyperliquid opportunity scanner.

    Detects 3 categories:
      1. DOUBLE CONFIRM: funding extreme + mark-vs-oracle premium agree
      2. CROWDED TRADES: massive OI + extreme funding = squeeze setup
      3. MOMENTUM: extreme Vol/OI turnover ratio

    Also returns isolated BTC/ETH/SOL Hyperliquid-only data.

    Returns:
        JSON with double_confirm, crowded_trades, momentum_anomalies, majors_only.
    """
    ts = datetime.now(timezone.utc).isoformat()
    try:
        data = _scan_alpha()
        return json.dumps({
            "status": "ok",
            "timestamp": ts,
            "exchange": "hyperliquid",
            **data,
        })
    except Exception as e:
        logger.error(f"scan_hl_alpha failed: {e}")
        return json.dumps({"status": "error", "timestamp": ts, "error": str(e)})
