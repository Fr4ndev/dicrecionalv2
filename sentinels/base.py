"""
sentinels/base.py — Base Sentinel Task
════════════════════════════════════════
Mixin base for all sentinel tasks.
Extracted from original daemons/base.py.
"""

import asyncio
import logging
import os
import traceback
from typing import Optional

logger = logging.getLogger("BaseSentinel")


class BaseSentinelTask:
    """
    Mixin base for all sentinel tasks.
    Each task runs as an asyncio coroutine and shares the hub + gateway.
    """

    name: str = "BaseSentinel"
    poll_interval: float = 30.0
    enabled_env_var: str = ""

    def __init__(self, hub, gateway):
        self.hub = hub
        self.gateway = gateway
        self._running = True
        self._cycles = 0
        self._errors = 0
        self.log = logging.getLogger(self.name)

    @property
    def is_enabled(self) -> bool:
        if self.enabled_env_var:
            return os.getenv(self.enabled_env_var, "0") != "1"
        return True

    async def run(self) -> None:
        """Supervised run loop. Override _cycle() in subclasses."""
        if not self.is_enabled:
            self.log.info(f"{self.name} DISABLED by env var.")
            return

        self.log.info(f"🚀 {self.name} STARTED (poll={self.poll_interval}s)")
        while self._running:
            self._cycles += 1
            try:
                await self._cycle()
                self._errors = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._errors += 1
                self.log.error(f"[{self.name}] Cycle #{self._cycles} error: {e}\n{traceback.format_exc()}")
                if self._errors > 10:
                    self.log.critical(f"[{self.name}] Too many errors, stopping.")
                    break
            await asyncio.sleep(self.poll_interval)

        self.log.info(f"{self.name} stopped ({self._cycles} cycles)")

    async def _cycle(self) -> None:
        """Override in subclass with the actual monitoring logic."""
        raise NotImplementedError

    async def alert(self, priority: int, text: str, dedup_key: str = "") -> None:
        """Convenience: dispatch a formatted alert through the gateway."""
        from alerts.gateway import AlertMessage
        msg = AlertMessage(source=self.name, priority=priority, text=text, dedup_key=dedup_key)
        await self.gateway.dispatch(msg)

    def stop(self) -> None:
        self._running = False
