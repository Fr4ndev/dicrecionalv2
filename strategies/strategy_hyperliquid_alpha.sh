#!/bin/bash
# ============================================================================
# strategy_hyperliquid_alpha.sh — Hyperliquid Altcoin Alpha Scanner
# ============================================================================
# Scans ALL 308 Hyperliquid perpetuals for:
#   - Extreme funding premiums (SHORT opportunities)
#   - Extreme funding discounts (LONG opportunities)
#   - Crowded trades with squeeze potential
#   - Low-cap early opportunities (low OI + extreme funding)
#   - Momentum anomalies (volume/OI ratio spikes)
#
# Usage:  bash strategy_hyperliquid_alpha.sh [mode]
#         bash strategy_hyperliquid_alpha.sh top        # Top opportunities
#         bash strategy_hyperliquid_alpha.sh crowded    # Squeeze targets
#         bash strategy_hyperliquid_alpha.sh alpha      # Low-cap alpha
#         bash strategy_hyperliquid_alpha.sh all        # Everything
# ============================================================================

MODE="${1:-all}"
BASE="http://localhost:8081/api/actions/hyperliquid-funding-server"

echo "═══════════════════════════════════════════════════════════════════"
echo "  HYPERLIQUID ALPHA SCANNER — $MODE"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "═══════════════════════════════════════════════════════════════════"
echo ""

# ── TOP FUNDING EXTREMES ────────────────────────────────────────────
if [ "$MODE" = "top" ] || [ "$MODE" = "all" ]; then
    echo "━━━ TOP FUNDING EXTREMES ━━━"
    echo ""
    
    TOP=$(curl -s -m 30 -X POST "$BASE/get-hl-funding-top/run" \
      -H "Content-Type: application/json" \
      -d '{"top_n":10}')
    
    echo "$TOP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)

print('🔥 LONG PAYERS → SHORT THESE (funding > +30% annualized):')
print(f'   {\"ASSET\":<16} {\"FUNDING\":>10} {\"OI\":>14} {\"PREMIUM\":>8} {\"VOL24H\":>12}')
print('   ' + '-'*65)
for item in d.get('top_funding_premium',[])[:10]:
    f=item.get('funding_annualized_pct',0)
    if f > 30:
        print(f'   {item[\"asset\"]:<16} {f:>9.1f}%/yr  \${item[\"oi_usd\"]:>11,.0f}  {item[\"premium_pct\"]:>7.1f}%  \${item[\"volume_24h\"]:>10,.0f}')

print()
print('🟢 SHORT PAYERS → LONG THESE (funding < -10% annualized):')
print(f'   {\"ASSET\":<16} {\"FUNDING\":>10} {\"OI\":>14} {\"PREMIUM\":>8} {\"VOL24H\":>12}')
print('   ' + '-'*65)
for item in d.get('top_funding_discount',[])[:10]:
    f=item.get('funding_annualized_pct',0)
    if f < -10:
        print(f'   {item[\"asset\"]:<16} {f:>9.1f}%/yr  \${item[\"oi_usd\"]:>11,.0f}  {item[\"premium_pct\"]:>7.1f}%  \${item[\"volume_24h\"]:>10,.0f}')
" 2>/dev/null
fi

# ── CROWDED TRADES (SQUEEZE POTENTIAL) ──────────────────────────────
if [ "$MODE" = "crowded" ] || [ "$MODE" = "all" ]; then
    echo ""
    echo "━━━ CROWDED TRADES (SQUEEZE POTENTIAL) ━━━"
    echo ""
    
    CROWDED=$(curl -s -m 60 -X POST "$BASE/scan-hl-alpha/run" \
      -H "Content-Type: application/json" \
      -d '{"min_annualized":20,"max_oi_usd":200000000}')
    
    echo "$CROWDED" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)

