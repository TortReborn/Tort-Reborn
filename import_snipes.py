"""
Import TAq snipe CSV exports into the live snipe tracker tables.

Workflow:
1. Run the script. If you do not pass any CSV paths, it imports every `*.csv` from `data/old_snipes`.
2. If a new nickname or malformed row appears, either update the embedded data below
   or pass --nickname-map / --row-overrides to use external files for that run.
3. Re-run the script with --dry-run, then without it to import.

Example:
    python import_snipes.py --dry-run
    python import_snipes.py --logged-by 123456789012345678
    python import_snipes.py "data/old_snipes/TAq Snipes w_ More Data and Shit - Season29.csv"
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from Helpers.database import DB
from Helpers.snipe_utils import get_max_conns, normalize_hq_for_storage


ROOT = Path(__file__).resolve().parent
DEFAULT_OLD_SNIPES_DIR = ROOT / "data" / "old_snipes"
SEASON_FILE = ROOT / "data" / "war_season.json"
ROLE_COLUMNS = {
    "Healer": "Healer",
    "Guards": "Tank",
    "Dps(s)": "DPS",
}
CONN_PATTERN = re.compile(r"^\s*(\d+)\s*conn[s]?\s*$", re.IGNORECASE)
DIFFICULTY_PATTERN = re.compile(r"(-?\d+)")
SEASON_PATTERN = re.compile(r"season\s*(\d+)", re.IGNORECASE)

EMBEDDED_NICKNAME_MAP = {
    "a3": "ikp3a",
    "Blader": "TSBlader",
    "censing": "_SlyGuy_",
    "Change": "_SlyGuy_",
    "etw": "nolenthusiast99",
    "Fallen": "FallenImpact",
    "Flo": "EmoFlo",
    "Fred": "catboymaduro",
    "Gonner": "LordGonner",
    "Goose": "GooseIverseIndex",
    "Hiarta": "Hiarta",
    "Iga": "Igasingularity",
    "iga": "Igasingularity",
    "Kenji": "Kenji121",
    "Kio": "Kioabc1",
    "kio": "Kioabc1",
    "Lava": "Sasuo_",
    "Loca": "Locarot",
    "Miki": "MikiTq",
    "Norman": "Stormin_Norman64",
    "progamer": "progamer167",
    "Restless": "Restlessfish",
    "Rippi": "_Rippi",
    "Slyguy": "_SlyGuy_",
    "Snowbunny": "catboymaduro",
    "Sonny": "SonnyVA",
    "Spydog": "SpyDog",
    "Squid": "_ghostsquid",
    "Stormin": "Stormin_Norman64",
    "Tex": "TexasBearKat",
    "Theo": "theoretisiert",
    "Vico": "ilyvicodin",
    "Zombie": "Zombie_Mods",
}

EMBEDDED_ROW_OVERRIDES = {
    "TAq Snipes w_ More Data and Shit - Season28.csv:10": {
        "conns": 3,
    },
}


@dataclass(frozen=True)
class ParsedSnipe:
    source_key: str
    source_path: Path
    season: int
    timestamp: int
    hq: str
    difficulty: int
    guild_tag: str
    conns: int
    participants: tuple[tuple[str, str], ...]


def get_current_season() -> int:
    try:
        with SEASON_FILE.open(encoding="utf-8") as f:
            return int(json.load(f).get("current_season", 1))
    except (FileNotFoundError, json.JSONDecodeError, TypeError, ValueError):
        return 1


def get_default_csv_paths() -> list[Path]:
    if not DEFAULT_OLD_SNIPES_DIR.exists():
        return []
    return sorted(DEFAULT_OLD_SNIPES_DIR.glob("*.csv"), key=lambda path: path.name.casefold())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import TAq snipe CSV files into the database.")
    parser.add_argument(
        "csv_files",
        nargs="*",
        help="Optional exported snipe CSV files. Defaults to every CSV in data/old_snipes.",
    )
    parser.add_argument(
        "--nickname-map",
        default=None,
        help="Optional nickname-to-IGN CSV path to merge on top of the embedded nickname map.",
    )
    parser.add_argument(
        "--row-overrides",
        default=None,
        help="Optional malformed-row override JSON path to merge on top of the embedded row overrides.",
    )
    parser.add_argument(
        "--default-season",
        type=int,
        default=get_current_season(),
        help="Season number to use when it cannot be inferred from a file name. Defaults to data/war_season.json current_season.",
    )
    parser.add_argument(
        "--logged-by",
        type=int,
        default=0,
        help="Discord user ID to store in snipe_logs.logged_by. Default: 0",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and compare against the DB without inserting anything.",
    )
    return parser.parse_args()


def read_csv_rows(path: Path) -> list[tuple[str, dict[str, str]]]:
    rows: list[tuple[str, dict[str, str]]] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            has_core_data = any(
                (row.get(field) or "").strip()
                for field in ("Date", "Healer", "Guards", "Dps(s)", "Guild", "HQ Name", "Difficulty (Conns)")
            )
            if not has_core_data:
                continue
            source_key = f"{path.name}:{reader.line_num}"
            rows.append((source_key, row))
    return rows


def collect_aliases(csv_paths: list[Path]) -> set[str]:
    aliases: set[str] = set()
    for path in csv_paths:
        for _, row in read_csv_rows(path):
            for column in ROLE_COLUMNS:
                raw_value = (row.get(column) or "").strip()
                if not raw_value:
                    continue
                for alias in raw_value.split(","):
                    cleaned = alias.strip()
                    if cleaned:
                        aliases.add(cleaned)
    return aliases


def load_nickname_map(path: Path | None) -> dict[str, str]:
    mapping: dict[str, str] = dict(EMBEDDED_NICKNAME_MAP)
    if path is None or not path.exists():
        return mapping

    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            alias = (row.get("alias") or "").strip()
            ign = (row.get("ign") or "").strip()
            if alias:
                mapping[alias] = ign
    return mapping


def sync_nickname_map(path: Path | None, aliases: set[str]) -> dict[str, str]:
    existing = load_nickname_map(path)
    if path is None:
        return existing

    changed = not path.exists()
    for alias in aliases:
        if alias not in existing:
            existing[alias] = ""
            changed = True

    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["alias", "ign"])
            writer.writeheader()
            for alias in sorted(existing, key=str.casefold):
                writer.writerow({"alias": alias, "ign": existing[alias]})

    return existing


def resolve_alias(alias: str, mapping: dict[str, str]) -> str | None:
    exact = mapping.get(alias, "").strip()
    if exact:
        return exact

    matches = {
        ign.strip()
        for key, ign in mapping.items()
        if key.casefold() == alias.casefold() and ign.strip()
    }
    if len(matches) == 1:
        return next(iter(matches))
    return None


def unresolved_aliases(aliases: set[str], mapping: dict[str, str]) -> list[str]:
    return sorted((alias for alias in aliases if not resolve_alias(alias, mapping)), key=str.casefold)


def load_row_overrides(path: Path | None) -> dict[str, dict]:
    overrides = {key: dict(value) for key, value in EMBEDDED_ROW_OVERRIDES.items()}
    if path is None or not path.exists():
        return overrides

    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return overrides

    for key, value in data.items():
        if isinstance(value, dict):
            overrides[key] = value
    return overrides


def sync_row_overrides(path: Path | None, templates: dict[str, dict]) -> dict[str, dict]:
    existing = load_row_overrides(path)
    if path is None:
        return existing

    changed = not path.exists()

    for source_key, template in templates.items():
        current = existing.setdefault(source_key, {})
        for field, default_value in template.items():
            if field not in current:
                current[field] = default_value
                changed = True

    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(dict(sorted(existing.items())), f, indent=2)
            f.write("\n")

    return existing


def infer_season(path: Path, default_season: int | None) -> int | None:
    match = SEASON_PATTERN.search(path.stem)
    if match:
        return int(match.group(1))
    return default_season


def parse_timestamp(date_value: str) -> int:
    dt = datetime.strptime(date_value.strip(), "%d/%m/%Y")
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def parse_difficulty(raw_value: object) -> int:
    text = str(raw_value or "").strip()
    match = DIFFICULTY_PATTERN.search(text)
    if not match:
        raise ValueError(f"Could not parse difficulty from {text!r}.")
    return int(match.group(1))


def parse_conns(raw_value: object, hq_value: str) -> int:
    if isinstance(raw_value, int):
        return raw_value

    text = str(raw_value or "").strip()
    if text.isdigit():
        return int(text)

    match = CONN_PATTERN.match(text)
    if match:
        return int(match.group(1))

    if text.casefold() == "dry":
        max_conns = get_max_conns(hq_value)
        if max_conns is None:
            raise ValueError(f"Cannot resolve Dry for HQ {hq_value!r}.")
        return max_conns

    raise ValueError(f"Could not parse connections from {text!r}.")


def build_participants(row: dict[str, str], mapping: dict[str, str]) -> tuple[tuple[str, str], ...]:
    participants: list[tuple[str, str]] = []
    for column, role in ROLE_COLUMNS.items():
        raw_value = (row.get(column) or "").strip()
        if not raw_value:
            continue
        for alias in raw_value.split(","):
            cleaned = alias.strip()
            if not cleaned:
                continue
            ign = resolve_alias(cleaned, mapping)
            if not ign:
                raise ValueError(f"Nickname {cleaned!r} is missing from the nickname map.")
            participants.append((ign, role))

    if not participants:
        raise ValueError("No participants were parsed from the row.")
    return tuple(participants)


def get_override_value(override: dict, key: str, default: object) -> object:
    if key not in override:
        return default
    value = override[key]
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    return value


def prepare_snipes(
    csv_paths: list[Path],
    mapping: dict[str, str],
    overrides: dict[str, dict],
    default_season: int | None,
) -> tuple[list[ParsedSnipe], list[str], dict[str, dict]]:
    parsed_rows: list[ParsedSnipe] = []
    errors: list[str] = []
    override_templates: dict[str, dict] = {}

    for path in csv_paths:
        season = infer_season(path, default_season)
        if season is None:
            errors.append(f"{path.name}: could not infer season from the file name. Use --default-season.")
            continue

        for source_key, row in read_csv_rows(path):
            row_override = overrides.get(source_key, {})
            try:
                date_value = str(get_override_value(row_override, "date", row.get("Date", ""))).strip()
                timestamp = parse_timestamp(date_value)

                hq_raw = str(get_override_value(row_override, "hq", row.get("HQ Name", ""))).strip()
                if not hq_raw:
                    raise ValueError("HQ Name is empty.")
                hq = normalize_hq_for_storage(hq_raw)

                guild_tag = str(get_override_value(row_override, "guild", row.get("Guild", ""))).strip().upper()
                if not guild_tag:
                    raise ValueError("Guild is empty.")

                difficulty = parse_difficulty(get_override_value(row_override, "difficulty", row.get("Difficulty (Dmg)", "")))
                conns = parse_conns(get_override_value(row_override, "conns", row.get("Difficulty (Conns)", "")), hq_raw)
                participants = build_participants(row, mapping)

                parsed_rows.append(
                    ParsedSnipe(
                        source_key=source_key,
                        source_path=path,
                        season=season,
                        timestamp=timestamp,
                        hq=hq,
                        difficulty=difficulty,
                        guild_tag=guild_tag,
                        conns=conns,
                        participants=participants,
                    )
                )
            except ValueError as exc:
                errors.append(f"{source_key}: {exc}")
                raw_conn_value = get_override_value(row_override, "conns", row.get("Difficulty (Conns)", ""))
                if "parse connections" in str(exc).lower() or str(raw_conn_value).strip().casefold() == "dry":
                    override_templates.setdefault(source_key, {"conns": None})

    return parsed_rows, errors, override_templates


def participants_match(db: DB, snipe_id: int, participants: tuple[tuple[str, str], ...]) -> bool:
    db.cursor.execute(
        """
        SELECT ign, role
        FROM snipe_participants
        WHERE snipe_id = %s
        ORDER BY ign, role
        """,
        (snipe_id,),
    )
    existing = tuple(db.cursor.fetchall())
    return existing == tuple(sorted(participants))


def find_existing_snipe(db: DB, snipe: ParsedSnipe) -> int | None:
    db.cursor.execute(
        """
        SELECT id
        FROM snipe_logs
        WHERE season = %s
          AND hq = %s
          AND difficulty = %s
          AND sniped_at = to_timestamp(%s)
          AND guild_tag = %s
          AND conns = %s
        ORDER BY id
        """,
        (snipe.season, snipe.hq, snipe.difficulty, snipe.timestamp, snipe.guild_tag, str(snipe.conns)),
    )
    for row in db.cursor.fetchall():
        snipe_id = row[0]
        if participants_match(db, snipe_id, snipe.participants):
            return snipe_id
    return None


def import_snipes(db: DB, snipes: list[ParsedSnipe], logged_by: int, dry_run: bool) -> tuple[int, int]:
    inserted = 0
    skipped = 0

    for snipe in snipes:
        existing_id = find_existing_snipe(db, snipe)
        if existing_id is not None:
            skipped += 1
            continue

        if dry_run:
            inserted += 1
            continue

        db.cursor.execute(
            """
            INSERT INTO snipe_logs (hq, difficulty, sniped_at, guild_tag, conns, logged_by, season)
            VALUES (%s, %s, to_timestamp(%s), %s, %s, %s, %s)
            RETURNING id
            """,
            (snipe.hq, snipe.difficulty, snipe.timestamp, snipe.guild_tag, str(snipe.conns), logged_by, snipe.season),
        )
        snipe_id = db.cursor.fetchone()[0]

        for ign, role in snipe.participants:
            db.cursor.execute(
                """
                INSERT INTO snipe_participants (snipe_id, ign, role)
                VALUES (%s, %s, %s)
                ON CONFLICT (snipe_id, ign) DO NOTHING
                """,
                (snipe_id, ign, role),
            )

        inserted += 1

    return inserted, skipped


def main() -> int:
    load_dotenv()
    args = parse_args()

    csv_inputs = args.csv_files or [str(path) for path in get_default_csv_paths()]
    csv_paths = [Path(path).resolve() for path in csv_inputs]
    nickname_map_path = Path(args.nickname_map).resolve() if args.nickname_map else None
    row_overrides_path = Path(args.row_overrides).resolve() if args.row_overrides else None

    if not csv_paths:
        print(f"No CSV files found. Add files under {DEFAULT_OLD_SNIPES_DIR} or pass paths explicitly.")
        return 1

    missing_files = [str(path) for path in csv_paths if not path.exists()]
    if missing_files:
        for missing in missing_files:
            print(f"Missing file: {missing}")
        return 1

    aliases = collect_aliases(csv_paths)
    nickname_map = sync_nickname_map(nickname_map_path, aliases)
    missing_aliases = unresolved_aliases(aliases, nickname_map)
    if missing_aliases:
        if nickname_map_path is not None:
            print(f"Nickname map updated at {nickname_map_path}")
            print("Fill in the IGN column for these aliases, then rerun:")
        else:
            print("These aliases are missing from the embedded nickname map. Add them in import_snipes.py or pass --nickname-map:")
        for alias in missing_aliases:
            print(f"  - {alias}")
        return 1

    row_overrides = load_row_overrides(row_overrides_path)
    snipes, parse_errors, override_templates = prepare_snipes(
        csv_paths=csv_paths,
        mapping=nickname_map,
        overrides=row_overrides,
        default_season=args.default_season,
    )

    if override_templates:
        sync_row_overrides(row_overrides_path, override_templates)

    if parse_errors:
        if override_templates and row_overrides_path is not None:
            print(f"Row override file updated at {row_overrides_path}")
        elif override_templates:
            print("Some malformed rows need overrides. Add them in EMBEDDED_ROW_OVERRIDES or pass --row-overrides.")
        print("Fix these CSV row issues, then rerun:")
        for error in parse_errors:
            print(f"  - {error}")
        return 1

    db = DB()
    db.connect()
    try:
        inserted, skipped = import_snipes(db, snipes, logged_by=args.logged_by, dry_run=args.dry_run)
        if args.dry_run:
            db.connection.rollback()
        else:
            db.connection.commit()
    finally:
        db.close()

    mode = "Dry run" if args.dry_run else "Import complete"
    print(f"{mode}: {inserted} new snipes, {skipped} already present, {len(snipes)} total validated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
