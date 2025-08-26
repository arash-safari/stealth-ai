"""
Business profile: central place to declare brand, services, service areas,
fees policy text, and technician skill tags. Tools can read from here
instead of hardcoding. Swap this out per brand/location.
"""

BUSINESS_PROFILE = {
    "brand": "ACME Plumbing",
    "service_area_zips": ["94107","94110","94112"],
    "surcharge_zips": ["94016"],
    "fees": {
        "callout": 59,
        "after_hours_emergency": 129,
        "overtime_multiplier": 1.5,
        "tz": "America/Los_Angeles",
        "disclosure_weekday": "The standard callout is $59 on weekdays 8–6.",
        "disclosure_after_hours": "Emergency after-hours visits are $129.",
        "disclosure_overtime": "Overtime rates may apply (×1.5)."
    },
    "services": [
        {"key": "leak_repair", "label": "Leak repair", "skills": ["leak repair"], "desc": "Fix active/slow leaks on pipes and fixtures."},
        {"key": "dripping_faucet", "label": "Dripping faucet", "skills": ["dripping faucet"], "desc": "Repair/replace cartridges, aerators, seals."},
        {"key": "water_heater", "label": "Water heater (repair/replace)", "skills": ["water heater leak","water heaters"], "desc": "Pilot/power issues, leaks, replacements."},
        {"key": "drain_clog", "label": "Drain/Clog clearing", "skills": ["drain/clog"], "desc": "Kitchen, bath, main line clogs."},
        {"key": "installations", "label": "Installations", "skills": ["installations"], "desc": "Faucets, disposals, toilets, supply lines."}
    ],
    "voice": {
        "style": "friendly, concise, professional",
        "no_jargon": True
    }
}

def list_services_text() -> str:
    return ", ".join(s["label"] for s in BUSINESS_PROFILE.get("services", []))
