"""
One-time migration script: Upload existing shell exchange images from the
filesystem to S3 and ensure matching DB entries exist.

Run from project root:
    python migrate_shell_images.py

Images are resized from their current size to 16x16 (stored size) before upload.
"""

import os
import sys

# Ensure project root is on the path so Helpers can be imported
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image
from Helpers.storage import save_shell_exchange_icon
from Helpers.database import (
    get_shell_exchange_ings,
    save_shell_exchange_ings,
    get_shell_exchange_mats,
    save_shell_exchange_mats,
)

BASE = os.path.dirname(os.path.abspath(__file__))
INGS_DIR = os.path.join(BASE, "images", "shell_exchange", "Ings")
MATS_DIR = os.path.join(BASE, "images", "shell_exchange", "Mats")

TARGET_SIZE = 32


def norm_key(filename: str) -> str:
    return os.path.splitext(filename)[0].replace("_", " ").strip().casefold()


def migrate_directory(folder: str, category: str, db_data: dict) -> tuple[int, int]:
    """Migrate all PNGs in *folder* to S3 and ensure DB entries exist.

    Returns (uploaded, db_created) counts.
    """
    uploaded = 0
    db_created = 0

    for fn in sorted(os.listdir(folder)):
        if not fn.lower().endswith(".png"):
            continue

        key = norm_key(fn)
        path = os.path.join(folder, fn)

        # Load and resize to 16x16
        img = Image.open(path).convert("RGBA")
        old_size = img.size
        img = img.resize((TARGET_SIZE, TARGET_SIZE), Image.NEAREST)

        # Upload to S3
        save_shell_exchange_icon(category, key, img)
        uploaded += 1
        print(f"  Uploaded {category}/{key} ({old_size[0]}x{old_size[1]} -> {TARGET_SIZE}x{TARGET_SIZE})")

        # Ensure DB entry exists
        if key not in db_data:
            if category == "mats":
                db_data[key] = {
                    "t1": {"shells": 1, "per": 1, "highlight": False, "toggled": True},
                    "t2": {"shells": 1, "per": 1, "highlight": False, "toggled": True},
                    "t3": {"shells": 1, "per": 1, "highlight": False, "toggled": True},
                }
            else:
                db_data[key] = {"shells": 1, "per": 1, "highlight": False, "toggled": True}
            db_created += 1
            print(f"  Created DB entry for {key}")

    return uploaded, db_created


def main():
    print("=== Shell Exchange Image Migration ===\n")

    # --- Ingredients ---
    print("Migrating ingredients...")
    ings_data = get_shell_exchange_ings()
    ings_uploaded, ings_created = migrate_directory(INGS_DIR, "ings", ings_data)
    if ings_created > 0:
        save_shell_exchange_ings(ings_data)
    print(f"  -> {ings_uploaded} images uploaded, {ings_created} new DB entries\n")

    # --- Materials ---
    print("Migrating materials...")
    mats_data = get_shell_exchange_mats()
    mats_uploaded, mats_created = migrate_directory(MATS_DIR, "mats", mats_data)
    if mats_created > 0:
        save_shell_exchange_mats(mats_data)
    print(f"  -> {mats_uploaded} images uploaded, {mats_created} new DB entries\n")

    total = ings_uploaded + mats_uploaded
    print(f"Done! {total} total images migrated to S3.")


if __name__ == "__main__":
    main()
