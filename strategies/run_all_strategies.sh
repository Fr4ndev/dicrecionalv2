#!/bin/bash
# ============================================================================
# run_all_strategies.sh — Master Strategy Runner
# ============================================================================
# Executes all institutional trading strategies and produces a unified
# daily report. Run as cron job or manually before trading sessions.
#
# Usage:  bash run_all_strategies.sh [ASSET]
#         bash run_all_strategies.sh BTC
#         bash run_all_strategies.sh ETH
#
# Cron:   0 */4 * * * /path/to/run_all_strategies.sh BTC >> logs/strategies.log
# ============================================================================

ASSET="${1:-BTC}"
DIR="$(cd "$(dirname "$0")" && pwd)"
REPORT="$DIR/../logs/strategy_report_$(date -u +%Y%m%d_%H%M).md"

echo "═══════════════════════════════════════════════════════════════════"
echo "  AI OPS MASTER SUITE — STRATEGY RUNNER"
echo "  Asset: $ASSET | $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "═══════════════════════════════════════════════════════════════════"

# 1. SCALP
echo ""
echo "━━━ 1/5 SCALP ━━━"
bash "$DIR/strategy_scalp.sh" "$ASSET" 2>/dev/null

# 2. SFP
echo ""
echo "━━━ 2/5 SFP ━━━"
bash "$DIR/strategy_sfp.sh" "$ASSET" 2>/dev/null

# 3. SWEEP / LIQUIDATION
echo ""
echo "━━━ 3/5 SWEEP & LIQUIDATION ━━━"
bash "$DIR/strategy_sweep_liq.sh" "$ASSET" 2>/dev/null

# 4. ABSORPTION
echo ""
echo "━━━ 4/5 ABSORPTION ━━━"
bash "$DIR/strategy_absorption.sh" "$ASSET" 2>/dev/null

# 5. HYPERLIQUID ALPHA
echo ""
echo "━━━ 5/5 HYPERLIQUID ALPHA ━━━"
bash "$DIR/strategy_hyperliquid_alpha.sh" "all" 2>/dev/null

echo ""
echo "═══════════════════════════════════════════════════════════════════"
echo "  ALL STRATEGIES COMPLETE"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "═══════════════════════════════════════════════════════════════════"
