import json
import math
import os
from functools import lru_cache

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAT_ID_KEYS_PATH = os.path.join(BASE, "data", "stat-id-keys.json")

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


@lru_cache(maxsize=1)
def stat_id_keys() -> tuple[str, ...]:
    with open(STAT_ID_KEYS_PATH, encoding="utf-8-sig") as file:
        keys = json.load(file)
    if not isinstance(keys, list) or not keys or not all(isinstance(key, str) and key for key in keys):
        raise ValueError("Invalid stat-id-keys.json")
    return tuple(keys)


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
    if reader.read() != 0 or reader.read() not in (0, 1, 2) or reader.read() != 1:
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
    dps = reader.variable_integer() if version == 2 else None
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
        if version == 2:
            flags = reader.read()
            if flags & 4:
                meter = reader.read()
                if meter > 35:
                    raise ValueError("Invalid crafted identification meter")
        identifications.append((keys[stat_id], value))
    return identifications


def _powders(reader: _Reader) -> dict:
    slots = reader.read()
    count = reader.read()
    encoded = reader.read_bytes(math.ceil(count * 5 / 8))
    bits = "".join(f"{byte:08b}" for byte in encoded)
    powders = []
    for index in range(count):
        value = int(bits[index * 5:index * 5 + 5], 2)
        if value == 0:
            continue
        element, tier = divmod(value, 6)
        if tier == 0:
            element -= 1
            tier = 6
        if element not in ELEMENTS or not 1 <= tier <= 6:
            raise ValueError("Invalid crafted item powder")
        powders.append(f"{ELEMENTS[element]} {tier}")
    return {"slots": slots, "powders": powders}


def decode_crafted_gear(code: str, name_hint: str | None) -> dict:
    reader = _Reader(_encoded_bytes(code))
    if reader.read() != 0:
        raise ValueError("Artemis item is missing its start block")
    version = reader.read()
    if version not in (0, 1, 2):
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
            item["powders"] = _powders(reader)
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
            if version == 2:
                item["identifications"] = _identifications(reader, version)
            else:
                keys = stat_id_keys()
                for _ in range(reader.read()):
                    stat_id = reader.read()
                    if stat_id >= len(keys):
                        raise ValueError(f"Unknown crafted identification id: {stat_id}")
                    possible_identifications.append((keys[stat_id], reader.variable_integer()))
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
