"""
audit_actions.py — Senior Desk Microstructure Audits
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Action endpoints for deep institutional auditing (Z-Score,
Basis Divergence, OBI sweeps) of active positions or setups.
Consolidates legacy senior_desk_audit, secure_audit, and verify_btc.
"""
import sys
import asyncio
import json
from datetime import datetime, timezone
import pandas as pd
import numpy as np
import logging

from sema4ai.actions import action
# Internal imports moved inside functions to avoid circularity

logger = logging.getLogger("AuditActions")

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import IntelligenceHub, _run_hub_sync
from shared.engines.zscore import ZScoreEngine


async def _microstructure_audit_internal(hub, symbol: str):
    """Internal helper to avoid circular imports in composite workflows."""
    try:
        spot_sym = symbol.split(":")[0] if ":" in symbol else symbol
        
        # Basis Check
        spot_df = await hub.fetch_ohlcv(spot_sym, "5m", limit=2)
        perp_df = await hub.fetch_ohlcv(symbol, "5m", limit=2)
        
        basis_pct = 0.0
        if spot_df is not None and perp_df is not None:
            spot_close = float(spot_df['close'].iloc[-1])
            perp_close = float(perp_df['close'].iloc[-1])
            basis_pct = ((perp_close - spot_close) / spot_close) * 100
            
        # OBI & Depth
        ob = await hub.fetch_order_book(symbol, limit=20)
        obi = 0.0
        if ob:
            bids = sum(b[1] for b in ob.get('bids', []))
            asks = sum(a[1] for a in ob.get('asks', []))
            obi = (bids - asks) / (bids + asks) if (bids+asks)>0 else 0.0
            
        # Quick M5 Z-Score check
        zscore_engine = ZScoreEngine()
        z_m5 = 0.0
        df_m5 = await hub.fetch_ohlcv(symbol, "5m", limit=30)
        if df_m5 is not None and not df_m5.empty:
            df_m5['log_ret'] = pd.Series(df_m5['close'].astype(float)).pct_change().apply(lambda x: np.log1p(x) if pd.notna(x) else 0)
            mean = df_m5['log_ret'].mean()
            std = df_m5['log_ret'].std()
            if std > 0:
                z_m5 = (df_m5['log_ret'].iloc[-1] - mean) / std

        # Trades CVD proxy
        trades = await hub.fetch_trades(symbol, limit=100)
        buy_vol = 0
        sell_vol = 0
        if trades is not None and not trades.empty:
            buys = trades[trades['side'] == 'buy']
            sells = trades[trades['side'] == 'sell']
            buy_vol = (buys['amount'].astype(float) * buys['price'].astype(float)).sum()
            sell_vol = (sells['amount'].astype(float) * sells['price'].astype(float)).sum()
        cvd_delta = buy_vol - sell_vol
            
        return {
            "symbol": symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "microstructure": {
                "obi_20": round(obi, 4),
                "basis_pct": round(basis_pct, 4),
                "zscore_m5": round(z_m5, 2),
                "cvd_100_trades_usd": round(cvd_delta, 2)
            },
            "verdict": "Bullish Confluence" if (obi>0.3 and basis_pct<0 and cvd_delta>0) else 
                       ("Bearish Confluence" if (obi<-0.3 and basis_pct>0 and cvd_delta<0) else "Mixed/Neutral")
        }
    except Exception as e:
        logger.error(f"Error in microstructure audit for {symbol}: {e}")
        return {"symbol": symbol, "error": str(e)}

@action(is_consequential=False)
def microstructure_audit(symbol: str = "BTC/USDT:USDT") -> dict:
    """
    Performs a deep microstructure audit of a single asset.
    Returns basis, z-score, recent CVD logic, and orderbook pressure.
    
    Args:
        symbol: The ticker symbol (e.g. BTC/USDT:USDT).
    """
    return _run_hub_sync(lambda hub: _microstructure_audit_internal(hub, symbol))

@action(is_consequential=False)
def run_scalp_workflow(assets: str = "BTC,ETH") -> dict:
    """
    Composite Routine: High-Frequency Micro-Trend Flow.
    1. Toxicity Index (VPIN).
    2. Microstructure Audit.
    3. Order Book Walls (Depth 20).
    
    Args:
        assets: Comma-separated list of assets (e.g. BTC,ETH).
    """
    async def _routine(hub):
        from .market_actions import _get_toxicity_index_internal, _get_ob_walls_internal
        
        asset_list = assets.split(",")
        results = {}
        for asset in asset_list:
            sym = f"{asset}/USDT:USDT"
            
            # Use internal async helpers directly
            tox_raw = await _get_toxicity_index_internal(hub, sym, 20, 500)
            try:
                tox_data = json.loads(tox_raw)
            except:
                tox_data = tox_raw

            results[asset] = {
                "toxicity": tox_data,
                "microstructure": await _microstructure_audit_internal(hub, sym),
                "walls": await _get_ob_walls_internal(hub, sym, 20)
            }
        
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "assets": asset_list,
            "workflow_results": results,
            "verdict": "SCALP_WORKFLOW_COMPLETE"
        }

    return _run_hub_sync(_routine)

