#!/usr/bin/env python3
"""
funding_actions.py
══════════════════════════════════════════════════════════════════════
Sema4.ai Action Server — Funding + OI + OBI via CCXT puro.

Sin scraping. Sin dependencias del proyecto ccxtv2.
Autocontenido. Datos directos de Binance, Bybit, OKX, Hyperliquid.

Actions disponibles:
  1. get_funding_rates_table       → Funding actual multi-exchange
  2. get_open_int                 → OI actual + ΔOI (30m / 1h)
  3. get_orderbook_imbalance       → OBI top-N niveles
  4. get_full_market_snapshot      → Funding + OI + OBI consolidado
  5. detect_confluence_trigger     → Evalúa reglas de la Skill (sensible/conservadora)
  6. get_funding_history           → Histórico de funding (últimas N velas)
  7. get_zscore_vs_history         → Z-Score del funding actual vs histórico

Start:
    action-server start --port 8082 --dir . --auto-reload

Author: AI-Ops Stack · 2026
"""

import asyncio
import json
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import ccxt
import numpy as np
from sema4ai.actions import action

# ── Directorio de datos persistidos ───────────────────────────────
_DATA_DIR = Path(__file__).parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
_STATE_FILE = _DATA_DIR / "oi_history.json"

# ── Ventana de historia OI en memoria (30 observaciones ≈ 5-7.5 min) ──
_OI_HISTORY: dict[str, deque] = {}   # key: "exchange:symbol" → deque de floats
_FUNDING_HISTORY: dict[str, deque] = {}  # key: "exchange:symbol" → deque de floats
_HISTORY_MAXLEN = 30


# ══════════════════════════════════════════════════════════════════
# HELPERS INTERNOS — Instancias CCXT y normalización de símbolos
# ══════════════════════════════════════════════════════════════════

# ── Símbolos universales con auto-deteccion ──────────────────────
# Fallback dinámico si el asset no está en la lista hardcodeada.

SYMBOLS: dict[str, dict[str, str]] = {
    "binance": {
        "BTC": "BTC/USDT:USDT",
        "ETH": "ETH/USDT:USDT",
        "SOL": "SOL/USDT:USDT",
        "LINK": "LINK/USDT:USDT",
        "TON": "TON/USDT:USDT",
        "TST": "TST/USDT:USDT",
    },
    "bybit": {
        "BTC": "BTC/USDT:USDT",
        "ETH": "ETH/USDT:USDT",
        "SOL": "SOL/USDT:USDT",
        "LINK": "LINK/USDT:USDT",
        "HYPE": "HYPE/USDT:USDT",
        "TON": "TON/USDT:USDT",
    },
    "okx": {
        "BTC": "BTC/USDT:USDT",
        "ETH": "ETH/USDT:USDT",
        "SOL": "SOL/USDT:USDT",
        "LINK": "LINK/USDT:USDT",
        "TON": "TON/USDT:USDT",
    },
    "hyperliquid": {
        "BTC": "BTC/USDC:USDC",
        "ETH": "ETH/USDC:USDC",
        "SOL": "SOL/USDC:USDC",
        "HYPE": "HYPE/USDC:USDC",
    },
}


def universal_symbol_mapper(asset: str, exchange: str) -> str:
    """
    Universal symbol normalizer. Tries hardcoded SYMBOLS first,
    then falls back to dynamic CCXT format detection.
    
    Format per exchange:
      - Binance/Bybit/OKX: ASSET/USDT:USDT
      - Hyperliquid: ASSET/USDC:USDC
    
    Returns:
        CCXT-compatible symbol string or empty string if unresolvable.
    """
    asset = asset.upper().strip()
    
    # 1. Hardcoded symbols (fast path)
    hardcoded = SYMBOLS.get(exchange, {}).get(asset)
    if hardcoded:
        return hardcoded
    
    # 2. Dynamic fallback: construct symbol from exchange convention
    try:
        if exchange == "hyperliquid":
            candidate = f"{asset}/USDC:USDC"
        else:
            candidate = f"{asset}/USDT:USDT"
        
        # Quick validation: try to create exchange and check if symbol exists
        import ccxt
        cls = getattr(ccxt, exchange)
        ex = cls({"enableRateLimit": True, "timeout": 3000})
        try:
            ex.load_markets()
            if candidate in ex.markets:
                return candidate
            # Try alternate casing
            for mkt in ex.markets:
                if mkt.startswith(f"{asset}/"):
                    return mkt
        finally:
            pass  # Don't cache the exchange instance
    except Exception:
        pass
    
    return ""  # Unresolvable


# Legacy compatibility
def _get_symbol(asset: str, exchange: str) -> str:
    """Get symbol with universal mapper fallback."""
    return universal_symbol_mapper(asset, exchange)

# Params adicionales por exchange (necesarios para algunos endpoints)
EXTRA_PARAMS: dict[str, dict] = {
    "binance":      {},
    "bybit":        {},
    "okx":          {},
    "hyperliquid":  {},
}


def _make_exchange(name: str) -> ccxt.Exchange:
    """Crea una instancia CCXT con configuración estándar."""
    cls = getattr(ccxt, name)
    return cls({
        "enableRateLimit": True,
        "timeout": 5000,
        "options": {
            "defaultType": "swap",
        },
    })


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _pct(val: float) -> float:
    """Convierte rate decimal a porcentaje, redondeado a 6 decimales."""
    return round(val * 100, 6)


def _oi_key(exchange: str, symbol: str) -> str:
    return f"{exchange}:{symbol}"