for trade in d.get('crowded_trades',[]):
    signal=trade.get('signal','?')
    conf=trade.get('confidence','?')
    score=trade.get('score',0)
    why=trade.get('why','?')
    oi=trade.get('oi_usd',0)
    fund=trade.get('funding_annual_pct',0)
    
    emoji='🟢' if 'LONG' in signal else '🔴'
    print(f'{emoji} {trade[\"asset\"]:<12} score={score:<4} {conf:<6} | {fund:+.1f}%/yr | OI=\${oi:,.0f}')
    print(f'   {why}')
    print()

for anomaly in d.get('momentum_anomalies',[]):
    print(f'⚡ MOMENTUM: {anomaly[\"asset\"]:<12} {anomaly.get(\"vol_oi_ratio\",0):.0f}x turnover | {anomaly.get(\"why\",\"?\")}')
" 2>/dev/null
fi

# ── LOW-CAP ALPHA (EARLY OPPORTUNITIES) ─────────────────────────────
if [ "$MODE" = "alpha" ] || [ "$MODE" = "all" ]; then
    echo ""
    echo "━━━ LOW-CAP ALPHA (EARLY OPPORTUNITIES) ━━━"
    echo ""
    
    ALPHA=$(curl -s -m 60 -X POST "$BASE/scan-hl-alpha/run" \
      -H "Content-Type: application/json" \
      -d '{"min_annualized":30,"max_oi_usd":500000}')
    
    echo "$ALPHA" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)

# Low OI + extreme funding = early opportunity (not crowded)
print(f'   {\"ASSET\":<16} {\"FUNDING\":>10} {\"OI\":>12} {\"SIGNAL\":<16} {\"CONF\":<6}')
print('   ' + '-'*65)

for trade in d.get('crowded_trades',[]):
    oi=trade.get('oi_usd',0)
    fund=trade.get('funding_annual_pct',0)
    if oi < 500000 and abs(fund) > 30:
        print(f'   {trade[\"asset\"]:<16} {fund:>9.1f}%/yr  \${oi:>9,.0f}  {trade.get(\"signal\",\"?\"):<16} {trade.get(\"confidence\",\"?\"):<6}')
" 2>/dev/null
fi

# ── SUMMARY ─────────────────────────────────────────────────────────
if [ "$MODE" = "all" ]; then
    echo ""
    echo "═══════════════════════════════════════════════════════════════════"
    
    OPPORTUNITIES=$(curl -s -m 60 -X POST "$BASE/scan-hl-opportunities/run" \
      -H "Content-Type: application/json" \
      -d '{"min_annualized":30}')
    
    echo "$OPPORTUNITIES" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)

s=d.get('summary',{})
print(f'  SCANNED: {s.get(\"total_scanned\",0)} assets')
print(f'  LONG OPPORTUNITIES:  {s.get(\"long_opportunities\",0)}')
print(f'  SHORT OPPORTUNITIES: {s.get(\"short_opportunities\",0)}')
print(f'  WATCHLIST:           {s.get(\"watchlist\",0)}')
print()

for setup in d.get('short_setups',[]):
    print(f'  🔴 SHORT: {setup[\"asset\"]:<12} {setup[\"funding_annualized_pct\"]:.0f}%/yr  score={setup[\"score\"]}  conf={setup[\"confidence\"]}')
    print(f'     Entry: funding extreme | Target: funding normalization')
    print(f'     Invalidation: funding flips negative | Timeout: 8h')

for setup in d.get('long_setups',[]):
    print(f'  🟢 LONG: {setup[\"asset\"]:<12} {setup[\"funding_annualized_pct\"]:.0f}%/yr  score={setup[\"score\"]}  conf={setup[\"confidence\"]}')
    print(f'     Entry: funding extreme negative | Target: funding normalization')
    print(f'     Invalidation: funding flips positive | Timeout: 8h')
" 2>/dev/null

    echo "═══════════════════════════════════════════════════════════════════"
fi
