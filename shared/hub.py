#!/usr/bin/env python3
"""
core/Core_Intelligence_Hub.py — Monolithic Intelligence Singleton v1.0
═══════════════════════════════════════════════════════════════════════════
Consolidates: DataEngine + ZScoreEngine + FundingFeesEngine + Market Analysis

ARCHITECTURE:
  - Single CCXT exchange connection per process (Singleton pattern)
  - Shared TTL cache layer — eliminates redundant API calls across daemons
  - ZScoreEngine: rolling funding rate z-scores (48h window)
  - FundingFeesEngine: normalized funding fee accumulation + annualized rates
  - PhD Analysis: toxicity/VPIN, OBI, CVD, basis, wall velocity (all private)
  - Public interface: clean, typed coroutines for Guardian_Daemon and action server

God Nodes absorbed (from graph.json):
  DataEngine (137 edges) · ZScoreEngine (51e) · FundingFeesEngine (59e)

GOLDEN RULES (DO NOT MODIFY THRESHOLDS):
  VPIN_THRESHOLD      = 0.62   # Informed flow gate
  BASIS_THRESHOLD_PCT = -0.05  # Spot premium / accumulation stealth
  CVD_ACCEL_GATE      = 0.0    # CVD'' > 0 = aggression confirmed
  OBI_IGNITION        = 0.40   # BTC/ETH coordinated ignition
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

import ccxt.async_support as ccxt
import numpy as np
import pandas as pd

# ── Project imports ───────────────────────────────────────────────
from shared.config import settings

logger = logging.getLogger("IntelligenceHub")

# ═══════════════════════════════════════════════════════════════════
# GOLDEN RULES — IMMUTABLE THRESHOLDS
# ═══════════════════════════════════════════════════════════════════
VPIN_THRESHOLD      = 0.62    # Informed flow — NO EXECUTION below this
BASIS_THRESHOLD_PCT = -0.05   # Spot premium: negative = accumulation stealth
CVD_ACCEL_GATE      = 0.0     # CVD'' must be positive to confirm aggression
OBI_IGNITION        = 0.40    # Coordinated BTC+ETH ignition threshold
OBI_SCALP_GATE      = 0.40    # Scalp OBI D20 gate
ABSORPTION_GATE     = 0.60    # Iceberg absorption rate gate


# ═══════════════════════════════════════════════════════════════════
# TYPED RESULT DATACLASSES
# ═══════════════════════════════════════════════════════════════════

@dataclass
class OBISnapshot:
    symbol:     str
    obi:        float     # [-1, +1]
    bid_depth:  float     # total bid qty
    ask_depth:  float     # total ask qty
    timestamp:  float     = field(default_factory=time.time)


@dataclass
class BasisSnapshot:
    symbol_spot:  str
    symbol_perp:  str
    spot_price:   float
    perp_price:   float
    basis_usd:    float
    basis_pct:    float   # negative = spot premium (accumulation signal)
    interpretation: str
    timestamp:    float   = field(default_factory=time.time)

    @property
    def is_spot_premium(self) -> bool:
        return self.basis_pct < BASIS_THRESHOLD_PCT


@dataclass
class CVDState:
    symbol:      str
    velocity:    float    # CVD' (delta between windows)
    acceleration: float   # CVD'' (change in velocity)
    verdict:     str      # IGNITION_ACCELERATING | DECAYING_MOMENTUM | etc.
    timestamp:   float    = field(default_factory=time.time)

    @property
    def is_aggression_confirmed(self) -> bool:
        return self.acceleration > CVD_ACCEL_GATE


@dataclass
class ToxicityResult:
    symbol:          str
    vpin_index:      float    # 0.0 – 1.0
    absorption_rate: float    # Iceberg detection
    obi_current:     float
    senior_verdict:  str      # INFORMED_FLOW | ELEVATED_ACTIVITY | CLEAN_FLOW
    is_scalp_valid:  bool     # VPIN > VPIN_THRESHOLD
    error_code:      str = "" # IMP-03 (Cycle 5): "" = ok, "DATA_FETCH_FAILED" = OB/trades unavailable
    timestamp:       float    = field(default_factory=time.time)


@dataclass
class FundingState:
    symbol:          str
    rate_pct:        float    # current funding rate in %
    rate_annualized: float    # annualized %
    zscore_48h:      float    # z-score vs 48h history
    regime:          str      # OVERHEATED | DEEP_DISCOUNT | NEUTRAL
    timestamp:       float    = field(default_factory=time.time)

    @property
    def is_squeeze_condition(self) -> bool:
        return self.rate_pct < -0.005   # shorts paying excessively


@dataclass
class MarketSnapshot:
    """Composite market state — one per symbol per cycle."""
    symbol:   str
    obi:      OBISnapshot
    basis:    Optional[BasisSnapshot]
    cvd:      Optional[CVDState]
    toxicity: Optional[ToxicityResult]
    funding:  Optional[FundingState]
    price:    float
    timestamp: float = field(default_factory=time.time)


# ═══════════════════════════════════════════════════════════════════
# TTL CACHE
# ═══════════════════════════════════════════════════════════════════

class _TTLCache:
    """
    Thread-safe(ish) async TTL key-value cache.
    Eliminates redundant exchange calls when multiple daemons share the Hub.
    """

    def __init__(self, default_ttl: float = 10.0):
        self._store: Dict[str, Tuple[Any, float]] = {}
        self._default_ttl = default_ttl
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0

    async def get(self, key: str, ttl: Optional[float] = None) -> Optional[Any]:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            val, ts = entry
            effective_ttl = ttl if ttl is not None else self._default_ttl
            if (time.time() - ts) > effective_ttl:
                del self._store[key]
                self._misses += 1
                return None
            self._hits += 1
            return val

    async def put(self, key: str, value: Any) -> None:
        async with self._lock:
            self._store[key] = (value, time.time())

    async def invalidate(self, prefix: str = "") -> None:
        async with self._lock:
            if not prefix:
                self._store.clear()
            else:
                for k in list(self._store):
                    if k.startswith(prefix):
                        del self._store[k]

    @property
    def stats(self) -> str:
        total = self._hits + self._misses
        rate = (self._hits / total * 100) if total > 0 else 0.0
        return f"Cache {self._hits}H/{self._misses}M ({rate:.0f}% hit)"


# ═══════════════════════════════════════════════════════════════════
# ZSCORE ENGINE (absorbed)
# ═══════════════════════════════════════════════════════════════════

class _ZScoreEngine:
    """
    Rolling Z-Score computation for funding rates and OBI.
    Maintains per-symbol history rings to avoid stateful DB dependency.
    Literature: He, Zhang, Giagkiozis, Bieganowski (validated thresholds).
    """

    def __init__(self, window: int = 192):   # 192 × 15min = 48h
        self._history: Dict[str, Deque[float]] = {}
        self._window = window

    def update(self, symbol: str, value: float) -> float:
        """Push new value, return current z-score. Returns 0 on insufficient data."""
        ring = self._history.setdefault(symbol, deque(maxlen=self._window))
        ring.append(value)
        if len(ring) < 10:
            return 0.0
        arr = np.array(ring, dtype=float)
        std = float(np.std(arr))
        if std < 1e-9:
            return 0.0
        return float((value - np.mean(arr)) / std)

    def zscore(self, symbol: str) -> float:
        ring = self._history.get(symbol)
        if not ring or len(ring) < 10:
            return 0.0
        arr = np.array(ring, dtype=float)
        std = float(np.std(arr))
        if std < 1e-9:
            return 0.0
        return float((ring[-1] - np.mean(arr)) / std)

    def regime(self, symbol: str) -> str:
        z = self.zscore(symbol)
        if z > 2.0:
            return "OVERHEATED"
        elif z < -2.0:
            return "DEEP_DISCOUNT"
        elif abs(z) < 0.5:
            return "MEAN_REVERT_RISK"
        return "NEUTRAL"


# ═══════════════════════════════════════════════════════════════════
# FUNDING FEES ENGINE (absorbed)
# ═══════════════════════════════════════════════════════════════════

class _FundingFeesEngine:
    """
    Funding rate normalization, annualization, and fee accumulation tracker.
    Consumed by both the Hub and market_actions.py endpoints.
    """

    @staticmethod
    def rate_to_float(rate_raw: Any) -> float:
        """Convert raw decimal rate to percentage, rounded to 6 decimals."""
        try:
            return round(float(rate_raw) * 100, 6)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def annualize(rate_pct: float, periods_per_day: int = 3) -> float:
        """Annualize funding rate. Binance/Bybit: 3 periods/day (8h intervals)."""
        return round(rate_pct * periods_per_day * 365, 4)

    async def fetch_current_rate(self, exchange: ccxt.Exchange, symbol: str) -> float:
        """Fetch current funding rate from exchange; returns rate as %."""
        try:
            fr = await exchange.fetch_funding_rate(symbol)
            raw = fr.get("fundingRate", 0)
            return self.rate_to_float(raw)
        except Exception as e:
            logger.warning(f"FundingRate fetch failed {symbol}: {e}")
            return 0.0


# ═══════════════════════════════════════════════════════════════════
# INTELLIGENCE HUB — SINGLETON
# ═══════════════════════════════════════════════════════════════════

class IntelligenceHub:
    """
    Process-wide singleton: one CCXT connection, one cache, all engines.

    Usage (context manager — preferred):
        async with IntelligenceHub.instance() as hub:
            snap = await hub.market_snapshot("BTC/USDT:USDT", "BTC/USDT")

    Usage (shared across daemons — manual lifecycle):
        hub = IntelligenceHub.instance()
        await hub.connect()
        ...
        await hub.close()
    """

    _inst: Optional["IntelligenceHub"] = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __new__(cls) -> "IntelligenceHub":
        raise TypeError("Use IntelligenceHub.instance() — this is a singleton.")

    @classmethod
    async def instance(cls) -> "IntelligenceHub":
        """Acquire (or create) the process-wide singleton asynchronously."""
        async with cls._lock:
            if cls._inst is None:
                obj = object.__new__(cls)
                obj._initialized = False
                cls._inst = obj
            return cls._inst

    @classmethod
    def instance_sync(cls) -> "IntelligenceHub":
        """
        Sync accessor for non-async contexts (e.g. action server wrappers).
        Caller is responsible for calling connect() before use.
        """
        if cls._inst is None:
            obj = object.__new__(cls)
            obj._initialized = False
            cls._inst = obj
        return cls._inst

    # ── Lifecycle ─────────────────────────────────────────────────

    def _init_internals(self) -> None:
        if getattr(self, "_initialized", False):
            return
        self._exchange: Optional[ccxt.Exchange] = None
        self._spot_exchange: Optional[ccxt.Exchange] = None
        self._max_retries = 3
        self._retry_delay = 2.0
        self._cache = _TTLCache(default_ttl=15.0)  # Slightly higher TTL for 429 safety
        self._zscore = _ZScoreEngine(window=192)
        self._funding = _FundingFeesEngine()
        self._cvd_velocity: Dict[str, float] = {}
        self._semaphore = asyncio.Semaphore(15)    # Global concurrent requests limit
        self._cooling_down = False
        self._cooling_until = 0.0
        self._initialized = True

    async def connect(self) -> None:
        """Initialize exchange connections (idempotent)."""
        self._init_internals()
        if self._exchange is not None:
            return

        exchange_cls = getattr(ccxt, settings.exchange.id)
        self._exchange = exchange_cls({
            "enableRateLimit": True,
            "timeout": getattr(settings.exchange, "timeout", 30000),
            "options": {"defaultType": getattr(settings.exchange, "type", "future")},
        })
        try:
            await self._exchange.load_markets()
            logger.info(f"[Hub] Connected futures: {settings.exchange.id}")
        except Exception as e:
            logger.error(f"[Hub] Futures connect failed: {e}")
            await self.close()
            raise

        # Spot exchange (same provider, spot mode) for basis calculations
        self._spot_exchange = exchange_cls({
            "enableRateLimit": True,
            "timeout": getattr(settings.exchange, "timeout", 30000),
            "options": {"defaultType": "spot"},
        })
        try:
            await self._spot_exchange.load_markets()
            logger.info("[Hub] Connected spot exchange.")
        except Exception as e:
            logger.warning(f"[Hub] Spot exchange init failed (non-fatal): {e}")
            self._spot_exchange = None

    async def close(self) -> None:
        """Gracefully close all exchange connections and wait for connectors."""
        logger.info("[Hub] Closing connections...")
        for ex in (self._exchange, self._spot_exchange):
            if ex:
                try:
                    await ex.close()
                except Exception as e:
                    logger.debug(f"[Hub] Error closing {ex.id}: {e}")
        self._exchange = None
        self._spot_exchange = None
        # Give aiohttp a moment to release sockets
        await asyncio.sleep(0.5)
        logger.info("[Hub] All connections closed.")

    async def __aenter__(self) -> "IntelligenceHub":
        self._init_internals()
        await self.connect()
        return self

    async def __aexit__(self, *_) -> None:
        # Singleton: do NOT close on context exit — other daemons still need it
        pass

    @property
    def cache_stats(self) -> str:
        """Expose internal TTL cache performance metrics."""
        return self._cache.stats if hasattr(self, "_cache") else "N/A"

    # ── Retry plumbing ────────────────────────────────────────────

    async def _retry(self, coro_func, *args, **kwargs):
        """Robust retry wrapper with throttling and 429 cooling circuit."""
        if self._cooling_down:
            if time.time() < self._cooling_until:
                return None
            self._cooling_down = False

        async with self._semaphore:
            last_err = None
            for attempt in range(self._max_retries):
                try:
                    return await coro_func(*args, **kwargs)
                except ccxt.DDoSProtection as e:
                    logger.warning(f"[Hub] 429 Rate Limit hit. Entering 60s cooldown. {e}")
                    self._cooling_down = True
                    self._cooling_until = time.time() + 60.0
                    return None
                except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as e:
                    last_err = e
                    wait = self._retry_delay * (2 ** attempt)
                    logger.warning(f"[Hub] retry {attempt+1}/{self._max_retries} in {wait:.1f}s: {e}")
                    await asyncio.sleep(wait)
                except Exception as e:
                    logger.error(f"[Hub] Unexpected error in {coro_func.__name__}: {e}")
                    raise
            return None

    # ═══════════════════════════════════════════════════════════════
    # PRIVATE DATA FETCHERS (formerly DataEngine public API)
    # ═══════════════════════════════════════════════════════════════

    async def _fetch_ohlcv(self, symbol: str, timeframe: str = "1h",
                           limit: int = 100, ttl: float = 8.0) -> Optional[pd.DataFrame]:
        cache_key = f"ohlcv:{symbol}:{timeframe}:{limit}"
        cached = await self._cache.get(cache_key, ttl=ttl)
        if cached is not None:
            return cached

        try:
            raw = await self._retry(self._exchange.fetch_ohlcv, symbol, timeframe, limit=limit)
            if not raw:
                return None
            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("datetime", inplace=True)
            df = df.astype({"open": "float64", "high": "float64",
                            "low": "float64", "close": "float64", "volume": "float64"})
            await self._cache.put(cache_key, df)
            return df
        except Exception as e:
            logger.error(f"[Hub] OHLCV {symbol} {timeframe}: {e}")
            return None

    async def _fetch_order_book(self, symbol: str, limit: int = 50,
                                ttl: float = 6.0) -> Optional[dict]:
        cache_key = f"ob:{symbol}:{limit}"
        cached = await self._cache.get(cache_key, ttl=ttl)
        if cached is not None:
            return cached
        try:
            ob = await self._retry(self._exchange.fetch_order_book, symbol, limit)
            await self._cache.put(cache_key, ob)
            return ob
        except Exception as e:
            logger.error(f"[Hub] OrderBook {symbol}: {e}")
            return None

    async def _fetch_trades(self, symbol: str, limit: int = 500,
                            ttl: float = 8.0) -> Optional[pd.DataFrame]:
        cache_key = f"trades:{symbol}:{limit}"
        cached = await self._cache.get(cache_key, ttl=ttl)
        if cached is not None:
            return cached
        try:
            trades = await self._retry(self._exchange.fetch_trades, symbol, limit=limit)
            if not trades:
                return None
            df = pd.DataFrame([{
                "timestamp": t["timestamp"], "price": t["price"],
                "amount": t["amount"], "side": t["side"],
            } for t in trades])
            df["price"] = pd.to_numeric(df["price"], errors="coerce")
            df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
            df["side"] = df["side"].astype(str).str.lower()
            df = df.dropna()
            if df.empty:
                return None
            await self._cache.put(cache_key, df)
            return df
        except Exception as e:
            logger.error(f"[Hub] Trades {symbol}: {e}")
            return None

    async def _fetch_ticker(self, symbol: str, ttl: float = 5.0) -> Optional[dict]:
        cache_key = f"ticker:{symbol}"
        cached = await self._cache.get(cache_key, ttl=ttl)
        if cached is not None:
            return cached
        try:
            t = await self._retry(self._exchange.fetch_ticker, symbol)
            await self._cache.put(cache_key, t)
            return t
        except Exception as e:
            logger.error(f"[Hub] Ticker {symbol}: {e}")
            return None

    async def _fetch_ohlcv_spot(self, symbol: str, timeframe: str = "1m",
                                limit: int = 2) -> Optional[pd.DataFrame]:
        if self._spot_exchange is None:
            return None
        try:
            raw = await self._retry(self._spot_exchange.fetch_ohlcv, symbol, timeframe, limit=limit)
            if not raw:
                return None
            df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df = df.astype({"close": "float64"})
            return df
        except Exception as e:
            logger.warning(f"[Hub] Spot OHLCV {symbol}: {e}")
            return None

    # ═══════════════════════════════════════════════════════════════
    # PRIVATE ANALYSIS METHODS — PhD-Level Microstructure
    # ═══════════════════════════════════════════════════════════════

    async def _compute_obi(self, symbol: str, depth: int = 50) -> OBISnapshot:
        """Order Book Imbalance: (bids - asks) / (bids + asks)."""
        ob = await self._fetch_order_book(symbol, limit=depth)
        if not ob:
            return OBISnapshot(symbol=symbol, obi=0.0, bid_depth=0.0, ask_depth=0.0)
        bids = sum(b[1] for b in ob.get("bids", []))
        asks = sum(a[1] for a in ob.get("asks", []))
        total = bids + asks
        obi = (bids - asks) / total if total > 0 else 0.0
        return OBISnapshot(symbol=symbol, obi=round(obi, 4), bid_depth=bids, ask_depth=asks)

    async def _compute_basis(self, symbol_perp: str,
                             symbol_spot: str) -> Optional[BasisSnapshot]:
        """Basis divergence: perp_price - spot_price. Negative = spot premium."""
        spot_df = await self._fetch_ohlcv_spot(symbol_spot, "1m", 2)
        perp_df = await self._fetch_ohlcv(symbol_perp, "1m", 2, ttl=5.0)
        if spot_df is None or perp_df is None or spot_df.empty or perp_df.empty:
            return None
        spot_p = float(spot_df["close"].iloc[-1])
        perp_p = float(perp_df["close"].iloc[-1])
        basis_usd = perp_p - spot_p
        basis_pct = (basis_usd / spot_p) * 100 if spot_p > 0 else 0.0
        interp = ("Spot Premium (Accumulation Stealth)"
                  if basis_pct < 0 else "Perp Premium (Retail FOMO/Hedging)")
        return BasisSnapshot(
            symbol_spot=symbol_spot, symbol_perp=symbol_perp,
            spot_price=spot_p, perp_price=perp_p,
            basis_usd=round(basis_usd, 4), basis_pct=round(basis_pct, 6),
            interpretation=interp,
        )

    async def _compute_cvd_state(self, symbol: str,
                                 trade_limit: int = 200) -> Optional[CVDState]:
        """
        CVD Velocity (CVD') and Acceleration (CVD'').
        CVD'' > 0 while OBI < 0 → Dynamic Absorption (God Candle ignition).
        """
        trades = await self._fetch_trades(symbol, limit=trade_limit * 2)
        if trades is None or len(trades) < trade_limit:
            return None

        def _window_cvd(df: pd.DataFrame) -> float:
            buys = df[df["side"] == "buy"]["amount"].sum()
            sells = df[df["side"] == "sell"]["amount"].sum()
            return float(buys - sells)

        half = len(trades) // 2
        cvd1 = _window_cvd(trades.iloc[:half])
        cvd2 = _window_cvd(trades.iloc[half:])
        velocity = cvd2 - cvd1

        prev_vel = self._cvd_velocity.get(symbol, 0.0)
        acceleration = velocity - prev_vel
        self._cvd_velocity[symbol] = velocity

        if acceleration > 0 and velocity > 0:
            verdict = "IGNITION_ACCELERATING"
        elif acceleration < 0 and velocity > 0:
            verdict = "DECAYING_MOMENTUM"
        elif acceleration > 0 and velocity < 0:
            verdict = "SHORT_SQUEEZE_ABSORPTION"
        else:
            verdict = "STABLE"

        return CVDState(
            symbol=symbol,
            velocity=round(velocity, 4),
            acceleration=round(acceleration, 4),
            verdict=verdict,
        )

    async def _compute_toxicity(self, symbol: str, ob_depth: int = 50,
                                trade_limit: int = 500) -> ToxicityResult:
        """
        PhD-Level VPIN / Absorption composite.
        VPIN > 0.62 = informed flow gate (Easley et al.)
        Absorption > 0.60 = iceberg orders detected.
        """
        ob = await self._fetch_order_book(symbol, limit=ob_depth)
        trades = await self._fetch_trades(symbol, limit=trade_limit)

        obi_val = 0.0
        absorption_rate = 0.0
        vpin_index = 0.0

        if ob:
            bids_tot = sum(b[1] for b in ob.get("bids", []))
            asks_tot = sum(a[1] for a in ob.get("asks", []))
            total = bids_tot + asks_tot
            obi_val = (bids_tot - asks_tot) / total if total > 0 else 0.0

            # Wall concentration: top 5 / total depth → proxy for iceberg absorption
            top_bids = sum(b[1] for b in sorted(ob.get("bids", []), key=lambda x: x[1], reverse=True)[:5])
            absorption_rate = (top_bids / bids_tot) if bids_tot > 0 else 0.0

        if trades is not None and not trades.empty:
            buy_vol = trades[trades["side"] == "buy"]["amount"].sum()
            sell_vol = trades[trades["side"] == "sell"]["amount"].sum()
            total_vol = buy_vol + sell_vol
            if total_vol > 0:
                # VPIN proxy: |buy_vol - sell_vol| / total_vol (order imbalance toxicity)
                imbalance = abs(buy_vol - sell_vol) / total_vol
                # Blend with OBI for composite toxicity
                vpin_index = round(0.60 * imbalance + 0.40 * abs(obi_val), 4)

        # IMP-03 (Cycle 5): Detect data fetch failure vs genuine low toxicity
        data_fetch_failed = (ob is None and (trades is None or trades.empty))
        error_code = "DATA_FETCH_FAILED" if data_fetch_failed else ""

        # Senior Desk verdict
        if data_fetch_failed:
            senior_verdict = "⚠️  DATA_UNAVAILABLE — Connectivity issue. Retry pending."
        elif vpin_index > 0.70 and absorption_rate > ABSORPTION_GATE:
            senior_verdict = "⚠️  INFORMED_FLOW — High conviction scalp setup forming."
        elif vpin_index > 0.40 or absorption_rate > 0.50:
            senior_verdict = "🟡 ELEVATED_ACTIVITY — Monitor 30s before entry."
        else:
            senior_verdict = "✅ CLEAN_FLOW — Retail soup. No institutional edge."

        return ToxicityResult(
            symbol=symbol,
            vpin_index=vpin_index,
            absorption_rate=round(absorption_rate, 4),
            obi_current=round(obi_val, 4),
            senior_verdict=senior_verdict,
            is_scalp_valid=(vpin_index >= VPIN_THRESHOLD and not data_fetch_failed),
            error_code=error_code,
        )

    async def _compute_funding_state(self, symbol: str) -> Optional[FundingState]:
        """Fetch, normalize, and z-score the current funding rate."""
        if self._exchange is None:
            return None
        try:
            rate_pct = await self._funding.fetch_current_rate(self._exchange, symbol)
        except Exception:
            return None

        z = self._zscore.update(symbol, rate_pct)
        regime = self._zscore.regime(symbol)
        annualized = self._funding.annualize(rate_pct)

        return FundingState(
            symbol=symbol,
            rate_pct=rate_pct,
            rate_annualized=annualized,
            zscore_48h=round(z, 4),
            regime=regime,
        )

    async def _compute_wall_velocity(self, symbol: str,
                                     prev_wall_state: Optional[dict] = None
                                     ) -> dict:
        """
        Wall Velocity Tracker — detects spoofing (ghost walls).
        Returns: {v_price, v_wall, rel_velocity, verdict, ask_wall}
        Requires Redis for cross-call state (falls back to snapshot if absent).
        """
        results = await asyncio.gather(
            self._fetch_order_book(symbol, limit=20),
            self._fetch_ticker(symbol),
        )
        ob, ticker = results
        if not ob or not ticker:
            return {"error": "fetch_failed"}

        price = float(ticker.get("last", 0))
        largest_ask = max(ob["asks"], key=lambda x: x[1]) if ob.get("asks") else [0, 0]

        if prev_wall_state is None:
            return {
                "status": "INITIALIZED",
                "price": price,
                "ask_wall": largest_ask,
                "verdict": "AWAITING_NEXT_TICK",
            }

        dt = time.time() - prev_wall_state.get("t", time.time())
        if dt < 0.001:
            dt = 0.001

        v_price = (price - prev_wall_state.get("price", price)) / dt
        v_wall = (largest_ask[0] - prev_wall_state.get("wall_price", largest_ask[0])) / dt
        rel_velocity = v_wall - v_price

        if abs(v_wall) > abs(v_price) * 1.5:
            verdict = "GHOST_SPOOFING_DETECTED"
        elif abs(rel_velocity) < abs(v_price) * 0.2:
            verdict = "ORGANIC_LADDERING"
        else:
            verdict = "STICKY"

        return {
            "status": "ok", "symbol": symbol,
            "v_price": round(v_price, 4),
            "v_wall": round(v_wall, 4),
            "rel_velocity": round(rel_velocity, 4),
            "verdict": verdict,
            "ask_wall": largest_ask,
            "t": time.time(), "price": price, "wall_price": largest_ask[0],
        }

    # ═══════════════════════════════════════════════════════════════
    # PUBLIC INTERFACE — for Guardian_Daemon and action server
    # ═══════════════════════════════════════════════════════════════

    async def get_obi(self, symbol: str, depth: int = 50) -> OBISnapshot:
        """Public: Order Book Imbalance snapshot."""
        return await self._compute_obi(symbol, depth)

    async def get_basis(self, symbol_perp: str,
                        symbol_spot: str) -> Optional[BasisSnapshot]:
        """Public: Spot/Perp basis divergence."""
        return await self._compute_basis(symbol_perp, symbol_spot)

    async def get_cvd_state(self, symbol: str,
                            trade_limit: int = 200) -> Optional[CVDState]:
        """Public: CVD velocity and acceleration."""
        return await self._compute_cvd_state(symbol, trade_limit)

    async def get_toxicity(self, symbol: str, ob_depth: int = 50,
                           trade_limit: int = 500) -> ToxicityResult:
        """Public: VPIN / Absorption composite toxicity index."""
        return await self._compute_toxicity(symbol, ob_depth, trade_limit)

    async def get_funding_state(self, symbol: str) -> Optional[FundingState]:
        """Public: Funding rate with z-score and regime classification."""
        return await self._compute_funding_state(symbol)

    async def get_wall_velocity(self, symbol: str,
                                prev_state: Optional[dict] = None) -> dict:
        """Public: Wall velocity / spoofing detector."""
        return await self._compute_wall_velocity(symbol, prev_state)

    async def get_ticker(self, symbol: str) -> Optional[dict]:
        """Public: Ticker information."""
        return await self._fetch_ticker(symbol)

    async def get_price(self, symbol: str) -> float:
        """Public: Latest price from ticker."""
        t = await self.get_ticker(symbol)
        return float(t["last"]) if t else 0.0

    async def get_ohlcv(self, symbol: str, timeframe: str = "1h",
                        limit: int = 100) -> Optional[pd.DataFrame]:
        """Public: OHLCV DataFrame."""
        return await self._fetch_ohlcv(symbol, timeframe, limit)

    async def get_order_book(self, symbol: str, limit: int = 50) -> Optional[dict]:
        """Public: Raw order book."""
        return await self._fetch_order_book(symbol, limit)

    async def get_trades(self, symbol: str, limit: int = 100) -> Optional[pd.DataFrame]:
        return await self._retry(self._fetch_trades, symbol, limit)

    # ── Legacy Aliases (Backward Compat) ──────────────────────────
    fetch_ohlcv      = get_ohlcv
    fetch_order_book = get_order_book
    fetch_trades     = get_trades
    fetch_ticker     = get_ticker

    async def market_snapshot(self, symbol_perp: str,
                              symbol_spot: Optional[str] = None,
                              ob_depth: int = 50,
                              trade_limit: int = 300) -> MarketSnapshot:
        """
        Composite market snapshot — fires all analysis in parallel.
        This is the primary method Guardian_Daemon calls every cycle.

        Args:
            symbol_perp:  perpetual symbol e.g. "BTC/USDT:USDT"
            symbol_spot:  spot symbol e.g. "BTC/USDT" (optional, for basis)
            ob_depth:     order book depth
            trade_limit:  trade history size

        Returns:
            MarketSnapshot with obi, basis, cvd, toxicity, funding, price.
        """
        tasks = [
            self._compute_obi(symbol_perp, ob_depth),
            self._compute_cvd_state(symbol_perp, trade_limit),
            self._compute_toxicity(symbol_perp, ob_depth, trade_limit),
            self._compute_funding_state(symbol_perp),
            self._fetch_ticker(symbol_perp),
        ]

        if symbol_spot:
            tasks.append(self._compute_basis(symbol_perp, symbol_spot))
            obi_r, cvd_r, tox_r, fund_r, ticker_r, basis_r = await asyncio.gather(*tasks)
        else:
            obi_r, cvd_r, tox_r, fund_r, ticker_r = await asyncio.gather(*tasks)
            basis_r = None

        price = float(ticker_r["last"]) if ticker_r else 0.0

        return MarketSnapshot(
            symbol=symbol_perp,
            obi=obi_r,
            basis=basis_r,
            cvd=cvd_r,
            toxicity=tox_r,
            funding=fund_r,
            price=price,
        )

    async def ignition_check(self, btc_sym: str = "BTC/USDT:USDT",
                              eth_sym: str = "ETH/USDT:USDT") -> dict:
        """
        Ignition Bridge dual-asset check.
        Returns combined state for RotationSentinel logic.

        GOLDEN RULE gates applied:
          - OBI_IGNITION = 0.40 (coordinated threshold)
          - VPIN_THRESHOLD = 0.62
          - BASIS_THRESHOLD_PCT = -0.05%
          - CVD_ACCEL_GATE = 0.0
        """
        btc_obi, eth_obi, btc_tox, eth_tox, eth_basis, eth_cvd = await asyncio.gather(
            self._compute_obi(btc_sym, 20),
            self._compute_obi(eth_sym, 20),
            self._compute_toxicity(btc_sym, 20, 300),
            self._compute_toxicity(eth_sym, 20, 300),
            self._compute_basis(eth_sym, "ETH/USDT"),
            self._compute_cvd_state(eth_sym, 200),
        )

        btc_consolidating = btc_tox.vpin_index < 0.60 and abs(btc_obi.obi) < 0.30
        btc_re_igniting   = btc_tox.vpin_index > 0.70
        eth_scalp_valid   = eth_tox.is_scalp_valid

        basis_ok   = eth_basis is not None and eth_basis.is_spot_premium
        cvd_ok     = eth_cvd is not None and eth_cvd.is_aggression_confirmed
        eth_ready  = basis_ok and eth_scalp_valid and cvd_ok

        if btc_re_igniting:
            phase = "BTC_RESURGENCE"
        elif btc_consolidating and eth_ready:
            phase = "ALPHA_ROTATION_TRIGGERED"
        elif btc_consolidating:
            phase = "STEALTH_PHASE"
        else:
            phase = "NEUTRAL"

        return {
            "phase": phase,
            "btc": {
                "obi": btc_obi.obi,
                "vpin": btc_tox.vpin_index,
                "consolidating": btc_consolidating,
                "re_igniting": btc_re_igniting,
            },
            "eth": {
                "obi": eth_obi.obi,
                "vpin": eth_tox.vpin_index,
                "basis_pct": eth_basis.basis_pct if eth_basis else None,
                "cvd_accel": eth_cvd.acceleration if eth_cvd else None,
                "ready": eth_ready,
            },
            "golden_rules": {
                "vpin_threshold": VPIN_THRESHOLD,
                "basis_threshold_pct": BASIS_THRESHOLD_PCT,
                "cvd_accel_gate": CVD_ACCEL_GATE,
                "obi_ignition": OBI_IGNITION,
            },
        }

    @property
    def cache_stats(self) -> str:
        return self._cache.stats


# ═══════════════════════════════════════════════════════════════════
# CONVENIENCE FACTORY (for action server sync wrappers)
# ═══════════════════════════════════════════════════════════════════

def _run_hub_sync(coro):
    """
    Sync bridge for action server (sema4ai @action wrappers).
    Creates a fresh Hub instance per call — stateless, safe for action server.
    """
    import asyncio

    async def _wrap():
        hub = object.__new__(IntelligenceHub)
        hub._initialized = False
        hub._init_internals()
        await hub.connect()
        try:
            return await coro(hub)
        finally:
            await hub.close()

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_wrap())
    finally:
        loop.close()
