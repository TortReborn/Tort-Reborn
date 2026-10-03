from time import monotonic

WYNN_ITEM_DATABASE_URL = "https://api.wynncraft.com/v3/item/database?fullResult"
DATABASE_TTL_SECONDS = 6 * 60 * 60
MAX_DATABASE_RESPONSE = 24 * 1024 * 1024


def _ranged_keys(entry: dict) -> set[str]:
    return {
        key for key, value in (entry.get("identifications") or {}).items()
        if isinstance(value, dict)
    }


def _covers(entry: dict, identifications: list[dict]) -> bool:
    ranges = entry.get("identifications") or {}
    for identification in identifications:
        stat_range = ranges.get(identification["key"])
        if not isinstance(stat_range, dict):
            return False
        value = identification["value"]
        bounds = (stat_range.get("min"), stat_range.get("max"))
        if not all(
            isinstance(bound, (int, float)) and not isinstance(bound, bool) for bound in bounds
        ) or not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        low, high = sorted(bounds)
        if not (low <= value <= high or low <= -value <= high):
            return False
    return True


class WynnItemIndex:
    def __init__(self):
        self._expires = 0.0
        self._by_name: dict[str, list[dict]] = {}

    @property
    def fresh(self) -> bool:
        return bool(self._by_name) and self._expires > monotonic()

    def load(self, entries) -> None:
        if not isinstance(entries, list) or not entries:
            raise ValueError("Invalid Wynncraft item database")
        by_name: dict[str, list[dict]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            internal_name = entry.get("internalName")
            if not isinstance(internal_name, str) or not internal_name:
                continue
            if not isinstance(entry.get("identifications"), dict):
                continue
            for name in (internal_name, entry.get("displayName")):
                if isinstance(name, str) and name and entry not in by_name.setdefault(name, []):
                    by_name[name].append(entry)
        if not by_name:
            raise ValueError("Wynncraft item database has no usable entries")
        self._by_name = by_name
        self._expires = monotonic() + DATABASE_TTL_SECONDS

    def resolve(self, name: str, identifications: list[dict]) -> dict:
        candidates = self._by_name.get(name) or []
        if not candidates:
            raise ValueError(f"No Wynncraft item named {name!r}")
        if len(candidates) == 1:
            return candidates[0]
        rolled = [
            identification for identification in identifications
            if identification.get("kind") == "actual"
        ]
        matching = [
            entry for entry in candidates
            if _ranged_keys(entry) == {identification["key"] for identification in rolled}
        ]
        if len(matching) == 1:
            return matching[0]
        covering = [entry for entry in (matching or candidates) if _covers(entry, rolled)]
        if len(covering) == 1:
            return covering[0]
        raise ValueError(f"Ambiguous Wynncraft item name {name!r}")
