"""
market_actions.py — On-Demand Market Analysis Actions
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Provides endpoints for quick market state snapshots,
order book walls, basis (spot vs perp) divergence,
and PhD-level toxicity / absorption scanning.
"""
import sys
import json
import time
import asyncio
from datetime import datetime, timezone

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import IntelligenceHub, _run_hub_sync, RedisBridge, redis, settings
from shared.engines.zscore import ZScoreEngine
from sema4ai.actions import action
from pathlib import Path

_LazyRedis = None

def _redis():
    global _LazyRedis
    if _LazyRedis is None:
        _LazyRedis = RedisBridge.instance()
    return _LazyRedis

_ROOT = Path(__file__).resolve().parent.parent.parent  # ccxtv2-next/
_DATA = _ROOT / "data"

def _is_running(pid_file):
    """Checks if a process is running based on a PID file."""
    p_path = _DATA / pid_file
    if not p_path.exists():
        return False
    try:
        pid = int(p_path.read_text().strip())
        return psutil.pid_exists(pid)
    except:
        return False

@action(is_consequential=False)
def get_system_health() -> dict:
    """
    Performs a comprehensive diagnostic of the CCXTV2 ecosystem.
    Checks daemon status and calculates a real-time 'Scalp Score' for BTC and ETH.
    """
    audit_log("Running system health check", component="ACTION_SERVER")
    health = {
        "daemons": {
            "guardian": "RUNNING" if _is_running("guardian.pid") else "STOPPED",
            "controller": "RUNNING" if _is_running("controller.pid") else "STOPPED",
            "action_server": "RUNNING" # If this runs, it's alive
        },
        "market_pulse": {},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    # Aggregate Scalp Scores (Logic centralized in utils/helpers.py)
    snapshot_dir = _DATA / "snapshots"
    for asset in ["btc", "eth"]:
        try:
            tox = load_json_safe(str(snapshot_dir / f"scalp_tox_{asset}.json"))
            audit = load_json_safe(str(snapshot_dir / f"scalp_audit_{asset}.json"))
            basis_data = load_json_safe(str(snapshot_dir / f"intraday_basis_{asset}.json"))
            
            t_idx = tox.get("toxicity", {}).get("index", 0)
            obi = audit.get("microstructure", {}).get("obi_20", 0)
            iceberg = tox.get("iceberg", {}).get("score", 0)
            cvd = audit.get("microstructure", {}).get("cvd_100_trades_usd", 0)
            basis = basis_data.get("basis_pct", 0)
            
            score, reasons = calculate_institutional_score(t_idx, obi, iceberg, cvd, basis)
            
            health["market_pulse"][asset.upper()] = {
                "score": score,
                "verdict": "IGNITION" if score >= 8 else "MONITOR" if score >= 4 else "NO_EDGE",
                "reasons": reasons,
                "metrics": {
                    "toxicity": round(t_idx, 4),
                    "obi": round(obi, 4),
                    "basis": round(basis, 4),
                    "iceberg": round(iceberg, 4)
                }
            }
        except Exception as e:
            health["market_pulse"][asset.upper()] = {"error": str(e)}

    return health

@action(is_consequential=False)
def get_htf_zscore(assets: str = "BTC,ETH") -> dict:
    """
    Calculates the High-Timeframe (HTF) Z-Score for specified assets.
    This provides macro statistical bias based on MVRV and Wyckoff phases.
    
    Args:
        assets: Comma-separated list of assets (e.g. BTC,ETH)
    """
    async def _fetch(hub):
        engine = ZScoreEngine()
        asset_list = assets.split(",")
        results = {}
        
        # Mapping assets to their standard supply and symbols
        asset_meta = {
            "BTC": {"symbol": "BTC/USDT", "supply": 19690000},
            "ETH": {"symbol": "ETH/USDT", "supply": 120000000}
        }
        
        for asset in asset_list:
            meta = asset_meta.get(asset.upper())
            if not meta:
                results[asset] = {"error": f"Metadata for {asset} not found"}
                continue
                
            symbol = meta["symbol"]
            try:
                data = await engine._fetch_all_data(symbol)
                if data and '1d' in data['price_data']:
                    supply = data['supply'] or meta["supply"]
                    df = engine._calculate_enhanced(data['price_data']['1d'], supply, data['cvd_data'], data['oi_data'])
                    
                    last = df.iloc[-1]
                    results[asset] = {
                        "symbol": symbol,
                        "z_score": round(float(last['mvrv_z_smooth']), 3),
                        "phase": str(last['wyckoff_phase']),
                        "signal": int(last['signal']),
                        "strength": int(last['signal_strength']),
                        "reason": str(last['signal_reason']),
                        "price": float(last['close'])
                    }
                else:
                    results[asset] = {"error": "No daily data fetched"}
            except Exception as e:
                results[asset] = {"error": str(e)}
        
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "results": results
        }
            
    return _run_hub_sync(lambda hub: _fetch(hub))

