#!/usr/bin/env python3
"""
Peak Scheduler — Asia/Shanghai 14:00-18:00 UTC+8 peak hours.

Solo per decisione R3-#5: il task in corso finisce SEMPRE, il blocco
riguarda solo l'inizio di nuovi task in fascia peak.
"""
from datetime import datetime, timezone
from dataclasses import dataclass

TZ = "Asia/Shanghai"

# Lazy import per ZoneInfo (Python 3.9+)
_zone_cache = {}


def _tz():
    if TZ not in _zone_cache:
        try:
            from zoneinfo import ZoneInfo
            _zone_cache[TZ] = ZoneInfo(TZ)
        except ImportError:
            # Fallback: UTC+8 fixed offset
            from datetime import timedelta

            _zone_cache[TZ] = timezone(timedelta(hours=8))
    return _zone_cache[TZ]
PEAK_START, PEAK_END = 14, 18  # 14:00-18:00 UTC+8

ALERT_LOG = None  # init_lazy


def _alert_log():
    global ALERT_LOG
    if ALERT_LOG is None:
        from pathlib import Path
        ALERT_LOG = Path.home() / ".claude" / "logs" / "glm-peak-alerts.log"
    return ALERT_LOG


def is_peak_hour() -> bool:
    """Ritorna True se siamo in fascia peak Asia/Shanghai."""
    return PEAK_START <= datetime.now(_tz()).hour < PEAK_END


def should_block_glm_model(tier: str) -> bool:
    """TOP e TURBO bloccati in peak — MID (GLM-4.7) continua sempre."""
    return is_peak_hour() and tier in ("TOP", "TURBO")


def cost_multiplier(model: str) -> float:
    """Moltiplicatore costo per fascia peak (3x) o normale (1x)."""
    return 3.0 if is_peak_hour() else 1.0


def scheduling_status() -> dict:
    """Stato per /health endpoint."""
    return {
        "peak_active": is_peak_hour(),
        "timezone": TZ,
        "peak_hours": f"{PEAK_START:02d}:00-{PEAK_END:02d}:00 {TZ}",
    }
