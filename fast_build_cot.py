"""Fast compiler for train_cot.csv.

Bypasses the slow reasoning.py generators and builds train_cot.csv in under 1 second by reading directly from the frozen cot/ directory and problems.jsonl.
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).parent
PROBLEMS_INDEX = BASE_DIR / "problems.jsonl"
TRAIN_CSV = BASE_DIR / "train.csv"
TRAIN_ORDER_PATH = BASE_DIR / "train_order.txt"
TRAIN_COT_CSV = BASE_DIR / "train_cot.csv"
COT_DIR = BASE_DIR / "cot"
REASONING_DIR = BASE_DIR / "reasoning"

def _strip_suffix(pid_with_suffix: str) -> str:
    return re.sub(r"-[a-z]\d+$", "", pid_with_suffix)

def main() -> None:
    if not PROBLEMS_INDEX.exists():
        print(f"Missing {PROBLEMS_INDEX.name}")
        return
    if not TRAIN_ORDER_PATH.exists():
        print(f"Missing {TRAIN_ORDER_PATH.name}")
        return
    if not TRAIN_CSV.exists():
        print(f"Missing {TRAIN_CSV.name}")
        return

    # 1. Load problem entries from problems.jsonl
    existing: dict[str, dict] = {}
    with PROBLEMS_INDEX.open() as f:
        for line in f:
            if line.strip():
                entry = json.loads(line)
                existing[entry["id"]] = entry

    # 2. Load prompts and answers from train.csv
    prompts: dict[str, str] = {}
    answers: dict[str, str] = {}
    with TRAIN_CSV.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pid = row["id"]
            prompts[pid] = row["prompt"]
            answers[pid] = row["answer"]

    # 3. Read training order
    order = [
        line.strip()
        for line in TRAIN_ORDER_PATH.read_text().splitlines()
        if line.strip()
    ]

    print(f"Loaded {len(order)} training entries from {TRAIN_ORDER_PATH.name}")

    rows_written = 0
    frozen_hits = 0
    fallback_hits = 0
    skipped = 0

    # 4. Write train_cot.csv
    with TRAIN_COT_CSV.open("w", encoding="utf-8-sig", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(["id", "prompt", "answer", "type", "generated_cot"])

        for pid_with_suffix in order:
            base_id = _strip_suffix(pid_with_suffix)

            frozen_path = COT_DIR / f"{base_id}.txt"
            if frozen_path.exists():
                generated_cot = frozen_path.read_text()
                frozen_hits += 1
            else:
                reasoning_path = REASONING_DIR / f"{base_id}.txt"
                if not reasoning_path.exists():
                    skipped += 1
                    continue
                reasoning_text = reasoning_path.read_text().rstrip("\n")
                boxed_matches = re.findall(r"\\boxed\{([^}]*)\}", reasoning_text)
                reasoning_answer = (
                    boxed_matches[-1] if boxed_matches else answers.get(base_id, "")
                )
                generated_cot = (
                    f"{reasoning_text}\n</think>\n\\boxed{{{reasoning_answer}}}"
                )
                fallback_hits += 1

            entry = existing.get(base_id, {})
            category = entry.get("category", "")
            writer.writerow(
                [
                    base_id,
                    prompts.get(base_id, ""),
                    answers.get(base_id, ""),
                    category,
                    generated_cot,
                ]
            )
            rows_written += 1

    print(f"Wrote {rows_written} rows to {TRAIN_COT_CSV.name}")
    print(f"  from cot/ (frozen): {frozen_hits}")
    print(f"  from reasoning/ (fallback): {fallback_hits}")
    if skipped:
        print(f"Skipped {skipped} entries due to missing files.")

if __name__ == "__main__":
    main()