@action(is_consequential=True)
def refresh_watchlist_levels() -> dict:
    """
    Calculates dynamic institutional levels (D1 High/Low, H4 S/R) for BTC and ETH.
    Updates 'data/watchlist_levels.json' for real-time daemon monitoring.
    """
    async def _update(hub):
        levels_file = _ROOT / "data" / "watchlist_levels.json"
        current_data = {}
        if levels_file.exists():
            try:
                current_data = json.loads(levels_file.read_text())
            except:
                pass

        assets = [
            {"perp": "BTC/USDT:USDT", "name": "BTC"},
            {"perp": "ETH/USDT:USDT", "name": "ETH"},
        ]
        
        results = {}
        for asset in assets:
            perp = asset["perp"]
            name = asset["name"]
            
            # D1 OHLCV
            df = await hub.fetch_ohlcv(perp, "1d", limit=7)
            if df is None or df.empty:
                continue
                
            d1_high = float(df["high"].iloc[-1])
            d1_low  = float(df["low"].iloc[-1])
            pd_high = float(df["high"].iloc[-2])
            pd_low  = float(df["low"].iloc[-2])
            
            # H4 OHLCV
            df_h4 = await hub.fetch_ohlcv(perp, "4h", limit=10)
            h4_high = float(df_h4["high"].max())
            h4_low  = float(df_h4["low"].min())
            
            resistances = sorted(list(set([d1_high, pd_high, h4_high])))
            supports    = sorted(list(set([d1_low, pd_low, h4_low])), reverse=True)
            
            current_data[perp] = {
                "resistances": resistances,
                "supports": supports,
                "last_updated": datetime.now(timezone.utc).isoformat()
            }
            results[name] = {"res": len(resistances), "sup": len(supports)}

        levels_file.write_text(json.dumps(current_data, indent=2))
        return {
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "updates": results,
            "file": str(levels_file)
        }

    return _run_hub_sync(lambda hub: _update(hub))

async def _get_ob_walls_internal(hub, symbol: str, depth: int = 50) -> dict:
    """Internal async helper for OB walls."""
    try:
        ob = await hub.fetch_order_book(symbol, limit=depth)
        if not ob:
            return {"error": "Failed to fetch order book"}
            
        bids = sorted(ob.get('bids', []), key=lambda x: x[1], reverse=True)[:5]
        asks = sorted(ob.get('asks', []), key=lambda x: x[1], reverse=True)[:5]
        
        return {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "top_supports_bids": [{"price": b[0], "size": b[1]} for b in bids],
            "top_resistances_asks": [{"price": a[0], "size": a[1]} for a in asks],
        }
    except Exception as e:
        return {"error": str(e)}

@action(is_consequential=False)
def get_ob_walls(symbol: str = "BTC/USDT:USDT", depth: int = 50) -> dict:
    """
    Detects major liquidity walls in the order book.
    Returns the largest bids (support) and asks (resistance).
    
    Args:
        symbol: The ticker symbol (e.g. BTC/USDT:USDT)
        depth: How deep to scan the order book.
    """
    return _run_hub_sync(lambda hub: _get_ob_walls_internal(hub, symbol, depth))

