"""Generate deterministic reasoning text for each rule_found problem.

Creates reasoning/<problem_id>.txt for every problem where the rule was found,
skipping cryptarithm_guess. The reasoning mirrors the solver logic as natural
chain-of-thought traces.

Also produces train_cot.csv containing the row-ordered training corpus
(id, prompt, answer, type, generated_cot). The row order, including the
multi-copy training duplicates, is sourced from train_order.txt, which captures
the deterministic stratified shuffle used to assemble training/sft/04-08-16-14.

Usage:
    uv run reasoning.py
    uv run reasoning.py --delete-investigations   # delete investigation files when answer is correct
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from tqdm import tqdm

from reasoners.bit_manipulation import reasoning_bit_manipulation
from reasoners.cipher import reasoning_cipher
from reasoners.equation_numeric import reasoning_equation_numeric
from reasoners.cryptarithm import reasoning_cryptarithm
from reasoners.gravity import reasoning_gravity
from reasoners.numeral import reasoning_numeral
from reasoners.store_types import Problem
from reasoners.unit_conversion import reasoning_unit_conversion

PROBLEMS_INDEX = Path(__file__).parent / "problems.jsonl"
REASONING_DIR = Path(__file__).parent / "reasoning"
INVESTIGATIONS_DIR = Path(__file__).parent / "investigations"
TRAIN_CSV = Path(__file__).parent / "train.csv"
TRAIN_ORDER_PATH = Path(__file__).parent / "train_order.txt"
TRAIN_ORDER_FULL_PATH = Path(__file__).parent / "train_order.full.txt"
TRAIN_COT_CSV = Path(__file__).parent / "train_cot.csv"
COT_DIR = Path(__file__).parent / "cot"
# The 0.86 baseline layout directory holds the canonical per-category row count
# (gravity=1055, numeral=730, unit_conversion=1070, cipher=1656, ...). We cap
# ONLY the easy/over-abundant categories so they can't flood the corpus; hard
# categories (cryptarithm_*, equation_*, bit_manipulation) flow freely so the
# new solves reach training at full strength.
CAP_CATEGORIES = {"cipher", "gravity", "numeral", "unit_conversion"}
BASELINE_LAYOUT_DIR = Path(__file__).parent / "training" / "sft" / "04-08-16-14"
BASELINE_INDEX = BASELINE_LAYOUT_DIR / "logprobs" / "index.jsonl"


def _baseline_per_category_cap() -> dict[str, int]:
    """Per-category row count from the 0.86 layout, filtered to CAP_CATEGORIES."""
    if not BASELINE_INDEX.exists():
        return {}
    from collections import Counter

    c: Counter = Counter()
    for line in BASELINE_INDEX.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        cat = rec.get("category", "")
        if cat in CAP_CATEGORIES:
            c[cat] += 1
    return dict(c)

INVESTIGATION_CATEGORIES: set[str] = {
    "cryptarithm_deduce",
    "cryptarithm_guess",
    "equation_numeric_deduce",
    "equation_numeric_guess",
}

SKIP_CATEGORIES: set[str] = set()

GENERATORS: dict[str, Callable] = {
    "gravity": reasoning_gravity,
    "unit_conversion": reasoning_unit_conversion,
    "cipher": reasoning_cipher,
    "bit_manipulation": reasoning_bit_manipulation,
    "numeral": reasoning_numeral,
    "equation_numeric_deduce": reasoning_equation_numeric,
    "equation_numeric_guess": reasoning_equation_numeric,
    "cryptarithm_deduce": reasoning_cryptarithm,
    "cryptarithm_guess": reasoning_cryptarithm,
}


def extract_answer(reasoning_text: str) -> str:
    """Extract the answer from \\boxed{...}, matching metric_reference.extract_final_answer."""
    matches = re.findall(r"\\boxed\{([^}]*)(?:\}|$)", reasoning_text)
    if matches:
        non_empty = [m.strip() for m in matches if m.strip()]
        if non_empty:
            return non_empty[-1]
        return matches[-1].strip()
    return ""


def compare_answer(stored_answer: str, predicted: str) -> bool:
    """Verify if the answer matches.

    For numerical answers, allow them to be judged as equal within a certain relative tolerance (1e-2);
    otherwise, compare strictly as strings (case-insensitive).

    Examples:
        >>> verify("10011000", "10011000")
        True
        >>> verify("10011000", "10011001")
        False
        >>> verify("24.64", "24.6401")
        True
        >>> verify("XLVII", "xlvii")
        True
        >>> verify("11011", "00011011")
        False
    """
    # Clean up strings
    stored_answer = stored_answer.strip()
    predicted = predicted.strip()

    # If the answer is a binary string, compare strictly as strings
    if re.fullmatch(r"[01]+", stored_answer):
        return predicted.lower() == stored_answer.lower()

    try:
        # Try to convert the answers to floating point numbers
        stored_num = float(stored_answer)
        predicted_num = float(predicted)
        # Use a small absolute tolerance for numbers near zero
        return math.isclose(stored_num, predicted_num, rel_tol=1e-2, abs_tol=1e-5)
    except Exception:
        # Fallback to case-insensitive string comparison
        return predicted.lower() == stored_answer.lower()


@dataclass
class CategoryCounts:
    rule_found: int = 0
    total: int = 0
    runtimes: list[float] = field(default_factory=list)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--delete-investigations",
        action="store_true",
        help="Delete investigation files when answer is correct",
    )
    args = parser.parse_args()

    if not PROBLEMS_INDEX.exists():
        print(f"No {PROBLEMS_INDEX} found.")
        return

    # Read existing entries to preserve fields, then merge results back
    existing: dict[str, dict] = {}
    with PROBLEMS_INDEX.open() as f:
        for line in f:
            if line.strip():
                entry = json.loads(line)
                existing[entry["id"]] = entry

    if REASONING_DIR.exists():
        shutil.rmtree(REASONING_DIR)
    REASONING_DIR.mkdir(parents=True)
    INVESTIGATIONS_DIR.mkdir(parents=True, exist_ok=True)

    stats: dict[str, bool] = {}
    category_stats: dict[str, CategoryCounts] = {}
    generated = 0
    skipped = 0

    for entry in tqdm(existing.values(), total=len(existing), desc="reasoning"):
        pid = entry["id"]
        category = entry["category"]

        cat = category
        if cat not in category_stats:
            category_stats[cat] = CategoryCounts()
        category_stats[cat].total += 1

        if category in SKIP_CATEGORIES:
            existing[pid]["status"] = "rule_unknown"
            existing[pid]["submission"] = ""
            continue

        generator = GENERATORS.get(category)
        if not generator:
            existing[pid]["status"] = "rule_unknown"
            existing[pid]["submission"] = ""
            continue

        problem = Problem.load_from_json(pid)
        t0 = time.perf_counter()
        reasoning_text = generator(problem)
        elapsed = time.perf_counter() - t0
        category_stats[cat].runtimes.append(elapsed)

        if reasoning_text is None:
            # Do NOT fall back to copying investigation files into reasoning/
            skipped += 1
            existing[pid]["status"] = "rule_unknown"
            existing[pid]["submission"] = ""
            continue

        submission = extract_answer(reasoning_text)
        result = compare_answer(problem.answer, submission)
        stats[pid] = result
        existing[pid]["status"] = "rule_found" if result else "rule_unknown"
        existing[pid]["submission"] = submission

        if result:
            category_stats[cat].rule_found += 1

        out_path = REASONING_DIR / f"{pid}.txt"
        with open(out_path, "w") as f:
            f.write(reasoning_text)

        if category in INVESTIGATION_CATEGORIES:
            inv_path = INVESTIGATIONS_DIR / f"{pid}.txt"
            if result and args.delete_investigations and inv_path.exists():
                inv_path.unlink()

        generated += 1

    # Update status for problems with investigation files (only if not already rule_found)
    hypothesis_formed = 0
    for inv_path in INVESTIGATIONS_DIR.glob("*.txt"):
        pid = inv_path.stem
        if pid not in existing:
            continue
        if existing[pid]["status"] == "rule_found":
            continue
        existing[pid]["status"] = "hypothesis_formed"
        hypothesis_formed += 1

    # Write merged results back to problems.jsonl
    with PROBLEMS_INDEX.open("w") as f:
        for entry in existing.values():
            entry.pop("has_investigation", None)
            f.write(json.dumps(entry) + "\n")

    # Print accuracy stats
    total = sum(c.total for c in category_stats.values())
    rule_found = sum(c.rule_found for c in category_stats.values())
    print(f"\nGenerated {generated} reasoning files in {REASONING_DIR}/")
    if skipped:
        print(f"Skipped {skipped} (no generator for category)")
    if hypothesis_formed:
        print(
            f"Hypothesis formed: {hypothesis_formed} (investigation without reasoning)"
        )
    w = 64
    print(f"\n{'=' * w}")
    print(f"{'Category':<28} {'Found':>6} {'Total':>6} {'Accuracy':>10} {'Avg ms':>10}")
    print(f"{'-' * w}")
    all_runtimes: list[float] = []
    for category_name, counts in sorted(category_stats.items()):
        acc = counts.rule_found / counts.total * 100 if counts.total else 0
        avg_ms = (
            sum(counts.runtimes) / len(counts.runtimes) * 1000 if counts.runtimes else 0
        )
        all_runtimes.extend(counts.runtimes)
        acc_str = f"{acc:.1f}%"
        print(
            f"{category_name:<28} {counts.rule_found:>6} {counts.total:>6} {acc_str:>10} {avg_ms:>10.1f}"
        )
    print(f"{'-' * w}")
    overall_acc = rule_found / total * 100 if total else 0
    overall_avg_ms = sum(all_runtimes) / len(all_runtimes) * 1000 if all_runtimes else 0
    overall_acc_str = f"{overall_acc:.1f}%"
    print(
        f"{'TOTAL':<28} {rule_found:>6} {total:>6} {overall_acc_str:>10} {overall_avg_ms:>10.1f}"
    )
    print(f"{'=' * w}")

    _write_train_cot_csv(existing)

    print("\nIf you were given an example to fix, please verify that example.")
    print(
        "\nIf the user has previously asked to run corpus.py, you should run `uv run corpus.py`"
    )


def _strip_suffix(pid_with_suffix: str) -> str:
    """Strip a -p0 / -dN training-duplicate suffix to recover the base problem id."""
    return re.sub(r"-[a-z]\d+$", "", pid_with_suffix)


def _assemble_cot(base_id: str, answers: dict[str, str]) -> tuple[str, str] | None:
    """Return (generated_cot, source) for a base id, or None if unavailable.

    Prefers the *frozen* cot/<base_id>.txt (the golden completion the 0.86 LoRA
    trained on); otherwise wraps the current reasoning/<base_id>.txt with the
    </think> + boxed-answer suffix. source is "frozen" or "reasoning".
    """
    frozen_path = COT_DIR / f"{base_id}.txt"
    if frozen_path.exists():
        return frozen_path.read_text(), "frozen"
    reasoning_path = REASONING_DIR / f"{base_id}.txt"
    if not reasoning_path.exists():
        return None
    reasoning_text = reasoning_path.read_text().rstrip("\n")
    boxed_matches = re.findall(r"\\boxed\{([^}]*)\}", reasoning_text)
    reasoning_answer = boxed_matches[-1] if boxed_matches else answers.get(base_id, "")
    return f"{reasoning_text}\n</think>\n\\boxed{{{reasoning_answer}}}", "reasoning"


def _write_train_cot_csv(existing: dict[str, dict]) -> None:
    """Write train_cot.csv: the frozen 0.86 corpus PLUS new solves, category-balanced.

    The first block replays train_order.txt — the deterministic stratified-shuffle
    order (with upsampling duplicates) used to train the 0.86 LoRA — sourcing each
    completion from the *frozen* cot/<base_id>.txt where it exists. Existing frozen
    CoTs are never replaced (the current reasoners produce different text that, when
    swapped in wholesale, drops the score ~0.85->0.66), so the proven baseline is
    preserved byte-for-byte. The curated order is also how we recover the per-
    category balance that produced 0.86: hard/scarce categories are oversampled,
    easy/abundant ones are undersampled.

    The second block subsamples every rule_found problem NOT already in train_order
    to top up each category up to its CURATED count. This is how freshly-solved
    problems (e.g. the extended cryptarithm / equation solvers) reach training
    WITHOUT flooding easy categories and re-creating the corpus-balance trap that
    caps the leaderboard at 0.85. If a category's curated count already exceeds its
    new-solve count, no extra rows are added (no downsampling of curated entries).

    The effective row order is written to train_order.full.txt so corpus.py can name
    the token directories 1:1. Both outputs are regenerated from scratch on every
    run, so the step is idempotent.
    """
    if not TRAIN_ORDER_PATH.exists():
        print(
            f"\nSkipping train_cot.csv: {TRAIN_ORDER_PATH.name} not found "
            "(needed to preserve the stratified shuffle order)."
        )
        return
    if not TRAIN_CSV.exists():
        print(f"\nSkipping train_cot.csv: {TRAIN_CSV.name} not found.")
        return

    prompts: dict[str, str] = {}
    answers: dict[str, str] = {}
    with TRAIN_CSV.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = row["id"]
            prompts[pid] = row["prompt"]
            answers[pid] = row["answer"]

    order: list[str] = [
        line.strip()
        for line in TRAIN_ORDER_PATH.read_text().splitlines()
        if line.strip()
    ]
    order_base: set[str] = {_strip_suffix(x) for x in order}

    # Per-category curated count = the per-category cap for new solves. The curated
    # baseline deliberately over/under-samples categories to produce the 0.86 mix.
    curated_per_cat: dict[str, int] = {}
    for suffixed in order:
        base_id = _strip_suffix(suffixed)
        cat = existing.get(base_id, {}).get("category", "")
        curated_per_cat[cat] = curated_per_cat.get(cat, 0) + 1
    # The 0.86 baseline layout directory is the canonical cap source — it has
    # the row counts the LoRA was actually trained on, and is more durable than
    # the train_order-stratified view (which can drift if categories churn).
    baseline_cap = _baseline_per_category_cap()
    cat_cap = {**curated_per_cat, **baseline_cap}
    if baseline_cap:
        print(
            f"Per-category cap from {BASELINE_LAYOUT_DIR.name}: {baseline_cap}"
        )

    # New solves per category, sorted deterministically.
    new_by_cat: dict[str, list[str]] = {}
    for base_id, entry in existing.items():
        if entry.get("status") != "rule_found":
            continue
        if base_id in order_base:
            continue
        if base_id not in prompts:
            continue
        cat = entry.get("category", "")
        if cat in {"gravity", "numeral", "unit_conversion"}:
            continue
        new_by_cat.setdefault(cat, []).append(base_id)
    for cat in new_by_cat:
        new_by_cat[cat].sort()

    rows_written = 0
    frozen_hits = 0
    fallback_hits = 0
    skipped = 0
    new_added_per_cat: dict[str, int] = {}
    effective_order: list[str] = []

    def _emit(writer, suffixed_id: str, base_id: str) -> None:
        nonlocal rows_written, frozen_hits, fallback_hits, skipped
        result = _assemble_cot(base_id, answers)
        if result is None:
            skipped += 1
            return
        generated_cot, source = result
        if source == "frozen":
            frozen_hits += 1
        else:
            fallback_hits += 1
        category = existing.get(base_id, {}).get("category", "")
        writer.writerow(
            [
                base_id,
                prompts.get(base_id, ""),
                answers.get(base_id, ""),
                category,
                generated_cot,
            ]
        )
        effective_order.append(suffixed_id)
        rows_written += 1

    unique_baseline_per_cat: dict[str, int] = {}
    for base_id in order_base:
        frozen_path = COT_DIR / f"{base_id}.txt"
        if frozen_path.exists():
            cat = existing.get(base_id, {}).get("category", "")
            if cat:
                unique_baseline_per_cat[cat] = unique_baseline_per_cat.get(cat, 0) + 1

    dup_ratios: dict[str, float] = {}
    for cat, total_runs in curated_per_cat.items():
        unique_cnt = unique_baseline_per_cat.get(cat, 0)
        if unique_cnt > 0:
            dup_ratios[cat] = total_runs / unique_cnt
        else:
            dup_ratios[cat] = 1.0

    import random
    rng = random.Random(42)

    with TRAIN_COT_CSV.open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(["id", "prompt", "answer", "type", "generated_cot"])
        for pid_with_suffix in tqdm(order, desc="train_cot.csv (curated)"):
            _emit(writer, pid_with_suffix, _strip_suffix(pid_with_suffix))
        # Top up each category up to its cap, deterministic order. Easy categories
        # like gravity/numeral/unit_conversion can't grow past their cap, while
        # hard categories are oversampled to match their baseline ratio and flow freely.
        for cat in sorted(new_by_cat):
            if cat in CAP_CATEGORIES:
                cap = cat_cap.get(cat, curated_per_cat.get(cat, 0))
            else:
                cap = 999999  # hard categories flow freely
            ratio = dup_ratios.get(cat, 1.0)
            if cat in {"cryptarithm_deduce", "cryptarithm_guess", "equation_numeric_guess"}:
                ratio = min(ratio, 3.0)
            for base_id in tqdm(
                new_by_cat[cat], desc=f"train_cot.csv (new/{cat})", leave=False
            ):
                if new_added_per_cat.get(cat, 0) >= cap:
                    break
                # Probabilistic duplication to match the float ratio on average
                base_dup = int(ratio)
                prob = ratio - base_dup
                dup = base_dup + (1 if rng.random() < prob else 0)
                dup = max(1, dup)

                for d_idx in range(dup):
                    if d_idx == 0:
                        suffixed_id = base_id
                    elif d_idx == 1:
                        suffixed_id = f"{base_id}-p0"
                    else:
                        suffixed_id = f"{base_id}-d{d_idx - 2}"
                    _emit(writer, suffixed_id, base_id)
                new_added_per_cat[cat] = new_added_per_cat.get(cat, 0) + dup

    TRAIN_ORDER_FULL_PATH.write_text("\n".join(effective_order) + "\n")

    print(f"\nWrote {rows_written} rows to {TRAIN_COT_CSV.name}")
    print(f"  from cot/ (frozen):         {frozen_hits}")
    print(f"  from reasoning/ (current):  {fallback_hits}")
    print("  per-category new top-ups (capped at curated count):")
    for cat in sorted(new_added_per_cat):
        print(f"    {cat:28s} +{new_added_per_cat[cat]}")
    print(f"  effective order written to: {TRAIN_ORDER_FULL_PATH.name}")
    if skipped:
        print(f"Skipped {skipped} ids with no frozen CoT or reasoning")


if __name__ == "__main__":
    main()
