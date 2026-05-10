"""
absorption_detector.py — PhD-Level Institutional Absorption Rate Detector
═══════════════════════════════════════════════════════════════════════════
Detects hidden institutional accumulation/distribution by analyzing the
relationship between aggressive market orders (CVD) and passive limit
order book replenishment (OBI delta over time).

Theory (Kyle's Lambda / Amihud Illiquidity):
  - When price FALLS but large limit bids keep replenishing (absorption),
    institutions are passively accumulating while retail panics.
  - When price RISES but large limit asks keep replenishing,
    institutions are passively distributing while retail FOMOs.
  - The "Absorption Rate" = CVD_velocity / OBI_delta_velocity
  - A high positive AR during a price drop = STEALTH ACCUMULATION
  - A high negative AR during a price rise = STEALTH DISTRIBUTION

This is what the 0.1% use: they don't chase price, they watch the
invisible hand of the limit order book absorbing aggression.

Metrics Produced:
  1. Absorption Rate (AR): CVD change / OBI change over N snapshots
  2. Iceberg Score: Detection of hidden liquidity via trade-size clustering
  3. Kyle's Lambda: Price impact per unit of order flow (market depth quality)
  4. Toxicity Index: Probability of informed trading (VPIN-inspired)

Usage via Action Server:
    from actions.absorption_detector import absorption_scan
    result = absorption_scan("ETH/USDT:USDT")

Author: ccxtv2 Senior Desk · PhD Quant Module
"""

import asyncio
import sys
import os
import json
import time
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared import IntelligenceHub

def _get_hub():
    """Get shared IntelligenceHub singleton (sync accessor)."""
    return IntelligenceHub.instance_sync()


@dataclass
class AbsorptionResult:
    symbol: str
    price: float
    # Core metrics
    absorption_rate: float = 0.0          # CVD_velocity / OBI_delta
    absorption_verdict: str = "NEUTRAL"   # STEALTH_ACCUMULATION / STEALTH_DISTRIBUTION / NEUTRAL
    # Iceberg detection
    iceberg_score: float = 0.0            # 0-1, probability of hidden liquidity
    iceberg_side: str = "NONE"            # BID / ASK / NONE
    # Kyle's Lambda (price impact)
    kyles_lambda: float = 0.0             # Price impact per $1M order flow
    market_quality: str = "NORMAL"        # DEEP / NORMAL / THIN
    # Toxicity (informed trading probability)
    toxicity_index: float = 0.0           # 0-1, VPIN-inspired
    toxicity_verdict: str = "CLEAN"       # TOXIC / ELEVATED / CLEAN
    # Trade flow decomposition
    whale_pct: float = 0.0               # % of volume from top 5% trades
    retail_pct: float = 0.0              # % of volume from bottom 50% trades
    # Raw data
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "absorption": {
                "rate": round(self.absorption_rate, 4),
                "verdict": self.absorption_verdict,
            },
            "iceberg": {
                "score": round(self.iceberg_score, 4),
                "side": self.iceberg_side,
            },
            "kyles_lambda": {
                "value": round(self.kyles_lambda, 6),
                "market_quality": self.market_quality,
            },
            "toxicity": {
                "index": round(self.toxicity_index, 4),
                "verdict": self.toxicity_verdict,
            },
            "flow_decomposition": {
                "whale_pct": round(self.whale_pct, 2),
                "retail_pct": round(self.retail_pct, 2),
            },
            "details": self.details,
        }


