"""Score generated CoTs against the held-out problem set.

Reads `holdout/index.jsonl` (produced by `holdout_split.py --apply`) and a
directory of generated completions. For each held-out problem, extracts the
final `\\boxed{...}` from the completion using the same regex as the kaggle
metric, then compares against the ground-truth answer with the same comparison
rule used by `reasoning.py:compare_answer`.

Prints per-category accuracy and total, matching the local-validation table
format the user has been sharing.

Usage:
    uv run eval_holdout.py --completions <dir>
        # <dir> contains one file per id, named "<problem_id>.txt"

    uv run eval_holdout.py --completions cot/
        # score the cot/ directory itself (sanity check — every held-out
        # problem already has its frozen CoT here, so this should print 100%)
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
HOLDOUT_INDEX = PROJECT_ROOT / "holdout" / "index.jsonl"

CATEGORY_WEIGHTS = {
    "bit_manipulation": 0.178,
    "cipher": 0.171,
    "cryptarithm_deduce": 0.075,
    "cryptarithm_guess": 0.015,
    "equation_numeric_deduce": 0.051,
    "equation_numeric_guess": 0.007,
    "gravity": 0.167,
    "numeral": 0.157,
    "unit_conversion": 0.180,
}


def _extract_boxed(text: str) -> str:
    matches = re.findall(r"\\boxed\{([^}]*)(?:\}|$)", text)
    if not matches:
        return ""
    non_empty = [m.strip() for m in matches if m.strip()]
    if non_empty:
        return non_empty[-1]
    return matches[-1].strip()


def _verify(stored: str, predicted: str) -> bool:
    stored = stored.strip()
    predicted = predicted.strip()
    if re.fullmatch(r"[01]+", stored):
        return predicted.lower() == stored.lower()
    try:
        return math.isclose(float(stored), float(predicted), rel_tol=1e-2, abs_tol=1e-5)
    except Exception:
        return predicted.lower() == stored.lower()


def _load_holdout() -> list[dict]:
    if not HOLDOUT_INDEX.exists():
        raise FileNotFoundError(
            f"{HOLDOUT_INDEX} not found. Run `uv run holdout_split.py --apply` first."
        )
    entries = []
    with HOLDOUT_INDEX.open() as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--completions",
        type=Path,
        required=True,
        help="directory containing <id>.txt files to score",
    )
    args = parser.parse_args()

    if not args.completions.is_dir():
        raise NotADirectoryError(args.completions)

    holdout = _load_holdout()
    per_cat_correct: dict[str, int] = defaultdict(int)
    per_cat_total: dict[str, int] = defaultdict(int)
    missing: list[str] = []

    for entry in holdout:
        pid = entry["id"]
        cat = entry["category"]
        per_cat_total[cat] += 1

        comp_path = args.completions / f"{pid}.txt"
        if not comp_path.exists():
            missing.append(pid)
            continue
        text = comp_path.read_text()
        predicted = _extract_boxed(text)
        if _verify(entry["answer"], predicted):
            per_cat_correct[cat] += 1

    if missing:
        print(
            f"WARNING: {len(missing)} held-out problems have no completion in "
            f"{args.completions}/ (counted as incorrect)."
        )
        if len(missing) <= 10:
            print("  Missing ids:", ", ".join(missing))
        print()

    categories = sorted(per_cat_total)
    name_w = max(len(c) for c in categories)
    print(f"{'category':<{name_w}}  correct  total  weight%   acc%   contrib%")
    total_weighted_score = 0.0
    total_weight = 0.0
    total_correct = sum(per_cat_correct.values())
    total_total = sum(per_cat_total.values())
    for cat in categories:
        c = per_cat_correct[cat]
        t = per_cat_total[cat]
        w = CATEGORY_WEIGHTS.get(cat, 0.0) * 100
        acc = (c / t * 100) if t else 0
        contrib = (c / t) * w if t else 0
        total_weighted_score += contrib
        total_weight += w
        print(
            f"{cat:<{name_w}}  {c:>7}  {t:>5}  {w:>6.1f}%  {acc:>5.1f}%  {contrib:>7.2f}%"
        )
    overall_acc = (total_correct / total_total * 100) if total_total else 0
    print(
        f"{'TOTAL':<{name_w}}  {total_correct:>7}  {total_total:>5}  "
        f"{total_weight:>6.1f}%  {overall_acc:>5.1f}%  {total_weighted_score:>7.2f}%"
    )


if __name__ == "__main__":
    main()
