"""
hub_reader.py — Interfaz única para leer métricas reales del Core_Intelligence_Hub.
Todos los módulos del sistema importan desde aquí. NUNCA hardcodear valores.
"""
import logging
import asyncio
from typing import NamedTuple, Optional

logger = logging.getLogger("ccxtv2.hub_reader")

class LiveMetrics(NamedTuple):
    vpin:     float
    basis:    float
    obi:      float
    z_htf:    float
    z_daily:  float
    z_weekly: float
    regime:   str
    symbol:   str
    source:   str   # "hub" | "fallback"

# Fallback SOLO si el hub no responde — nunca como valor operativo
_FALLBACK = LiveMetrics(
    vpin=0.0, basis=0.0, obi=0.0,
    z_htf=0.0, z_daily=0.0, z_weekly=0.0,
    regime="unknown", symbol="unknown", source="fallback"
)

async def get_live_metrics(symbol: str = "BTC/USDT:USDT") -> LiveMetrics:
    """
    Lee métricas en tiempo real del Core_Intelligence_Hub de forma asíncrona.
    """
    try:
        from shared.hub import IntelligenceHub
        
        # El hub debe estar ya inicializado por el daemon principal
        hub = await IntelligenceHub.instance()
        
        # Mapeo de símbolos para base de cálculo de basis (BTC/USDT:USDT -> BTC/USDT)
        spot_sym = symbol.split(":")[0] if ":" in symbol else None
        
        # Obtenemos snapshot completo
        snap = await hub.market_snapshot(symbol, symbol_spot=spot_sym)
        
        return LiveMetrics(
            vpin    = float(snap.toxicity.vpin_index) if snap.toxicity else 0.0,
            basis   = float(snap.basis.basis_pct) if snap.basis else 0.0,
            obi     = float(snap.obi.obi) if snap.obi else 0.0,
            z_htf   = float(snap.funding.zscore_48h) if snap.funding else 0.0, # Usamos zscore_48h de funding para HTF
            z_daily = 0.0, # TODO: implementar en el Hub si es necesario
            z_weekly= 0.0,
            regime  = str(snap.funding.regime) if snap.funding else "unknown",
            symbol  = symbol,
            source  = "hub"
        )
    except Exception as e:
        logger.warning(f"[HubReader] Error leyendo Hub ({e}). Retornando fallback.")
        return _FALLBACK._replace(symbol=symbol)

def get_live_metrics_sync(symbol: str = "BTC/USDT:USDT") -> LiveMetrics:
    """
    Versión síncrona para contextos que no soportan await.
    ADVERTENCIA: Puede bloquear el hilo si el loop no está corriendo.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # No podemos llamar a run_until_complete si el loop ya corre
            # Retornamos fallback o usamos instance_sync (pero el Hub suele requerir await para red)
        from shared.hub import IntelligenceHub
            hub = IntelligenceHub.instance_sync()
            # Si no hay loop corriendo o es síncrono, los métodos async del Hub fallarán
            return _FALLBACK._replace(symbol=symbol)
        return loop.run_until_complete(get_live_metrics(symbol))
    except:
        return _FALLBACK._replace(symbol=symbol)
