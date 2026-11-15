"""Offline tests for read-only DPP handling (no Home Assistant, no device)."""
import importlib.util
import pathlib

import pytest
from e3dc._rscpLib import rscpDecode, rscpEncode
from e3dc._rscpTags import RscpTag, RscpType

_p = pathlib.Path(__file__).parents[1] / "custom_components/e3dc_rscp/dpp.py"
_spec = importlib.util.spec_from_file_location("dpp", _p)
dpp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dpp)


def wire(tag, typ, value):
    """Encode and decode like a real RSCP round-trip."""
    return rscpDecode(rscpEncode(tag, typ, value))[0]


GOOD = {
    "price-limit": wire(RscpTag.EMS_DPP_PRICE_LIMIT_BATTERY, RscpType.Float32, 35.0),
    "soc": wire(RscpTag.EMS_DPP_SOC_BATTERY, RscpType.UChar8, 100),
    "months": wire(RscpTag.EMS_DPP_MONTHS_ACTIVE, RscpType.Uint16, 3599),
    "enabled": wire(RscpTag.EMS_DPP_PRICE_BASED_BATTERY_CHARGE_ENABLED, RscpType.Bool, True),
}


def test_good_values_all_four():
    raw = {k: dpp.validate_response(k, v) for k, v in GOOD.items()}
    vals = dpp.evaluate(raw)
    assert vals == {
        "dpp-price-limit-battery": 35.0,
        "dpp-soc-battery": 100,
        "dpp-months-active": "Jan, Feb, Mar, Apr, Oct, Nov, Dec",
        "dpp-months-active-raw": 3599,
        "dpp-charge-enabled": True,
    }


def test_float32_rounding():
    v = wire(RscpTag.EMS_DPP_PRICE_LIMIT_BATTERY, RscpType.Float32, 34.9)
    assert v[2] == pytest.approx(34.900001525878906)
    assert dpp.validate_response("price-limit", v) == 34.9


def test_error_frame_is_unknown():
    err = ("EMS_DPP_PRICE_LIMIT_BATTERY", "Error", 4)
    assert dpp.validate_response("price-limit", err) is None


@pytest.mark.parametrize("key,bad", [
    ("price-limit", ("EMS_DPP_SOC_BATTERY", "Float32", 35.0)),        # wrong tag
    ("price-limit", ("EMS_DPP_PRICE_LIMIT_BATTERY", "Double64", 35.0)),  # wrong type
    ("price-limit", ("EMS_DPP_PRICE_LIMIT_BATTERY", "Float32", -1.0)),   # range
    ("soc", ("EMS_DPP_SOC_BATTERY", "UChar8", 101)),
    ("soc", ("EMS_DPP_SOC_BATTERY", "UChar8", True)),
    ("months", ("EMS_DPP_MONTHS_ACTIVE", "Uint16", "3599")),
    ("enabled", ("EMS_DPP_PRICE_BASED_BATTERY_CHARGE_ENABLED", "Bool", 1)),
    ("enabled", None),
    ("enabled", ()),
])
def test_unexpected_responses_are_unknown(key, bad):
    assert dpp.validate_response(key, bad) is None


def test_partial_read_gives_all_unknown():
    raw = {k: dpp.validate_response(k, v) for k, v in GOOD.items()}
    raw["soc"] = None
    assert dpp.evaluate(raw) is None
    assert dpp.evaluate(None) is None
    assert dpp.evaluate({}) is None


def test_months_text():
    assert dpp.months_text(0) == "none"
    assert dpp.months_text(1) == "Jan"
    assert dpp.months_text(0x0800) == "Dec"


def test_unknown_response_tag_fails_decode():
    """A response tag missing from pye3dc (e.g. 0x01800054) cannot be decoded.

    This is why the unverified 'charging active' read is not requested.
    """
    import struct
    frame_tag = 0x01800054
    data = struct.pack("<IBH", frame_tag, RscpType.Bool.value, 1) + b"\x01"
    with pytest.raises(ValueError):
        rscpDecode(data)


def test_installed_distribution_matches_ha_requirement():
    from importlib.metadata import version
    from urllib.parse import urlparse
    from packaging.requirements import Requirement
    import json
    manifest = json.loads((_p.parent / "manifest.json").read_text())
    req = manifest["requirements"][0]
    r = Requirement(urlparse(req).fragment)
    assert r.name == "pye3dc" and r.url is None
    assert r.specifier.contains(version("pye3dc"), prereleases=True)
    assert not r.specifier.contains("0.10.0", prereleases=True)
