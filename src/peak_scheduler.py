#!/usr/bin/env python3
"""
Peak Scheduler — Asia/Shanghai 14:00-18:00 UTC+8 peak hours.

Solo per decisione R3-#5: il task in corso finisce SEMPRE, il blocco
riguarda solo l'inizio di nuovi task in fascia peak.
"""
from datetime import datetime, timezone

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

# _alert_log e ALERT_LOG rimosse il 2026-08-03: la funzione non era mai chiamata
# e la variabile la leggeva solo lei.


def is_peak_hour() -> bool:
    """Ritorna True se siamo in fascia peak Asia/Shanghai.

    Il weekend NON e' mai peak: la doc z.ai (devpack/notice/usage-revision,
    letta il 2026-08-18) dice "Peak hours: Monday to Friday, 14:00-18:00
    Singapore Standard Time (UTC+8)" e "usage on weekends will be deducted at
    off-peak rates all day". Prima il sabato e la domenica fra le 14 e le 18
    il cap declassava glm-5.3 a glm-4.7 per un sovrapprezzo che non esisteva.
    """
    now = datetime.now(_tz())
    if now.weekday() >= 5:  # 5 = sabato, 6 = domenica
        return False
    return PEAK_START <= now.hour < PEAK_END


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