@action(is_consequential=False)
def run_intraday_workflow(assets: str = "BTC,ETH") -> dict:
    """
    Composite Routine: Daily Session Capture Flow.
    1. Basis (Spot vs Perp).
    2. Tactical Reports (Scalp + Swing modes).
    3. Ultra-Deep Confluence (Depth 100).
    
    Args:
        assets: Comma-separated list of assets (e.g. BTC,ETH).
    """
    async def _routine(hub):
        from .market_actions import _get_basis_internal
        from .funding_actions import get_ultra_deep_confluence, get_tactical_report
        
        asset_list = assets.split(",")
        results = {}
        for asset in asset_list:
            results[asset] = {
                "basis": await _get_basis_internal(hub, f"{asset}/USDT", f"{asset}/USDT:USDT")
            }
        
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "assets": asset_list,
            "basis_data": results,
            "tactical": {
                "scalp": get_tactical_report(assets, "scalp"),
                "swing": get_tactical_report(assets, "swing")
            },
            "udc_walls": get_ultra_deep_confluence(assets, 100),
            "verdict": "INTRADAY_WORKFLOW_COMPLETE"
        }

    return _run_hub_sync(_routine)

@action(is_consequential=False)
def eth_ele_audit(symbol: str = "ETH/USDT:USDT") -> dict:
    """
    Performs a specialized 'ETH Liquidity Engine' (ELE) audit.
    Checks SFP levels (H4/Daily), OBI, Basis trend, and Z-Score.
    
    Args:
        symbol: The ticker symbol (e.g. ETH/USDT:USDT).
    """
    async def _audit(hub):
        try:
            ohlcv_1d = await hub.fetch_ohlcv(symbol, '1d', limit=2)
            d_high = float(ohlcv_1d['high'].iloc[-2]) if ohlcv_1d is not None else 0
            d_low = float(ohlcv_1d['low'].iloc[-2]) if ohlcv_1d is not None else 0
            
            ohlcv_4h = await hub.fetch_ohlcv(symbol, '4h', limit=3)
            h4_high = float(ohlcv_4h['high'].iloc[-2]) if ohlcv_4h is not None else 0
            h4_low = float(ohlcv_4h['low'].iloc[-2]) if ohlcv_4h is not None else 0
            
            ob = await hub.fetch_order_book(symbol, limit=50)
            obi = 0
            if ob:
                bids = sum(b[1] for b in ob.get('bids', []))
                asks = sum(a[1] for a in ob.get('asks', []))
                obi = (bids - asks) / (bids + asks) if (bids+asks)>0 else 0
            
            spot_sym = symbol.split(":")[0] if ":" in symbol else symbol
            spot_ticker = await hub.fetch_ticker(spot_sym)
            perp_ticker = await hub.fetch_ticker(symbol)
            basis_pct = 0
            if spot_ticker and perp_ticker:
                basis_pct = ((float(perp_ticker['last']) - float(spot_ticker['last'])) / float(spot_ticker['last'])) * 100

            curr_p = float(perp_ticker['last']) if perp_ticker else 0
            is_sfp_l = curr_p < min(d_low, h4_low) if d_low > 0 else False
            is_sfp_s = curr_p > max(d_high, h4_high) if d_high > 0 else False
            
            transition_potential = "LOW"
            if (is_sfp_l and obi > 0.4 and basis_pct > 0.05) or (is_sfp_s and obi < -0.4 and basis_pct < 0.01):
                transition_potential = "HIGH (Intraday Go)"
            elif abs(obi) > 0.2:
                transition_potential = "MEDIUM (Scalp Only)"

            return {
                "symbol": symbol,
                "price": curr_p,
                "levels": {"daily_h": d_high, "daily_l": d_low, "h4_h": h4_high, "h4_l": h4_low},
                "microstructure": {"obi": round(obi, 4), "basis": round(basis_pct, 4)},
                "transition_potential": transition_potential,
                "verdict": "ELE_ACTIVE" if transition_potential != "LOW" else "NO_EDGE"
            }
        except Exception as e:
            return {"symbol": symbol, "error": str(e)}
            
    return _run_hub_sync(_audit)

@action(is_consequential=False)
def detect_sfp_confluence(assets: str = "BTC,ETH") -> dict:
    """
    Composite Routine: Institutional Reversal Flow (SFP).
    
    Args:
        assets: Comma-separated list of assets (e.g. BTC,ETH).
    """
    async def _routine(hub):
        from .funding_actions import detect_confluence_trigger, get_ultra_deep_confluence
        
        triggers = detect_confluence_trigger(assets)
        asset_list = assets.split(",")
        walls = {}
        for asset in asset_list:
            walls[asset] = get_ultra_deep_confluence(asset, 100)
        
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "assets": asset_list,
            "triggers": triggers,
            "ultra_deep_confluence": walls,
            "verdict": "SFP_CONFLUENCE_COMPLETE"
        }

    return _run_hub_sync(_routine)

@action(is_consequential=False)
def senior_desk_universe_audit(assets: str = "BTC,ETH,SOL") -> dict:
    """
    Runs the full Senior Desk audit across the multi-ticker universe.
    
    Args:
        assets: Comma-separated list of assets (e.g. BTC,ETH,SOL).
    """
    from .funding_actions import get_full_market_snapshot
    return _run_hub_sync(lambda hub: get_full_market_snapshot(assets))
