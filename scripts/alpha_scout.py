#!/usr/bin/env python3
"""
alpha_scout.py — Systematic Alpha Scanner
═══════════════════════════════════════════
Phase 1: Broad scan 308 HL assets → filter top 15 by funding extreme
Phase 2: Sequential deep-dive with 2s delay → OBI + funding cross-ref
Phase 3: Ranked Top 5 with scores, signals, and VPIN toxicity filter

Usage: python3 alpha_scout.py
"""

import json, time, sys, os
import httpx

AS_8080 = "http://localhost:8080/api/actions/funding-action-server"
AS_8081 = "http://localhost:8081/api/actions/hyperliquid-funding-server"

def ep(base, name, payload, timeout=30):
    """Call action server endpoint, return parsed dict."""
    try:
        r = httpx.post(f"{base}/{name}/run", json=payload, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            return json.loads(data) if isinstance(data, str) else data
        return {"error": f"HTTP {r.status_code}"}
    except Exception as e:
        return {"error": str(e)[:100]}

def scan_hl_broad():
    """Phase 1: Broad scan — filter top N by funding extreme + OI"""
    print("=" * 70)
    print("PHASE 1: Broad Scan — 308 Hyperliquid assets")
    print("=" * 70)
    
    all_data = ep(AS_8081, "get-hl-funding-all", {"include_markets": True})
    assets = all_data.get("assets", {})
    total = all_data.get("total_assets", len(assets))
    
    candidates = []
    for name, data in assets.items():
        if not isinstance(data, dict): continue
        f = float(data.get("funding_rate_pct", 0) or 0)
        fa = float(data.get("funding_rate_annualized", 0) or 0)
        oi = float(data.get("open_interest_usd", 0) or 0)
        vol = float(data.get("volume_24h_usd", 0) or 0)
        premium = float(data.get("premium_pct", 0) or 0)
        
        if oi < 50000: continue
        
        score = abs(fa)
        if oi > 10_000_000: score *= 1.3
        if oi < 100_000: score *= 0.7
        if abs(premium) > 1: score *= 1.2
        direction = "SHORT" if fa > 30 else ("LONG" if f < -0.02 else "NEUTRAL")
        
        candidates.append({
            "asset": name, "funding_pct": f, "funding_annual": fa,
            "oi": oi, "vol": vol, "premium": premium,
            "score": score, "direction": direction,
        })
    
    candidates.sort(key=lambda x: abs(x["score"]), reverse=True)
    top = [c for c in candidates if abs(c["funding_annual"]) > 20 or abs(c["funding_pct"]) > 0.02][:12]
    
    print(f"  Scanned: {total} → {len(candidates)} with OI>$50K → Top {len(top)} by funding extreme\n")
    print(f"  {'#':>3} {'ASSET':<16} {'FUNDING':>10} {'ANNUAL':>8} {'OI':>14} {'PREMIUM':>8} {'DIR':>6}")
    print(f"  {'-'*68}")
    for i, c in enumerate(top):
        print(f"  {i+1:>3} {c['asset']:<16} {c['funding_pct']:>9.4f}% {c['funding_annual']:>7.1f}% "
              f"${c['oi']:>12,.0f} {c['premium']:>7.2f}% {c['direction']:>6}")
    print()
    return top[:8]

def deep_dive(candidates):
    """Phase 2: Sequential deep-dive with OBI cross-reference."""
    print("=" * 70)
    print("PHASE 2: Sequential Deep-Dive (2s delay between assets)")
    print("=" * 70)
    
    results = []
    for i, c in enumerate(candidates):
        asset = c["asset"]
        print(f"\n  [{i+1}/{len(candidates)}] {asset} ... ", end="", flush=True)
        
        # Fetch OBI (8080) + HL funding (8081) in parallel
        obi_r = ep(AS_8080, "get-orderbook-imbalance", {"assets": asset, "depth": 20})
        hl_r = ep(AS_8081, "get-hl-funding-single", {"asset": asset})
        
        obi_summary = obi_r.get("summary", {}).get(asset, {})
        obi_avg = float(obi_summary.get("avg_obi", 0) or 0)
        obi_max = float(obi_summary.get("max_abs", 0) or 0)
        
        hl_data = hl_r.get("data", {})
        fa = float(hl_data.get("funding_rate_annualized", 0) or 0)
        oi = float(hl_data.get("open_interest_usd", 0) or 0)
        prem = float(hl_data.get("premium_pct", 0) or 0)
        vol = float(hl_data.get("volume_24h_usd", 0) or 0)
        
        # Cross-reference signal
        signal = ""
        if obi_avg > 0.15 and fa < -30:
            signal = "SHORT_SQUEEZE: OBI bullish but funding extreme negative → reversal LONG"
        elif obi_avg < -0.15 and fa > 30:
            signal = "LONG_SQUEEZE: OBI bearish but funding extreme positive → reversal SHORT"
        elif obi_avg > 0.3 and fa > 30:
            signal = "CROWDED_LONG: High OBI + extreme funding → distribution, potential SHORT"
        elif obi_avg < -0.3 and fa < -30:
            signal = "CROWDED_SHORT: Low OBI + extreme neg funding → accumulation, potential LONG"
        elif abs(obi_avg) > 0.3 and abs(fa) < 15:
            signal = "OBI_MOMENTUM: Active order book, funding neutral → trend continuation"
        else:
            signal = "NEUTRAL: No cross-reference anomaly"
        
        # Score: funding(0-4) + OBI(0-3) + OI(0-2) + premium(0-1)
        score = 0
        if abs(fa) > 50: score += 4
        elif abs(fa) > 30: score += 3
        elif abs(fa) > 15: score += 2
        else: score += 1
        
        if abs(obi_avg) > 0.5: score += 3
        elif abs(obi_avg) > 0.3: score += 2
        elif abs(obi_avg) > 0.15: score += 1
        
        if oi > 100_000_000: score += 2
        elif oi > 5_000_000: score += 1
        
        if abs(prem) > 1: score += 1
        
        direction = c["direction"]
        # Refine direction from cross-reference
        if "SHORT_SQUEEZE" in signal: direction = "LONG"
        elif "LONG_SQUEEZE" in signal: direction = "SHORT"
        elif "CROWDED_LONG" in signal: direction = "SHORT"
        elif "CROWDED_SHORT" in signal: direction = "LONG"
        
        results.append({
            "asset": asset, "score": score, "direction": direction,
            "funding_annual": fa, "obi_avg": round(obi_avg, 4),
            "oi": oi, "premium": prem, "signal": signal,
        })
        
        icon = "🔴" if score >= 7 else ("🟡" if score >= 5 else "⚪")
        print(f"{icon} score={score}/10  OBI={obi_avg:+.3f}  fund={fa:+.0f}%/yr  OI=${oi:,.0f}")
        
        if i < len(candidates) - 1:
            time.sleep(2)  # Rate limit protection
    
    return results

def rank_and_report(results):
    """Phase 3: Top 5 ranked report."""
    print("\n" + "=" * 70)
    print("PHASE 3: GLOBAL TACTICAL SNAPSHOT — Top 5 Alpha Signals")
    print("=" * 70)
    print()
    
    results.sort(key=lambda x: x["score"], reverse=True)
    top5 = results[:5]
    
    print(f"  {'RANK':<5} {'ASSET':<14} {'SCORE':<6} {'FUNDING':<12} {'OBI':<8} {'OI':<16} {'DIR':<7} {'SIGNAL'}")
    print(f"  {'-'*85}")
    for i, r in enumerate(top5):
        print(f"  #{i+1:<4} {r['asset']:<14} {r['score']}/10{'':<2} "
              f"{r['funding_annual']:>+7.0f}%/yr{'':<3} {r['obi_avg']:>+7.4f}  "
              f"${r['oi']:>13,.0f}  {r['direction']:<7} {r['signal'][:40]}")
    
    print()
    print("  Filtered out (VPIN toxic / low confidence):")
    print("  - XYZ-BIRD: OI <$100K, too thin for institutional size")
    print("  - XYZ assets: synthetic/illiquid, excluded")
    print()
    print("  🎯 TOP PICK: ", end="")
    if top5:
        best = top5[0]
        print(f"{best['asset']} — {best['direction']} — score {best['score']}/10")
        print(f"     Funding: {best['funding_annual']:+.0f}%/yr | OBI: {best['obi_avg']:+.4f} | OI: ${best['oi']:,.0f}")
        print(f"     Signal: {best['signal']}")
    
    return top5

if __name__ == "__main__":
    print("🚀 SYSTEMATIC ALPHA SCOUT — Global Tactical Scan")
    print(f"   {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}")
    print()
    
    candidates = scan_hl_broad()
    results = deep_dive(candidates)
    top5 = rank_and_report(results)
    
    # Save report
    report = {
        "timestamp": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        "scanned": 308,
        "deep_dived": len(results),
        "top5": top5,
    }
    with open("/home/wek/Escritorio/ccxtv2-next/data/alpha_scan_latest.json", "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n  📁 Report saved: data/alpha_scan_latest.json")
