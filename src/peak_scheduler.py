"""Peak scheduler per AI router proxy - logica oraria costi GLM/z.ai."""

import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

# === Configurazione da environment ===
PEAK_TZ = os.environ.get("AIROUTER_PEAK_TZ", "Asia/Shanghai")
PEAK_START_HOUR = int(os.environ.get("AIROUTER_PEAK_START", "14"))
PEAK_END_HOUR = int(os.environ.get("AIROUTER_PEAK_END", "18"))
PROMO_OFFPEAK_END = os.environ.get("AIROUTER_PROMO_END", "2026-09-30")
GLM_PEAK_3X_MODELS = {
    m.strip().lower()
    for m in os.environ.get("AIROUTER_GLM_3X_MODELS", "glm-5.2,glm-5-turbo").split(",")
    if m.strip()
}
GLM_PEAK_TIER_CAP = os.environ.get("AIROUTER_GLM_PEAK_CAP", "glm-4.7")

# === Funzioni ===

def is_peak_hour(now: datetime | None = None) -> bool:
    """
    True se l'orario corrente (o now passato) cade nella fascia peak [14, 18) Asia/Shanghai.
    Gestisce automaticamente DST tramite zoneinfo.
    """
    if now is None:
        now = datetime.now(ZoneInfo(PEAK_TZ))
    elif now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo(PEAK_TZ))
    else:
        now = now.astimezone(ZoneInfo(PEAK_TZ))
    return PEAK_START_HOUR <= now.hour < PEAK_END_HOUR


def is_glm_model_3x(model: str) -> bool:
    """True se il modello e' nella lista 3x (case-insensitive)."""
    if not model:
        return False
    return model.strip().lower() in GLM_PEAK_3X_MODELS


def should_block_glm_model(model: str, now: datetime | None = None) -> bool:
    """
    True se il modello va evitato per costo: peak E modello 3x.
    Fuori peak: mai bloccato. In peak: solo se 3x.
    """
    return is_peak_hour(now) and is_glm_model_3x(model)


def peak_tier_cap(now: datetime | None = None) -> str | None:
    """Ritorna tier cap GLM in peak, None fuori peak."""
    if is_peak_hour(now):
        return GLM_PEAK_TIER_CAP
    return None


def offpeak_promo_active(today: date | None = None) -> bool:
    """
    True se oggi e' entro la promo off-peak 1x.
    Fallisce robustly su date malformate.
    """
    if today is None:
        today = date.today()
    try:
        promo_end = date.fromisoformat(PROMO_OFFPEAK_END)
        return today <= promo_end
    except (ValueError, TypeError):
        return False


def cost_multiplier(model: str, now: datetime | None = None) -> float:
    """
    Moltiplicatore di costo per (model, orario):
    - peak + 3x -> 3.0
    - peak + non-3x -> 1.0
    - off-peak + promo attiva -> 1.0
    - off-peak + promo scaduta -> 2.0
    """
    if is_peak_hour(now):
        return 3.0 if is_glm_model_3x(model) else 1.0
    return 1.0 if offpeak_promo_active() else 2.0


def scheduling_status(now: datetime | None = None) -> dict:
    """
    Snapshot per /health e logging.
    Include orario locale e Shanghai per debugging timezone.
    """
    now_local = datetime.now()
    now_shanghai = datetime.now(ZoneInfo(PEAK_TZ))
    return {
        "peak": is_peak_hour(now),
        "local_time": now_local.isoformat(),
        "shanghai_time": now_shanghai.isoformat(),
        "tier_cap": peak_tier_cap(now),
        "promo_active": offpeak_promo_active(),
        "promo_ends": PROMO_OFFPEAK_END,
    }


if __name__ == "__main__":
    print("=== Scheduling Status ===")
    print(scheduling_status())

    print("\n=== Test is_peak_hour (Shanghai) ===")
    sh_tz = ZoneInfo(PEAK_TZ)

    # Test edge cases peak
    assert is_peak_hour(datetime(2025, 1, 15, 14, 0, tzinfo=sh_tz)) is True, "14:00 e' peak"
    assert is_peak_hour(datetime(2025, 1, 15, 17, 59, tzinfo=sh_tz)) is True, "17:59 e' peak"
    assert is_peak_hour(datetime(2025, 1, 15, 18, 0, tzinfo=sh_tz)) is False, "18:00 non e' peak"
    assert is_peak_hour(datetime(2025, 1, 15, 13, 59, tzinfo=sh_tz)) is False, "13:59 non e' peak"

    # Test UTC conversion (10:00 UTC = 18:00 Shanghai, non peak)
    utc_tz = ZoneInfo("UTC")
    assert is_peak_hour(datetime(2025, 1, 15, 10, 0, tzinfo=utc_tz)) is False, "10:00 UTC non e' peak"

    # Test naive (assumed Shanghai)
    assert is_peak_hour(datetime(2025, 1, 15, 15, 0)) is True, "15:00 naive = peak Shanghai"

    print("Tutti i test passati!")
