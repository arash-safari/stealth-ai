from __future__ import annotations

import re
from typing import Dict, Any

# Use mock business profile to determine service area/surcharge.
# In production, your validator might call a geo/address API and scheduler coverage API instead.
try:
    from mocks.mock_business_profile import BUSINESS_PROFILE
except Exception:
    BUSINESS_PROFILE = {
        "service_area_zips": [],
        "surcharge_zips": [],
    }

ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")


def _normalize_display(address: Dict[str, Any]) -> str:
    parts = []
    line1 = (address.get("line1") or "").strip()
    unit = (address.get("unit") or "").strip()
    city = (address.get("city") or "").strip()
    state = (address.get("state") or "").strip()
    zipc = (address.get("zip") or "").strip()
    country = (address.get("country") or "").strip()

    if line1:
        parts.append(line1)
    if unit:
        parts.append(unit)
    if city:
        parts.append(city)
    if state:
        parts.append(state)
    if zipc:
        parts.append(zipc)
    if country and country.upper() not in ("US", "USA", "UNITED STATES"):
        parts.append(country)

    return ", ".join(parts)


def validate_address(address: Dict[str, Any]) -> Dict[str, Any]:
    """
    Mock address validation.
    - Basic ZIP sanity via regex.
    - Flags service-area and surcharge using BUSINESS_PROFILE.
    - Returns a structure compatible with the agent's expectations.

    In production:
      * Call a real address normalization/verification API (e.g., USPS, Loqate, Google, etc.).
      * Call your scheduler/coverage service to determine serviceability by geo.
      * Return lat/lng, deliverability, DPV, etc., as needed.
    """
    line1 = (address.get("line1") or "").strip()
    zipc = (address.get("zip") or "").strip()

    looks_like_zip = bool(ZIP_RE.fullmatch(zipc))
    has_line = bool(line1)

    is_valid = has_line and looks_like_zip

    in_service = zipc in set(BUSINESS_PROFILE.get("service_area_zips", []))
    in_surcharge = zipc in set(BUSINESS_PROFILE.get("surcharge_zips", []))

    normalized = _normalize_display(address)

    # Heuristic confidence: 0.9 with both; lower otherwise
    confidence = 0.9 if is_valid else 0.3
    if is_valid and not in_service:
        # valid address, but outside coverage → keep high-ish confidence on address, not on coverage
        confidence = 0.8

    return {
        "is_valid": is_valid,
        "normalized": normalized,
        "zip": zipc,
        "in_service_area": in_service,
        "in_surcharge_zip": in_surcharge,
        "confidence": confidence,
        # Room for richer metadata from a real service:
        # "geo": {"lat": ..., "lng": ...},
        # "deliverability": "DPV_CONFIRMED" | "DPV_MISSING" | "UNKNOWN",
        # "components": {...},
        # "notes": "...",
    }


__all__ = ["validate_address"]
