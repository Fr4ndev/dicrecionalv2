"""
sentinels/orchestrator.py — Sentinel Orchestrator
═══════════════════════════════════════════════════
Reemplaza GuardianDaemon.py (1211L monolito).
Solo supervisa. No contiene lógica de trading.

Cada sentinel vive en su propio archivo bajo sentinels/.
El orchestrator los lanza como tareas asyncio independientes.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from typing import List, Optional

from intelligence_core.hub import IntelligenceHub
from alerts.gateway import SentinelGateway
from sentinels.base import BaseSentinelTask

logger = logging.getLogger("Orchestrator")


class SentinelOrchestrator:
    """Process-level supervisor for all sentinel tasks.

    Lifecycle:
        1. Connect to IntelligenceHub (singleton)
        2. Instantiate all sentinel tasks
        3. Run asyncio.gather() on all tasks
        4. Handle shutdown gracefully (SIGTERM/SIGINT)
    """

    def __init__(self):
        self.hub: Optional[IntelligenceHub] = None
        self.gateway: Optional[SentinelGateway] = None
        self.sentinels: List[BaseSentinelTask] = []
        self._tasks: List[asyncio.Task] = []
        self._running = True

    def register(self, sentinel_cls, **kwargs):
        """Lazy-load sentinels. Import only what's enabled."""
        if not self.hub or not self.gateway:
            raise RuntimeError("Call orchestrator.setup() before registering sentinels.")
        sentinel = sentinel_cls(self.hub, self.gateway, **kwargs)
        if sentinel.is_enabled:
            self.sentinels.append(sentinel)
            logger.info(f"Registered: {sentinel.name}")

    async def setup(self):
        """Initialize hub + gateway connections."""
        from intelligence_core.hub import IntelligenceHub
        from alerts.gateway import SentinelGateway

        self.hub = IntelligenceHub()
        await self.hub.connect()
        self.gateway = SentinelGateway.instance()
        logger.info("Hub + Gateway connected")

    async def run(self):
        """Launch all registered sentinels as concurrent tasks."""
        await self.setup()
        self._load_sentinels()

        self._tasks = [asyncio.create_task(s.run(), name=s.name) for s in self.sentinels]
        logger.info(f"Orchestrator running {len(self._tasks)} sentinels")

        try:
            await asyncio.gather(*self._tasks)
        except asyncio.CancelledError:
            logger.info("Shutdown signal received")
            await self.shutdown()

    def _load_sentinels(self):
        """Import and register only enabled sentinels."""
        import os

        # Each sentinel can be disabled via env var (e.g. DISABLE_SQUEEZE=1)
        if os.getenv("DISABLE_IGNITION", "0") != "1":
            from sentinels.ignition_bridge import IgnitionBridgeSentinel
            self.register(IgnitionBridgeSentinel)

        if os.getenv("DISABLE_SQUEEZE", "0") != "1":
            from sentinels.squeeze_monitor import SqueezeMonitorSentinel
            self.register(SqueezeMonitorSentinel)

        if os.getenv("DISABLE_SPOOF", "0") != "1":
            from sentinels.spoof_detector import SpoofDetectorSentinel
            self.register(SpoofDetectorSentinel)

        if os.getenv("DISABLE_VOLUME", "0") != "1":
            from sentinels.volume_monitor import VolumeMonitorSentinel
            self.register(VolumeMonitorSentinel)

        if os.getenv("DISABLE_WHALE", "0") != "1":
            from sentinels.whale_monitor import WhaleMonitorSentinel
            self.register(WhaleMonitorSentinel)

        if os.getenv("DISABLE_SFP", "0") != "1":
            from sentinels.sfp_sentinel import SFPSentinel
            self.register(SFPSentinel)

        if os.getenv("DISABLE_LEVELBREAK", "0") != "1":
            from sentinels.level_break import LevelBreakSentinel
            self.register(LevelBreakSentinel)

        if os.getenv("DISABLE_OPPORTUNITY", "0") != "1":
            from sentinels.opportunity import OpportunitySentinel
            self.register(OpportunitySentinel)

        if os.getenv("DISABLE_SENIOR_AUDIT", "0") != "1":
            from sentinels.senior_audit import SeniorAuditSentinel
            self.register(SeniorAuditSentinel)

    async def shutdown(self):
        """Graceful shutdown: cancel all tasks, close connections."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.hub:
            await self.hub.close()
        logger.info("Orchestrator shut down")


async def main():
    orchestrator = SentinelOrchestrator()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(orchestrator.shutdown()))

    await orchestrator.run()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(message)s')
    asyncio.run(main())
