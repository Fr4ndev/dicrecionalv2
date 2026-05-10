#!/bin/bash
# ============================================================================
# strategy_absorption.sh — Institutional Absorption Detection
# ============================================================================
# Detects hidden accumulation/distribution when institutions absorb
# aggressive flow without moving price (Kyle's Lambda / Amihud).
#
# ABSORPTION = institutions buying into sell walls OR selling into buy walls
# This is what the 0.1% do: they don't chase price, they absorb it silently.
#
# Usage:  bash strategy_absorption.sh [ASSET]
# ============================================================================

ASSET="${1:-BTC}"
BASE="http://localhost:8080/api/actions/funding-action-server"
SYMBOL="${ASSET}/USDT:USDT"
SPOT="${ASSET}/USDT"

echo "═══════════════════════════════════════════════════════════════════"
echo "  INSTITUTIONAL ABSORPTION DETECTION — $ASSET"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "═══════════════════════════════════════════════════════════════════"
echo ""

# ── Phase 1: VPIN + Absorption Rate ─────────────────────────────────
echo "[PHASE 1/4] VPIN + Absorption scan..."
VPIN=$(curl -s -m 30 -X POST "$BASE/get-toxicity-index/run" \
  -H "Content-Type: application/json" \
  -d "{\"symbol\":\"$SYMBOL\",\"ob_depth\":100,\"trade_limit\":500}" 2>/dev/null)

VPIN_VAL=$(echo "$VPIN" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)
v=float(d.get('toxicity_index',d.get('vpin_index',0))or 0)
a=float(d.get('absorption_rate',0))or 0
print(f'{v:.4f}|{a:.4f}')
" 2>/dev/null)

VPIN_NUM=$(echo "$VPIN_VAL" | cut -d'|' -f1)
ABS_RATE=$(echo "$VPIN_VAL" | cut -d'|' -f2)

echo "   VPIN: $VPIN_NUM  |  Absorption Rate: $ABS_RATE"

# ── Phase 2: CVD vs OBI divergence ──────────────────────────────────
echo ""
echo "[PHASE 2/4] CVD vs OBI divergence..."
CVD=$(curl -s -m 30 -X POST "$BASE/get-cvd-divergence/run" \
  -H "Content-Type: application/json" \
  -d "{\"symbol\":\"$SYMBOL\"}" 2>/dev/null)

echo "$CVD" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)
print(json.dumps(d, indent=2)[:400] if isinstance(d,dict) else str(d)[:200])
" 2>/dev/null || echo "   (loading...)"

# ── Phase 3: Microstructure Audit ───────────────────────────────────
echo ""
echo "[PHASE 3/4] Full microstructure audit..."
AUDIT=$(curl -s -m 60 -X POST "$BASE/microstructure-audit/run" \
  -H "Content-Type: application/json" \
  -d "{\"symbol\":\"$SYMBOL\"}")

echo "$AUDIT" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)

# Extract key microstructure metrics
if isinstance(d,dict):
    micro=d.get('microstructure',d)
    if isinstance(micro,dict):
        obi=float(micro.get('obi',0)or 0)
        cvd=float(micro.get('cvd',micro.get('cvd_acceleration',0))or 0)
        basis=float(micro.get('basis_pct',0)or 0)
        print(f'   OBI: {obi:.4f}  |  CVD: {cvd:.4f}  |  Basis: {basis:.4f}%')
        
        # Absorption detection logic
        if abs(obi) < 0.3 and abs(cvd) > 0.5:
            print(f'   ⚠️  ABSORPTION DETECTED: CVD moving but OBI flat')
            print(f'   → Institutions absorbing flow without moving book')
            if cvd > 0 and obi < 0.1:
                print(f'   🎯 STEALTH ACCUMULATION: CVD bullish, OBI suppressed')
                print(f'   → Institutions buying into passive walls')
            elif cvd < 0 and obi > -0.1:
                print(f'   🎯 STEALTH DISTRIBUTION: CVD bearish, OBI suppressed')
                print(f'   → Institutions selling into passive walls')
        elif obi > 0.6:
            print(f'   ⚡ BID AGGRESSION: Heavy bid pressure — possible breakout')
        elif obi < -0.6:
            print(f'   ⚡ ASK AGGRESSION: Heavy ask pressure — possible breakdown')
" 2>/dev/null || echo "   (loading...)"

# ── Phase 4: Basis + OBI + CVD confluence ───────────────────────────
echo ""
echo "[PHASE 4/4] Absorption score (multi-factor)..."
BASIS=$(curl -s -m 30 -X POST "$BASE/get-basis/run" \
  -H "Content-Type: application/json" \
  -d "{\"symbol_spot\":\"$SPOT\",\"symbol_perp\":\"$SYMBOL\"}")

BASIS_PCT=$(echo "$BASIS" | python3 -c "
import sys,json
d=json.load(sys.stdin)
if isinstance(d,str): d=json.loads(d)
print(round(float(d.get('basis_pct',0)),4))
" 2>/dev/null)

echo "   Basis: ${BASIS_PCT}%"

# ── ABSORPTION SCORE ────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo "  ABSORPTION SCORE:"
echo ""

python3 -c "
vpin=float('$VPIN_NUM' or 0)
abs_rate=float('$ABS_RATE' or 0)
basis=float('$BASIS_PCT' or 0)

score=0
signals=[]

# VPIN component (informed flow)
if vpin > 0.62:
    score+=30
    signals.append('VPIN={:.3f} → INFORMED (+30)'.format(vpin))
elif vpin > 0.40:
    score+=15
    signals.append('VPIN={:.3f} → ELEVATED (+15)'.format(vpin))
else:
    signals.append('VPIN={:.3f} → CLEAN (0)'.format(vpin))

# Absorption rate component
if abs(abs_rate) > 0.60:
    score+=25
    signals.append('Absorption={:.3f} → HIGH (+25)'.format(abs_rate))
elif abs(abs_rate) > 0.30:
    score+=10
    signals.append('Absorption={:.3f} → MODERATE (+10)'.format(abs_rate))
else:
    signals.append('Absorption={:.3f} → LOW (0)'.format(abs_rate))

# Basis component (spot premium = accumulation)
if basis < -0.05:
    score+=20
    signals.append('Basis={:.3f}% → SPOT PREMIUM (+20)'.format(basis))
elif basis > 0.05:
    score+=10
    signals.append('Basis={:.3f}% → PERP PREMIUM (+10)'.format(basis))
else:
    score+=5
    signals.append('Basis={:.3f}% → NEUTRAL (+5)'.format(basis))

# Verdict
if score >= 60:
    verdict='ACCUMULATION_DETECTED — institutional absorption in progress'
    emoji='🟢'
elif score >= 40:
    verdict='ELEVATED_ABSORPTION — monitor for confirmation'
    emoji='🟡'
else:
    verdict='NO_ABSORPTION — retail-driven flow'
    emoji='🔴'

print(f'  {emoji} SCORE: {score}/75 — {verdict}')
for s in signals:
    print(f'     {s}')
print()
print(f'  Entry: When score ≥ 60 AND OBI confirms direction')
print(f'  Target: Opposite liquidity cluster')
print(f'  Invalidation: VPIN drops < 0.40 OR absorption rate flips')
"

echo "═══════════════════════════════════════════════════════════════════"
