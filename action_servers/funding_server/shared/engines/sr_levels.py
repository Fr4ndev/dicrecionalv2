"""
shared/engines/sr_levels.py — Support/Resistance Level Detection Engine
=======================================================================
Extracted from optimied_sr_BUENO_mtfv2.py (ccxt.god/heatmap/).
LOGIC ONLY — no matplotlib, no visualization.

Detects:
  - Fractal pivot highs/lows with reversal confirmation (Elder-inspired)
  - SR heatmap: touch frequency + pivot boosts + Wyckoff low-vol zones
  - Volume Profile: accumulated volume per price level
  - Key SR levels: top N heat clusters extracted as discrete levels

Usage:
    from shared.engines.sr_levels import SRLevelsEngine
    engine = SRLevelsEngine()
    levels = engine.compute_key_levels(df)  # [{price, strength, type, volume_boost}]
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import List, Dict, Tuple, Optional
from scipy.ndimage import gaussian_filter1d


class SRLevelsEngine:
    """
    Support/Resistance level detection using fractal pivots,
    volume touch frequency, and Wyckoff accumulation analysis.
    """

    def __init__(self, n_bins: int = 50, rolling_window: int = 50,
                 pivot_window: int = 5, reversal_threshold: float = 0.02,
                 vol_boost_factor: float = 1.2, vol_threshold: float = 0.8,
                 smooth_sigma: float = 3.0):
        self.n_bins = n_bins
        self.rolling_window = rolling_window
        self.pivot_window = pivot_window
        self.reversal_threshold = reversal_threshold
        self.vol_boost_factor = vol_boost_factor
        self.vol_threshold = vol_threshold
        self.smooth_sigma = smooth_sigma

    # ── Fractal Pivot Detection ────────────────────────────────────

    def detect_fractal_pivots(self, df: pd.DataFrame) -> List[Tuple[int, float, str]]:
        """
        Detect fractal pivot highs/lows with reversal confirmation.
        
        Args:
            df: OHLCV DataFrame with 'high', 'low', 'close' columns.
        
        Returns:
            List of (index, price, type) where type is 'high' or 'low'.
        """
        highs = df['high'].values
        lows = df['low'].values
        w = self.pivot_window
        threshold = self.reversal_threshold
        
        pivots = []
        
        for i in range(w, len(df) - w):
            # Pivot high: strict local max
            is_high = all(highs[i] > highs[i - j] for j in range(1, w + 1)) and \
                      all(highs[i] > highs[i + j] for j in range(1, w + 1))
            
            if is_high:
                # Reversal confirmation: drop > threshold in next window
                future_lows = lows[i + 1:i + w + 1]
                if len(future_lows) > 0 and (highs[i] - np.min(future_lows)) / highs[i] >= threshold:
                    pivots.append((i, float(highs[i]), 'high'))
            
            # Pivot low: strict local min
            is_low = all(lows[i] < lows[i - j] for j in range(1, w + 1)) and \
                     all(lows[i] < lows[i + j] for j in range(1, w + 1))
            
            if is_low:
                # Reversal confirmation: rise > threshold in next window
                future_highs = highs[i + 1:i + w + 1]
                if len(future_highs) > 0 and (np.max(future_highs) - lows[i]) / lows[i] >= threshold:
                    pivots.append((i, float(lows[i]), 'low'))
        
        return pivots

    # ── Volume Profile ────────────────────────────────────────────

    def calculate_volume_profile(self, df: pd.DataFrame, num_bins: int = 100) -> Tuple[np.ndarray, np.ndarray]:
        """
        Calculate Volume Profile: accumulated volume per price level.
        
        Args:
            df: OHLCV DataFrame with 'high', 'low', 'volume'.
            num_bins: Number of price bins.
        
        Returns:
            (bin_centers, volume_profile) arrays.
        """
        price_min = df['low'].min()
        price_max = df['high'].max()
        bins = np.linspace(price_min, price_max, num_bins)
        volume_profile = np.zeros(num_bins - 1)
        
        for _, row in df.iterrows():
            low, high, volume = float(row['low']), float(row['high']), float(row['volume'])
            # Distribute volume across price bins the candle touches
            touched = np.digitize(np.linspace(low, high, 10), bins) - 1
            for bin_idx in touched:
                if 0 <= bin_idx < len(volume_profile):
                    volume_profile[bin_idx] += volume / len(touched)
        
        bin_centers = (bins[:-1] + bins[1:]) / 2
        return bin_centers, volume_profile

    # ── SR Heatmap ─────────────────────────────────────────────────

    def compute_sr_heatmap(self, df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
        """
        Compute S/R heatmap: touch frequency + pivot boosts + Wyckoff low-vol boost + smoothing.
        
        Returns:
            (heat_matrix[n_bins, len(df)], bin_centers[n_bins])
        """
        closes = df['close'].values
        highs = df['high'].values
        lows = df['low'].values
        volumes = df['volume'].values
        n = self.n_bins
        rw = self.rolling_window
        
        # Price bins
        min_p, max_p = float(closes.min()), float(closes.max())
        bins = np.linspace(min_p, max_p, n + 1)
        bin_centers = (bins[:-1] + bins[1:]) / 2
        
        # Touch matrix: (n_bins, len(df)) — 1 if candle touches price bin
        touch_matrix = np.zeros((n, len(df)))
        for j in range(len(df)):
            touched = np.where((bin_centers >= lows[j]) & (bin_centers <= highs[j]))[0]
            touch_matrix[touched, j] = 1.0
        
        # Rolling normalized touch frequency per price level
        heat_matrix = np.zeros_like(touch_matrix)
        for i in range(n):
            touches_rolling = pd.Series(touch_matrix[i]).rolling(rw, min_periods=1).mean().values
            heat_matrix[i] = np.clip(touches_rolling, 0, 1)
        
        # Pivot boosts: increase heat around detected pivots
        pivots = self.detect_fractal_pivots(df)
        pivot_w = rw // 2
        for idx, pivot_price, _ in pivots:
            bin_idx = np.digitize(pivot_price, bins) - 1
            if 0 <= bin_idx < n:
                start, end = max(0, idx - pivot_w), min(len(df), idx + pivot_w + 1)
                heat_matrix[bin_idx, start:end] = np.minimum(1.0, heat_matrix[bin_idx, start:end] + 0.3)
        
        # Wyckoff: boost in low-volume accumulation zones
        avg_vol = float(np.mean(volumes))
        low_vol_mask = volumes < self.vol_threshold * avg_vol
        for j in range(len(df)):
            if low_vol_mask[j]:
                touched = np.where((bin_centers >= lows[j]) & (bin_centers <= highs[j]))[0]
                heat_matrix[touched, max(0, j - pivot_w):min(len(df), j + pivot_w + 1)] *= self.vol_boost_factor
                heat_matrix[touched, max(0, j - pivot_w):min(len(df), j + pivot_w + 1)] = np.clip(
                    heat_matrix[touched, max(0, j - pivot_w):min(len(df), j + pivot_w + 1)], 0, 1
                )
        
        # Gaussian smoothing per price level (row)
        for i in range(n):
            if np.sum(heat_matrix[i]) > 0:
                heat_matrix[i] = gaussian_filter1d(heat_matrix[i], sigma=self.smooth_sigma)
        
        # Final normalization
        heat_max = heat_matrix.max()
        if heat_max > 0:
            heat_matrix /= heat_max
        
        return heat_matrix, bin_centers

    # ── Key Levels Extraction ──────────────────────────────────────

    def compute_key_levels(self, df: pd.DataFrame, top_n: int = 10) -> List[Dict]:
        """
        Extract discrete support/resistance levels from the heatmap.
        
        Returns:
            List of {price, strength, type, volume_score, pivot_count} sorted by strength desc.
        """
        heat, bin_centers = self.compute_sr_heatmap(df)
        pivots = self.detect_fractal_pivots(df)
        
        # Current heat (last column) — instantaneous S/R strength
        current_heat = heat[:, -1]
        
        # Count pivots per bin
        pivot_counts = np.zeros(len(bin_centers))
        for _, price, _ in pivots:
            bin_idx = np.digitize(price, np.append(bin_centers, bin_centers[-1] + 1)) - 1
            if 0 <= bin_idx < len(pivot_counts):
                pivot_counts[bin_idx] += 1
        
        # Volume profile for volume_score
        vp_bins, vp_vol = self.calculate_volume_profile(df, num_bins=self.n_bins)
        vol_norm = vp_vol / vp_vol.max() if vp_vol.max() > 0 else vp_vol
        
        # Find local maxima in heat (clusters)
        levels = []
        for i in range(1, len(current_heat) - 1):
            if current_heat[i] > current_heat[i-1] and current_heat[i] > current_heat[i+1]:
                # This is a local heat peak = potential S/R level
                price = float(bin_centers[i])
                strength = float(current_heat[i])
                
                # Determine type (support or resistance) based on pivot count and price vs current
                current_price = float(df['close'].iloc[-1])
                level_type = 'resistance' if price > current_price else 'support'
                
                # Volume score at this level
                vol_idx = np.argmin(np.abs(vp_bins - price))
                vol_score = float(vol_norm[vol_idx]) if 0 <= vol_idx < len(vol_norm) else 0.0
                
                levels.append({
                    "price": round(price, 2),
                    "strength": round(strength, 4),
                    "type": level_type,
                    "volume_score": round(vol_score, 4),
                    "pivot_count": int(pivot_counts[i]),
                })
        
        # Sort by strength descending, take top_n
        levels.sort(key=lambda x: x['strength'], reverse=True)
        return levels[:top_n]

    def compute_mtf_levels(self, dataframes: Dict[str, pd.DataFrame]) -> Dict[str, List[Dict]]:
        """
        Compute key levels for multiple timeframes.
        
        Args:
            dataframes: Dict like {'4h': df_4h, '1d': df_1d, '1w': df_1w}
        
        Returns:
            Dict of timeframe → list of levels.
        """
        results = {}
        for tf, df in dataframes.items():
            levels = self.compute_key_levels(df, top_n=8)
            results[tf] = levels
        return results

    def get_level_confluence(self, mtf_levels: Dict[str, List[Dict]], tolerance_pct: float = 0.005) -> List[Dict]:
        """
        Find levels that appear across multiple timeframes (confluence zones).
        
        Args:
            mtf_levels: Output from compute_mtf_levels().
            tolerance_pct: Max % distance to consider two levels as "same".
        
        Returns:
            List of confluent levels with timeframe sources.
        """
        all_levels = []
        for tf, levels in mtf_levels.items():
            for lvl in levels:
                all_levels.append({**lvl, "timeframe": tf})
        
        # Cluster nearby levels
        confluent = []
        used = set()
        for i, a in enumerate(all_levels):
            if i in used:
                continue
            cluster = [a]
            for j, b in enumerate(all_levels):
                if j <= i or j in used:
                    continue
                if abs(a['price'] - b['price']) / a['price'] < tolerance_pct:
                    cluster.append(b)
                    used.add(j)
            
            if len(cluster) >= 2:
                avg_price = np.mean([c['price'] for c in cluster])
                avg_strength = np.mean([c['strength'] for c in cluster])
                tfs = list(set(c['timeframe'] for c in cluster))
                confluent.append({
                    "price": round(float(avg_price), 2),
                    "strength": round(float(avg_strength), 4),
                    "confluence_count": len(cluster),
                    "timeframes": tfs,
                    "type": cluster[0]['type'],
                })
        
        confluent.sort(key=lambda x: x['confluence_count'], reverse=True)
        return confluent
