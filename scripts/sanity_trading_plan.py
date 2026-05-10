#!/usr/bin/env python3
"""
BTC/ETH Trading Plan — using ccxtv2 update_levels.py logic
═══════════════════════════════════════════════════════════
Calculates REAL price levels from D1 + PD + H4 + ATR.
Resistances = above price. Supports = below price.
TP above entry for LONG, below entry for SHORT. 
"""
import json, time, ccxt, pandas as pd, numpy as np, httpx

AS = 'http://localhost:8080/api/actions/funding-action-server'
HL = 'http://localhost:8081/api/actions/hyperliquid-funding-server'

def ep(b, n, p, t=25):
    try:
        r = httpx.post(f'{b}/{n}/run', json=p, timeout=t)
        if r.status_code == 200:
            d = r.json()
            return json.loads(d) if isinstance(d, str) else d
        return {'error': f'HTTP {r.status_code}'}
    except Exception as e:
        return {'error': str(e)[:100]}

def get_levels(ex, symbol):
    """EXACT logic from ccxtv2 scripts/update_levels.py.
    Returns {resistances: [prices above], supports: [prices below]}"""
    d1 = ex.fetch_ohlcv(symbol, '1d', limit=7)
    df1 = pd.DataFrame(d1, columns=['ts','o','h','l','c','v'])
    d1_high = float(df1['h'].iloc[-1]); d1_low = float(df1['l'].iloc[-1])
    pd_high = float(df1['h'].iloc[-2]); pd_low = float(df1['l'].iloc[-2])
    
    h4 = ex.fetch_ohlcv(symbol, '4h', limit=10)
    df4 = pd.DataFrame(h4, columns=['ts','o','h','l','c','v'])
    h4_high = float(df4['h'].max()); h4_low = float(df4['l'].min())
    
    all_r = sorted(set([d1_high, pd_high, h4_high]))
    all_s = sorted(set([d1_low, pd_low, h4_low]), reverse=True)
    return {'resistances': all_r, 'supports': all_s}


