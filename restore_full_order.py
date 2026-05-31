"""Restore holdout to reconstruct the 100% full dataset order.

Merges the pre-holdout order (7,880 entries including the 50 cryptarithms)
with the 66 new bit_manipulation entries from _newbit_ids.json,
producing the final 7,946-example training order.
"""

import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
PREHOLDOUT_PATH = PROJECT_ROOT / "train_order.txt.preholdout"
NEWBIT_PATH = PROJECT_ROOT / "_newbit_ids.json"
TRAIN_ORDER_PATH = PROJECT_ROOT / "train_order.txt"

def main():
    if not PREHOLDOUT_PATH.exists():
        print(f"Error: pre-holdout file {PREHOLDOUT_PATH} not found.")
        return
    if not NEWBIT_PATH.exists():
        print(f"Error: new bit_manip file {NEWBIT_PATH} not found.")
        return

    # 1. Load the 7,880 pre-holdout entries
    preholdout_entries = [
        line.strip() for line in PREHOLDOUT_PATH.read_text().splitlines() if line.strip()
    ]
    print(f"Loaded {len(preholdout_entries)} pre-holdout entries (includes original 7,830 + 50 cryptarithms).")

    # 2. Load the 66 new bit_manipulation entries
    newbit_ids = json.loads(NEWBIT_PATH.read_text())
    print(f"Loaded {len(newbit_ids)} new bit_manipulation entries.")

    # 3. Combine them
    full_order = preholdout_entries + newbit_ids
    print(f"Combined total entries: {len(full_order)}")

    # 4. Write back to train_order.txt
    TRAIN_ORDER_PATH.write_text("\n".join(full_order) + "\n")
    print(f"Successfully wrote {TRAIN_ORDER_PATH.name}")

    print("\nNext steps:")
    print("1. Run: uv run reasoning.py")
    print("2. Run: uv run corpus.py")
    print("This will materialize the full 7,946-example corpus in training/sft/nemotron")

if __name__ == "__main__":
    main()
