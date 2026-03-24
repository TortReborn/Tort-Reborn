import json
import os

from Helpers.territory_abbrevs import TERRITORY_TO_ABBREV

_TERRITORIES_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "territories_verbose.json")
with open(_TERRITORIES_PATH, encoding="utf-8") as f:
    _TERRITORIES = json.load(f)

_ROUTE_COUNTS_BY_TERRITORY = {
    territory_name: len(territory_data.get("Trading Routes", []))
    for territory_name, territory_data in _TERRITORIES.items()
}

# full name → abbrev (all 406); used by normalize_hq_for_storage
_STORAGE_BY_CANONICAL_NAME = TERRITORY_TO_ABBREV

# abbrev → full name (all 406); used by display_hq
_TERRITORY_FROM_ABBREV = {abbrev: name for name, abbrev in TERRITORY_TO_ABBREV.items()}

# case-insensitive lookup: "cwr" → "Corrupted Warfront", "corrupted warfront" → "Corrupted Warfront"
_ALIASES: dict[str, str] = {}
for _full_name, _abbrev in TERRITORY_TO_ABBREV.items():
    _ALIASES[_abbrev.casefold()] = _full_name
    _ALIASES[_full_name.casefold()] = _full_name

_ALIASES["nomad's refuge".casefold()] = "Nomads' Refuge"
_ALIASES["nomad's refugee".casefold()] = "Nomads' Refuge"
_ALIASES["almuji".casefold()] = "Almuj"


def get_canonical_territory_name(hq_value: str) -> str | None:
    raw = (hq_value or "").strip()
    if not raw:
        return None
    if raw in _ROUTE_COUNTS_BY_TERRITORY:
        return raw
    return _ALIASES.get(raw.casefold())


def normalize_hq_for_storage(hq_value: str) -> str:
    canonical_name = get_canonical_territory_name(hq_value)
    if canonical_name is None:
        return (hq_value or "").strip()
    return _STORAGE_BY_CANONICAL_NAME.get(canonical_name, canonical_name)


def display_hq(hq_value: str) -> str:
    raw = (hq_value or "").strip()
    if raw in _TERRITORY_FROM_ABBREV:
        return _TERRITORY_FROM_ABBREV[raw]
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
