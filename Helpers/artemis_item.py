import json
import math
import os
from functools import lru_cache

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAT_ID_KEYS_PATH = os.path.join(BASE, "data", "stat-id-keys.json")
SHINY_STATS_PATH = os.path.join(BASE, "data", "shiny-stats.json")

GEAR_ITEM_TYPE = 0
CRAFTED_GEAR_ITEM_TYPE = 3
CRAFTED_CONSUMABLE_ITEM_TYPE = 4
MOUNT_ITEM_TYPE = 5
# Wynntils' VERSION_n is written as version byte n - 1.
WYNNTILS_VERSION_3 = 2
WYNNTILS_VERSION_4 = 3
SUPPORTED_VERSIONS = (0, 1, WYNNTILS_VERSION_3, WYNNTILS_VERSION_4)
VANILLA_METER_FLAG = 4
MAX_POWDER_SPECIAL_ID = 10
MAX_POWDER_TIER = 7
MAX_VANILLA_METER_OFFSET = 0x23

GEAR_TYPES = {
    0: "Spear",
    1: "Wand",
    2: "Dagger",
    3: "Bow",
    4: "Relik",
    5: "Ring",
    6: "Bracelet",
    7: "Necklace",
    8: "Helmet",
    9: "Chestplate",
    10: "Leggings",
    11: "Boots",
    12: "Weapon",
    13: "Accessory",
}
ATTACK_SPEEDS = {
    0: "Super Fast",
    1: "Very Fast",
    2: "Fast",
    3: "Normal",
    4: "Slow",
    5: "Very Slow",
    6: "Super Slow",
}
ELEMENTS = {0: "Earth", 1: "Thunder", 2: "Water", 3: "Fire", 4: "Air"}
DAMAGE_TYPES = {**ELEMENTS, 5: "Neutral"}
CLASSES = {0: None, 1: "Mage", 2: "Archer", 3: "Warrior", 4: "Assassin", 5: "Shaman"}
CONSUMABLE_TYPES = {0: "Potion", 1: "Food", 2: "Scroll", 3: "Consumable"}
CONSUMABLE_EFFECTS = {0: "heal", 1: "mana", 2: "duration"}
# name, item suffix, combat level, unlocking quest
MOUNT_TYPES = {
    0: ("Horse", "Saddle", 9, "Stable Story"),
    1: ("Wyvern", "Reins", 84, "The Canyon Guides"),
    2: ("Adasaur", "Harness", 115, "Burning Bonds"),
}
MOUNT_STATS = {
    0: "Speed",
    1: "Acceleration",
    2: "Jump Height",
    3: "Altitude",
    4: "Energy",
    5: "Handling",
    6: "Toughness",
    7: "Boost",
    8: "Training",
}
MAX_MOUNT_POTENTIAL = 80_000
MAX_MOUNT_STAT = 10_000


@lru_cache(maxsize=1)
def stat_id_keys() -> tuple[str, ...]:
    with open(STAT_ID_KEYS_PATH, encoding="utf-8-sig") as file:
        keys = json.load(file)
    if not isinstance(keys, list) or not keys or not all(isinstance(key, str) and key for key in keys):
        raise ValueError("Invalid stat-id-keys.json")
    return tuple(keys)


@lru_cache(maxsize=1)
def shiny_stat_names() -> dict:
    with open(SHINY_STATS_PATH, encoding="utf-8-sig") as file:
        entries = json.load(file)
    if not isinstance(entries, list) or not entries:
        raise ValueError("Invalid shiny-stats.json")
    names = {}
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or isinstance(entry.get("id"), bool)
            or not isinstance(entry.get("id"), int)
            or not isinstance(entry.get("displayName"), str)
            or not entry["displayName"]
        ):
            raise ValueError("Invalid shiny-stats.json")
        names[entry["id"]] = entry["displayName"]
    return names


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.position = 0

    def read(self) -> int:
        if self.position >= len(self.data):
            raise ValueError("Truncated Artemis item data")
        value = self.data[self.position]
        self.position += 1
        return value

    def read_bytes(self, count: int) -> bytes:
        if count < 0 or self.position + count > len(self.data):
            raise ValueError("Truncated Artemis item data")
        value = self.data[self.position:self.position + count]
        self.position += count
        return value

    def variable_integer(self) -> int:
        value = 0
        for index in range(10):
            byte = self.read()
            value |= (byte & 0x7f) << (7 * index)
            if byte & 0x80 == 0:
                return (value >> 1) ^ -(value & 1)
        raise ValueError("Oversized Artemis variable integer")

    def ascii_string(self) -> str:
        output = bytearray()
        while True:
            byte = self.read()
            if byte == 0:
                return output.decode("ascii")
            output.append(byte)


