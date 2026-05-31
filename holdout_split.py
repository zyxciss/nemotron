"""Stratified-holdout split for local validation.

Carves 100 problems out of the current `rule_found` training set, stratified
proportionally across categories, and writes:

  holdout/index.jsonl       one line per held-out problem (id, category, answer, prompt)
  holdout/train_order.txt   train_order.txt with the 100 held-out ids removed
                            (replaces the existing file when --apply is passed)

The held-out problems are NOT removed from cot/, problems.jsonl, or train.csv;
the holdout is purely a *training-order* exclusion, so the same corpus pipeline
can re-include them on demand. The default seed (1337) is sticky so reruns of
this script reproduce the same split.

Sample sizes per category are proportional to the rule_found count, with a
floor of 5 problems per category so every category contributes some signal.
The total is capped at 100.

Usage:
    uv run holdout_split.py              # dry-run; print what would change
    uv run holdout_split.py --apply      # rewrite train_order.txt and write holdout/index.jsonl
    uv run holdout_split.py --restore    # restore train_order.txt from train_order.txt.preholdout
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
PROBLEMS_INDEX = PROJECT_ROOT / "problems.jsonl"
TRAIN_ORDER = PROJECT_ROOT / "train_order.txt"
TRAIN_ORDER_BACKUP = PROJECT_ROOT / "train_order.txt.preholdout"
HOLDOUT_DIR = PROJECT_ROOT / "holdout"
HOLDOUT_INDEX = HOLDOUT_DIR / "index.jsonl"

TARGET_SIZE = 100
PER_CATEGORY_FLOOR = 5
DEFAULT_SEED = 1337


def _load_rule_found_by_category() -> dict[str, list[dict]]:
    by_cat: dict[str, list[dict]] = {}
    with PROBLEMS_INDEX.open() as f:
        for line in f:
            entry = json.loads(line)
            if entry.get("status") != "rule_found":
                continue
            by_cat.setdefault(entry["category"], []).append(entry)
    return by_cat


def _planned_counts(rule_found_by_cat: dict[str, list[dict]]) -> dict[str, int]:
    """Allocate TARGET_SIZE proportionally to category sizes, with a floor."""
    categories = sorted(rule_found_by_cat.keys())
    total_rule_found = sum(len(v) for v in rule_found_by_cat.values())
    proportional = {
        cat: max(
            PER_CATEGORY_FLOOR,
            round(TARGET_SIZE * len(rule_found_by_cat[cat]) / total_rule_found),
        )
        for cat in categories
    }
    # Trim down to TARGET_SIZE by reducing the largest allocations first.
    current = sum(proportional.values())
    while current > TARGET_SIZE:
        largest = max(
            (c for c in categories if proportional[c] > PER_CATEGORY_FLOOR),
            key=lambda c: proportional[c],
        )
        proportional[largest] -= 1
        current -= 1
    # And cap each by available rule_found count.
    for cat in categories:
        proportional[cat] = min(proportional[cat], len(rule_found_by_cat[cat]))
    return proportional


def _select_holdout(seed: int) -> list[dict]:
    """Return the list of held-out entries (id, category, answer, prompt)."""
    rule_found_by_cat = _load_rule_found_by_category()
    plan = _planned_counts(rule_found_by_cat)

    rng = random.Random(seed)
    selected: list[dict] = []
    for cat, n in sorted(plan.items()):
        pool = sorted(rule_found_by_cat[cat], key=lambda e: e["id"])
        rng.shuffle(pool)
        chosen = pool[:n]
        for entry in chosen:
            with (PROJECT_ROOT / "problems" / f"{entry['id']}.jsonl").open() as f:
                payload = json.loads(f.readline())
            selected.append(
                {
                    "id": entry["id"],
                    "category": entry["category"],
                    "answer": payload.get("answer", entry.get("submission", "")),
                    "prompt": payload.get("prompt", ""),
                }
            )
    return selected


def _print_plan(plan: dict[str, int], rule_found: dict[str, list[dict]]) -> None:
    print("Holdout plan (stratified):")
    width = max(len(c) for c in plan)
    for cat in sorted(plan):
        print(
            f"  {cat:<{width}}  {plan[cat]:>3} / {len(rule_found[cat]):>4} rule_found"
        )
    print(f"  {'TOTAL':<{width}}  {sum(plan.values()):>3}")


def apply(seed: int) -> None:
    rule_found_by_cat = _load_rule_found_by_category()
    plan = _planned_counts(rule_found_by_cat)
    _print_plan(plan, rule_found_by_cat)

    selected = _select_holdout(seed)
    selected_ids = {e["id"] for e in selected}

    if not TRAIN_ORDER_BACKUP.exists():
        shutil.copy(TRAIN_ORDER, TRAIN_ORDER_BACKUP)

    # Rewrite train_order.txt without the held-out ids.
    import re

    def base(s: str) -> str:
        return re.sub(r"-[a-z]\d+$", "", s)

    kept: list[str] = []
    dropped = 0
    with TRAIN_ORDER_BACKUP.open() as f:
        for line in f:
            pid = line.strip()
            if not pid:
                continue
            if base(pid) in selected_ids:
                dropped += 1
                continue
            kept.append(pid)
    with TRAIN_ORDER.open("w") as f:
        for pid in kept:
            f.write(pid + "\n")

    HOLDOUT_DIR.mkdir(parents=True, exist_ok=True)
    with HOLDOUT_INDEX.open("w") as f:
        for entry in selected:
            f.write(json.dumps(entry) + "\n")

    cat_counts = Counter(e["category"] for e in selected)
    print()
    print(f"Held out {len(selected)} problems.")
    print(f"  Per category: {dict(sorted(cat_counts.items()))}")
    print(
        f"  train_order.txt: {len(kept)} entries kept ({dropped} dropped, "
        f"including suffixed -p0/-dN duplicates)"
    )
    print(f"  Holdout index: {HOLDOUT_INDEX}")
    print()
    print(
        "Re-run `uv run reasoning.py && uv run corpus.py` to materialise the held-out corpus."
    )


def restore() -> None:
    if not TRAIN_ORDER_BACKUP.exists():
        print(f"No backup at {TRAIN_ORDER_BACKUP}, nothing to restore.")
        return
    shutil.copy(TRAIN_ORDER_BACKUP, TRAIN_ORDER)
    print(f"Restored {TRAIN_ORDER} from {TRAIN_ORDER_BACKUP}.")
    print("Re-run `uv run reasoning.py && uv run corpus.py` to re-include the holdout.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="rewrite train_order.txt and write holdout/index.jsonl",
    )
    parser.add_argument(
        "--restore",
        action="store_true",
        help="restore train_order.txt from the .preholdout backup",
    )
    args = parser.parse_args()

    if args.restore:
        restore()
        return

    rule_found_by_cat = _load_rule_found_by_category()
    plan = _planned_counts(rule_found_by_cat)
    _print_plan(plan, rule_found_by_cat)

    if args.apply:
        print()
        apply(args.seed)
    else:
        print()
        print(
            f"(dry-run, no changes; pass --apply to rewrite train_order.txt "
            f"and write {HOLDOUT_INDEX.relative_to(PROJECT_ROOT)})"
        )


if __name__ == "__main__":
    main()
