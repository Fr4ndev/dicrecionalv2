"""
shadow_actions.py — Live Stats & Shadow Tester Telemetry Endpoints
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Exposes ShadowTester data as ActionServer endpoints consumed by
the Glass Room institutional dashboard.

Endpoints (autodiscovered by sema4ai ActionServer):
  get_shadow_stats            # Contingency table + p-value
  get_veto_log                # Veto verdict cascade
  submit_shadow_signal        # Submit discretionary signal
  close_shadow_track          # Close track with exit price
  get_active_shadow_signals   # Currently tracked positions
  get_guardian_health         # Guardian cooldown + drawdown
  force_update_shadow_stats   # Manual p-value recompute
"""
import sys
import json
import os
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.shadow_tester import (
    get_shadow_tester_sync,
    LIVE_STATS_PATH,
    SHADOW_LOG_PATH,
)
from shared.veto_system import MLVetoSystem
from shared.hub_reader import get_live_metrics_sync, LiveMetrics
from shared.execution_guardian import ExecutionGuardian
from sema4ai.actions import action

_ROOT = Path(__file__).resolve().parent.parent.parent  # ccxtv2-next/
_LOG_DIR = _ROOT / "logs"
os.makedirs(_LOG_DIR, exist_ok=True)