class AbsorptionDetector:
    """
    PhD-Level Absorption Detector.
    
    Uses multi-snapshot OBI tracking + CVD decomposition + trade clustering
    to detect when institutions are silently absorbing aggressive flow.
    """

    def __init__(self, engine: IntelligenceHub):
        self.engine = engine
        self._obi_history: Dict[str, List[float]] = {}
        self._price_history: Dict[str, List[float]] = {}

    async def scan(self, symbol: str, ob_depth: int = 50, trade_limit: int = 500) -> AbsorptionResult:
        """Run full absorption scan on a single symbol."""
        
        # 1. Fetch order book
        ob = await self.engine.fetch_order_book(symbol, limit=ob_depth)
        if not ob:
            return AbsorptionResult(symbol=symbol, price=0.0)

        bids = ob.get('bids', [])
        asks = ob.get('asks', [])
        bid_vol = sum(b[1] for b in bids)
        ask_vol = sum(a[1] for a in asks)
        mid_price = (bids[0][0] + asks[0][0]) / 2 if bids and asks else 0

        obi = (bid_vol - ask_vol) / (bid_vol + ask_vol) if (bid_vol + ask_vol) > 0 else 0

        result = AbsorptionResult(symbol=symbol, price=mid_price)

        # Track OBI history for this symbol
        if symbol not in self._obi_history:
            self._obi_history[symbol] = []
            self._price_history[symbol] = []
        self._obi_history[symbol].append(obi)
        self._price_history[symbol].append(mid_price)
        # Keep last 10 snapshots
        self._obi_history[symbol] = self._obi_history[symbol][-10:]
        self._price_history[symbol] = self._price_history[symbol][-10:]

        # 2. Fetch recent trades
        trades_df = await self.engine.fetch_trades(symbol, limit=trade_limit)
        if trades_df is None or trades_df.empty:
            return result

        trades_df['usd_value'] = trades_df['amount'] * trades_df['price']
        
        # ═══ METRIC 1: Absorption Rate ═══════════════════════════
        buys = trades_df[trades_df['side'] == 'buy']
        sells = trades_df[trades_df['side'] == 'sell']
        buy_usd = float(buys['usd_value'].sum())
        sell_usd = float(sells['usd_value'].sum())
        cvd = buy_usd - sell_usd

        # OBI delta: how much the book shifted
        obi_history = self._obi_history[symbol]
        if len(obi_history) >= 2:
            obi_delta = obi_history[-1] - obi_history[0]
        else:
            obi_delta = 0

        # Absorption Rate = CVD / OBI_delta
        # High CVD (buying) but OBI staying negative = someone absorbing sells
        if abs(obi_delta) > 0.01:
            result.absorption_rate = cvd / (obi_delta * (buy_usd + sell_usd)) if (buy_usd + sell_usd) > 0 else 0
        else:
            # OBI barely moved despite large CVD = STRONG absorption
            if abs(cvd) > 50000:  # > $50k CVD
                result.absorption_rate = cvd / 50000  # Normalize
            
        # Determine verdict
        price_history = self._price_history[symbol]
        price_falling = len(price_history) >= 2 and price_history[-1] < price_history[0]
        price_rising = len(price_history) >= 2 and price_history[-1] > price_history[0]

        if result.absorption_rate > 0.5 and price_falling:
            result.absorption_verdict = "STEALTH_ACCUMULATION"
        elif result.absorption_rate < -0.5 and price_rising:
            result.absorption_verdict = "STEALTH_DISTRIBUTION"
        else:
            result.absorption_verdict = "NEUTRAL"

        # ═══ METRIC 2: Iceberg Detection ═════════════════════════
        # Icebergs create clusters of identically-sized trades at the same price
        trade_sizes = trades_df['amount'].values
        if len(trade_sizes) > 20:
            # Check for repeated trade sizes (iceberg signature)
            size_counts = pd.Series(trade_sizes).round(4).value_counts()
            repeated = size_counts[size_counts >= 5]  # Same size appears 5+ times
            if not repeated.empty:
                iceberg_volume = 0
                for size, count in repeated.items():
                    mask = (trades_df['amount'].round(4) == size)
                    matching = trades_df[mask]
                    iceberg_volume += float(matching['usd_value'].sum())
                    # Determine side
                    buy_count = len(matching[matching['side'] == 'buy'])
                    sell_count = len(matching[matching['side'] == 'sell'])
                    if buy_count > sell_count:
                        result.iceberg_side = "BID"
                    elif sell_count > buy_count:
                        result.iceberg_side = "ASK"
                
                total_volume = float(trades_df['usd_value'].sum())
                result.iceberg_score = min(iceberg_volume / total_volume, 1.0) if total_volume > 0 else 0

        # ═══ METRIC 3: Kyle's Lambda (Price Impact) ══════════════
        # Lambda = delta_price / delta_order_flow
        # Adaptive Normalization: (Price Change %) / ($ Net Flow in Millions)
        if len(trades_df) > 50:
            bucket_size = 50
            lambdas = []
            for i in range(0, len(trades_df) - bucket_size, bucket_size):
                bucket = trades_df.iloc[i:i+bucket_size]
                price_change_pct = abs(float(bucket['price'].iloc[-1] - bucket['price'].iloc[0])) / mid_price * 100
                net_flow_m = abs(float(bucket[bucket['side']=='buy']['usd_value'].sum() - 
                                     bucket[bucket['side']=='sell']['usd_value'].sum())) / 1_000_000
                
                if net_flow_m > 0.0001:  # min $100 flow to avoid noise
                    lambdas.append(price_change_pct / net_flow_m)
            
            if lambdas:
                result.kyles_lambda = float(np.median(lambdas))
                # New Adaptive Thresholds (BP impact per $1M)
                if result.kyles_lambda < 5.0:
                    result.market_quality = "DEEP"
                elif result.kyles_lambda > 50.0:
                    result.market_quality = "THIN"
                else:
                    result.market_quality = "NORMAL"


        # ═══ METRIC 4: Toxicity Index (VPIN-inspired) ════════════
        # Volume-Synchronized Probability of Informed Trading
        # High toxicity = informed traders are dominating the flow
        if len(trades_df) > 100:
            bucket_size = len(trades_df) // 10
            imbalances = []
            for i in range(0, len(trades_df) - bucket_size, bucket_size):
                bucket = trades_df.iloc[i:i+bucket_size]
                bv = float(bucket[bucket['side']=='buy']['usd_value'].sum())
                sv = float(bucket[bucket['side']=='sell']['usd_value'].sum())
                total = bv + sv
                if total > 0:
                    imbalances.append(abs(bv - sv) / total)
            
            if imbalances:
                result.toxicity_index = float(np.mean(imbalances))
                if result.toxicity_index > 0.7:
                    result.toxicity_verdict = "TOXIC"
                elif result.toxicity_index > 0.4:
                    result.toxicity_verdict = "ELEVATED"
                else:
                    result.toxicity_verdict = "CLEAN"

        # ═══ METRIC 5: Flow Decomposition ════════════════════════
        # What % of volume comes from whales vs retail?
        total_vol = float(trades_df['usd_value'].sum())
        if total_vol > 0:
            q95 = trades_df['usd_value'].quantile(0.95)
            q50 = trades_df['usd_value'].quantile(0.50)
            whale_vol = float(trades_df[trades_df['usd_value'] >= q95]['usd_value'].sum())
            retail_vol = float(trades_df[trades_df['usd_value'] <= q50]['usd_value'].sum())
            result.whale_pct = (whale_vol / total_vol) * 100
            result.retail_pct = (retail_vol / total_vol) * 100

        result.details = {
            "obi_current": round(obi, 4),
            "obi_snapshots": len(obi_history),
            "cvd_usd": round(cvd, 2),
            "total_trades": len(trades_df),
            "bid_vol": round(bid_vol, 2),
            "ask_vol": round(ask_vol, 2),
        }

        return result


async def run_full_scan():
    """Scan all tickers in the universe."""
    engine = _get_hub()
    engine._init_internals()
    await engine.connect()
    detector = AbsorptionDetector(engine)
    
    tickers = [
        'BTC/USDT:USDT', 'ETH/USDT:USDT', 'SOL/USDT:USDT',
        'TAO/USDT:USDT', 'HYPE/USDT:USDT', 'LINK/USDT:USDT'
    ]
    
    results = {}
    for sym in tickers:
        try:
            r = await detector.scan(sym)
            results[sym] = r.to_dict()
        except Exception as e:
            results[sym] = {"error": str(e)}
    
    # Singleton: do NOT close engine
    # await engine.close()
    return results


if __name__ == "__main__":
    res = asyncio.run(run_full_scan())
    print(json.dumps(res, indent=2))
