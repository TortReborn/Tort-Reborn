import json
import os


HQ_LOCATIONS = {
    "BT": "Bandit's Toll",
    "CC": "Corkus City",
    "CO": "Cinfras Outskirts",
    "BTRAIL": "Bloody Trail",
    "CI": "Central Islands",
    "NWE": "Nivla Woods Exit",
    "PTT": "Path to Talor",
    "CW": "Corrupted Warfront",
    "NN": "Nodguj Nation",
    "NR": "Nomads' Refuge",
    "MBP": "Mine Base Plains",
    "AL": "Almuj",
}

HQ_CHOICES = list(HQ_LOCATIONS.keys())

_TERRITORIES_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "territories_verbose.json")
with open(_TERRITORIES_PATH, encoding="utf-8") as f:
    _TERRITORIES = json.load(f)

_ROUTE_COUNTS_BY_TERRITORY = {
    territory_name: len(territory_data.get("Trading Routes", []))
    for territory_name, territory_data in _TERRITORIES.items()
}

_STORAGE_BY_CANONICAL_NAME = {
    territory_name: code
    for code, territory_name in HQ_LOCATIONS.items()
}

_ALIASES = {}
for code, territory_name in HQ_LOCATIONS.items():
    _ALIASES[code.casefold()] = territory_name
    _ALIASES[territory_name.casefold()] = territory_name

_ALIASES["nomad's refuge".casefold()] = "Nomads' Refuge"
_ALIASES["nomad's refugee".casefold()] = "Nomads' Refuge"
_ALIASES["almuji".casefold()] = "Almuj"


def get_canonical_territory_name(hq_value: str) -> str | None:
    raw = (hq_value or "").strip()
    if not raw:
        return None
    if raw in _ROUTE_COUNTS_BY_TERRITORY:
        return raw
    return _ALIASES.get(raw.casefold(), raw if raw in _ROUTE_COUNTS_BY_TERRITORY else None)


def normalize_hq_for_storage(hq_value: str) -> str:
    canonical_name = get_canonical_territory_name(hq_value)
    if canonical_name is None:
        return (hq_value or "").strip()
    return _STORAGE_BY_CANONICAL_NAME.get(canonical_name, canonical_name)


def display_hq(hq_value: str) -> str:
    raw = (hq_value or "").strip()
    if raw in HQ_LOCATIONS:
        return HQ_LOCATIONS[raw]
    canonical_name = get_canonical_territory_name(raw)
    return canonical_name or raw


def get_max_conns(hq_value: str) -> int | None:
    canonical_name = get_canonical_territory_name(hq_value)
    if canonical_name is None:
        return None
    return _ROUTE_COUNTS_BY_TERRITORY.get(canonical_name)


def is_dry(hq_value: str, conns: int) -> bool:
    max_conns = get_max_conns(hq_value)
    return max_conns is not None and conns == max_conns
