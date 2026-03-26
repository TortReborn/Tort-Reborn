import json
import os

_TERRITORIES_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "territories_verbose.json")
with open(_TERRITORIES_PATH, encoding="utf-8") as f:
    _TERRITORIES = json.load(f)

_ROUTE_COUNTS_BY_TERRITORY = {
    territory_name: len(territory_data.get("Trading Routes", []))
    for territory_name, territory_data in _TERRITORIES.items()
}

# case-insensitive full name → canonical name
_CANONICAL_BY_CASEFOLD = {name.casefold(): name for name in _ROUTE_COUNTS_BY_TERRITORY}

ALL_TERRITORY_NAMES: list[str] = sorted(_ROUTE_COUNTS_BY_TERRITORY)


def normalize_hq_for_storage(hq_value: str) -> str | None:
    return _CANONICAL_BY_CASEFOLD.get((hq_value or "").strip().casefold())


def display_hq(hq_value: str) -> str:
    canonical = _CANONICAL_BY_CASEFOLD.get((hq_value or "").strip().casefold())
    return canonical or (hq_value or "").strip()


def get_max_conns(hq_value: str) -> int | None:
    canonical = _CANONICAL_BY_CASEFOLD.get((hq_value or "").strip().casefold())
    if canonical is None:
        return None
    return _ROUTE_COUNTS_BY_TERRITORY.get(canonical)


def is_dry(hq_value: str, conns: int) -> bool:
    max_conns = get_max_conns(hq_value)
    return max_conns is not None and conns == max_conns
