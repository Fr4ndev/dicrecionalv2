#!/bin/bash
# ============================================================================
# strategy_scalp.sh — Institutional Scalp Routine (1-15 min trades)
# ============================================================================
# Chains funding server endpoints into a complete scalp decision pipeline.
# 
# Usage:  bash strategy_scalp.sh [ASSET]
#         bash strategy_scalp.sh BTC
#         bash strategy_scalp.sh ETH
#
# Output: Entry/Target/Invalidation or NO_TRADE with reasons.
# ============================================================================

ASSET="${1:-BTC}"
BASE="http://localhost:8080/api/actions/funding-action-server"
SYMBOL="${ASSET}/USDT:USDT"

echo "═══════════════════════════════════════════════════════════════════"
echo "  INSTITUTIONAL SCALP ROUTINE — $ASSET ($SYMBOL)"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "═══════════════════════════════════════════════════════════════════"
echo ""

# ── Phase 1: Market Snapshot (OBI + Funding + OI) ────────────────────
echo "[PHASE 1/5] Full market snapshot..."
SNAP=$(curl -s -m 30 -X POST "$BASE/get-full-market-snapshot/run" \
  -H "Content-Type: application/json" \
  -d "{\"assets\":\"$ASSET\",\"ob_depth\":50}")

OBI=$(echo "$SNAP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)
triggers=d.get('triggers',{})
max_obi=0.0
for k,v in triggers.items():
    if isinstance(v,dict):
        o=float(v.get('max_obi',0)or 0); o=abs(o)
        if o>abs(max_obi): max_obi=float(v.get('max_obi',0)or 0)
print(round(max_obi,4))
" 2>/dev/null)

echo "   OBI: $OBI"

# ── Phase 2: Funding Rates ──────────────────────────────────────────
echo "[PHASE 2/5] Funding rates multi-exchange..."
FUND=$(curl -s -m 30 -X POST "$BASE/get-funding-rates-table/run" \
  -H "Content-Type: application/json" \
  -d "{\"assets\":\"$ASSET\"}")

FUND_MAX=$(echo "$FUND" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)
mx=0.0; mn=0.0
for row in d.get('table',[]):
    for k,v in row.items():
        if k!='exchange' and isinstance(v,(int,float)):
            mx=max(mx,v); mn=min(mn,v)
print(f'{round(mx,4)},{round(mn,4)}')
" 2>/dev/null)

FUND_HI=$(echo "$FUND_MAX" | cut -d',' -f1)
FUND_LO=$(echo "$FUND_MAX" | cut -d',' -f2)
echo "   Funding: hi=$FUND_HI% lo=$FUND_LO%"

# ── Phase 3: Confluence Trigger ─────────────────────────────────────
echo "[PHASE 3/5] Confluence trigger evaluation..."
TRIGGER=$(curl -s -m 30 -X POST "$BASE/detect-confluence-trigger/run" \
  -H "Content-Type: application/json" \
  -d "{\"assets\":\"$ASSET\",\"ob_depth\":50}")

LEVEL=$(echo "$TRIGGER" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)
print(d.get('trigger_level','NONE'))
" 2>/dev/null)

echo "   Trigger: $LEVEL"

# ── Phase 4: Order Book Walls ───────────────────────────────────────
echo "[PHASE 4/5] Order book walls..."
WALLS=$(curl -s -m 30 -X POST "$BASE/get-ob-walls/run" \
  -H "Content-Type: application/json" \
  -d "{\"symbol\":\"$SYMBOL\",\"depth\":20}")

WALL_DATA=$(echo "$WALLS" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)
bids=d.get('bids',[])[:3] if d.get('bids') else []
asks=d.get('asks',[])[:3] if d.get('asks') else []
bid_wall='none'; ask_wall='none'
if bids:
    b=bids[0]
    if isinstance(b,list): bid_wall=f'{b[0]:.1f} ({b[1]:.1f})'
if asks:
    a=asks[0]
    if isinstance(a,list): ask_wall=f'{a[0]:.1f} ({a[1]:.1f})'
print(f'{bid_wall}|{ask_wall}')
" 2>/dev/null)

BID_WALL=$(echo "$WALL_DATA" | cut -d'|' -f1)
ASK_WALL=$(echo "$WALL_DATA" | cut -d'|' -f2)
echo "   Nearest Bid: $BID_WALL | Nearest Ask: $ASK_WALL"

# ── Phase 5: CVD Divergence ─────────────────────────────────────────
echo "[PHASE 5/5] CVD divergence..."
CVD=$(curl -s -m 30 -X POST "$BASE/get-cvd-divergence/run" \
  -H "Content-Type: application/json" \
  -d "{\"symbol\":\"$SYMBOL\"}")
echo "   CVD: $(echo "$CVD" | head -c 100)"

# ── DECISION ─────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════════"

OBI_ABS=$(echo "$OBI" | awk '{print ($1<0 ? -$1 : $1)}')
FUND_HI_ABS=$(echo "$FUND_HI" | awk '{print ($1<0 ? -$1 : $1)}')
FUND_LO_ABS=$(echo "$FUND_LO" | awk '{print ($1<0 ? -$1 : $1)}')

GATE_OBI=0
GATE_CONFLUENCE=0
GATE_FUNDING=0

python3 -c "exit(0 if abs(float('$OBI')) > 0.40 else 1)" 2>/dev/null && GATE_OBI=1
[ "$LEVEL" = "SENSITIVE" ] || [ "$LEVEL" = "CONSERVATIVE" ] && GATE_CONFLUENCE=1
python3 -c "exit(0 if float('$FUND_HI_ABS') > 0.05 or float('$FUND_LO_ABS') > 0.05 else 1)" 2>/dev/null && GATE_FUNDING=1

echo "  OBI Gate (>0.40):  $([ $GATE_OBI -eq 1 ] && echo '✅' || echo '❌')  $OBI"
echo "  Confluence Gate:   $([ $GATE_CONFLUENCE -eq 1 ] && echo '✅' || echo '❌')  $LEVEL"
echo "  Funding Gate:      $([ $GATE_FUNDING -eq 1 ] && echo '✅' || echo '❌')  hi=$FUND_HI% lo=$FUND_LO%"

if [ $GATE_OBI -eq 1 ] && [ $GATE_CONFLUENCE -eq 1 ] && [ $GATE_FUNDING -eq 1 ]; then
    BIAS="LONG"
    python3 -c "exit(0 if float('$OBI') > 0 else 1)" || BIAS="SHORT"
    
    TARGET="$ASK_WALL"
    [ "$BIAS" = "SHORT" ] && TARGET="$BID_WALL"
    
    echo ""
    echo "  🟢 VERDICT: EXECUTE $BIAS"
    echo "  🎯 TARGET:  $TARGET"
    echo "  🛑 INVALIDATION: OBI flip ${OBI_ABS} → -0.20 | Timeout 15min | Max loss -0.5%"
else
    echo ""
    echo "  🔴 VERDICT: NO_TRADE"
    [ $GATE_OBI -eq 0 ] && echo "     → OBI insufficient ($OBI)"
    [ $GATE_CONFLUENCE -eq 0 ] && echo "     → Confluence gate failed ($LEVEL)"
    [ $GATE_FUNDING -eq 0 ] && echo "     → No extreme funding (hi=$FUND_HI% lo=$FUND_LO%)"
fi

echo "═══════════════════════════════════════════════════════════════════"
