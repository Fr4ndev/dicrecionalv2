#!/usr/bin/env python3
"""
scalp_analysis.py — BTC/ETH Exhaustive Scalp Deep-Dive
═══════════════════════════════════════════════════════════
Extracts: SR levels (TP1/TP2/TP3), OTE zone (entry), SL (ATR-based),
sweeps, FVG, OBI, VPIN, funding, Hyperliquid context.

Output: Complete scalp setup with numbered targets and invalidation.
"""

import sys, os, json, time, asyncio
import numpy as np
import pandas as pd
import httpx

# Add project root and action_server to path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "action_servers", "funding_server"))

from shared.engines.sr_levels import SRLevelsEngine
from shared.engines.ict_engine import ICTEngine
from shared.redis_bridge import RedisBridge, redis

AS = "http://localhost:8080/api/actions/funding-action-server"
HL = "http://localhost:8081/api/actions/hyperliquid-funding-server"

SR = SRLevelsEngine(n_bins=50, rolling_window=50)
ICT = ICTEngine(ema_period=50, fvg_min_atr_ratio=0.5)

def ep(base, name, payload, timeout=30):
    try:
        r = httpx.post(f"{base}/{name}/run", json=payload, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            return json.loads(data) if isinstance(data, str) else data
        return {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:100]}

