"""
shared/config.py — Minimal Configuration
=========================================
Extracted from ccxtv2 core/config.py. Standalone config loader
for action servers. Falls back to defaults if no settings.yaml.
"""

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class TickerConfig:
    symbol: str = "BTC/USDT:USDT"
    spot: str = "BTC/USDT"
    name: str = "Bitcoin"
    circulating_supply: int = 19800000


@dataclass
class ExchangeConfig:
    id: str = "binance"
    type: str = "swap"
    rate_limit: bool = True
    timeout: int = 30000


@dataclass
class ThresholdsConfig:
    zscore_overvalued: float = 2.0
    zscore_undervalued: float = -2.0
    absorption_z_threshold: float = 2.0
    kelly_risk_reward: float = 2.0
    significant_delta_std: float = 1.5
    fractal_window: int = 5
    heatmap_bins: int = 50
    heatmap_smooth_sigma: int = 3


@dataclass
class Settings:
    universe: List[TickerConfig] = field(default_factory=lambda: [
        TickerConfig("BTC/USDT:USDT", "BTC/USDT", "Bitcoin", 19800000),
        TickerConfig("ETH/USDT:USDT", "ETH/USDT", "Ethereum", 120000000),
    ])
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    thresholds: ThresholdsConfig = field(default_factory=ThresholdsConfig)
    telegram_token: str = ""
    chat_id: str = ""

    def get_ticker(self, query: str) -> Optional[TickerConfig]:
        q = query.upper().strip()
        for t in self.universe:
            if q in t.symbol.upper() or q in t.name.upper():
                return t
        return None

    def get_all_symbols(self) -> List[str]:
        return [t.symbol for t in self.universe]

    @property
    def default_ticker(self) -> TickerConfig:
        return self.universe[0] if self.universe else TickerConfig()


def _load_settings() -> Settings:
    """Load from settings.yaml if available, else defaults."""
    try:
        import yaml
        from dotenv import load_dotenv
        load_dotenv()

        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "config", "settings.yaml")
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                raw = yaml.safe_load(f) or {}

            universe = [
                TickerConfig(
                    symbol=item.get("symbol", ""),
                    spot=item.get("spot", ""),
                    name=item.get("name", ""),
                    circulating_supply=item.get("circulating_supply", 0),
                )
                for item in raw.get("universe", [])
            ] or Settings.universe

            ex_raw = raw.get("exchange", {})
            exchange = ExchangeConfig(
                id=ex_raw.get("id", "binance"),
                type=ex_raw.get("type", "swap"),
                rate_limit=ex_raw.get("rate_limit", True),
                timeout=ex_raw.get("timeout", 30000),
            )

            return Settings(
                universe=universe,
                exchange=exchange,
                telegram_token=os.getenv("TELEGRAM_TOKEN", ""),
                chat_id=os.getenv("CHAT_ID", ""),
            )
    except ImportError:
        pass

    return Settings()


settings = _load_settings()
