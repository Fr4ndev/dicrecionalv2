"""
shared/redis_bridge.py — Standalone Redis Singleton with JSON Failover
========================================================================
Extracted from ccxtv2 core/redis_bridge.py. Zero dependencies on project.
Thread-safe singleton. Auto-failover to JSON when Redis is unavailable.

Usage:
    from shared.redis_bridge import RedisBridge

    bridge = RedisBridge.instance()        # Auto-connect, no crash if Redis down
    bridge = RedisBridge.require()         # Raises RedisUnavailableError if mandatory

    # Wall velocity (anti-spoofing)
    bridge.set_wall_state("BTC/USDT:USDT", price, size, ticker_price)
    prev = bridge.get_prev_state("BTC/USDT:USDT")

    # CVD velocity persistence (cross-request)
    prev_vel = bridge.get_cvd_velocity("cvd:btc")
    bridge.set_cvd_velocity("cvd:btc", velocity, ttl=3600)

    # Health check
    RedisBridge.health()  # → {"redis": "ONLINE", "latency_ms": 1.2, "failover_active": false}
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

import redis as redis_lib

logger = logging.getLogger("RedisBridge")

# Default failover path — relative to project or overridable via env
_DEFAULT_FAILOVER = os.environ.get(
    "REDIS_FAILOVER_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "logs", "redis_failover.json")
)


class RedisUnavailableError(Exception):
    """Raised when Redis is required but unreachable."""


class RedisBridge:
    """
    Singleton Redis manager with JSON failover.

    - Connects on first use (lazy)
    - Falls back to JSON file if Redis is unreachable
    - Thread-safe singleton via double-checked locking
    - All public methods handle failover transparently
    """

    _instance: Optional["RedisBridge"] = None
    _lock = threading.Lock()

    # ── Lifecycle ──────────────────────────────────────────────────

    def __init__(self, failover_path: str = ""):
        self.r: Optional[redis_lib.Redis] = None
        self.connected: bool = False
        self.use_failover: bool = False
        self.failover_path: str = failover_path or _DEFAULT_FAILOVER
        self._failover_data: Dict[str, Any] = {}
        self._failover_lock = threading.Lock()
        self._connect()

    @classmethod
    def instance(cls) -> "RedisBridge":
        """Get the singleton. Auto-connects on first call. Never raises."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def require(cls, timeout_seconds: int = 3) -> "RedisBridge":
        """Get singleton OR raise if Redis is mandatory and unreachable."""
        bridge = cls.instance()
        if not bridge.connected:
            bridge._connect(timeout=timeout_seconds)
        if not bridge.connected:
            raise RedisUnavailableError(
                "Redis REQUIRED but unreachable at localhost:6379. "
                "Start: sudo service redis-server start"
            )
        return bridge

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (testing only)."""
        with cls._lock:
            cls._instance = None

    @classmethod
    def health(cls) -> Dict[str, Any]:
        """Fast health check — safe to call at any time."""
        bridge = cls.instance()
        latency_ms = None
        if bridge.connected and bridge.r:
            try:
                start = time.monotonic()
                bridge.r.ping()
                latency_ms = round((time.monotonic() - start) * 1000, 2)
            except Exception:
                bridge.connected = False
                bridge.use_failover = True
        return {
            "redis": "ONLINE" if bridge.connected else "OFFLINE",
            "latency_ms": latency_ms,
            "failover_active": bridge.use_failover,
            "failover_path": bridge.failover_path if bridge.use_failover else None,
        }

    # ── Connection ─────────────────────────────────────────────────

    def _connect(self, timeout: int = 3) -> None:
        try:
            os.makedirs(os.path.dirname(self.failover_path), exist_ok=True)
            self.r = redis_lib.Redis(
                host="localhost", port=6379, db=0,
                decode_responses=True,
                socket_timeout=timeout,
                socket_connect_timeout=timeout,
            )
            self.r.ping()
            self.connected = True
            self.use_failover = False
            logger.info("Redis connected — bridge active")
        except Exception as e:
            self.connected = False
            self.use_failover = True
            logger.warning(
                f"Redis unavailable ({e}) — JSON failover active. "
                "Wall velocity and CVD persistence degraded."
            )
            self._init_failover()

    # ── Failover (JSON persistence when Redis is down) ────────────

    def _init_failover(self) -> None:
        """Load or create failover JSON."""
        with self._failover_lock:
            if not os.path.exists(self.failover_path):
                os.makedirs(os.path.dirname(self.failover_path), exist_ok=True)
                with open(self.failover_path, "w") as f:
                    json.dump({}, f)
            try:
                with open(self.failover_path) as f:
                    self._failover_data = json.load(f)
            except (json.JSONDecodeError, IOError):
                self._failover_data = {}

    def _read_failover(self) -> Dict:
        with self._failover_lock:
            try:
                with open(self.failover_path) as f:
                    return json.load(f)
            except Exception:
                return {}

    def _write_failover(self, data: Dict) -> None:
        with self._failover_lock:
            try:
                with open(self.failover_path, "w") as f:
                    json.dump(data, f)
            except Exception as e:
                logger.error(f"Failed to write failover JSON: {e}")

    # ── Wall State API (anti-spoofing velocity) ────────────────────

    def set_wall_state(self, symbol: str, price: float, size: float,
                       ticker_price: float) -> None:
        """Persist wall snapshot for velocity calculation."""
        clean = symbol.replace("/", "").replace(":", "").lower()
        state = {"t": time.time(), "p": price, "s": size, "tp": ticker_price}

        if self.use_failover:
            data = self._read_failover()
            if clean not in data:
                data[clean] = []
            data[clean].append(state)
            data[clean] = data[clean][-10:]  # Keep last 10 snapshots
            self._write_failover(data)
            return

        key_hist = f"prod:{clean}:wall:v_vec"
        self.r.hset(f"prod:{clean}:wall:latest", mapping=state)
        self.r.zadd(key_hist, {json.dumps(state): state["t"]})
        self.r.expire(key_hist, 3600)

    def get_prev_state(self, symbol: str) -> Optional[Dict]:
        """Get previous wall snapshot for velocity comparison."""
        clean = symbol.replace("/", "").replace(":", "").lower()

        if self.use_failover:
            data = self._read_failover()
            history = data.get(clean, [])
            return history[-2] if len(history) >= 2 else None

        key_hist = f"prod:{clean}:wall:v_vec"
        history = self.r.zrange(key_hist, -2, -2)
        return json.loads(history[0]) if history else None

    def get_wall_velocity(self, symbol: str) -> Optional[Dict]:
        """Get latest wall state for velocity calculation."""
        clean = symbol.replace("/", "").replace(":", "").lower()

        if self.use_failover:
            data = self._read_failover()
            history = data.get(clean, [])
            return history[-1] if history else None

        try:
            return self.r.hgetall(f"prod:{clean}:wall:latest")
        except Exception:
            return None

    # ── CVD Velocity API (cross-request persistence) ───────────────

    def get_cvd_velocity(self, key: str) -> Optional[float]:
        """Get stored CVD velocity for acceleration calculation."""
        if self.use_failover:
            return None  # No failover for CVD — requires real persistence

        try:
            prev = self.r.get(key)
            return float(prev) if prev else None
        except Exception:
            return None

    def set_cvd_velocity(self, key: str, velocity: float, ttl: int = 3600) -> None:
        """Store CVD velocity with TTL (1h default)."""
        if self.use_failover:
            return  # Silently skip — CVD persistence needs Redis

        try:
            self.r.set(key, velocity, ex=ttl)
        except Exception as e:
            logger.debug(f"Failed to set CVD velocity '{key}': {e}")

    # ── Generic Cache API ──────────────────────────────────────────

    def get(self, key: str) -> Optional[str]:
        """Generic key-value get."""
        if self.use_failover:
            data = self._read_failover()
            return data.get(key)
        try:
            return self.r.get(key)
        except Exception:
            return None

    def set(self, key: str, value: str, ttl: int = 3600) -> None:
        """Generic key-value set with TTL."""
        if self.use_failover:
            data = self._read_failover()
            data[key] = value
            self._write_failover(data)
            return
        try:
            self.r.set(key, value, ex=ttl)
        except Exception as e:
            logger.debug(f"Failed to set '{key}': {e}")

    def delete(self, key: str) -> None:
        """Delete a key."""
        if self.use_failover:
            data = self._read_failover()
            data.pop(key, None)
            self._write_failover(data)
            return
        try:
            self.r.delete(key)
        except Exception:
            pass

    def exists(self, key: str) -> bool:
        """Check if key exists."""
        if self.use_failover:
            data = self._read_failover()
            return key in data
        try:
            return bool(self.r.exists(key))
        except Exception:
            return False


# ═══════════════════════════════════════════════════════════════════
# FAST ACCESSORS (for direct use in action files)
# ═══════════════════════════════════════════════════════════════════

def redis() -> RedisBridge:
    """Shortcut: get RedisBridge singleton."""
    return RedisBridge.instance()


def redis_require() -> RedisBridge:
    """Shortcut: get RedisBridge or raise."""
    return RedisBridge.require()
