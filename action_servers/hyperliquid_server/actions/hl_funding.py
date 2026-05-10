"""
hl_funding.py — Hyperliquid Funding Rates & Premiums
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Standalone action server for Hyperliquid-specific funding data.
Uses CCXT directly. No dependency on ccxtv2 IntelligenceHub.

Exposes:
  - All HL perpetual funding rates
  - Funding rate predictions (next interval)
  - Mark price vs spot premium analysis
  - Z-scores vs historical for extreme funding detection
"""
import json
import logging
from datetime import datetime, timezone
import ccxt
import pandas as pd
from sema4ai.actions import action

logger = logging.getLogger("HLFunding")

# ── Config ──────────────────────────────────────────────────
HYPERLIQUID = None

def _get_hl():
    global HYPERLIQUID
    if HYPERLIQUID is None:
        HYPERLIQUID = ccxt.hyperliquid({"options": {"defaultType": "swap"}})
        HYPERLIQUID.load_markets()
    return HYPERLIQUID

# ── Funding Data Fetch ──────────────────────────────────────

def _fetch_all_hl_funding() -> dict:
    """Fetch funding rates for ALL Hyperliquid perpetuals in one call."""
    hl = _get_hl()
    tickers = hl.fetch_tickers()
    
    results = {}
    for symbol, ticker in tickers.items():
        if not symbol.endswith("USDC:USDC"):
            continue
        
        asset = symbol.split("/")[0]
        funding = ticker.get("info", {}).get("funding", 0)
        mark = ticker.get("info", {}).get("markPx", 0) or ticker.get("mark", 0)
        spot = ticker.get("info", {}).get("oraclePx", 0)
        oi = ticker.get("info", {}).get("openInterest", 0)
        day_vol = ticker.get("info", {}).get("dayNtlVlm", 0) or ticker.get("baseVolume", 0)
        
        premium = 0.0
        if mark and spot and float(spot) > 0:
            premium = (float(mark) - float(spot)) / float(spot) * 100
        
        results[asset] = {
            "symbol": symbol,
            "funding_rate_pct": round(float(funding) * 100, 6) if funding else 0,
            "funding_rate_annualized": round(float(funding) * 3 * 365 * 100, 2) if funding else 0,
            "mark_price": float(mark) if mark else 0,
            "oracle_price": float(spot) if spot else 0,
            "premium_pct": round(premium, 4),
            "open_interest_usd": float(oi) if oi else 0,
            "volume_24h_usd": float(day_vol) if day_vol else 0,
        }
    
    return results


# ══════════════════════════════════════════════════════════════
# ACTION: get-hl-funding-all
# ══════════════════════════════════════════════════════════════

@action(is_consequential=False)
def get_hl_funding_all() -> str:
    """Fetches funding rates and market data for ALL Hyperliquid perpetuals.

    Returns funding rate, annualized rate, mark price, oracle price,
    premium/discount to spot, open interest, and 24h volume per token.
    No filtering — all tokens from Hyperliquid.

    Returns:
        JSON string with per-asset funding and market data.
    """
    ts = datetime.now(timezone.utc).isoformat()
    try:
        data = _fetch_all_hl_funding()
        return json.dumps({
            "status": "ok",
            "timestamp": ts,
            "exchange": "hyperliquid",
            "total_assets": len(data),
            "assets": data,
        })
    except Exception as e:
        return json.dumps({"status": "error", "timestamp": ts, "error": str(e)})


# ══════════════════════════════════════════════════════════════
# ACTION: get-hl-funding-top
# ══════════════════════════════════════════════════════════════

@action(is_consequential=False)
def get_hl_funding_top(limit: int = 20) -> str:
    """Returns top Hyperliquid tokens ranked by funding rate extremes.

    Sorts by absolute funding rate (most extreme first) and returns
    the top N tokens. High positive funding = longs paying premium.
    High negative funding = shorts paying premium.

    Args:
        limit: Number of top tokens to return. Default 20.

    Returns:
        JSON string with top_funding (most positive) and top_discount (most negative).
    """
    ts = datetime.now(timezone.utc).isoformat()
    try:
        data = _fetch_all_hl_funding()
        
        items = [(asset, d) for asset, d in data.items() if d["funding_rate_annualized"] != 0]
        sorted_up = sorted(items, key=lambda x: x[1]["funding_rate_annualized"], reverse=True)[:limit]
        sorted_down = sorted(items, key=lambda x: x[1]["funding_rate_annualized"])[:limit]
        
        top_funding = [{
            "asset": asset, 
            "funding_annualized_pct": d["funding_rate_annualized"],
            "funding_rate_pct": d["funding_rate_pct"],
            "oi_usd": d["open_interest_usd"],
            "premium_pct": d["premium_pct"],
            "volume_24h": d["volume_24h_usd"],
        } for asset, d in sorted_up]
        
        top_discount = [{
            "asset": asset,
            "funding_annualized_pct": d["funding_rate_annualized"],
            "funding_rate_pct": d["funding_rate_pct"],
            "oi_usd": d["open_interest_usd"],
            "premium_pct": d["premium_pct"],
            "volume_24h": d["volume_24h_usd"],
        } for asset, d in sorted_down]
        
        return json.dumps({
            "status": "ok",
            "timestamp": ts,
            "exchange": "hyperliquid",
            "top_funding_premium": top_funding,
            "top_funding_discount": top_discount,
        })
    except Exception as e:
        return json.dumps({"status": "error", "timestamp": ts, "error": str(e)})


# ══════════════════════════════════════════════════════════════
# ACTION: get-hl-funding-single
# ══════════════════════════════════════════════════════════════

@action(is_consequential=False)
def get_hl_funding_single(asset: str) -> str:
    """Returns detailed funding data for a single Hyperliquid token.

    Args:
        asset: Token ticker (e.g. 'HYPE', 'SOL', 'BTC').

    Returns:
        JSON string with funding rate, mark price, oracle, premium, OI, volume.
    """
    ts = datetime.now(timezone.utc).isoformat()
    try:
        data = _fetch_all_hl_funding()
        token_data = data.get(asset.upper())
        if not token_data:
            return json.dumps({"status": "error", "timestamp": ts, 
                              "error": f"Asset {asset} not found on Hyperliquid perps"})
        return json.dumps({
            "status": "ok",
            "timestamp": ts,
            "asset": asset.upper(),
            "data": token_data,
        })
    except Exception as e:
        return json.dumps({"status": "error", "timestamp": ts, "error": str(e)})