def _encoded_bytes(code: str) -> bytes:
    if not isinstance(code, str) or not code or len(code) > 1024:
        raise ValueError("Invalid Artemis item code")
    output = bytearray()
    for character in code:
        codepoint = ord(character)
        if 0xF0000 <= codepoint <= 0xFFFFD:
            value = codepoint - 0xF0000
            output.extend((value >> 8, value & 0xff))
        elif 0x100000 <= codepoint <= 0x10FFFD:
            if codepoint & 0xff == 0xee:
                output.append((codepoint - 0x1000EE) >> 8)
            else:
                value = codepoint - 0x100000
                output.extend((0xff, (0xfe + (value & 0xff)) & 0xff))
        else:
            raise ValueError("Invalid character in Artemis item code")
    if len(output) > 2048:
        raise ValueError("Artemis item data exceeds 2 KiB")
    return bytes(output)


def item_type(code: str) -> int:
    reader = _Reader(_encoded_bytes(code))
    if reader.read() != 0 or reader.read() not in SUPPORTED_VERSIONS or reader.read() != 1:
        raise ValueError("Invalid Artemis item header")
    return reader.read()


def _requirements(reader: _Reader) -> dict:
    level = reader.read()
    class_id = reader.read()
    if class_id not in CLASSES:
        raise ValueError("Invalid crafted item class requirement")
    skills = {}
    for _ in range(reader.read()):
        element_id = reader.read()
        if element_id not in ELEMENTS:
            raise ValueError("Invalid crafted item skill requirement")
        skills[ELEMENTS[element_id]] = reader.variable_integer()
    return {"level": level, "class": CLASSES[class_id], "skills": skills}


def _damage(reader: _Reader, version: int) -> dict:
    dps = reader.variable_integer() if version >= WYNNTILS_VERSION_3 else None
    attack_speed_id = reader.read()
    if attack_speed_id not in ATTACK_SPEEDS:
        raise ValueError("Invalid crafted item attack speed")
    damages = []
    for _ in range(reader.read()):
        damage_type_id = reader.read()
        if damage_type_id not in DAMAGE_TYPES:
            raise ValueError("Invalid crafted item damage type")
        damages.append((DAMAGE_TYPES[damage_type_id], reader.variable_integer(), reader.variable_integer()))
    return {"dps": dps, "attackSpeed": ATTACK_SPEEDS[attack_speed_id], "damages": damages}


def _defense(reader: _Reader) -> dict:
    health = reader.variable_integer()
    defenses = []
    for _ in range(reader.read()):
        element_id = reader.read()
        if element_id not in ELEMENTS:
            raise ValueError("Invalid crafted item defense type")
        defenses.append((ELEMENTS[element_id], reader.variable_integer()))
    return {"health": health, "defenses": defenses}


def _identifications(reader: _Reader, version: int) -> list[tuple[str, int]]:
    keys = stat_id_keys()
    identifications = []
    for _ in range(reader.read()):
        stat_id = reader.read()
        if stat_id >= len(keys):
            raise ValueError(f"Unknown crafted identification id: {stat_id}")
        value = reader.variable_integer()
        if version == WYNNTILS_VERSION_3:
            flags = reader.read()
            if flags & 4:
                meter = reader.read()
                if meter > 35:
                    raise ValueError("Invalid crafted identification meter")
        elif version == WYNNTILS_VERSION_4:
            reader.read_bytes(2)  # meter, perfect
        identifications.append((keys[stat_id], value))
    return identifications


def _possible_identifications(reader: _Reader) -> list[tuple[str, int]]:
    """Versions 1 and 2 share each crafted identification's maximum, not its value."""
    keys = stat_id_keys()
    identifications = []
    for _ in range(reader.read()):
        stat_id = reader.read()
        if stat_id >= len(keys):
            raise ValueError(f"Unknown crafted identification id: {stat_id}")
        identifications.append((keys[stat_id], reader.variable_integer()))
    return identifications


