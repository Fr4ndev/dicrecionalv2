#!/bin/bash
# ============================================================================
# strategy_sweep_liq.sh — Liquidation Sweep & Cascade Confirmation
# ============================================================================
# Detects liquidation cascades and sweep events by monitoring:
#   - Liquidation levels (where stops cluster)
#   - Wall velocity (how fast walls are moving → spoof detection)
#   - Delta acceleration (order flow aggression)
#   - Toxicity index (VPIN — informed vs noise flow)
#
# Usage:  bash strategy_sweep_liq.sh [ASSET]
# ============================================================================

ASSET="${1:-BTC}"
BASE="http://localhost:8080/api/actions/funding-action-server"
SYMBOL="${ASSET}/USDT:USDT"

echo "═══════════════════════════════════════════════════════════════════"
echo "  LIQUIDATION SWEEP & CASCADE CONFIRMATION — $ASSET"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "═══════════════════════════════════════════════════════════════════"
echo ""

# ── Phase 1: Toxicity Index (VPIN) ──────────────────────────────────
echo "[PHASE 1/6] Toxicity/VPIN — informed flow detection..."
VPIN=$(curl -s -m 30 -X POST "$BASE/get-toxicity-index/run" \
  -H "Content-Type: application/json" \
  -d "{\"symbol\":\"$SYMBOL\",\"ob_depth\":50,\"trade_limit\":500}" 2>/dev/null)

echo "$VPIN" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str):
    try: d=json.loads(d)
    except: pass
if isinstance(d,dict):
    v=round(float(d.get('toxicity_index',d.get('vpin_index',0))or 0),4)
    a=round(float(d.get('absorption_rate',0))or 0,4)
    print(f'   VPIN: {v}  |  Absorption Rate: {a}')
    print(f'   Gate: {\"✅ INFORMED (>0.62)\" if v>0.62 else \"❌ RETAIL (<0.62)\"}')
" 2>/dev/null || echo "   (endpoint loading...)"

# ── Phase 2: Wall Velocity (anti-spoofing) ──────────────────────────
echo ""
echo "[PHASE 2/6] Wall velocity — spoof detection..."
WALL_VEL=$(curl -s -m 30 -X POST "$BASE/get-wall-velocity/run" \
  -H "Content-Type: application/json" \
  -d "{\"symbol\":\"$SYMBOL\",\"min_size\":1.0}" 2>/dev/null)

echo "$WALL_VEL" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)
print(json.dumps(d, indent=2)[:300] if isinstance(d,dict) else str(d)[:200])
" 2>/dev/null || echo "   (endpoint loading...)"

# ── Phase 3: Delta Acceleration ─────────────────────────────────────
echo ""
echo "[PHASE 3/6] Delta acceleration (order flow aggression)..."
DELTA=$(curl -s -m 30 -X POST "$BASE/get-delta-acceleration/run" \
  -H "Content-Type: application/json" \
  -d "{\"symbol\":\"$SYMBOL\",\"window_trades\":200}" 2>/dev/null)

echo "$DELTA" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)
print(json.dumps(d, indent=2)[:300] if isinstance(d,dict) else str(d)[:200])
" 2>/dev/null || echo "   (endpoint loading...)"

# ── Phase 4: Liquidation Monitor ────────────────────────────────────
echo ""
echo "[PHASE 4/6] Liquidation levels..."
LIQ=$(curl -s -m 30 -X POST "$BASE/get-liquidation-monitor/run" \
  -H "Content-Type: application/json" \
  -d "{\"symbol\":\"$SYMBOL\",\"threshold_usd\":1000000}" 2>/dev/null)

echo "$LIQ" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)
print(json.dumps(d, indent=2)[:400] if isinstance(d,dict) else str(d)[:200])
" 2>/dev/null || echo "   (endpoint loading...)"

# ── Phase 5: CVD Divergence (trap detection) ────────────────────────
echo ""
echo "[PHASE 5/6] CVD divergence — trap score..."
TRAP=$(curl -s -m 30 -X POST "$BASE/get-trap-score/run" \
  -H "Content-Type: application/json" \
  -d "{\"symbol\":\"$SYMBOL\"}" 2>/dev/null)

echo "$TRAP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)
print(json.dumps(d, indent=2)[:300] if isinstance(d,dict) else str(d)[:200])
" 2>/dev/null || echo "   (endpoint loading...)"

# ── Phase 6: Order Flow ─────────────────────────────────────────────
echo ""
echo "[PHASE 6/6] Order book sweep detection..."
OBI=$(curl -s -m 30 -X POST "$BASE/get-orderbook-imbalance/run" \
  -H "Content-Type: application/json" \
  -d "{\"assets\":\"$ASSET\",\"depth\":100}" 2>/dev/null)

echo "$OBI" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)
detail=d.get('detail',{})
for k,v in detail.items():
    if isinstance(v,dict):
        o=float(v.get('obi',v.get('imbalance_score',0)) or 0)
        if isinstance(o,str): o=0.5 if 'BID' in o else (-0.5 if 'ASK' in o else 0)
        d_val=float(v.get('bid_depth',0) or 0)
        a_val=float(v.get('ask_depth',0) or 0)
        sweep_ratio = a_val/(d_val+0.0001)
        print(f'   OBI: {o:.4f}  |  Bid depth: {d_val:.0f}  |  Ask depth: {a_val:.0f}')
        if sweep_ratio > 3:
            print(f'   ⚡ SWEEP DETECTED: Ask depth {sweep_ratio:.1f}x vs bid — BEARISH SWEEP')
        elif sweep_ratio < 0.33:
            print(f'   ⚡ SWEEP DETECTED: Bid depth {1/sweep_ratio:.1f}x vs ask — BULLISH SWEEP')
        break
" 2>/dev/null || echo "   (endpoint loading...)"

# ── DECISION ─────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo "  SWEEP / LIQUIDATION ASSESSMENT:"
echo ""
echo "  🎯 LONGS AT:  Bid wall clusters + sweep confirmations"
echo "  🎯 SHORTS AT: Ask wall clusters + cascade confirmations"
echo "  🛑 INVALIDATED IF: VPIN < 0.62 (no informed flow)"
echo "                     Wall velocity negative (spoof detected)"
echo "                     CVD divergence flips"
echo "═══════════════════════════════════════════════════════════════════"