for ASSET, SYM, SPOT in [('BTC','BTC/USDT:USDT','BTC/USDT'),('ETH','ETH/USDT:USDT','ETH/USDT')]:
    t0 = time.time()
    bar = "=" * 65
    print(f'\n{bar}\n  {ASSET} INSTITUTIONAL TRADING PLAN (ccxtv2 levels)\n  {time.strftime("%H:%M:%S UTC",time.gmtime())}\n{bar}')

    ex = ccxt.binance({'enableRateLimit': True})
    levels = get_levels(ex, SYM)
    
    df3 = pd.DataFrame(ex.fetch_ohlcv(SYM, '3m', limit=150), columns=['ts','o','h','l','c','v'])
    df15 = pd.DataFrame(ex.fetch_ohlcv(SYM, '15m', limit=150), columns=['ts','o','h','l','c','v'])
    df1h = pd.DataFrame(ex.fetch_ohlcv(SYM, '1h', limit=200), columns=['ts','o','h','l','c','v'])
    
    px = float(df3['c'].iloc[-1])
    atr3 = float((df3['h'] - df3['l']).rolling(14).mean().iloc[-1])
    atr15 = float((df15['h'] - df15['l']).rolling(14).mean().iloc[-1])

    snap = ep(AS, 'get-full-market-snapshot', {'assets': ASSET, 'ob_depth': 50})
    basis = ep(AS, 'get-basis', {'symbol_spot': SPOT, 'symbol_perp': SYM})
    hl = ep(HL, 'get-hl-funding-single', {'asset': ASSET})

    t = snap.get('triggers', {}).get(ASSET, {})
    obi = float(t.get('max_obi', 0) or 0)
    fund = float(t.get('avg_funding_pct', 0) or 0)
    bp = float((basis.get('basis_pct', 0) or 0))
    hd = hl.get('data', {})
    hfa = float(hd.get('funding_rate_annualized', 0) or 0)
    hoi = float(hd.get('open_interest_usd', 0) or 0)
    hvol = float(hd.get('volume_24h_usd', 0) or 0)

    # Bias from microstructure + price vs levels
    bs = 0
    if obi > 0.40: bs += 1
    if obi < -0.40: bs -= 1
    if bp < -0.05: bs += 1
    if bp > 0.05: bs -= 1
    # Price vs D1 range
    d1_h = levels['resistances'][-1] if levels['resistances'] else 0
    d1_l = levels['supports'][0] if levels['supports'] else 0
    pd_h = levels['resistances'][-2] if len(levels['resistances']) > 1 else d1_h
    pd_l = levels['supports'][1] if len(levels['supports']) > 1 else d1_l
    d1_mid = (d1_h + d1_l) / 2 if d1_h and d1_l else px
    if px > d1_h: bs += 2  # Breakout above D1 high
    if px < d1_l: bs -= 2  # Breakdown below D1 low
    if px > d1_mid: bs += 1  # Above D1 midpoint = bullish
    else: bs -= 1
    
    bias = 'LONG' if bs >= 2 else ('SHORT' if bs <= -2 else 'NEUTRAL')

    # Swings
    swh = float(df3['h'].max())
    swl = float(df3['l'].min())
    
    # OTE zone
    diff = abs(swh - swl)
    if bias == 'LONG':
        entry_low = round(swh - diff * 0.786, 2)
        entry_high = round(swh - diff * 0.618, 2)
    elif bias == 'SHORT':
        entry_low = round(swl + diff * 0.618, 2)
        entry_high = round(swl + diff * 0.786, 2)
    else:
        ote_l_low = round(swh - diff * 0.786, 2)
        ote_l_high = round(swh - diff * 0.618, 2)
        ote_s_low = round(swl + diff * 0.618, 2)
        ote_s_high = round(swl + diff * 0.786, 2)

    # TPs from REAL levels
    if bias == 'LONG':
        tp_candidates = [r for r in levels['resistances'] if r > px]
        sl = round(entry_low - 2 * atr15, 2)  # SL from ENTRY, not from current price
    elif bias == 'SHORT':
        tp_candidates = [s for s in levels['supports'] if s < px]
        sl = round(entry_high + 2 * atr15, 2)
    else:
        tp_candidates = []

    # Print
    print(f'\n  PRICE: ${px:,.2f} | ATR: {atr3:.2f}(3m) {atr15:.2f}(15m)')
    print(f'  D1 range: ${d1_l:,.2f} — ${d1_h:,.2f} | PD: ${levels["supports"][1] if len(levels["supports"])>1 else "?"} — ${levels["resistances"][1] if len(levels["resistances"])>1 else "?"}')
    print()
    print(f'  [SANITY] Basis: {bp:+.4f}% | HL: {hfa:+.0f}%/yr OI=${hoi:,.0f} Vol=${hvol:,.0f}')
    print(f'  [SIGNAL] OBI={obi:+.4f} | Fund={fund:+.4f}% | BiasScore={bs:+d} -> {bias}')
    print()

    print(f'  [D1+PD+H4 LEVELS]')
    for r in levels['resistances']:
        dist = (r - px) / px * 100
        tag = ''
        if abs(r - d1_h) < 0.01: tag = ' D1 HIGH'
        elif abs(r - pd_h) < 0.01: tag = ' PD HIGH'
        else: tag = ' H4'
        print(f'    ▲ ${r:>10,.2f} ({dist:+.2f}%){tag}')
    
    for s in levels['supports']:
        dist = (s - px) / px * 100
        tag = ''
        if abs(s - d1_l) < 0.01: tag = ' D1 LOW'
        elif abs(s - pd_l) < 0.01: tag = ' PD LOW'
        else: tag = ' H4'
        print(f'    ▼ ${s:>10,.2f} ({dist:+.2f}%){tag}')
    print()

    print(f'  [TRADING PLAN]')
    if bias != 'NEUTRAL':
        print(f'    BIAS: {bias}')
        print(f'    ENTRY: ${entry_low:,.2f} — ${entry_high:,.2f} (OTE 61.8%-78.6%)')
        print(f'    SL:    ${sl:,.2f} (2x ATR 15m)')
        for i, tp in enumerate(tp_candidates[:3]):
            dd = (tp - px) / px * 100
            rr_val = abs(tp - px) / abs(sl - px) if sl != px else 0
            print(f'    TP{i+1}:   ${tp:,.2f} ({dd:+.2f}%  R:R={rr_val:.1f})')
        print(f'    ❌ INVALIDATION: OBI flip | Sweep against | SL hit | 15min')
    else:
        print(f'    ⚪ NO ENTRY — bias {bs:+d} (need |2|)')
        print(f'    OTE LONG:   ${ote_l_low:,.2f} — ${ote_l_high:,.2f}')
        print(f'    OTE SHORT:  ${ote_s_low:,.2f} — ${ote_s_high:,.2f}')
        r_above = [r for r in levels['resistances'] if r > px]
        s_below = [s for s in levels['supports'] if s < px]
        if r_above: print(f'    TP LONG:  {", ".join(f"${x:,.2f}" for x in r_above[:3])}')
        if s_below: print(f'    TP SHORT: {", ".join(f"${x:,.2f}" for x in s_below[:3])}')
        print(f'    ⏳ TRIGGER: bias ≥|2| or breakout D1 high/low')

    elapsed = time.time() - t0
    print(f'\n  [DONE in {elapsed:.1f}s]')

print(f'\n{bar}\n  BOTH ASSETS COMPLETE\n{bar}')