def _powders(reader: _Reader, version: int) -> dict:
    slots = reader.read()
    count = reader.read()
    width = 6 if version == WYNNTILS_VERSION_4 else 5
    encoded = reader.read_bytes(math.ceil(count * width / 8))
    bits = "".join(f"{byte:08b}" for byte in encoded)
    powders = []
    for index in range(count):
        value = int(bits[index * width:index * width + width], 2)
        if value == 0:
            continue
        if version == WYNNTILS_VERSION_4:
            element, tier = divmod(value - 1, MAX_POWDER_TIER)
            tier += 1
        else:
            element, tier = divmod(value, 6)
            if tier == 0:
                element -= 1
                tier = 6
        if element not in ELEMENTS or not 1 <= tier <= MAX_POWDER_TIER:
            raise ValueError("Invalid item powder")
        powders.append(f"{ELEMENTS[element]} {tier}")
    if version == WYNNTILS_VERSION_4:
        special_id = reader.read()
        if special_id > MAX_POWDER_SPECIAL_ID:
            raise ValueError("Invalid powder special")
        if special_id and not 1 <= reader.read() <= MAX_POWDER_TIER:
            raise ValueError("Invalid powder special tier")
    return {"slots": slots, "powders": powders}


def _gear_identifications(reader: _Reader, version: int) -> list[dict]:
    keys = stat_id_keys()
    count = reader.read()
    extended = reader.read() == 1
    pre_identified_count = reader.read() if extended else 0
    identifications = []
    for index in range(pre_identified_count + count):
        stat_id = reader.read()
        if stat_id >= len(keys):
            raise ValueError(f"Unknown gear identification id: {stat_id}")
        base_value = reader.variable_integer() if extended else None
        if index < pre_identified_count:
            identifications.append({"key": keys[stat_id], "value": base_value, "kind": "fixed"})
            continue
        if version >= WYNNTILS_VERSION_3:
            value = reader.variable_integer()
            if version == WYNNTILS_VERSION_3:
                flags = reader.read()
                if flags & VANILLA_METER_FLAG and reader.read() > MAX_VANILLA_METER_OFFSET:
                    raise ValueError("Invalid gear identification meter offset")
            else:
                reader.read_bytes(2)  # meter, perfect
            kind = "actual"
        else:
            value = reader.read()
            kind = "roll"
        identifications.append({"key": keys[stat_id], "value": value, "kind": kind})
    return identifications


def _shiny(reader: _Reader) -> dict:
    names = shiny_stat_names()
    shiny_id = reader.read()
    if shiny_id not in names:
        raise ValueError(f"Unknown shiny stat id: {shiny_id}")
    reader.read()
    return {"key": names[shiny_id], "value": reader.variable_integer()}


def decode_gear(code: str) -> dict:
    reader = _Reader(_encoded_bytes(code))
    if reader.read() != 0:
        raise ValueError("Artemis item is missing its start block")
    version = reader.read()
    if version not in (WYNNTILS_VERSION_3, WYNNTILS_VERSION_4):
        raise ValueError(f"Unsupported Artemis gear version: {version + 1}")

    item = {
        "itemName": None,
        "identifications": [],
        "powders": None,
        "rerollCount": 0,
        "shiny": None,
    }
    item_type_id = None
    found_end = False
    while reader.position < len(reader.data):
        block = reader.read()
        if block == 255:
            found_end = True
            break
        if block == 1:
            item_type_id = reader.read()
        elif block == 2:
            item["itemName"] = reader.ascii_string()
        elif block == 3:
            item["identifications"] = _gear_identifications(reader, version)
        elif block == 4:
            item["powders"] = _powders(reader, version)
        elif block == 5:
            item["rerollCount"] = reader.read()
        elif block == 6:
            item["shiny"] = _shiny(reader)
        else:
            raise ValueError(f"Unsupported gear item data block: {block}")

    if not found_end or reader.position != len(reader.data):
        raise ValueError("Invalid Artemis item end block")
    if item_type_id != GEAR_ITEM_TYPE:
        raise ValueError("Artemis item is not gear")
    if not isinstance(item["itemName"], str) or not item["itemName"] or len(item["itemName"]) > 200:
        raise ValueError("Missing or invalid gear item name")
    return item


