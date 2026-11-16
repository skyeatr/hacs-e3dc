"""Handling of E3DC dynamic power price (DPP / AI 360) settings.

Reading all four values; writing only the battery price limit (ha2dpp3).

Pure functions without Home Assistant imports, so they can be unit tested.
"""

from __future__ import annotations

import math
from time import monotonic
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


# --- ha2dpp3: write price limit, target SoC and enable flag ----------------

# key (same as DPP_READS) -> (set request tag, expected set response tag, request type)
DPP_WRITES: dict[str, tuple[RscpTag, RscpTag, RscpType]] = {
    "price-limit": (
        RscpTag.EMS_REQ_DPP_SET_PRICE_LIMIT_BATTERY,
        RscpTag.EMS_DPP_SET_PRICE_LIMIT_BATTERY,
        RscpType.Float32,
    ),
    "soc": (
        RscpTag.EMS_REQ_DPP_SET_SOC_BATTERY,
        RscpTag.EMS_DPP_SET_SOC_BATTERY,
        RscpType.UChar8,
    ),
    "enabled": (
        RscpTag.EMS_REQ_DPP_SET_BATTERY_CHARGE_ENABLED,
        RscpTag.EMS_DPP_SET_BATTERY_CHARGE_ENABLED,
        RscpType.Bool,
    ),
}

PRICE_LIMIT_MIN = 0.0
PRICE_LIMIT_MAX = 100.0
WRITE_MIN_INTERVAL = 300.0
PRICE_LIMIT_READBACK_TOLERANCE = 0.05


def _finite_number(value: Any) -> bool:
    """True for int/float (not bool) that is finite."""
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(float(value)))


def normalize_write_value(key: str, value: Any) -> Any:
    """Return the value to write for key, or None if it is invalid (fail-closed).

    The raw value is range-checked BEFORE rounding, so -0.04 or 100.04 are rejected.
    price-limit: ct/kWh 0-100, rounded to 0.1
    soc:         percent 0-100, whole numbers only
    enabled:     bool only
    """
    if key == "price-limit":
        if not _finite_number(value):
            return None
        if not PRICE_LIMIT_MIN <= float(value) <= PRICE_LIMIT_MAX:
            return None
        return round(float(value), 1)
    if key == "soc":
        if not _finite_number(value):
            return None
        if isinstance(value, float):
            if not value.is_integer():
                return None
            value = int(value)
        if not 0 <= value <= 100:
            return None
        return value
    if key == "enabled":
        return value if isinstance(value, bool) else None
    return None


def normalize_price_limit(value: Any) -> float | None:
    """Backwards compatible helper for the price limit."""
    return normalize_write_value("price-limit", value)


def write_allowed(now: float, last_write: float | None,
                  min_interval: float = WRITE_MIN_INTERVAL) -> bool:
    """Rate limit per DPP value (protects the E3DC EMS from write storms)."""
    return last_write is None or now - last_write >= min_interval


def price_write_allowed(now: float, last_write: float | None,
                        min_interval: float = WRITE_MIN_INTERVAL) -> bool:
    """Backwards compatible helper for the price limit."""
    return write_allowed(now, last_write, min_interval)


def validate_set_response(data: Any, key: str = "price-limit") -> bool:
    """True only if the device answered the SET request with the expected tag.

    Fail-closed: anything else (no tuple, error type, foreign tag) is False.
    pye3dc decodes tag and type as strings (rscpDecode) and raises an exception
    for a top-level RSCP error frame; enum values are accepted as well.
    The value itself is not trusted; the setting is always read back.
    """
    if not isinstance(data, (tuple, list)) or len(data) < 2:
        return False
    if str(_name(data[1])) == "Error":
        return False
    return str(_name(data[0])) == DPP_WRITES[key][1].name


def readback_matches(requested: Any, readback: Any, key: str = "price-limit") -> bool:
    """Compare requested and read-back value; anything unexpected is False."""
    if readback is None:
        return False
    if key == "price-limit":
        if not _finite_number(readback) or not _finite_number(requested):
            return False
        return abs(float(readback) - float(requested)) <= PRICE_LIMIT_READBACK_TOLERANCE
    if key == "soc":
        return (not isinstance(readback, bool) and isinstance(readback, int)
                and readback == requested)
    if key == "enabled":
        return isinstance(readback, bool) and readback is requested
    return False


class DppWriteGate:
    """Per-value rate limit for DPP writes with startup cooldown.

    check_and_record() must be called while holding the DPP asyncio lock, so the
    check and the recorded time are atomic for parallel service calls.
    Uses a monotonic clock (immune to clock changes).

    Safe-direction exception: switching price-based grid charging OFF
    (enabled=false) is allowed once during the startup cooldown, so a fixed-price
    tariff can be enforced right after an HA restart. The normal 5 min interval
    between two writes still applies; there is no exception for switching ON.
    """

    def __init__(self, interval: float = WRITE_MIN_INTERVAL, clock=monotonic) -> None:
        self._interval = interval
        self._clock = clock
        start = clock()
        self._last: dict[str, float] = {key: start for key in DPP_WRITES}
        self._written: set[str] = set()

    def check_and_record(self, key: str, value: Any = None) -> bool:
        now = self._clock()
        safe_off = key == "enabled" and value is False and key not in self._written
        if not safe_off and not write_allowed(now, self._last.get(key), self._interval):
            return False
        self._last[key] = now
        self._written.add(key)
        return True


def strict_service_value(key: str):
    """Voluptuous validator: no silent coercion (90.9 -> 90, "on" -> True, "25" -> 25).

    Raises ValueError, which voluptuous reports as an invalid service value.
    """

    def _validate(value: Any) -> Any:
        result = normalize_write_value(key, value)
        if result is None:
            raise ValueError(f"invalid value for DPP {key}: {value!r}")
        return result

    return _validate
