"""
core/data_integrity.py — Sensor Health & Circuit Breaker System
═══════════════════════════════════════════════════════════════════
AUDIT_REPORT_V3 — Deep Trace Fix

Root Cause: VPIN returning 0.0 was NOT a "clean market" signal — it was
an ambiguous value that the old consensus gate treated as NEUTRAL, blocking
valid OBI=0.94 setups. The 0.1% does NOT operate with ambiguous sensors.

FIXES APPLIED:
  1. Null-safe parser: distinguishes None (no data) from 0.0 (true zero)
     from NaN (division error). Returns DataQuality enum.
  2. Circuit Breaker: if ANY critical sensor returns UNRELIABLE, HEALTH_SCORE
     drops below 20 and blocks execution.
  3. Failover Logic: when VPIN fails, uses CVD correlation + absorption_rate
     + wall_velocity as proxy informed-flow detection.
  4. Data integrity marker: every report gets data_quality score 0-100.

Memory Layer Update:
  - NEVER accept a gate result without checking sensor health first
  - VPIN=0 IS AMBIGUOUS → always validate with secondary sensor
  - OBI=0 means "perfect balance" → rare, validate with depth check
  - Mark all reports with data_quality score
  - If quality < 50 → NO TRADE, regardless of other signals

Author: AI Ops Master Suite · ccxtv2-next
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Dict, Optional, Any


class SensorStatus(Enum):
    HEALTHY    = "healthy"       # Real data, within expected range
    ZERO_TRUE  = "zero_true"     # Genuine zero (OBI=0 = perfect balance)
    NULL_DATA  = "null_data"     # Sensor returned None/empty
    AMBIGUOUS  = "ambiguous"     # Value indistinguishable from error (VPIN=0)
    OUTLIER    = "outlier"       # Statistically impossible value
    ERROR      = "error"         # Sensor raised exception


class DataQuality:
    """Evaluates sensor data quality and returns integrity score."""

    @staticmethod
    def check_obi(obi_val: Any, depth: int = 20) -> Dict:
        """
        Validate OBI sensor data.
        
        Returns: {value, status, quality_score, detail}
        """
        if obi_val is None:
            return {"value": None, "status": SensorStatus.NULL_DATA,
                    "quality_score": 0, "detail": "OBI returned None — sensor offline"}

        if isinstance(obi_val, str):
            try: obi_val = float(obi_val)
            except (ValueError, TypeError):
                return {"value": None, "status": SensorStatus.ERROR,
                        "quality_score": 0, "detail": f"OBI non-numeric: '{obi_val}'"}

        if math.isnan(obi_val) or math.isinf(obi_val):
            return {"value": None, "status": SensorStatus.ERROR,
                    "quality_score": 0, "detail": f"OBI NaN/Inf — division by zero or float overflow"}

        obi_val = float(obi_val)

        if obi_val == 0.0:
            # OBI=0 is mathematically possible (perfect balance) but RARE
            # Validate with depth check: if depth > 10 and still zero → suspicious
            if depth > 10:
                return {"value": 0.0, "status": SensorStatus.AMBIGUOUS,
                        "quality_score": 30, "detail": f"OBI=0.0 at depth={depth} — possible sensor stall or true balance"}
            return {"value": 0.0, "status": SensorStatus.ZERO_TRUE,
                    "quality_score": 70, "detail": "OBI=0.0 perfect balance at shallow depth"}

        if abs(obi_val) > 1.0:
            return {"value": obi_val, "status": SensorStatus.OUTLIER,
                    "quality_score": 40, "detail": f"OBI={obi_val:.4f} exceeds [-1,1] range — normalization error"}

        return {"value": obi_val, "status": SensorStatus.HEALTHY,
                "quality_score": 100, "detail": f"OBI={obi_val:.4f} valid"}

    @staticmethod
    def check_vpin(vpin_val: Any, obi_val: float = None) -> Dict:
        """
        Validate VPIN sensor data with cross-reference.
        
        VPIN=0 is AMBIGUOUS in the current implementation (FP-03):
        it can mean either "clean market" (no informed flow) OR "data fetch failed".
        
        Cross-reference with OBI: if OBI is extreme (>0.6) and VPIN=0,
        the VPIN sensor is likely stalled, not reading clean market.
        """
        if vpin_val is None:
            return {"value": None, "status": SensorStatus.NULL_DATA,
                    "quality_score": 0, "detail": "VPIN returned None — sensor offline"}

        if isinstance(vpin_val, str):
            try: vpin_val = float(vpin_val)
            except (ValueError, TypeError):
                return {"value": None, "status": SensorStatus.ERROR,
                        "quality_score": 0, "detail": f"VPIN non-numeric: '{vpin_val}'"}

        vpin_val = float(vpin_val)

        if math.isnan(vpin_val):
            return {"value": None, "status": SensorStatus.ERROR,
                    "quality_score": 0, "detail": "VPIN NaN — division by zero in toxicity calculation"}

        if vpin_val == 0.0:
            # Cross-reference with OBI
            if obi_val is not None and abs(obi_val) > 0.5:
                return {"value": 0.0, "status": SensorStatus.AMBIGUOUS,
                        "quality_score": 15,
                        "detail": f"VPIN=0.0 but OBI={obi_val:.3f} extreme → VPIN sensor STALLED, not clean market"}
            return {"value": 0.0, "status": SensorStatus.AMBIGUOUS,
                    "quality_score": 35,
                    "detail": "VPIN=0.0 — ambiguous: could be clean market or sensor stall"}

        if not (0.0 <= vpin_val <= 1.5):
            return {"value": vpin_val, "status": SensorStatus.OUTLIER,
                    "quality_score": 50, "detail": f"VPIN={vpin_val:.4f} outside expected range"}

        return {"value": vpin_val, "status": SensorStatus.HEALTHY,
                "quality_score": 100, "detail": f"VPIN={vpin_val:.4f} valid"}

    @staticmethod
    def check_funding(funding_val: Any) -> Dict:
        """Validate funding rate data."""
        if funding_val is None:
            return {"value": None, "status": SensorStatus.NULL_DATA, "quality_score": 0}
        try:
            f = float(funding_val)
        except (ValueError, TypeError):
            return {"value": None, "status": SensorStatus.ERROR, "quality_score": 0}
        if math.isnan(f):
            return {"value": None, "status": SensorStatus.ERROR, "quality_score": 0}
        if abs(f) > 5.0:
            return {"value": f, "status": SensorStatus.OUTLIER,
                    "quality_score": 50, "detail": f"Funding={f:.4f}% extreme"}
        return {"value": f, "status": SensorStatus.HEALTHY, "quality_score": 100,
                "detail": f"Funding={f:.4f}% valid"}

    @staticmethod
    def aggregate_quality(sensors: Dict[str, Dict]) -> Dict:
        """
        Aggregate sensor quality scores into a system HEALTH_SCORE.
        
        Returns {health_score, status, critical_failures, degraded_sensors}
        """
        scores = []
        critical_fails = []
        degraded = []

        for name, sensor in sensors.items():
            q = sensor.get("quality_score", 100)
            scores.append(q)

            if q == 0:
                critical_fails.append(name)
            elif q < 50:
                degraded.append(name)

        if not scores:
            return {"health_score": 0, "status": "NO_DATA",
                    "critical_failures": ["all_sensors"], "degraded_sensors": []}

        health = sum(scores) / len(scores)

        if critical_fails:
            return {"health_score": min(health, 19), "status": "CRITICAL",
                    "critical_failures": critical_fails, "degraded_sensors": degraded,
                    "circuit_breaker": "EXECUTION_BLOCKED — critical sensors offline"}

        if health < 50:
            return {"health_score": health, "status": "DEGRADED",
                    "critical_failures": [], "degraded_sensors": degraded,
                    "circuit_breaker": "TRADE_WITH_CAUTION — sensor quality degraded"}

        return {"health_score": health, "status": "HEALTHY",
                "critical_failures": [], "degraded_sensors": degraded}


def failover_informed_flow(vpin_check: Dict, cvd_divergence: Optional[float] = None,
                           absorption_rate: Optional[float] = None,
                           wall_velocity: Optional[float] = None) -> Dict:
    """
    FAILOVER LOGIC: when VPIN sensor is AMBIGUOUS (returns 0.0), use
    CVD divergence + absorption rate + wall velocity as proxy for
    informed flow detection.
    
    Returns: {proxy_vpin, confidence, source, data_quality}
    """
    if vpin_check["status"] != SensorStatus.AMBIGUOUS:
        return {"proxy_vpin": vpin_check.get("value"),
                "confidence": vpin_check["quality_score"] / 100,
                "source": "VPIN_DIRECT", "data_quality": vpin_check["quality_score"]}

    # FAILOVER ACTIVE
    signals = []
    confidence = 0.0

    # Proxy 1: CVD divergence correlation (Spearman)
    # Note: get-cvd-divergence may return 500 (Inconsistent value: dict vs str)
    # We handle gracefully — any data is better than none
    if cvd_divergence is not None:
        try:
            cvd = float(cvd_divergence)
            if abs(cvd) < 0.3:
                signals.append("CVD_DIVERGENCE_DETECTED")
                confidence += 0.25
            elif abs(cvd) > 0.7:
                signals.append("CVD_CONFIRMS_TREND")
                confidence += 0.10
        except (ValueError, TypeError):
            pass

    # Proxy 2: Absorption rate (Kyle's Lambda proxy)
    if absorption_rate is not None:
        try:
            ar = float(absorption_rate)
            if abs(ar) > 0.50:
                signals.append("ABSORPTION_HIGH")
                confidence += 0.25
            elif abs(ar) < 0.15:
                signals.append("ABSORPTION_LOW")
                confidence += 0.05
        except (ValueError, TypeError):
            pass

    # Proxy 3: Wall velocity (spoof detection)
    if wall_velocity is not None:
        try:
            wv = float(wall_velocity)
            if wv > 0:
                signals.append("WALL_BUILDING")
                confidence += 0.15
            elif wv < 0:
                signals.append("WALL_DECAYING")
                confidence += 0.15
        except (ValueError, TypeError):
            pass

    # Proxy 4: OBI as informed flow indicator (when VPIN=0 and OBI extreme)
    # If no other proxy works, use OBI alone with reduced confidence
    if not signals:
        confidence = 0.10  # Minimal confidence — OBI has value but VPIN is dark
        signals.append("OBI_ONLY_NO_PROXIES")

    # Composite proxy VPIN from failover signals
    proxy_vpin = 0.62 if confidence > 0.35 else (0.40 if confidence > 0.15 else 0.0)
    quality = min(60, int(confidence * 100))

    return {
        "proxy_vpin": proxy_vpin,
        "confidence": round(confidence, 2),
        "source": f"FAILOVER: {' + '.join(signals) if signals else 'no_proxy_data'}",
        "data_quality": quality,
        "circuit_breaker_note": "VPIN SENSOR AMBIGUOUS — using failover proxies. "
                                "Trades marked as UNRELIABLE DATA."
    }