def decode_crafted_gear(code: str, name_hint: str | None) -> dict:
    reader = _Reader(_encoded_bytes(code))
    if reader.read() != 0:
        raise ValueError("Artemis item is missing its start block")
    version = reader.read()
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(f"Unsupported Artemis item version: {version + 1}")

    item = {
        "itemName": name_hint or "Crafted Item",
        "gearType": None,
        "durability": None,
        "requirements": None,
        "damage": None,
        "defense": None,
        "identifications": [],
        "powders": None,
    }
    possible_identifications = []
    item_type_id = None
    found_end = False
    while reader.position < len(reader.data):
        block = reader.read()
        if block == 255:
            found_end = True
            break
        if block == 1:
            item_type_id = reader.read()
        elif block == 2:
            item["itemName"] = reader.ascii_string()
        elif block == 4:
            item["powders"] = _powders(reader, version)
        elif block == 7:
            gear_type_id = reader.read()
            if gear_type_id not in GEAR_TYPES:
                raise ValueError("Invalid crafted gear type")
            item["gearType"] = GEAR_TYPES[gear_type_id]
        elif block == 8:
            effect_strength = reader.read() if version in (0, 1) else 100
            item["durability"] = {
                "effectStrength": effect_strength,
                "max": reader.variable_integer(),
                "current": reader.variable_integer(),
            }
        elif block == 9:
            item["requirements"] = _requirements(reader)
        elif block == 10:
            item["damage"] = _damage(reader, version)
        elif block == 11:
            item["defense"] = _defense(reader)
        elif block == 12:
            if version >= WYNNTILS_VERSION_3:
                item["identifications"] = _identifications(reader, version)
            else:
                possible_identifications = _possible_identifications(reader)
        else:
            raise ValueError(f"Unsupported crafted item data block: {block}")

    if not found_end or reader.position != len(reader.data):
        raise ValueError("Invalid Artemis item end block")
    if item_type_id != 3:
        raise ValueError("Artemis item is not crafted gear")
    if item["gearType"] is None or item["durability"] is None or item["requirements"] is None:
        raise ValueError("Incomplete crafted gear data")
    if possible_identifications:
        effect_strength = item["durability"]["effectStrength"]
        item["identifications"] = [
            (key, maximum if maximum <= 0 else math.floor(maximum * effect_strength / 100 + 0.5))
            for key, maximum in possible_identifications
        ]
    if name_hint:
        item["itemName"] = name_hint
    if not isinstance(item["itemName"], str) or not item["itemName"] or len(item["itemName"]) > 200:
        raise ValueError("Missing or invalid crafted item name")
    return item


def _start(reader: _Reader) -> int:
    if reader.read() != 0:
        raise ValueError("Artemis item is missing its start block")
    version = reader.read()
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(f"Unsupported Artemis item version: {version + 1}")
    return version


def _finish(reader: _Reader, found_end: bool) -> None:
    if not found_end or reader.position != len(reader.data):
        raise ValueError("Invalid Artemis item end block")


def _checked_name(name: str) -> str:
    if not isinstance(name, str) or not name or len(name) > 200:
        raise ValueError("Missing or invalid item name")
    return name


def decode_crafted_consumable(code: str, name_hint: str | None) -> dict:
    reader = _Reader(_encoded_bytes(code))
    version = _start(reader)
    item = {
        "itemName": None,
        "consumableType": None,
        "uses": None,
        "level": None,
        "effects": {},
        "identifications": [],
    }
    item_type_id = None
    found_end = False
    while reader.position < len(reader.data):
        block = reader.read()
        if block == 255:
            found_end = True
            break
        if block == 1:
            item_type_id = reader.read()
        elif block == 2:
            item["itemName"] = reader.ascii_string()
        elif block == 9:
            item["level"] = _requirements(reader)["level"]
        elif block == 12:
            if version >= WYNNTILS_VERSION_3:
                item["identifications"] = _identifications(reader, version)
            else:
                item["identifications"] = _possible_identifications(reader)
        elif block == 13:
            type_id = reader.read()
            if type_id not in CONSUMABLE_TYPES:
                raise ValueError(f"Unknown consumable type id: {type_id}")
            item["consumableType"] = CONSUMABLE_TYPES[type_id]
        elif block == 14:
            item["uses"] = {"current": reader.read(), "max": reader.read()}
        elif block == 15:
            for _ in range(reader.read()):
                effect_id = reader.read()
                if effect_id not in CONSUMABLE_EFFECTS:
                    raise ValueError(f"Unknown consumable effect id: {effect_id}")
                item["effects"][CONSUMABLE_EFFECTS[effect_id]] = reader.variable_integer()
        else:
            raise ValueError(f"Unsupported crafted consumable data block: {block}")

    _finish(reader, found_end)
    if item_type_id != CRAFTED_CONSUMABLE_ITEM_TYPE:
        raise ValueError("Artemis item is not a crafted consumable")
    if item["consumableType"] is None or item["uses"] is None or item["level"] is None:
        raise ValueError("Incomplete crafted consumable data")
    item["itemName"] = _checked_name(name_hint or item["itemName"] or f"Crafted {item['consumableType']}")
    return item