def _push_history(store: dict, key: str, value: float) -> None:
    if key not in store:
        store[key] = deque(maxlen=_HISTORY_MAXLEN)
    store[key].append(value)


def _load_oi_state() -> dict:
    if _STATE_FILE.exists():
        try:
            with open(_STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_oi_state(state: dict) -> None:
    try:
        with open(_STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception:
        pass


def _sync_oi_history_load() -> None:
    global _OI_HISTORY
    loaded = _load_oi_state()
    if loaded:
        for key, val in loaded.items():
            _OI_HISTORY[key] = deque(val, maxlen=_HISTORY_MAXLEN)


def _sync_oi_history_save() -> None:
    state_to_save = {}
    for key, deq in _OI_HISTORY.items():
        state_to_save[key] = list(deq)
    _save_oi_state(state_to_save)


# ══════════════════════════════════════════════════════════════════
# LÓGICA DE FETCH — Funciones síncronas envueltas para asyncio
# ══════════════════════════════════════════════════════════════════

def _fetch_funding_one(exchange_name: str, asset: str) -> dict:
    """Obtiene funding rate actual de un exchange/asset."""
    symbol = SYMBOLS.get(exchange_name, {}).get(asset)
    if not symbol:
        return {"exchange": exchange_name, "asset": asset,
                "error": f"Symbol not configured for {exchange_name}"}
    try:
        ex = _make_exchange(exchange_name)
        
        # Eliminado ex.load_markets() para latencia ultra-baja (<2s)

        # Hyperliquid usa fetchFundingRate (singular), los demás también
        fr_data = ex.fetch_funding_rate(symbol)

        rate = _safe_float(fr_data.get("fundingRate"))
        next_rate = _safe_float(fr_data.get("nextFundingRate"))
        next_time = fr_data.get("nextFundingDatetime") or fr_data.get("fundingDatetime")

        return {
            "exchange":       exchange_name,
            "asset":          asset,
            "symbol":         symbol,
            "funding_rate":   _pct(rate),       # en %
            "next_rate":      _pct(next_rate) if next_rate else None,
            "next_funding":   next_time,
            "timestamp":      _ts(),
            "error":          None,
        }
    except Exception as e:
        return {"exchange": exchange_name, "asset": asset,
                "symbol": symbol, "error": str(e)}


def _fetch_oi_one(exchange_name: str, asset: str) -> dict:
    """Obtiene Open Interest actual de un exchange/asset."""
    symbol = SYMBOLS.get(exchange_name, {}).get(asset)
    if not symbol:
        return {"exchange": exchange_name, "asset": asset,
                "error": f"Symbol not configured for {exchange_name}"}
    try:
        ex = _make_exchange(exchange_name)
        
        # Eliminado ex.load_markets() para máxima velocidad

        oi_data = ex.fetch_open_interest(symbol)

        # CCXT unifica en openInterest (en contratos) y openInterestValue (en USD)
        oi_contracts = _safe_float(oi_data.get("openInterest"))
        oi_usd       = _safe_float(oi_data.get("openInterestValue"))

        # Guardar en historia para calcular delta
        key = _oi_key(exchange_name, asset)
        _push_history(_OI_HISTORY, key, oi_usd if oi_usd > 0 else oi_contracts)

        history = list(_OI_HISTORY[key])
        delta_30m = None
        delta_1h  = None

        # 10s de polling → 30m ≈ 180 obs, 1h ≈ 360 obs
        # Pero con maxlen=30 guardamos las últimas 30 lecturas
        # → con polling de 10s esto son ~5 min de historia
        # → con polling de 60s esto son ~30 min
        # El trigger evalúa proporcional a la historia disponible
        if len(history) >= 2:
            prev = history[0]
            curr = history[-1]
            delta_30m = round(((curr - prev) / prev) * 100, 4) if prev > 0 else None

        return {
            "exchange":       exchange_name,
            "asset":          asset,
            "symbol":         symbol,
            "oi_contracts":   oi_contracts,
            "oi_usd":         oi_usd,
            "delta_pct":      delta_30m,   # % cambio respecto a inicio de ventana
            "history_len":    len(history),
            "timestamp":      _ts(),
            "error":          None,
        }
    except Exception as e:
        return {"exchange": exchange_name, "asset": asset,
                "symbol": symbol, "error": str(e)}


def _fetch_obi_one(exchange_name: str, asset: str, depth: int = 50) -> dict:
    """
    Obtiene Order Book Imbalance (OBI) en top-N niveles.
    OBI = (bid_vol - ask_vol) / (bid_vol + ask_vol)
    Rango: [-1, +1]. |OBI| > 0.40 = presión significativa.
    """
    symbol = universal_symbol_mapper(asset, exchange_name)
    if not symbol:
        return {"exchange": exchange_name, "asset": asset,
                "error": f"Symbol '{asset}' not found on {exchange_name} — try different exchange or check perpetual availability"}
    try:
        ex = _make_exchange(exchange_name)
        
        # Eliminado ex.load_markets()

        ob = ex.fetch_order_book(symbol, limit=depth)

        bids = ob.get("bids", [])[:depth]   # [[price, size], ...]
        asks = ob.get("asks", [])[:depth]

        bid_vol = sum(b[1] for b in bids if len(b) >= 2)
        ask_vol = sum(a[1] for a in asks if len(a) >= 2)
        total   = bid_vol + ask_vol

        obi = (bid_vol - ask_vol) / total if total > 0 else 0.0

        # Precio mid
        best_bid = bids[0][0] if bids else None
        best_ask = asks[0][0] if asks else None
        mid = ((best_bid + best_ask) / 2) if (best_bid and best_ask) else None

        return {
            "exchange":   exchange_name,
            "asset":      asset,
            "symbol":     symbol,
            "obi":        round(obi, 6),
            "bid_volume": round(bid_vol, 4),
            "ask_volume": round(ask_vol, 4),
            "best_bid":   best_bid,
            "best_ask":   best_ask,
            "mid_price":  round(mid, 2) if mid else None,
            "depth":      depth,
            "pressure":   "BUY" if obi > 0.1 else ("SELL" if obi < -0.1 else "NEUTRAL"),
            "timestamp":  _ts(),
            "error":      None,
        }
    except Exception as e:
        return {"exchange": exchange_name, "asset": asset,
                "symbol": symbol, "error": str(e)}


def _fetch_funding_history_one(exchange_name: str, asset: str, limit: int = 30) -> dict:
    """Obtiene histórico de funding rates (velas de funding)."""
    symbol = SYMBOLS.get(exchange_name, {}).get(asset)
    if not symbol:
        return {"exchange": exchange_name, "asset": asset,
                "error": f"Symbol not configured for {exchange_name}"}
    try:
        ex = _make_exchange(exchange_name)
        
        # Eliminado ex.load_markets()

        # fetchFundingRateHistory devuelve lista de dicts con timestamp + fundingRate
        history = ex.fetch_funding_rate_history(symbol, limit=limit)

        records = []
        rates   = []
        for h in history[-limit:]:
            r = _safe_float(h.get("fundingRate"))
            rates.append(r)
            records.append({
                "datetime":     h.get("datetime"),
                "funding_rate": _pct(r),
            })

        # Z-Score del último valor vs la ventana
        arr = np.array(rates)
        zscore = None
        if len(arr) >= 3:
            mean = arr.mean()
            std  = arr.std()
            zscore = float((arr[-1] - mean) / std) if std > 1e-9 else 0.0

        return {
            "exchange":        exchange_name,
            "asset":           asset,
            "symbol":          symbol,
            "history":         records,
            "count":           len(records),
            "current_zscore":  round(zscore, 4) if zscore is not None else None,
            "mean_pct":        round(float(arr.mean()) * 100, 6) if len(arr) else None,
            "std_pct":         round(float(arr.std()) * 100, 6) if len(arr) else None,
            "timestamp":       _ts(),
            "error":           None,
        }
    except Exception as e:
        return {"exchange": exchange_name, "asset": asset,
                "symbol": symbol, "error": str(e)}


# ══════════════════════════════════════════════════════════════════
# LÓGICA DE TRIGGER (Skill: versión sensible + conservadora)
# ══════════════════════════════════════════════════════════════════

def _evaluate_triggers(snapshot: dict) -> dict:
    """
    Evalúa las reglas de confluencia de la Skill sobre un snapshot completo.

    Versión Sensible:  (|ΔFunding| > 0.005% OR |OBI| > 0.40) AND ΔOI > 5%
    Versión Conservadora: |Funding| > 0.01% AND |ΔFunding| > 0.005%
                          AND ΔOI > 10% AND |OBI| > 0.45

    Umbral ΔFunding estimado: diferencia entre funding actual y media histórica.
    """
    results = {}

    # Discover all assets present in the snapshot (dynamic — works for BTC,ETH,SOL,HYPE,LINK...)
    active_assets = set()
    for key in snapshot.get("funding", {}):
        parts = key.split(":")
        if len(parts) == 2:
            active_assets.add(parts[1])
    if not active_assets:
        active_assets = {"BTC", "ETH"}  # fallback

    for asset in sorted(active_assets):
        # Recopilar valores por asset (promedio ponderado multi-exchange)
        funding_vals  = []
        delta_funding = []
        oi_deltas     = []
        obi_vals      = []

        for ex_name in ["binance", "bybit", "okx", "hyperliquid"]:
            key = f"{ex_name}:{asset}"

            # Funding
            fr = snapshot.get("funding", {}).get(key, {})
            if not fr.get("error") and fr.get("funding_rate") is not None:
                funding_vals.append(fr["funding_rate"])

            # OI delta
            oi = snapshot.get("oi", {}).get(key, {})
            if not oi.get("error") and oi.get("delta_pct") is not None:
                oi_deltas.append(oi["delta_pct"])

            # OBI
            ob = snapshot.get("obi", {}).get(key, {})
            if not ob.get("error") and ob.get("obi") is not None:
                obi_vals.append(abs(ob["obi"]))

        # Calcular medias
        avg_funding  = float(np.mean(funding_vals))  if funding_vals else 0.0
        avg_oi_delta = float(np.mean(oi_deltas))     if oi_deltas   else 0.0
        max_obi      = float(max(obi_vals))           if obi_vals    else 0.0

        # ΔFunding estimado: |funding actual - 0.01%| (baseline de equilibrio)
        delta_f = abs(avg_funding - 0.01)

        # Evaluar triggers
        sensitive = (
            (delta_f > 0.005 or max_obi > 0.40)
            and avg_oi_delta > 5.0
        )
        conservative = (
            abs(avg_funding) > 0.01
            and delta_f > 0.005
            and avg_oi_delta > 10.0
            and max_obi > 0.45
        )

        # Tipo de trade sugerido
        if conservative:
            trade_type = "SWING/POSITION — alta convicción"
            trigger_level = "CONSERVATIVE"
        elif sensitive:
            trade_type = "SCALP — señal temprana"
            trigger_level = "SENSITIVE"
        else:
            trade_type = "NO SIGNAL"
            trigger_level = "NONE"

        # Dirección: funding positivo → longs pagan → sesgo short
        direction = "SHORT_BIAS" if avg_funding > 0.01 else (
            "LONG_BIAS" if avg_funding < -0.01 else "NEUTRAL"
        )

        results[asset] = {
            "avg_funding_pct":   round(avg_funding, 6),
            "delta_funding_pct": round(delta_f, 6),
            "avg_oi_delta_pct":  round(avg_oi_delta, 4),
            "max_obi":           round(max_obi, 6),
            "trigger_sensitive": sensitive,
            "trigger_conservative": conservative,
            "trigger_level":     trigger_level,
            "trade_type":        trade_type,
            "direction":         direction,
            "action_required":   sensitive or conservative,
        }

    return results


# ── NUEVOS HELPERS DE ANÁLISIS PROFUNDO ───────────────────────────────

def _fetch_spot_price(exchange_name: str, asset: str) -> float:
    """Helper rápido para obtener precio spot."""
    try:
        ex = _make_exchange(exchange_name)
        # Usamos el ticker spot estándar
        spot_symbol = f"{asset}/USDT"
        ticker = ex.fetch_ticker(spot_symbol)
        return float(ticker.get('last'))
    except:
        return 0.0

def _calculate_spotdiff(exchange_name: str, asset: str, perp_price: float) -> dict:
    """
    Calcula el Basis (Spotdiff).
    Basis = (Perp - Spot) / Spot.
    Un Basis alto indica oportunidad de arbitraje o Funding extremo.
    """
    spot_price = _fetch_spot_price(exchange_name, asset)
    if spot_price > 0:
        basis_pct = ((perp_price - spot_price) / spot_price) * 100
        return {
            "exchange": exchange_name,
            "spot": spot_price,
            "perp": perp_price,
            "basis_pct": round(basis_pct, 4),
            "signal": "LONG_PREMIUM" if basis_pct > 0.05 else ("SHORT_PREMIUM" if basis_pct < -0.05 else "FAIR_VALUE")
        }
    return {"error": "Could not fetch spot price"}

def _generate_heatmap_data(order_book: dict) -> dict:
    """
    Genera datos simplificados para un heatmap de liquidez (Bid/Ask walls).
    Agrupa volúmenes en niveles de precio redondos.
    """
    bids = order_book.get("bids", [])
    asks = order_book.get("asks", [])
    
    if not isinstance(bids, list) or (bids and not isinstance(bids[0], (list, tuple))):
        return {"bid_walls": [], "ask_walls": [], "imbalance_score": "UNKNOWN"}
        
    bid_walls = sorted(bids, key=lambda x: x[1], reverse=True)[:3]
    ask_walls = sorted(asks, key=lambda x: x[1], reverse=True)[:3]
    
    return {
        "bid_walls": [{"price": b[0], "volume": b[1]} for b in bid_walls],
        "ask_walls": [{"price": a[0], "volume": a[1]} for a in ask_walls],
        "imbalance_score": "HEAVY_BIDS" if bid_walls and ask_walls and sum(b[1] for b in bid_walls) > sum(a[1] for a in ask_walls) else "HEAVY_ASKS"
    }


# ══════════════════════════════════════════════════════════════════
# ACTIONS PÚBLICAS
# ══════════════════════════════════════════════════════════════════

@action(is_consequential=False)
def get_funding_rates_table(assets: str = "BTC,ETH") -> str:
    """
    Obtiene el funding rate actual para BTC y/o ETH desde
    Binance, Bybit, OKX y Hyperliquid vía CCXT.
    Sin scraping. Datos directos de cada exchange.

    Args:
        assets: Activos separados por coma. Default: "BTC,ETH"

    Returns:
        JSON con funding rate (en %) por exchange y asset,
        próximo funding, timestamp y errores si los hay.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    asset_list = [a.strip().upper() for a in assets.split(",")]
    exchanges  = ["binance", "bybit", "okx", "hyperliquid"]
    results    = {}

    tasks = []
    for ex in exchanges:
        for asset in asset_list:
            tasks.append((ex, asset))

    with ThreadPoolExecutor(max_workers=12) as executor:
        future_to_key = {
            executor.submit(_fetch_funding_one, ex, asset): f"{ex}:{asset}"
            for ex, asset in tasks
        }
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                results[key] = future.result()
            except Exception as e:
                results[key] = {"error": str(e)}

    # Tabla resumen plana para el LLM
    table = []
    for ex in exchanges:
        row = {"exchange": ex}
        for asset in asset_list:
            key = f"{ex}:{asset}"
            r   = results.get(key, {})
            row[asset] = r.get("funding_rate") if not r.get("error") else f"ERR: {r.get('error', '')[:40]}"
        table.append(row)

    return json.dumps({
        "status":    "ok",
        "timestamp": _ts(),
        "assets":    asset_list,
        "table":     table,         # vista compacta para el LLM
        "detail":    results,       # detalle completo por key
    }, indent=2, default=str)


@action(is_consequential=False)
def get_open_int(assets: str = "BTC,ETH") -> str:
    """
    Obtiene el Open Interest actual desde los 4 exchanges,
    calcula ΔOI respecto a la historia en memoria.

    Los primeros N polls construyen historia. A partir de la
    segunda llamada ya hay delta disponible.

    Args:
        assets: Activos separados por coma. Default: "BTC,ETH"

    Returns:
        JSON con OI en contratos + USD, delta %, historia acumulada.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    _sync_oi_history_load()

    asset_list = [a.strip().upper() for a in assets.split(",")]
    exchanges  = ["binance", "bybit", "okx", "hyperliquid"]
    results    = {}

    with ThreadPoolExecutor(max_workers=12) as executor:
        future_to_key = {
            executor.submit(_fetch_oi_one, ex, asset): f"{ex}:{asset}"
            for ex in exchanges for asset in asset_list
        }
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                results[key] = future.result()
            except Exception as e:
                results[key] = {"error": str(e)}

    _sync_oi_history_save()

    return json.dumps({
        "status":    "ok",
        "timestamp": _ts(),
        "assets":    asset_list,
        "detail":    results,
    }, indent=2, default=str)


@action(is_consequential=False)
def get_orderbook_imbalance(assets: str = "BTC,ETH", depth: int = 50) -> str:
    """
    Calcula el Order Book Imbalance (OBI) en los top-N niveles.

    OBI = (bid_vol - ask_vol) / (bid_vol + ask_vol)
    Rango [-1, +1]. |OBI| > 0.40 = presión de liquidez significativa.

    Args:
        assets: Activos separados por coma. Default: "BTC,ETH"
        depth:  Niveles del libro a considerar. Default: 50

    Returns:
        JSON con OBI, volúmenes, precio mid, presión direccional.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    asset_list = [a.strip().upper() for a in assets.split(",")]
    exchanges  = ["binance", "bybit", "okx", "hyperliquid"]
    results    = {}

    with ThreadPoolExecutor(max_workers=12) as executor:
        future_to_key = {
            executor.submit(_fetch_obi_one, ex, asset, depth): f"{ex}:{asset}"
            for ex in exchanges for asset in asset_list
        }
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                results[key] = future.result()
            except Exception as e:
                results[key] = {"error": str(e)}

    # Resumen de presión dominante por asset
    summary = {}
    for asset in asset_list:
        obi_vals = []
        for ex in exchanges:
            r = results.get(f"{ex}:{asset}", {})
            if not r.get("error") and r.get("obi") is not None:
                obi_vals.append(r["obi"])
        if obi_vals:
            avg_obi = float(np.mean(obi_vals))
            summary[asset] = {
                "avg_obi":  round(avg_obi, 6),
                "max_abs":  round(max(abs(v) for v in obi_vals), 6),
                "pressure": "BUY" if avg_obi > 0.10 else ("SELL" if avg_obi < -0.10 else "NEUTRAL"),
                "spike":    max(abs(v) for v in obi_vals) > 0.40,
            }

    return json.dumps({
        "status":    "ok",
        "timestamp": _ts(),
        "depth":     depth,
        "summary":   summary,
        "detail":    results,
    }, indent=2, default=str)


@action(is_consequential=False)
def get_full_market_snapshot(assets: str = "BTC,ETH", ob_depth: int = 50) -> str:
    """
    Snapshot completo: Funding + OI + OBI en una sola llamada.
    Llama a los 3 endpoints en paralelo (threading interno).
    Ideal para un briefing rápido antes de abrir posición.

    Args:
        assets:   Activos separados por coma. Default: "BTC,ETH"
        ob_depth: Niveles del order book. Default: 50

    Returns:
        JSON consolidado con todos los indicadores + evaluación de triggers.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    _sync_oi_history_load()

    asset_list = [a.strip().upper() for a in assets.split(",")]
    exchanges  = ["binance", "bybit", "okx", "hyperliquid"]

    funding_data = {}
    oi_data      = {}
    obi_data     = {}

    tasks = []
    for ex in exchanges:
        for asset in asset_list:
            tasks.append(("funding", ex, asset))
            tasks.append(("oi",      ex, asset))
            tasks.append(("obi",     ex, asset))

    def _run_task(task_type, ex, asset):
        key = f"{ex}:{asset}"
        if task_type == "funding":
            return ("funding", key, _fetch_funding_one(ex, asset))
        elif task_type == "oi":
            return ("oi",      key, _fetch_oi_one(ex, asset))
        else:
            return ("obi",     key, _fetch_obi_one(ex, asset, depth=ob_depth))

    with ThreadPoolExecutor(max_workers=min(len(tasks), 12)) as pool:
        futures = [pool.submit(_run_task, *t) for t in tasks]
        for fut in as_completed(futures):
            kind, key, data = fut.result()
            if kind == "funding":
                funding_data[key] = data
            elif kind == "oi":
                oi_data[key] = data
            else:
                obi_data[key] = data

    snapshot = {"funding": funding_data, "oi": oi_data, "obi": obi_data}
    triggers = _evaluate_triggers(snapshot)

    _sync_oi_history_save()

    return json.dumps({
        "status":    "ok",
        "timestamp": _ts(),
        "assets":    asset_list,
        "triggers":  triggers,
        "funding":   funding_data,
        "oi":        oi_data,
        "obi":       obi_data,
    }, indent=2, default=str)


@action(is_consequential=False)
def detect_confluence_trigger(assets: str = "BTC,ETH", ob_depth: int = 50) -> str:
    """
    Evalúa las reglas de confluencia de la Skill sobre datos frescos.

    Regla Sensible:      (|ΔFunding| > 0.005% OR |OBI| > 0.40) AND ΔOI > 5%
    Regla Conservadora:  |Funding| > 0.01% AND |ΔFunding| > 0.005%
                         AND ΔOI > 10% AND |OBI| > 0.45

    Si se activa un trigger, devuelve tipo de trade sugerido y dirección.
    El trader decide siempre — el sistema solo prepara el dossier de decisión.

    Args:
        assets:   Activos a evaluar. Default: "BTC,ETH"
        ob_depth: Niveles de order book. Default: 50

    Returns:
        JSON con resultado de triggers por asset + indicadores usados.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    _sync_oi_history_load()

    asset_list = [a.strip().upper() for a in assets.split(",")]
    exchanges  = ["binance", "bybit", "okx", "hyperliquid"]

    funding_data = {}
    oi_data      = {}
    obi_data     = {}

    tasks = []
    for ex in exchanges:
        for asset in asset_list:
            tasks.append(("funding", ex, asset))
            tasks.append(("oi",      ex, asset))
            tasks.append(("obi",     ex, asset))

    def _run(task_type, ex, asset):
        key = f"{ex}:{asset}"
        if task_type == "funding":
            return ("funding", key, _fetch_funding_one(ex, asset))
        elif task_type == "oi":
            return ("oi",      key, _fetch_oi_one(ex, asset))
        else:
            return ("obi",     key, _fetch_obi_one(ex, asset, depth=ob_depth))

    with ThreadPoolExecutor(max_workers=12) as pool:
        for kind, key, data in [f.result() for f in
                                  as_completed([pool.submit(_run, *t) for t in tasks])]:
            if kind == "funding":
                funding_data[key] = data
            elif kind == "oi":
                oi_data[key] = data
            else:
                obi_data[key] = data

    snapshot = {"funding": funding_data, "oi": oi_data, "obi": obi_data}
    triggers = _evaluate_triggers(snapshot)

    # Filtrar solo los activos con señal activa para respuesta limpia
    active = {a: v for a, v in triggers.items() if v["action_required"]}

    # --- NUEVO: Ejecutar análisis profundo si hay trigger ---
    deep_analysis = {}
    
    if active:
        print(f"[DEBUG] Trigger activo. Ejecutando SpotDiff y Heatmap...")
        
        analysis_tasks = []
        for asset in active.keys():
            for ex_name in ["binance", "bybit", "okx"]:
                # 1. SpotDiff
                perp_data = funding_data.get(f"{ex_name}:{asset}")
                if perp_data and not perp_data.get("error"):
                    ob_data = obi_data.get(f"{ex_name}:{asset}")
                    mid = ob_data.get("mid_price") if ob_data else 0
                    if mid > 0:
                        analysis_tasks.append(("spotdiff", ex_name, asset, mid))

                # 2. Heatmap
                ob_data = obi_data.get(f"{ex_name}:{asset}")
                if ob_data and not ob_data.get("error"):
                    hm_data = _generate_heatmap_data({
                        "bids": ob_data.get("bid_volume", []), 
                        "asks": ob_data.get("ask_volume", []) 
                    })
                    deep_analysis[f"{ex_name}:{asset}:heatmap"] = hm_data

        def run_spotdiff(ex, asset, perp_price):
            return ("spotdiff", f"{ex}:{asset}", _calculate_spotdiff(ex, asset, perp_price))

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [pool.submit(run_spotdiff, ex, asset, price) for _, ex, asset, price in analysis_tasks]
            for f in as_completed(futures):
                kind, key, data = f.result()
                deep_analysis[key] = data
    # --- FIN NUEVO ---

    # Guardar dossier si hay trigger activo
    if active:
        dossier_path = _DATA_DIR / f"trigger_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        with open(dossier_path, "w") as f:
            json.dump({
                "timestamp": _ts(),
                "triggers":  triggers,
                "snapshot":  snapshot,
                "deep_analysis": deep_analysis,
            }, f, indent=2, default=str)

    _sync_oi_history_save()

    return json.dumps({
        "status":          "ok",
        "timestamp":       _ts(),
        "any_signal":      bool(active),
        "active_triggers": active,
        "all_triggers":    triggers,
        "deep_analysis":   deep_analysis if active else "No active triggers to analyze",
    }, indent=2, default=str)


@action(is_consequential=False)
def get_funding_history(
    exchange: str = "binance",
    asset:    str = "BTC",
    limit:    int = 30,
) -> str:
    """
    Obtiene el histórico de funding rates de un exchange/asset.
    Calcula Z-Score del valor actual vs la ventana histórica.

    Un |Z-Score| > 2.0 indica funding en territorio de spike estadístico.

    Args:
        exchange: binance | bybit | okx | hyperliquid. Default: binance
        asset:    BTC | ETH. Default: BTC
        limit:    Número de velas de funding a recuperar. Default: 30

    Returns:
        JSON con histórico, mean, std, z-score actual, dirección.
    """
    data = _fetch_funding_history_one(exchange.lower(), asset.upper(), limit=limit)

    # Interpretación del z-score
    z = data.get("current_zscore")
    if z is not None:
        if abs(z) >= 3.0:
            severity = "EXTREME"
        elif abs(z) >= 2.0:
            severity = "HIGH"
        elif abs(z) >= 1.5:
            severity = "ELEVATED"
        else:
            severity = "NORMAL"
        data["zscore_severity"] = severity
        data["zscore_direction"] = "POSITIVE_SPIKE" if z > 0 else "NEGATIVE_SPIKE" if z < 0 else "NEUTRAL"

    return json.dumps({
        "status": "ok",
        **data,
    }, indent=2, default=str)


@action(is_consequential=False)
def get_zscore_vs_history(assets: str = "BTC,ETH") -> str:
    """
    Calcula el Z-Score del funding actual versus el histórico de las
    últimas 30 velas de funding para todos los exchanges configurados.

    Útil para detectar desviaciones estadísticas significativas
    que justifican entrar en el flujo de análisis profundo.

    Args:
        assets: Activos separados por coma. Default: "BTC,ETH"

    Returns:
        JSON con z-score por exchange/asset, severidad y dirección.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    asset_list = [a.strip().upper() for a in assets.split(",")]
    exchanges  = ["binance", "bybit", "okx", "hyperliquid"]
    results    = {}

    with ThreadPoolExecutor(max_workers=12) as executor:
        future_to_key = {
            executor.submit(_fetch_funding_history_one, ex, asset, 30): f"{ex}:{asset}"
            for ex in exchanges for asset in asset_list
        }
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                data = future.result()
            except Exception as e:
                data = {"error": str(e)}
                
            z = data.get("current_zscore")
            ex, asset = key.split(":")
            results[key] = {
                "exchange":   ex,
                "asset":      asset,
                "zscore":     z,
                "severity":   (
                    "EXTREME" if z and abs(z) >= 3.0 else
                    "HIGH"    if z and abs(z) >= 2.0 else
                    "ELEVATED" if z and abs(z) >= 1.5 else
                    "NORMAL"
                ) if z is not None else "UNKNOWN",
                "mean_pct":   data.get("mean_pct"),
                "std_pct":    data.get("std_pct"),
                "error":      data.get("error"),
            }

    # Destacar los que merecen atención
    alerts = {k: v for k, v in results.items()
              if v.get("zscore") and abs(v["zscore"]) >= 2.0}

    return json.dumps({
        "status":    "ok",
        "timestamp": _ts(),
        "assets":    asset_list,
        "alerts":    alerts,
        "detail":    results,
    }, indent=2, default=str)


@action(is_consequential=False)
def get_tactical_report(assets: str = "BTC,ETH", strategy: str = "scalp") -> str:
    """
    Analizador de microestructura dual para activos crypto. 
    Proporciona inteligencia de mercado bifurcada en dos horizontes temporales.

    Args:
        assets: Lista de activos separados por coma (ej. 'BTC,ETH').
        strategy: 'scalp' para análisis de OrderBook/OBI en tiempo real o 'swing' para 
                  análisis de acumulación de OI y Z-Score de 48h.

    Returns:
        Un string JSON con el reporte táctico detallado, señales de entrada y régimen de mercado.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    _sync_oi_history_load()
    
    asset_list = [a.strip().upper() for a in assets.split(",")]
    exchanges  = ["binance", "bybit", "okx", "hyperliquid"]
    
    results = {
        "status": "ok",
        "strategy": strategy.upper(),
        "timestamp": _ts(),
        "assets": asset_list,
        "detail": {},
        "deep_analysis": {}
    }

    if strategy.lower() == "scalp":
        obi_data = {}
        funding_data = {}
        
        def _run_scalp(task_type, ex, asset):
            key = f"{ex}:{asset}"
            if task_type == "obi":
                return ("obi", key, _fetch_obi_one(ex, asset, depth=20))
            elif task_type == "funding":
                return ("funding", key, _fetch_funding_one(ex, asset))

        tasks = []
        for ex in exchanges:
            for asset in asset_list:
                tasks.append(("obi", ex, asset))
                tasks.append(("funding", ex, asset))

        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(_run_scalp, *t) for t in tasks]
            for f in as_completed(futures):
                try:
                    kind, key, data = f.result()
                    if kind == "obi": obi_data[key] = data
                    elif kind == "funding": funding_data[key] = data
                except Exception:
                    pass

        active_scalp = []
        for asset in asset_list:
            max_obi = 0.0
            for ex in exchanges:
                ob = obi_data.get(f"{ex}:{asset}", {})
                if not ob.get("error") and ob.get("obi") is not None:
                    max_obi = max(max_obi, abs(ob["obi"]))
            
            results["detail"][asset] = {
                "max_obi": round(max_obi, 4),
                "trigger_scalp": max_obi > 0.45
            }
            if max_obi > 0.45:
                active_scalp.append(asset)
                
        if active_scalp:
            analysis_tasks = []
            for asset in active_scalp:
                for ex in ["binance", "bybit", "okx"]:
                    ob = obi_data.get(f"{ex}:{asset}")
                    if ob and not ob.get("error"):
                        mid = ob.get("mid_price", 0)
                        if mid > 0:
                            analysis_tasks.append((ex, asset, mid))
            
            with ThreadPoolExecutor(max_workers=6) as pool:
                def _do_spotdiff(ex, a, m):
                    key = f"{ex}:{a}"
                    return (key, _calculate_spotdiff(ex, a, m))
                
                deep = {}
                futures = [pool.submit(_do_spotdiff, *t) for t in analysis_tasks]
                for f in as_completed(futures):
                    try:
                        k, res = f.result()
                        deep[k] = res
                    except Exception:
                        pass
                results["deep_analysis"]["spotdiff"] = deep

    else:
        oi_data = {}
        funding_hist = {}
        
        def _run_swing(task_type, ex, asset):
            key = f"{ex}:{asset}"
            if task_type == "oi":
                return ("oi", key, _fetch_oi_one(ex, asset))
            elif task_type == "history":
                return ("history", key, _fetch_funding_history_one(ex, asset, limit=15))

        tasks = []
        for ex in exchanges:
            for asset in asset_list:
                tasks.append(("oi", ex, asset))
                if ex in ["binance", "bybit"]:
                    tasks.append(("history", ex, asset))

        with ThreadPoolExecutor(max_workers=12) as pool:
            futures = [pool.submit(_run_swing, *t) for t in tasks]
            for f in as_completed(futures):
                try:
                    kind, key, data = f.result()
                    if kind == "oi": oi_data[key] = data
                    elif kind == "history": funding_hist[key] = data
                except Exception:
                    pass

        for asset in asset_list:
            max_oi_delta = 0.0
            for ex in exchanges:
                oi = oi_data.get(f"{ex}:{asset}", {})
                if not oi.get("error") and oi.get("delta_pct") is not None:
                    max_oi_delta = max(max_oi_delta, abs(oi["delta_pct"]))

            hist_avg = 0.0
            max_zscore = 0.0
            for ex in ["binance", "bybit"]:
                fh = funding_hist.get(f"{ex}:{asset}", {})
                if not fh.get("error") and "mean_pct" in fh:
                    hist_avg = max(hist_avg, abs(fh["mean_pct"]))
                if not fh.get("error") and "current_zscore" in fh and fh["current_zscore"] is not None:
                    max_zscore = max(max_zscore, abs(fh["current_zscore"]))

            results["detail"][asset] = {
                "max_oi_delta_pct": round(max_oi_delta, 2),
                "historical_funding_avg": round(hist_avg, 6),
                "max_abs_zscore": round(max_zscore, 2),
                "trigger_swing": max_oi_delta > 10.0 and max_zscore > 2.0
            }

    _sync_oi_history_save()
    return json.dumps(results, indent=2, default=str)


@action(is_consequential=False)
def get_ultra_deep_confluence(assets: str = "BTC,ETH", depth: int = 100) -> str:
    """
    Escaneo de alta resolución (Depth 100) para detectar muros institucionales.
    Incluye contexto de Funding y OI para evitar pérdida de semántica frente a agentes de IA.

    Args:
        assets: Activos a analizar (ej. 'BTC,ETH').
        depth: Niveles de profundidad. Recomendado: 100 para ver muros reales.

    Returns:
        JSON Híbrido con 'summary' (veredicto rápido) y 'evidence' (datos crudos).
    """
    import json
    import numpy as np
    asset_list = [a.strip().upper() for a in assets.split(",")]
    results = {}

    # Obtenemos snapshot total (Funding + OI + OBI depth 100)
    raw_data = get_full_market_snapshot(assets=assets, ob_depth=depth) 
    data = json.loads(raw_data)
    
    funding_data = data.get("funding", {})
    oi_data = data.get("oi", {})
    obi_data = data.get("obi", {})
    
    for asset in asset_list:
        # Extraer OBI validos
        valid_obis = [
            v['obi'] for k, v in obi_data.items() 
            if k.endswith(f":{asset}") and not v.get('error') and v.get('obi') is not None
        ]
        
        if not valid_obis: 
            continue
            
        avg_obi = float(np.mean(valid_obis))
        confluence_count = sum(1 for o in valid_obis if (avg_obi > 0 and o > 0) or (avg_obi < 0 and o < 0))
        confluence_pct = (confluence_count / len(valid_obis)) * 100
        
        # Extraer Funding validos
        valid_funding = [
            v['funding_rate'] for k, v in funding_data.items()
            if k.endswith(f":{asset}") and not v.get('error') and v.get('funding_rate') is not None
        ]
        avg_funding = float(np.mean(valid_funding)) if valid_funding else 0.0
        
        # Extraer OI validos
        valid_oi = [
            v['delta_pct'] for k, v in oi_data.items()
            if k.endswith(f":{asset}") and not v.get('error') and v.get('delta_pct') is not None
        ]
        avg_oi_delta = float(np.mean(valid_oi)) if valid_oi else 0.0

        # Lógica de Veredicto
        is_extreme_obi = abs(avg_obi) > 0.45 and confluence_pct >= 75
        is_high_funding = abs(avg_funding) > 0.01
        is_high_oi = abs(avg_oi_delta) > 5.0
        
        confidence_score = 0
        if abs(avg_obi) > 0.50: confidence_score += 40
        elif abs(avg_obi) > 0.30: confidence_score += 20
        
        if confluence_pct >= 75: confidence_score += 20
        if is_high_funding: confidence_score += 20
        if is_high_oi: confidence_score += 20

        if confidence_score >= 80:
            verdict = "EXTREME_PRESSURE"
        elif confidence_score >= 50:
            verdict = "PRESSURE_BUILDUP"
        else:
            verdict = "NORMAL_FLOW"

        if avg_obi < -0.10: 
            direction = "SHORT_BIAS"
            primary_driver = "OBI_ASK_WALL" if abs(avg_obi) > 0.40 else "MILD_ASK_PRESSURE"
        elif avg_obi > 0.10: 
            direction = "LONG_BIAS"
            primary_driver = "OBI_BID_WALL" if abs(avg_obi) > 0.40 else "MILD_BID_PRESSURE"
        else:
            direction = "NEUTRAL"
            primary_driver = "NONE"

        results[asset] = {
            "summary": {
                "direction": direction,
                "verdict": verdict,
                "confidence_score": confidence_score,
                "confluence_pct": f"{round(confluence_pct, 1)}%",
                "primary_driver": primary_driver
            },
            "evidence": {
                "funding": {
                    "value": round(avg_funding, 6),
                    "status": "OVERHEATED" if is_high_funding else "NEUTRAL"
                },
                "oi_delta": {
                    "value": round(avg_oi_delta, 2),
                    "status": "ACCUMULATING" if is_high_oi else "FLAT"
                },
                "obi": {
                    "value": round(avg_obi, 4),
                    "status": "EXTREME_SELL_PRESSURE" if avg_obi < -0.45 else ("EXTREME_BUY_PRESSURE" if avg_obi > 0.45 else "BALANCED")
                }
            }
        }

    return json.dumps(results, indent=2)
