#!/usr/bin/env python3
"""
strategies/zscore.py — MVRV Z-Score Engine
Institutional valuation metric with multi-exchange data, OI, Funding rates,
CVD, Liquidation sweeps, and Wyckoff phase integration. 
Based on: institucional_zscore2bueno.py
"""

from __future__ import annotations

import logging
import io
import asyncio
import requests
import numpy as np
import pandas as pd
import datetime
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.gridspec as gridspec
import seaborn as sns
import ccxt.async_support as ccxt
from typing import Tuple, Optional, Dict

from shared.config import settings, TickerConfig

logger = logging.getLogger("ZScore")

class ZScoreEngine:
    """Enhanced Institutional MVRV Z-Score Engine."""

    async def run(self, ticker: TickerConfig,
                  engine: DataEngine) -> Optional[Tuple[io.BytesIO, str]]:
        """Execute full Institutional MVRV Z-Score analysis pipeline."""
        try:
            # We need the base symbol for multi-exchange fetches (e.g. BTC/USDT)
            base_symbol = ticker.symbol.split(':')[0] if ':' in ticker.symbol else ticker.symbol

            logger.info(f"Fetching institutional metrics for {ticker.name} ({base_symbol})...")
            data = await self._fetch_all_data(base_symbol)
            if not data or '1d' not in data['price_data']:
                logger.warning(f"Failed to fetch sufficient data for {ticker.name}")
                return None

            supply = data['supply'] or ticker.circulating_supply

            # Calculate enhanced metrics
            df_daily = self._calculate_enhanced(
                data['price_data']['1d'], supply, data['cvd_data'], data['oi_data']
            )
            data['price_data']['1d'] = df_daily

            if '1w' in data['price_data']:
                data['price_data']['1w'] = self._calculate_enhanced(
                    data['price_data']['1w'], supply, data['cvd_data'], data['oi_data']
                )

            # Generate chart
            chart_buf = self._generate_chart(ticker, data)

            # Generate caption based on signals
            last_z = float(df_daily['mvrv_z_smooth'].iloc[-1]) if not pd.isna(df_daily['mvrv_z_smooth'].iloc[-1]) else 0.0
            phase = str(df_daily['wyckoff_phase'].iloc[-1])
            sig = df_daily['signal'].iloc[-1]
            strength = df_daily['signal_strength'].iloc[-1]
            reason = df_daily['signal_reason'].iloc[-1]

            sig_str = "NEUTRAL"
            emoji = "⚪"
            if sig == 1:
                sig_str = "BUY"
                emoji = "🟢"
            elif sig == -1:
                sig_str = "SELL"
                emoji = "🔴"

            caption = (
                f"{emoji} **{ticker.name} | INSTITUTIONAL Z-SCORE**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📉 **Z-Score:** {last_z:.2f}\n"
                f"🔄 **Phase:** {phase}\n"
                f"🎯 **Signal:** {sig_str} ({strength}/10)\n"
                f"ℹ️ **Reason:** {reason if reason else 'N/A'}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"_Multi-Exchange: OI + CVD + Funding + Sweeps_"
            )

            return chart_buf, caption

        except Exception as e:
            logger.error(f"ZScore error ({ticker.name}): {e}", exc_info=True)
            return None

    # ─── DATA FETCHING ───────────────────────────────────────────────

    async def _fetch_all_data(self, symbol: str) -> Optional[Dict]:
        exchanges = [ccxt.okx(), ccxt.bybit(), ccxt.hyperliquid()]
        data = {'price_data': {}}
        try:
            for tf in ['1d', '1w']:
                for ex in exchanges:
                    try:
                        limit = 200 if tf == '1w' else 500
                        ohlcv = await ex.fetch_ohlcv(symbol, tf, limit=limit)
                        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                        df['date'] = pd.to_datetime(df['timestamp'], unit='ms')
                        df['range'] = df['high'] - df['low']
                        df['body'] = abs(df['close'] - df['open'])
                        data['price_data'][tf] = df
                        break
                    except Exception as e:
                        logger.debug(f"{ex.id} failed {tf} for {symbol}: {e}")
                        continue

            if '1d' not in data['price_data']:
                raise Exception("Failed to fetch daily data from all exchanges.")

            fr_data, oi_data, cvd_data = await asyncio.gather(
                self._fetch_funding_rates(symbol),
                self._fetch_open_interest(symbol),
                self._fetch_cvd(symbol)
            )

            data['funding_rates'] = fr_data
            data['oi_data'] = oi_data
            data['cvd_data'] = cvd_data

            if 'BTC' in symbol:
                data['supply'] = await self._fetch_supply('bitcoin')
            elif 'ETH' in symbol:
                data['supply'] = await self._fetch_supply('ethereum')
            else:
                data['supply'] = None

            return data
        finally:
            for ex in exchanges:
                try: await ex.close()
                except: pass

    async def _fetch_funding_rates(self, symbol: str) -> Optional[pd.DataFrame]:
        exchanges = [ccxt.okx(), ccxt.bybit(), ccxt.hyperliquid(), ccxt.binance()]
        rates_data = []
        mappings = {
            'okx': {'BTC/USDT': 'BTC-USDT-SWAP', 'ETH/USDT': 'ETH-USDT-SWAP'},
            'bybit': {'BTC/USDT': 'BTC/USDT:USDT', 'ETH/USDT': 'ETH/USDT:USDT'},
            'hyperliquid': {'BTC/USDT': 'BTC', 'ETH/USDT': 'ETH'},
            'binance': {'BTC/USDT': 'BTCUSDT', 'ETH/USDT': 'ETHUSDT'}
        }
        for ex in exchanges:
            try:
                perp = mappings.get(ex.id, {}).get(symbol)
                if perp and hasattr(ex, 'fetch_funding_rate_history'):
                    rates = await ex.fetch_funding_rate_history(perp, limit=100)
                    if rates:
                        df = pd.DataFrame(rates)
                        if 'timestamp' in df.columns: df['date'] = pd.to_datetime(df['timestamp'], unit='ms')
                        elif 'datetime' in df.columns: df['date'] = pd.to_datetime(df['datetime'])
                        df['exchange'] = ex.id
                        rates_data.append(df)
            except: pass
            finally:
                try: await ex.close()
                except: pass
        if rates_data: return pd.concat(rates_data)
        return None

    async def _fetch_open_interest(self, symbol: str) -> Optional[pd.DataFrame]:
        exchanges = [ccxt.okx(), ccxt.bybit(), ccxt.hyperliquid()]
        oi_data = []
        mappings = {
            'okx': {'BTC/USDT': 'BTC-USDT-SWAP', 'ETH/USDT': 'ETH-USDT-SWAP'},
            'bybit': {'BTC/USDT': 'BTC/USDT:USDT', 'ETH/USDT': 'ETH/USDT:USDT'},
            'hyperliquid': {'BTC/USDT': 'BTC', 'ETH/USDT': 'ETH'}
        }
        for ex in exchanges:
            try:
                perp = mappings.get(ex.id, {}).get(symbol)
                if perp and hasattr(ex, 'fetch_open_interest_history'):
                    oi = await ex.fetch_open_interest_history(perp, limit=100)
                    if oi:
                        df = pd.DataFrame(oi)
                        if 'timestamp' in df.columns: df['date'] = pd.to_datetime(df['timestamp'], unit='ms')
                        elif 'datetime' in df.columns: df['date'] = pd.to_datetime(df['datetime'])
                        df['exchange'] = ex.id
                        oi_data.append(df)
            except: pass
            finally:
                try: await ex.close()
                except: pass
        if oi_data: return pd.concat(oi_data)
        return None

    async def _fetch_cvd(self, symbol: str) -> Optional[pd.DataFrame]:
        ex = ccxt.okx()
        try:
            trades = await ex.fetch_trades(symbol, limit=1000)
            if not trades: return None
            df = pd.DataFrame(trades)
            df['date'] = pd.to_datetime(df['timestamp'], unit='ms')
            df['side_calc'] = np.where(df['price'] > df['price'].shift(1), 'buy', 'sell')
            df['cvd'] = np.where(df['side_calc'] == 'buy', df['amount'], -df['amount'])
            df['cumulative_delta'] = df['cvd'].cumsum()
            return df
        except: return None
        finally:
            try: await ex.close()
            except: pass

    async def _fetch_supply(self, coin_id: str) -> Optional[float]:
        try:
            r = await asyncio.to_thread(requests.get, f'https://api.coingecko.com/api/v3/coins/{coin_id}', timeout=5)
            return r.json()['market_data']['circulating_supply']
        except:
            if coin_id == 'bitcoin': return 19500000
            if coin_id == 'ethereum': return 120000000
            return None

    # ─── CORE CALCULATION LOGIC ──────────────────────────────────────

    def _calculate_enhanced(self, df: pd.DataFrame, supply: float, cvd_data: Optional[pd.DataFrame], oi_data: Optional[pd.DataFrame]) -> pd.DataFrame:
        df = df.copy()
        if len(df) < 50: return df
        if not supply: supply = 1

        df['market_cap'] = df['close'] * supply
        df['vwap'] = (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()
        
        decay_factor = 0.995
        df['decay'] = np.power(decay_factor, range(len(df))[::-1])
        df['vwap_decay'] = df['vwap'] * df['decay']
        df['realized_cap'] = df['vwap_decay'] * supply

        df['mvrv'] = (df['market_cap'] / df['realized_cap']) - 1
        
        window = min(200, len(df) - 1)
        df['mvrv_mean'] = df['mvrv'].rolling(window=window).mean()
        df['mvrv_std'] = df['mvrv'].rolling(window=window).std()
        df['mvrv_z'] = (df['mvrv'] - df['mvrv_mean']) / df['mvrv_std'].replace(0, np.nan)
        df['mvrv_z_smooth'] = df['mvrv_z'].ewm(alpha=0.05).mean()

        df = self._detect_wyckoff_phases(df)
        df = self._detect_liquidity_sweeps(df)
        
        if cvd_data is not None and not cvd_data.empty:
            cvd_res = cvd_data.set_index('date').resample('1D').last().reset_index()
            if not cvd_res.empty and 'cumulative_delta' in cvd_res.columns:
                df = pd.merge_asof(df, cvd_res[['date', 'cumulative_delta']], on='date', direction='nearest')
                if 'cumulative_delta' in df.columns:
                    df['cvd_signal'] = 0
                    for i in range(10, len(df)):
                        if pd.notna(df['cumulative_delta'].iloc[i]) and pd.notna(df['cumulative_delta'].iloc[i-10]):
                            if df['close'].iloc[i] < df['close'].iloc[i-10] and df['cumulative_delta'].iloc[i] > df['cumulative_delta'].iloc[i-10]:
                                df.at[df.index[i], 'cvd_signal'] = 1
                            elif df['close'].iloc[i] > df['close'].iloc[i-10] and df['cumulative_delta'].iloc[i] < df['cumulative_delta'].iloc[i-10]:
                                df.at[df.index[i], 'cvd_signal'] = -1

        if oi_data is not None and not oi_data.empty:
            oi_res = oi_data.groupby('date').last().reset_index()
            if not oi_res.empty and 'openInterestAmount' in oi_res.columns:
                df = pd.merge_asof(df, oi_res[['date', 'openInterestAmount']], on='date', direction='nearest')
                if 'openInterestAmount' in df.columns:
                    df['oi_signal'] = 0
                    for i in range(10, len(df)):
                        if pd.notna(df['openInterestAmount'].iloc[i]) and pd.notna(df['openInterestAmount'].iloc[i-10]):
                            if df['close'].iloc[i] > df['close'].iloc[i-10] and df['openInterestAmount'].iloc[i] > df['openInterestAmount'].iloc[i-10]:
                                df.at[df.index[i], 'oi_signal'] = 1
                            elif df['close'].iloc[i] < df['close'].iloc[i-10] and df['openInterestAmount'].iloc[i] > df['openInterestAmount'].iloc[i-10]:
                                df.at[df.index[i], 'oi_signal'] = -1

        df = self._generate_signals(df)
        return df

    def _detect_wyckoff_phases(self, df: pd.DataFrame) -> pd.DataFrame:
        df['volume_sma'] = df['volume'].rolling(20).mean()
        df['volume_ratio'] = df['volume'] / df['volume_sma'].replace(0, np.nan)
        df['range_sma'] = df['range'].rolling(20).mean()
        df['range_ratio'] = df['range'] / df['range_sma'].replace(0, np.nan)
        df['wyckoff_phase'] = 'Neutral'

        for i in range(50, len(df)):
            c, c_20 = df['close'].iloc[i], df['close'].iloc[i-20]
            vr, rr = df['volume_ratio'].iloc[i], df['range_ratio'].iloc[i]
            z = df['mvrv_z_smooth'].iloc[i]
            
            if pd.isna(c_20) or pd.isna(vr) or pd.isna(rr) or pd.isna(z): continue

            if c > c_20 and vr > 1.2 and rr < 0.8 and z < 0:
                df.at[df.index[i], 'wyckoff_phase'] = 'Accumulation'
            elif c > c_20 and vr > 1.0 and 0 < z < 0.25:
                df.at[df.index[i], 'wyckoff_phase'] = 'Markup'
            elif c < c_20 and vr > 1.2 and rr < 0.8 and z > 0.25:
                df.at[df.index[i], 'wyckoff_phase'] = 'Distribution'
            elif c < c_20 and vr > 1.0 and z < 0:
                df.at[df.index[i], 'wyckoff_phase'] = 'Markdown'
        return df

    def _detect_liquidity_sweeps(self, df: pd.DataFrame) -> pd.DataFrame:
        df['high_20'] = df['high'].rolling(20).max()
        df['low_20'] = df['low'].rolling(20).min()
        df['high_50'] = df['high'].rolling(50).max()
        df['low_50'] = df['low'].rolling(50).min()
        df['liquidity_sweep'] = False
        df['sweep_type'] = None

        for i in range(50, len(df)):
            if (df['low'].iloc[i] < df['low_20'].iloc[i-1] and
                df['low'].iloc[i] < df['low_50'].iloc[i-1] and
                df['volume_ratio'].iloc[i] > 1.5 and
                df['close'].iloc[i] > df['open'].iloc[i] and
                df['close'].iloc[i] > df['low_20'].iloc[i-1]):
                df.at[df.index[i], 'liquidity_sweep'] = True
                df.at[df.index[i], 'sweep_type'] = 'Bullish'
            elif (df['high'].iloc[i] > df['high_20'].iloc[i-1] and
                  df['high'].iloc[i] > df['high_50'].iloc[i-1] and
                  df['volume_ratio'].iloc[i] > 1.5 and
                  df['close'].iloc[i] < df['open'].iloc[i] and
                  df['close'].iloc[i] < df['high_20'].iloc[i-1]):
                df.at[df.index[i], 'liquidity_sweep'] = True
                df.at[df.index[i], 'sweep_type'] = 'Bearish'
        return df

    def _generate_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        df['signal'] = 0
        df['signal_strength'] = 0
        df['signal_reason'] = ''

        for i in range(50, len(df)):
            signals, strength, reasons = [], 0, []
            
            z = df['mvrv_z_smooth'].iloc[i]
            if pd.notna(z):
                if z < -0.5:
                    signals.append(1); strength += 2; reasons.append("Undervalued Z")
                elif z > 0.5:
                    signals.append(-1); strength += 2; reasons.append("Overvalued Z")
                    
            w = df['wyckoff_phase'].iloc[i]
            if w == 'Accumulation':
                signals.append(1); strength += 3; reasons.append("Accumulation")
            elif w == 'Distribution':
                signals.append(-1); strength += 3; reasons.append("Distribution")
                
            if df['liquidity_sweep'].iloc[i]:
                if df['sweep_type'].iloc[i] == 'Bullish':
                    signals.append(1); strength += 4; reasons.append("Bullish Sweep")
                elif df['sweep_type'].iloc[i] == 'Bearish':
                    signals.append(-1); strength += 4; reasons.append("Bearish Sweep")
                    
            if 'cvd_signal' in df.columns and df['cvd_signal'].iloc[i] != 0:
                signals.append(df['cvd_signal'].iloc[i])
                strength += 2
                reasons.append("CVD Divergence")
                
            if 'oi_signal' in df.columns and df['oi_signal'].iloc[i] != 0:
                signals.append(df['oi_signal'].iloc[i])
                strength += 1
                reasons.append("OI Signal")
                
            if signals:
                avg_sig = sum(signals) / len(signals)
                if avg_sig > 0.5: df.at[df.index[i], 'signal'] = 1
                elif avg_sig < -0.5: df.at[df.index[i], 'signal'] = -1
                df.at[df.index[i], 'signal_strength'] = min(strength, 10)
                df.at[df.index[i], 'signal_reason'] = ", ".join(reasons)

        return df

    # ─── VISUALIZATION ───────────────────────────────────────────────

    def _generate_chart(self, ticker: TickerConfig, data: Dict) -> io.BytesIO:
        plt.style.use('dark_background')
        fig = plt.figure(figsize=(16, 20), facecolor='#0a0a0a')
        fig.patch.set_facecolor('#0a0a0a')
        
        ctime = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        fig.suptitle(f"Institutional Z-Score Analysis for {ticker.name} - {ctime}", fontsize=16, fontweight='bold', color='white')
        gs = fig.add_gridspec(6, 2, height_ratios=[2, 1, 1, 1, 1, 1], hspace=0.35)
        
        df_daily = data['price_data']['1d']

        # 1. Price and Z-Score
        ax1 = fig.add_subplot(gs[0, :], facecolor='#0a0a0a')
        ax1.plot(df_daily['date'], df_daily['close'], color='white', linewidth=1.5, label='Close')
        ax1.set_ylabel('Price (USD)', color='white')
        
        ax1_twin = ax1.twinx()
        ax1_twin.plot(df_daily['date'], df_daily['mvrv_z_smooth'], color='#00ffcc', linewidth=2, label='MVRV Z-Score')
        ax1_twin.axhline(0, color='blue', linestyle='--', alpha=0.5)
        ax1_twin.axhline(0.25, color='red', linestyle='-', alpha=0.6, label='Overvalued (>0.25)')
        ax1_twin.axhline(-0.25, color='yellow', linestyle='-', alpha=0.6, label='Undervalued (<-0.25)')
        ax1_twin.set_ylabel('Z-Score', color='#00ffcc')
        
        sweeps = df_daily[df_daily['liquidity_sweep'] == True]
        for _, row in sweeps.iterrows():
            c = 'lime' if row['sweep_type'] == 'Bullish' else 'red'
            ax1.scatter(row['date'], row['close'], color=c, s=80, marker='*', zorder=5)

        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax1_twin.get_legend_handles_labels()
        ax1.legend(lines1+lines2, labels1+labels2, loc='upper left', fontsize=9, facecolor='#111', edgecolor='#333', labelcolor='white')
        ax1.set_title('Price & MVRV Z-Score (Daily)', color='white', fontweight='bold')
        ax1.grid(True, alpha=0.15)
        
        # 2. Weekly Chart
        ax2 = fig.add_subplot(gs[1, 0], facecolor='#0a0a0a')
        if '1w' in data['price_data']:
            df_w = data['price_data']['1w']
            ax2.plot(df_w['date'], df_w['close'], color='white', linewidth=1.5)
            a2t = ax2.twinx()
            a2t.plot(df_w['date'], df_w['mvrv_z_smooth'], color='#00ffcc', linewidth=2)
            a2t.set_ylabel('Z', color='#00ffcc')
        ax2.set_title('Weekly Chart', color='white', fontweight='bold')
        ax2.grid(True, alpha=0.15)
        
        # 3. Wyckoff Phases
        ax3 = fig.add_subplot(gs[1, 1], facecolor='#0a0a0a')
        ax3.plot(df_daily['date'], df_daily['close'], color='white', linewidth=1)
        colors = {'Accumulation': 'lime', 'Markup': 'cyan', 'Distribution': 'orange', 'Markdown': 'red'}
        for ph, c in colors.items():
            dp = df_daily[df_daily['wyckoff_phase'] == ph]
            if not dp.empty: ax3.scatter(dp['date'], dp['close'], color=c, s=15, alpha=0.6, label=ph)
        ax3.legend(loc='upper left', fontsize=8, facecolor='#111')
        ax3.set_title('Wyckoff Phases', color='white', fontweight='bold')
        
        # 4. Volume Profile
        ax4 = fig.add_subplot(gs[2, 0], facecolor='#0a0a0a')
        ax4.bar(df_daily['date'], df_daily['volume'], color='orange', alpha=0.6, width=1)
        ax4.set_title('Volume', color='white', fontweight='bold')
        
        # 5. CVD
        ax5 = fig.add_subplot(gs[2, 1], facecolor='#0a0a0a')
        if 'cumulative_delta' in df_daily.columns:
            ax5.plot(df_daily['date'], df_daily['cumulative_delta'], color='magenta')
            ax5.set_title('Cumulative Delta (CVD)', color='white', fontweight='bold')
            
        # 6. Open Interest
        ax6 = fig.add_subplot(gs[3, 0], facecolor='#0a0a0a')
        oi = data.get('oi_data')
        if oi is not None and not oi.empty:
            for ex in oi['exchange'].unique():
                exd = oi[oi['exchange'] == ex]
                ax6.plot(exd['date'], exd['openInterestAmount'], label=ex)
            ax6.legend(loc='upper left', fontsize=8, facecolor='#111')
        ax6.set_title('Open Interest', color='white', fontweight='bold')
        
        # 7. Funding Rates
        ax7 = fig.add_subplot(gs[3, 1], facecolor='#0a0a0a')
        fr = data.get('funding_rates')
        if fr is not None and not fr.empty:
            for ex in fr['exchange'].unique():
                exd = fr[fr['exchange'] == ex]
                ax7.plot(exd['date'], exd['fundingRate'], label=ex)
            ax7.axhline(0, color='white', linestyle='--', alpha=0.5)
            ax7.legend(loc='upper left', fontsize=8, facecolor='#111')
        ax7.set_title('Funding Rates', color='white', fontweight='bold')
        
        # 8. Signals
        ax8 = fig.add_subplot(gs[4, :], facecolor='#0a0a0a')
        sdl = df_daily.copy()
        sdl['b'] = np.where(sdl['signal']==1, sdl['signal_strength'], 0)
        sdl['s'] = np.where(sdl['signal']==-1, sdl['signal_strength'], 0)
        ax8.bar(sdl['date'], sdl['b'], color='lime', alpha=0.7, label='BUY')
        ax8.bar(sdl['date'], -sdl['s'], color='red', alpha=0.7, label='SELL')
        ax8.set_title('Signals Strength', color='white', fontweight='bold')
        ax8.legend(loc='upper left', fontsize=8, facecolor='#111')
        
        # 9. Table
        ax9 = fig.add_subplot(gs[5, :], facecolor='#0a0a0a')
        ax9.axis('off')
        recent = df_daily[df_daily['signal']!=0].tail(8)
        if not recent.empty:
            tdata = []
            for _, r in recent.iterrows():
                tdata.append([r['date'].strftime('%Y-%m-%d'), "BUY" if r['signal']==1 else "SELL", 
                             f"{r['signal_strength']}/10", r['signal_reason'][:80]])
            tab = ax9.table(cellText=tdata, colLabels=['Date', 'Signal', 'Strength', 'Reason'],
                            loc='center', colWidths=[0.15, 0.1, 0.1, 0.65])
            tab.auto_set_font_size(False)
            tab.set_fontsize(10)
            tab.scale(1, 1.5)
            for i in range(len(tdata)):
                if tdata[i][1] == "BUY": tab[(i+1, 1)].set_facecolor('#0f3b1b')
                else: tab[(i+1, 1)].set_facecolor('#401216')
                
        for ax in [ax1, ax2, ax3, ax4, ax5, ax6, ax7, ax8]:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            ax.tick_params(colors='white')
            for sp in ax.spines.values(): sp.set_color('#333')
            ax.grid(True, alpha=0.15)
            
        plt.tight_layout(rect=[0, 0, 1, 0.98])
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=150, facecolor='#0a0a0a', bbox_inches='tight')
        plt.close(fig)
        buf.seek(0)
        return buf