async def _get_basis_internal(hub, symbol_spot: str, symbol_perp: str) -> dict:
    """Internal async helper for basis calculation."""
    try:
        # Use hub.get_basis which correctly handles spot vs perp exchanges
        basis_data = await hub.get_basis(symbol_perp, symbol_spot)
        
        if basis_data is None:
            return {"error": "Failed to fetch basis data"}
            
        return {
            "spot_symbol": symbol_spot,
            "perp_symbol": symbol_perp,
            "spot_price": basis_data.spot_price,
            "perp_price": basis_data.perp_price,
            "basis_usd": round(basis_data.basis_usd, 4),
            "basis_pct": round(basis_data.basis_pct, 4),
            "interpretation": basis_data.interpretation
        }
    except Exception as e:
        return {"error": str(e)}

@action(is_consequential=False)
def get_basis(symbol_spot: str = "BTC/USDT", symbol_perp: str = "BTC/USDT:USDT") -> dict:
    """
    Calculates the basis (divergence) between Spot and Perp prices.
    Positive basis = strong spot demand. Negative = perp discount (hedging).
    
    Args:
        symbol_spot: The spot ticker (e.g. BTC/USDT)
        symbol_perp: The perpetual ticker (e.g. BTC/USDT:USDT)
    """
    return _run_hub_sync(lambda hub: _get_basis_internal(hub, symbol_spot, symbol_perp))

@action(is_consequential=False)
def analyze_market_snapshot(symbol: str = "BTC/USDT:USDT") -> dict:
    """
    Provides a quick on-demand general market snapshot of a single ticker,
    fetching recent price, trades count, and basic OBI.
    
    Args:
        symbol: The ticker symbol (e.g. BTC/USDT:USDT)
    """
    async def _fetch(hub):
        try:
            df = await hub.fetch_ohlcv(symbol, "1h", limit=24)
            ob = await hub.fetch_order_book(symbol, limit=20)
            
            if df is None or not ob:
                return {"error": "Failed fetching market data"}
                
            current_p = float(df['close'].iloc[-1])
            high_24h = float(df['high'].max())
            low_24h = float(df['low'].min())
            
            bids = sum(b[1] for b in ob.get('bids', []))
            asks = sum(a[1] for a in ob.get('asks', []))
            obi = (bids - asks) / (bids + asks) if (bids+asks)>0 else 0
            
            return {
                "symbol": symbol,
                "current_price": current_p,
                "high_24h": high_24h,
                "low_24h": low_24h,
                "obi_20": round(obi, 4),
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            return {"error": str(e)}

    return _run_hub_sync(lambda hub: _fetch(hub))


async def _get_toxicity_index_internal(hub, symbol: str, ob_depth: int = 50, trade_limit: int = 500) -> str:
    """Internal async helper for toxicity index."""
    try:
        detector = AbsorptionDetector(hub)
        result = await detector.scan(symbol, ob_depth=ob_depth, trade_limit=trade_limit)
        d = result.to_dict()

        tox = d.get("toxicity", {}).get("index", 0) or 0
        absr = d.get("absorption", {}).get("rate", 0) or 0

        if tox > 0.7 and absr > 0.6:
            senior_verdict = "⚠️  INFORMED_FLOW — High conviction scalp setup forming."
        elif tox > 0.4 or absr > 0.5:
            senior_verdict = "🟡 ELEVATED_ACTIVITY — Monitor for 30s before entry."
        else:
            senior_verdict = "✅ CLEAN_FLOW — Retail soup. No institutional edge detected."

        return json.dumps({
            "status": "ok",
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "senior_verdict": senior_verdict,
            **d,
        }, indent=2, default=str)
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})

@action(is_consequential=False)
def get_toxicity_index(symbol: str = "BTC/USDT:USDT", ob_depth: int = 50, trade_limit: int = 500) -> str:
    """
    Risk-Flip (Abort Signal):
      toxicity_index drops below 0.4 within 30s → Flow was a fakeout, do NOT enter.
      iceberg_score drops to 0 → Hidden wall has been pulled, liquidity trap.

    Args:
        symbol:      Ticker symbol (e.g. BTC/USDT:USDT, ETH/USDT:USDT)
        ob_depth:    Order book depth for wall detection. Default 50 (use 20 for scalping).
        trade_limit: Number of recent trades to analyze for CVD/VPIN. Default 500.

    Returns:
        JSON string with full absorption result + senior desk verdict.
    """
    return _run_hub_sync(lambda hub: _get_toxicity_index_internal(hub, symbol, ob_depth, trade_limit))


