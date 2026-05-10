#!/bin/bash
# ============================================================================
# strategy_sfp.sh — Swing Failure Pattern (SFP) Institutional Detection
# ============================================================================
# Detects SFP: price sweeps a key level (PDH/PDL/4H high/low) and reverses.
# Institutional footprint — the "stop hunt" before the real move.
#
# Usage:  bash strategy_sfp.sh [ASSET]
# ============================================================================

ASSET="${1:-BTC}"
BASE="http://localhost:8080/api/actions/funding-action-server"
SYMBOL="${ASSET}/USDT:USDT"
SPOT="${ASSET}/USDT"

echo "═══════════════════════════════════════════════════════════════════"
echo "  SFP (SWING FAILURE PATTERN) DETECTION — $ASSET"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "═══════════════════════════════════════════════════════════════════"
echo ""

# ── Phase 1: SFP Confluence (multi-symbol) ──────────────────────────
echo "[PHASE 1/5] SFP confluence scan..."
SFP=$(curl -s -m 60 -X POST "$BASE/detect-sfp-confluence/run" \
  -H "Content-Type: application/json" \
  -d "{\"assets\":\"$ASSET\"}")

echo "$SFP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)
print(json.dumps(d, indent=2)[:500])
" 2>/dev/null || echo "$SFP" | head -c 300

# ── Phase 2: ETH ELE Audit (Liquidity Engine) ───────────────────────
echo ""
echo "[PHASE 2/5] ETH Liquidity Engine audit..."
ELE=$(curl -s -m 60 -X POST "$BASE/eth-ele-audit/run" \
  -H "Content-Type: application/json" \
  -d "{\"symbol\":\"ETH/USDT:USDT\"}")

TRANSITION=$(echo "$ELE" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)
print(d.get('transition_potential','?'))
" 2>/dev/null)

echo "   ETH Transition Potential: $TRANSITION"

# ── Phase 3: Order Book Walls (liquidity clusters) ──────────────────
echo ""
echo "[PHASE 3/5] Liquidity clusters..."
WALLS=$(curl -s -m 30 -X POST "$BASE/get-ob-walls/run" \
  -H "Content-Type: application/json" \
  -d "{\"symbol\":\"$SYMBOL\",\"depth\":50}")

echo "$WALLS" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)

# Find liquidity clusters (walls > 2x average size)
bids=d.get('bids',[]); asks=d.get('asks',[])
bid_avg=sum(b[1] for b in bids[:10])/10 if len(bids)>=10 else 0
ask_avg=sum(a[1] for a in asks[:10])/10 if len(asks)>=10 else 0

print(f'   Bid cluster (avg): {bid_avg:.1f}')
print(f'   Ask cluster (avg): {ask_avg:.1f}')

# Find walls > 3x avg (SFP targets)
for b in bids[:5]:
    if isinstance(b,list) and b[1] > bid_avg*3:
        print(f'   🎯 SFP TARGET (BID): {b[0]:.1f} size={b[1]:.1f} ({b[1]/bid_avg:.1f}x avg)')
for a in asks[:5]:
    if isinstance(a,list) and a[1] > ask_avg*3:
        print(f'   🎯 SFP TARGET (ASK): {a[0]:.1f} size={a[1]:.1f} ({a[1]/ask_avg:.1f}x avg)')
" 2>/dev/null

# ── Phase 4: Basis (spot vs perp — accumulation signal) ─────────────
echo ""
echo "[PHASE 4/5] Basis divergence..."
BASIS=$(curl -s -m 30 -X POST "$BASE/get-basis/run" \
  -H "Content-Type: application/json" \
  -d "{\"symbol_spot\":\"$SPOT\",\"symbol_perp\":\"$SYMBOL\"}")

BASIS_PCT=$(echo "$BASIS" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)
print(round(d.get('basis_pct',0),4))
" 2>/dev/null)

echo "   Basis: ${BASIS_PCT}% ($(python3 -c "print('spot premium = accumulation' if float('$BASIS_PCT') < -0.05 else 'perp premium = FOMO' if float('$BASIS_PCT') > 0.05 else 'neutral')"))"

# ── Phase 5: Z-Score regime ─────────────────────────────────────────
echo ""
echo "[PHASE 5/5] Z-Score regime..."
ZSCORE=$(curl -s -m 30 -X POST "$BASE/get-zscore-vs-history/run" \
  -H "Content-Type: application/json" \
  -d "{\"assets\":\"$ASSET\"}")

Z_REGIME=$(echo "$ZSCORE" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)
detail=d.get('detail',{})
for k,v in detail.items():
    if isinstance(v,dict):
        z=v.get('zscore',0) or 0
        r=v.get('regime','?')
        print(f'{r} (z={z:.2f})')
        break
" 2>/dev/null)

echo "   Regime: $Z_REGIME"

# ── DECISION ─────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo "  SFP SETUP ASSESSMENT:"
echo ""

# SFP is valid when:
# - Basis shows spot premium (accumulation) → bearish SFP (sweep high, go short)
# - Basis shows perp premium (FOMO) → bullish SFP (sweep low, go long)
# - Z-Score extreme confirms direction
# - Liquidity clusters exist at extreme levels

SFP_SIGNAL="NEUTRAL"
python3 -c "
bsp=float('$BASIS_PCT')
z=$Z_REGIME
if bsp < -0.05 and 'OVERHEATED' in str(z):
    print('SFP_SHORT')
elif bsp > 0.05 and 'MEAN_REVERT' in str(z):
    print('SFP_LONG')
elif 'OVERHEATED' in str(z):
    print('SFP_SHORT_POTENTIAL')
elif 'MEAN_REVERT' in str(z):
    print('SFP_LONG_POTENTIAL')
else:
    print('NO_SFP_SETUP')
" 2>/dev/null | while read signal; do
    case "$signal" in
        SFP_SHORT)
            echo "  🟢 SFP SIGNAL: SWEEP HIGH → SHORT"
            echo "     Entry: After sweep of 4H high + bearish OBI divergence"
            echo "     Target: Nearest liquidity cluster below"
            echo "     Invalidation: Price closes above swept level"
            ;;
        SFP_LONG)
            echo "  🟢 SFP SIGNAL: SWEEP LOW → LONG"
            echo "     Entry: After sweep of 4H low + bullish OBI divergence"
            echo "     Target: Nearest liquidity cluster above"
            echo "     Invalidation: Price closes below swept level"
            ;;
        SFP_SHORT_POTENTIAL)
            echo "  🟡 SFP POTENTIAL: Monitor for high sweep (regime overheated)"
            echo "     Wait for: OBI flip + price sweep above 4H high + rejection"
            ;;
        SFP_LONG_POTENTIAL)
            echo "  🟡 SFP POTENTIAL: Monitor for low sweep (regime mean-revert)"
            echo "     Wait for: OBI flip + price sweep below 4H low + rejection"
            ;;
        *)
            echo "  🔴 NO SFP SETUP: Regime neutral, no sweep conditions"
            echo "     Basis: ${BASIS_PCT}% | Regime: $Z_REGIME"
            ;;
    esac
done

echo "═══════════════════════════════════════════════════════════════════"
