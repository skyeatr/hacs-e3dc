"""Handling of E3DC dynamic power price (DPP / AI 360) settings.

Reading all four values; writing only the battery price limit (ha2dpp3).

Pure functions without Home Assistant imports, so they can be unit tested.
"""

from __future__ import annotations

from typing import Any

from e3dc._rscpTags import RscpTag, RscpType

# key -> (request tag, expected response tag, expected response type)
DPP_READS: dict[str, tuple[RscpTag, RscpTag, RscpType]] = {
    "price-limit": (
        RscpTag.EMS_REQ_DPP_PRICE_LIMIT_BATTERY,
        RscpTag.EMS_DPP_PRICE_LIMIT_BATTERY,
        RscpType.Float32,
    ),
    "soc": (
        RscpTag.EMS_REQ_DPP_SOC_BATTERY,
        RscpTag.EMS_DPP_SOC_BATTERY,
        RscpType.UChar8,
    ),
    "months": (
        RscpTag.EMS_REQ_DPP_MONTHS_ACTIVE,
        RscpTag.EMS_DPP_MONTHS_ACTIVE,
        RscpType.Uint16,
    ),
    "enabled": (
        RscpTag.EMS_REQ_DPP_PRICE_BASED_BATTERY_CHARGE_ENABLED,
        RscpTag.EMS_DPP_PRICE_BASED_BATTERY_CHARGE_ENABLED,
        RscpType.Bool,
    ),
}

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

ENTITY_KEYS = (
    "dpp-price-limit-battery",
    "dpp-soc-battery",
    "dpp-months-active",
    "dpp-months-active-raw",
    "dpp-charge-enabled",
)


def _name(value: Any) -> Any:
    """Return the enum name if value is an enum, otherwise the value itself."""
    return getattr(value, "name", value)


def validate_response(key: str, data: Any) -> Any:
    """Return the value of a DPP response, or None if it is not exactly as expected.

    Checks response tag, response type and Python value type/range.
    """
    _, resp_tag, resp_type = DPP_READS[key]
    if not isinstance(data, (tuple, list)) or len(data) < 3:
        return None
    if _name(data[0]) != resp_tag.name or _name(data[1]) != resp_type.name:
        return None
    value = data[2]
    if key == "price-limit":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if not 0.0 <= float(value) <= 200.0:
            return None
        return round(float(value), 2)
    if key == "soc":
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            return None
        return value
    if key == "months":
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF:
            return None
        return value
    if key == "enabled":
        return value if isinstance(value, bool) else None
    return None


def months_text(mask: int) -> str:
    """Translate the month bit mask (bit 0 = January) to a readable list."""
    text = ", ".join(name for bit, name in enumerate(MONTHS) if mask & (1 << bit))
    return text or "none"


def evaluate(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Build entity values from validated raw values.

    All-or-nothing: returns None (-> all entities unknown, no new timestamp)
    unless all four values were read and validated.
    """
    if not raw or any(raw.get(key) is None for key in DPP_READS):
        return None
    return {
        "dpp-price-limit-battery": raw["price-limit"],
        "dpp-soc-battery": raw["soc"],
        "dpp-months-active": months_text(raw["months"]),
        "dpp-months-active-raw": raw["months"],
        "dpp-charge-enabled": raw["enabled"],
    }


MANUAL_REFRESH_MIN_INTERVAL = 60.0


def manual_refresh_allowed(now: float, last_manual: float | None,
                           min_interval: float = MANUAL_REFRESH_MIN_INTERVAL) -> bool:
    """Rate limit for the manual 'read DPP now' button."""
    return last_manual is None or now - last_manual >= min_interval


# --- ha2dpp3: write the battery price limit only -------------------------

PRICE_LIMIT_MIN = 0.0
PRICE_LIMIT_MAX = 100.0
PRICE_LIMIT_WRITE_MIN_INTERVAL = 300.0
PRICE_LIMIT_READBACK_TOLERANCE = 0.05


def normalize_price_limit(value: Any) -> float | None:
    """Return the requested price limit in ct/kWh rounded to 0.1, or None if invalid."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = round(float(value), 1)
    if value != value or not PRICE_LIMIT_MIN <= value <= PRICE_LIMIT_MAX:  # NaN check
        return None
    return value


def price_write_allowed(now: float, last_write: float | None,
                        min_interval: float = PRICE_LIMIT_WRITE_MIN_INTERVAL) -> bool:
    """Rate limit for writing the price limit (protects the E3DC flash/EMS)."""
    return last_write is None or now - last_write >= min_interval


def validate_set_response(data: Any) -> bool:
    """True if the device answered the SET request with its own response tag.

    The value itself is not trusted; the price limit is always read back.
    """
    if not isinstance(data, (tuple, list)) or len(data) < 2:
        return False
    if _name(data[1]) == "Error":
        return False
    return _name(data[0]) == RscpTag.EMS_DPP_SET_PRICE_LIMIT_BATTERY.name


def readback_matches(requested: float, readback: Any,
                     tolerance: float = PRICE_LIMIT_READBACK_TOLERANCE) -> bool:
    """Compare requested and read-back price limit (Float32 rounding tolerated)."""
    if readback is None or isinstance(readback, bool):
        return False
    return abs(float(readback) - float(requested)) <= tolerance