@action(is_consequential=False)
def get_liquidation_monitor(symbol: str = "BTC/USDT:USDT", threshold_usd: float = 50000) -> str:
    """
    Real-time Liquidation & Heavy Fill Monitor.

    Filters the trade flow for high-intensity 'uninformed' liquidations. 
    Use this to validate Iceberg scores: if Toxicity is LOW but Iceberg is HIGH
    and Liquidation Intensity is HIGH, it's a forced liquidation, not informed buying.

    Args:
        symbol:        Ticker symbol.
        threshold_usd: USD value to consider a trade 'heavy' (default $50k).

    Returns:
        JSON with liquidation intensity, volume distribution, and bias.
    """
    async def _monitor(hub):
        try:
            trades = await hub.fetch_trades(symbol, limit=1000)
            if trades is None or trades.empty:
                return json.dumps({"error": "No trade data"})

            trades['usd_val'] = trades['amount'] * trades['price']
            heavy = trades[trades['usd_val'] >= threshold_usd]
            
            buy_heavy = float(heavy[heavy['side'] == 'buy']['usd_val'].sum())
            sell_heavy = float(heavy[heavy['side'] == 'sell']['usd_val'].sum())
            
            intensity = (buy_heavy + sell_heavy) / float(trades['usd_val'].sum()) if not trades.empty else 0
            bias = "LONG_LIQ_PRESSURE" if buy_heavy > sell_heavy else "SHORT_LIQ_PRESSURE"
            
            return json.dumps({
                "status": "ok",
                "symbol": symbol,
                "liquidation_intensity": round(intensity, 4),
                "bias": bias if intensity > 0.05 else "NEUTRAL",
                "heavy_buy_usd": round(buy_heavy, 2),
                "heavy_sell_usd": round(sell_heavy, 2),
                "count": len(heavy)
            }, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)})

    return _run_hub_sync(lambda hub: _monitor(hub))


@action(is_consequential=True)
def execute_emergency_kill_switch(symbol: str = "BTC/USDT:USDT") -> str:
    """
    Emergency Auto-Kill Switch.
    This action should be polled every 10 seconds.
    It runs an ultra-fast toxicity scan. If the toxicity_index > 0.80 (Extreme Toxicity),
    it flags a system-wide panic to abort all trades.

    Args:
        symbol: Ticker symbol.

    Returns:
        JSON with STOP_ALL_TRADES status and message.
    """
    async def _check(hub):
        try:
            detector = AbsorptionDetector(hub)
            # Ultra-fast scan: depth=10, trade_limit=200 for low latency
            res = await detector.scan(symbol, ob_depth=10, trade_limit=200)
            d = res.to_dict()
            tox = d.get("toxicity", {}).get("index", 0) or 0
            
            if tox > 0.80:
                return json.dumps({
                    "status": "ALERT",
                    "STOP_ALL_TRADES": True,
                    "symbol": symbol,
                    "message": f"🚨 Market Crash/Toxic Flow Detected! Toxicity at {tox:.4f}. Liquidate immediately."
                }, indent=2)
            else:
                return json.dumps({
                    "status": "SAFE",
                    "STOP_ALL_TRADES": False,
                    "symbol": symbol,
                    "toxicity_index": round(tox, 4),
                    "message": "Market flow within safe operational limits."
                }, indent=2)
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    return _run_hub_sync(lambda hub: _check(hub))