def _capped(reader: _Reader, label: str) -> dict:
    current, maximum = reader.variable_integer(), reader.variable_integer()
    if current < 0 or maximum < 0:
        raise ValueError(f"Mount {label} cannot be negative")
    return {"current": current, "max": maximum}


def _mount_stat(reader: _Reader) -> str:
    stat_id = reader.read()
    if stat_id not in MOUNT_STATS:
        raise ValueError(f"Unknown mount stat id: {stat_id}")
    return MOUNT_STATS[stat_id]


def decode_mount(code: str, name_hint: str | None) -> dict:
    reader = _Reader(_encoded_bytes(code))
    _start(reader)
    item = {
        "mountType": None,
        "potential": None,
        "colorIds": None,
        "energy": None,
        "stats": None,
        "maxStats": None,
        "estimatedMaxStats": False,
    }
    item_type_id = None
    found_end = False
    while reader.position < len(reader.data):
        block = reader.read()
        if block == 255:
            found_end = True
            break
        if block == 1:
            item_type_id = reader.read()
        elif block == 2:
            # Wynntils shares the mount's name as chat text; it ignores any encoded copy.
            reader.ascii_string()
        elif block == 16:
            type_id = reader.read()
            if type_id not in MOUNT_TYPES:
                raise ValueError(f"Unknown mount type id: {type_id}")
            item["mountType"] = type_id
        elif block == 17:
            item["potential"] = reader.variable_integer()
        elif block == 18:
            item["colorIds"] = (reader.read(), reader.read())
        elif block == 19:
            item["energy"] = _capped(reader, "energy")
        elif block == 20:
            item["stats"] = {}
            for _ in range(reader.read()):
                stat = _mount_stat(reader)
                item["stats"][stat] = _capped(reader, "stat")
        elif block == 21:
            item["estimatedMaxStats"] = reader.read() != 0
            item["maxStats"] = {}
            for _ in range(reader.read()):
                stat = _mount_stat(reader)
                item["maxStats"][stat] = reader.variable_integer()
        else:
            raise ValueError(f"Unsupported mount data block: {block}")

    _finish(reader, found_end)
    if item_type_id != MOUNT_ITEM_TYPE:
        raise ValueError("Artemis item is not a mount")
    if None in (item["mountType"], item["potential"], item["colorIds"], item["energy"],
                item["stats"], item["maxStats"]):
        raise ValueError("Incomplete mount data")
    if not 0 <= item["potential"] <= MAX_MOUNT_POTENTIAL:
        raise ValueError("Mount potential is out of range")
    flier = item["mountType"] == 1
    for stats in (item["stats"], item["maxStats"]):
        if ("Altitude" if not flier else "Jump Height") in stats:
            raise ValueError("Mount has a stat its type cannot have")
        if len(stats) != len(MOUNT_STATS) - 1:
            raise ValueError("Mount item does not contain all stats")
    for stat, value in item["stats"].items():
        limit = item["maxStats"][stat]
        if not value["current"] <= value["max"] <= limit <= MAX_MOUNT_STAT:
            raise ValueError(f"Mount {stat} exceeds its limit")
    name, suffix, level, quest = MOUNT_TYPES[item["mountType"]]
    owner = name_hint or name
    item.update({
        "itemName": _checked_name(owner + ("'" if owner.lower().endswith("s") else "'s") + " " + suffix),
        "mountName": name,
        "level": level,
        "quest": quest,
    })
    return item