def fetch_ohlcv(symbol, tf, limit=200):
    import ccxt
    ex = ccxt.binance({"enableRateLimit": True})
    try:
        ohlcv = ex.fetch_ohlcv(symbol, tf, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])
        df['datetime'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('datetime', inplace=True)
        return df
    except Exception as e:
        print(f"  ⚠️ OHLCV {symbol} {tf}: {e}")
        return None

def extract_sr_targets(levels, current_price, bias, top_n=3):
    """Extract TP1/TP2/TP3 from SR levels in bias direction."""
    candidates = []
    for lvl in levels:
        if (bias == "LONG" and lvl["price"] > current_price) or \
           (bias == "SHORT" and lvl["price"] < current_price):
            candidates.append(lvl)
    candidates.sort(key=lambda x: (x["strength"], x.get("volume_score", 0)), reverse=True)
    return [c["price"] for c in candidates[:top_n]]

def format_setup(asset, symbol, spot_sym):
    print(f"\n{'═'*70}")
    print(f"  {asset} SCALP SETUP — Institutional Deep-Dive")
    print(f"  {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print(f"{'═'*70}")

    # ═══════════════════════════════════════════════════════════════
    # DATA FETCH
    # ═══════════════════════════════════════════════════════════════
    
    # OHLCV (3m for scalp, 15m for FVG, 1h for SR)
    df_3m = fetch_ohlcv(symbol, "3m", limit=150)
    df_15m = fetch_ohlcv(symbol, "15m", limit=150)
    df_1h = fetch_ohlcv(symbol, "1h", limit=200)
    current_price = float(df_3m['close'].iloc[-1]) if df_3m is not None else 0

    # Action Server
    snap = ep(AS, "get-full-market-snapshot", {"assets": asset, "ob_depth": 50})
    vpin_r = ep(AS, "get-toxicity-index", {"symbol": symbol, "ob_depth": 50, "trade_limit": 500})
    walls = ep(AS, "get-ob-walls", {"symbol": symbol, "depth": 30})
    basis = ep(AS, "get-basis", {"symbol_spot": spot_sym, "symbol_perp": symbol})
    hl_r = ep(HL, "get-hl-funding-single", {"asset": asset})

    # ═══════════════════════════════════════════════════════════════
    # MICROSTRUCTURE
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  ── MICROSTRUCTURE ──")
    
    triggers = snap.get("triggers", {}).get(asset, {})
    obi_val = float(triggers.get("max_obi", 0) or 0)
    funding_avg = float(triggers.get("avg_funding_pct", 0) or 0)
    trigger_level = triggers.get("trigger_level", "NONE")
    
    vpin_raw = vpin_r.get("toxicity_index", vpin_r.get("vpin_index", 0))
    if isinstance(vpin_raw, str):
        try: vpin_raw = json.loads(vpin_raw).get("toxicity_index", 0)
        except: pass
    vpin_val = float(vpin_raw or 0)
    
    basis_pct = float((basis.get("basis_pct", 0) or 0))
    hl_data = hl_r.get("data", {})
    hl_funding = float(hl_data.get("funding_rate_annualized", 0) or 0)
    hl_oi = float(hl_data.get("open_interest_usd", 0) or 0)
    hl_premium = float(hl_data.get("premium_pct", 0) or 0)
    
    print(f"  Price: ${current_price:,.2f}")
    print(f"  OBI: {obi_val:+.4f}  |  VPIN: {vpin_val:.4f}  |  Funding(avg): {funding_avg:+.4f}%")
    print(f"  Basis: {basis_pct:+.4f}%  |  Trigger: {trigger_level}")
    print(f"  HL: funding={hl_funding:+.1f}%/yr  OI=${hl_oi:,.0f}  premium={hl_premium:+.2f}%")

    # ═══════════════════════════════════════════════════════════════
    # ICT ENGINE — Sweeps, FVG, OTE, Valeyre
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  ── ICT / SMART MONEY ──")
    
    sweeps = ICT.detect_sweeps(df_3m, lookback=48) if df_3m is not None else {}
    fvgs = ICT.detect_fvgs(df_15m) if df_15m is not None else []
    z_signal = ICT.get_zscore_signal(df_1h) if df_1h is not None else {}
    po3 = ICT.detect_po3_amd(df_1h) if df_1h is not None else {}
    
    # Only unfilled FVGs in the right direction
    unfilled_bull = [f for f in fvgs[-10:] if f["type"] == "bullish" and not f.get("filled")]
    unfilled_bear = [f for f in fvgs[-10:] if f["type"] == "bearish" and not f.get("filled")]
    
    print(f"  Sweep High: {sweeps.get('sweep_high', False)}  |  Sweep Low: {sweeps.get('sweep_low', False)}")
    print(f"  Range: {sweeps.get('range_low', 0):,.0f} — {sweeps.get('range_high', 0):,.0f}")
    print(f"  Valeyre Z: {z_signal.get('z_score', 0):.2f}  |  Regime: {z_signal.get('regime', '?')}  |  Bias: {z_signal.get('bias', '?')}")
    print(f"  PO3 Phase: {po3.get('phase', '?')}  |  Silver Bullet: {ICT.is_silver_bullet_window()}")
    print(f"  FVGs: {len(unfilled_bull)} bullish unfilled, {len(unfilled_bear)} bearish unfilled")

    # ═══════════════════════════════════════════════════════════════
    # SR LEVELS ENGINE — TP1, TP2, TP3
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  ── SR LEVELS (TP1/TP2/TP3) ──")
    
    sr_levels_1h = SR.compute_key_levels(df_1h, top_n=12) if df_1h is not None else []
    
    # Determine bias from ALL signals
    bias_score = 0
    if z_signal.get("bias") == "SHORT": bias_score -= 1
    if z_signal.get("bias") == "LONG": bias_score += 1
    if sweeps.get("sweep_high"): bias_score -= 1  # Sweep high = bearish SFP
    if sweeps.get("sweep_low"): bias_score += 1   # Sweep low = bullish SFP
    if obi_val > 0.40: bias_score += 1
    if obi_val < -0.40: bias_score -= 1
    if basis_pct < -0.05: bias_score += 1  # Spot premium = accumulation
    if basis_pct > 0.05: bias_score -= 1   # Perp premium = FOMO
    if funding_avg > 0.05: bias_score -= 1  # Longs paying = bearish
    if funding_avg < -0.05: bias_score += 1  # Shorts paying = bullish
    
    if bias_score >= 2: bias = "LONG"
    elif bias_score <= -2: bias = "SHORT"
    else: bias = "NEUTRAL"
    
    print(f"  Bias Score: {bias_score:+d} → {bias}")
    
    # SR Level display
    for lvl in sr_levels_1h[:8]:
        marker = "▲" if lvl["price"] > current_price else "▼"
        print(f"  {marker} ${lvl['price']:>10,.2f}  strength={lvl['strength']:.3f}  vol={lvl['volume_score']:.3f}  pivots={lvl['pivot_count']}  [{lvl['type']}]")
    
    # Extract targets
    tp_long = extract_sr_targets(sr_levels_1h, current_price, "LONG", 3)
    tp_short = extract_sr_targets(sr_levels_1h, current_price, "SHORT", 3)
    
    # ═══════════════════════════════════════════════════════════════
    # OTE ZONE (Entry)
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  ── ENTRY ZONE (OTE) ──")
    
    if df_3m is not None:
        sw_high = float(df_3m['high'].max())
        sw_low = float(df_3m['low'].min())
        ote_long = ICT.calculate_ote(sw_high, sw_low, 1)
        ote_short = ICT.calculate_ote(sw_high, sw_low, -1)
        print(f"  Swing: ${sw_low:,.2f} — ${sw_high:,.2f}")
        print(f"  OTE LONG:  ${ote_long[0]:,.2f} — ${ote_long[1]:,.2f}")
        print(f"  OTE SHORT: ${ote_short[0]:,.2f} — ${ote_short[1]:,.2f}")

    # ATR
    atr_3m = float((df_3m['high'] - df_3m['low']).rolling(14).mean().iloc[-1]) if df_3m is not None else 0
    atr_15m = float((df_15m['high'] - df_15m['low']).rolling(14).mean().iloc[-1]) if df_15m is not None else 0

    # ═══════════════════════════════════════════════════════════════
    # OB WALLS — liquidity clusters
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  ── LIQUIDITY (OB Walls) ──")
    
    bids = walls.get("bids", [])[:5]
    asks = walls.get("asks", [])[:5]
    
    if bids:
        bid_cluster = sum(b[1] for b in bids if isinstance(b, list) and len(b) >= 2)
        print(f"  Bid cluster: {bid_cluster:,.1f} contracts")
        for b in bids[:3]:
            if isinstance(b, list) and len(b) >= 2:
                print(f"    ${b[0]:,.2f} x {b[1]:,.1f}")
    if asks:
        ask_cluster = sum(a[1] for a in asks if isinstance(a, list) and len(a) >= 2)
        print(f"  Ask cluster: {ask_cluster:,.1f} contracts")
        for a in asks[:3]:
            if isinstance(a, list) and len(a) >= 2:
                print(f"    ${a[0]:,.2f} x {a[1]:,.1f}")
    
    # ═══════════════════════════════════════════════════════════════
    # TRADING PLAN
    # ═══════════════════════════════════════════════════════════════
    print(f"\n{'─'*70}")
    print(f"  📋 TRADING PLAN — {asset} SCALP")
    print(f"{'─'*70}")
    
    print(f"\n  BIAS: {bias} (score={bias_score:+d})")
    print(f"  ATR: {atr_3m:,.2f} (3m) / {atr_15m:,.2f} (15m)")
    
    if bias != "NEUTRAL":
        entry_low, entry_high = ote_long if bias == "LONG" else ote_short
        tp_list = tp_long if bias == "LONG" else tp_short
        sl_price = round(current_price - 2 * atr_15m, 2) if bias == "LONG" else round(current_price + 2 * atr_15m, 2)
        
        print(f"\n  🎯 ENTRY: ${entry_low:,.2f} — ${entry_high:,.2f} (OTE zone)")
        print(f"  🛑 SL:    ${sl_price:,.2f} ({'below' if bias == 'LONG' else 'above'} entry, 2x ATR)")
        
        for i, tp in enumerate(tp_list):
            dist_pct = abs(tp - current_price) / current_price * 100
            rr = abs(tp - current_price) / abs(sl_price - current_price) if sl_price != current_price else 0
            print(f"  🏁 TP{i+1}:   ${tp:,.2f}  (+{dist_pct:.2f}%  R:R={rr:.1f})")
        
        print(f"\n  ❌ INVALIDATION:")
        print(f"     OBI flips {'below -0.20' if bias == 'LONG' else 'above +0.20'}")
        print(f"     Sweep {'low' if bias == 'LONG' else 'high'} taken out")
        print(f"     VPIN drops below 0.40")
        print(f"     Timeout: 15 minutes without hitting TP1")
    else:
        print(f"\n  ⚪ NO BIAS — insufficient signal confluence")
        print(f"     Wait for: bias_score ≥ |2| or regime change")
    
    print(f"\n{'═'*70}\n")


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("🔬 INSTITUTIONAL SCALP DEEP-DIVE")
    print(f"   {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
    
    # BTC
    format_setup("BTC", "BTC/USDT:USDT", "BTC/USDT")
    
    # ETH
    format_setup("ETH", "ETH/USDT:USDT", "ETH/USDT")
    
    # Also show HL quick scan top 10
    print("═" * 70)
    print("  HYPERLIQUID QUICK SCAN — Top Funding Extremes")
    print("═" * 70)
    
    hl_all = ep(HL, "get-hl-funding-all", {"include_markets": True})
    if "error" not in hl_all:
        assets = hl_all.get("assets", {})
        candidates = []
        for name, data in assets.items():
            if not isinstance(data, dict): continue
            fa = float(data.get("funding_rate_annualized", 0) or 0)
            oi = float(data.get("open_interest_usd", 0) or 0)
            if oi > 50_000:
                candidates.append((name, fa, oi))
        
        candidates.sort(key=lambda x: x[1], reverse=True)
        print(f"\n  🔥 LONG PAYERS → SHORT opportunities:")
        for name, fa, oi in candidates[:5]:
            print(f"    {name:<18} {fa:>+8.1f}%/yr  OI=${oi:>12,.0f}")
        
        candidates.sort(key=lambda x: x[1])
        print(f"\n  🟢 SHORT PAYERS → LONG opportunities:")
        for name, fa, oi in candidates[:5]:
            print(f"    {name:<18} {fa:>+8.1f}%/yr  OI=${oi:>12,.0f}")