@action(is_consequential=False)
def get_delta_acceleration(symbol: str = "BTC/USDT:USDT", window_trades: int = 100) -> str:
    """
    Calculates CVD Acceleration (CVD''). 
    If CVD'' > 0 while OBI < 0, we have Dynamic Absorption (God Candle ignition).
    
    Args:
        symbol: Ticker symbol.
        window_trades: Number of trades to window for delta calculation.
        
    Returns:
        JSON with CVD Velocity and Acceleration metrics.
    """
    async def _acceleration(hub):
        try:
            trades = await hub.fetch_trades(symbol, limit=window_trades * 2)
            if trades is None or trades.empty:
                return json.dumps({"error": "Failed to fetch trades"}, indent=2)
            
            # Convert trades DataFrame to list of dicts for legacy logic if needed
            # But wait, hub.fetch_trades returns a DataFrame.
            t_list = trades.to_dict('records')
            
            # Split into two windows to get ΔCVD (Velocity)
            v1_trades = t_list[:window_trades]
            v2_trades = t_list[window_trades:]
            
            def calc_cvd(trades_list):
                return sum([t['amount'] if t['side'] == 'buy' else -t['amount'] for t in trades_list])
            
            cvd1 = calc_cvd(v1_trades)
            cvd2 = calc_cvd(v2_trades)
            
            # Velocity (CVD')
            vel = cvd2 - cvd1
            
            clean_symbol = symbol.replace("/", "").replace(":", "").lower()
            key_cvd = f"prod:{clean_symbol}:flow:cvd_vel"
            
            # Redis bridge — unified singleton with failover
            prev_vel = _redis().get_cvd_velocity(key_cvd) or 0
            _redis().set_cvd_velocity(key_cvd, vel)
            
            # Acceleration (CVD'')
            accel = vel - prev_vel
            
            verdict = "STABLE"
            if accel > 0 and vel > 0:
                verdict = "IGNITION_ACCELERATING"
            elif accel < 0 and vel > 0:
                verdict = "DECAYING_MOMENTUM"
            elif accel > 0 and vel < 0:
                verdict = "SHORT_SQUEEZE_ABSORPTION"
                
            return json.dumps({
                "status": "ok",
                "symbol": symbol,
                "metrics": {
                    "cvd_velocity": round(vel, 4),
                    "cvd_acceleration": round(accel, 4),
                    "verdict": verdict
                }
            }, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)})
            
    return _run_hub_sync(lambda hub: _acceleration(hub))


@action(is_consequential=False)
def get_wall_velocity(symbol: str = "BTC/USDT:USDT", min_size: float = 5.0) -> str:
    """
    Refactored PRO-Level Wall Velocity Tracker.
    Uses Redis HSET namespacing for microsecond precision state.

    Args:
        symbol: Ticker symbol (e.g. BTC/USDT:USDT).
        min_size: Minimum wall size to track.
    """
    async def _velocity(hub):
        try:
            res = await asyncio.gather(
                hub.fetch_order_book(symbol, limit=20),
                hub.fetch_ticker(symbol)
            )
            ob, ticker = res[0], res[1]
            if not ob or not ticker:
                return json.dumps({"error": "Fetch failed"}, indent=2)
            
            price = ticker['last']
            largest_ask = max(ob['asks'], key=lambda x: x[1]) if ob['asks'] else [0, 0]
            
            curr_t = time.time()
            prev = _redis().get_prev_state(symbol)
            _redis().set_wall_state(symbol, largest_ask[0], largest_ask[1], price)
            
            if not prev: return json.dumps({"status": "INITIALIZED", "p": price}, indent=2)
            
            dt = curr_t - prev['t']
            v_price = (price - prev['tp']) / dt if dt > 0 else 0
            v_wall = (largest_ask[0] - prev['p']) / dt if dt > 0 else 0 # Note: prev['p'] is wall price in hset logic
            
            rel_velocity = v_wall - v_price
            verdict = "STICKY"
            if abs(v_wall) > abs(v_price) * 1.5:
                verdict = "GHOST (Spoofing Detected)"
            elif abs(v_wall) > 0 and abs(rel_velocity) < abs(v_price) * 0.2:
                verdict = "ORGANIC_LADDERING"
                
            return json.dumps({
                "status": "ok",
                "symbol": symbol,
                "v_price": round(v_price, 4),
                "v_wall": round(v_wall, 4),
                "rel_velocity": round(rel_velocity, 4),
                "verdict": verdict,
                "ask_wall": largest_ask
            }, indent=2)
        except Exception as e:
            return json.dumps({"error": str(e)})
            
    return _run_hub_sync(lambda hub: _velocity(hub))

