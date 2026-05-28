"""Tokenize train_cot.csv into the training/sft/nemotron corpus.

Reads train_cot.csv (produced by reasoning.py) plus train_order.txt and writes a
directory layout that mirrors training/sft/04-08-16-14:

    training/sft/nemotron/
        tokens/<problem_id[-suffix]>/synthetic.json   {"tokens": [...], "mask": [...]}
        logprobs/0/<problem_id[-suffix]>/synthetic.jsonl
        logprobs/index.jsonl                          training-order index
        config.json                                   training config + aggregate stats

`train_order.txt` carries the deterministic stratified-shuffle order that was
used to lay out 04-08-16-14. Every line gives the suffixed problem id (e.g.
524cb5c6-d3) for the row at the matching position in train_cot.csv, so we can
recover the original directory naming without re-running the random shuffle.

Usage:
    uv run corpus.py
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from tokenizers import Tokenizer  # type: ignore[import-untyped]
from tqdm import tqdm
from transformers import AutoTokenizer  # type: ignore[import-untyped]

TRAIN_COT_CSV = Path(__file__).parent / "train_cot.csv"
TRAIN_ORDER_PATH = Path(__file__).parent / "train_order.txt"
TOKENIZER_PATH = Path(__file__).parent / "tokenizer.json"

OUTPUT_ROOT = Path(__file__).parent / "training" / "sft" / "nemotron"
TOKENS_DIR = OUTPUT_ROOT / "tokens"
LOGPROBS_DIR = OUTPUT_ROOT / "logprobs"
LOGPROBS_INDEX = LOGPROBS_DIR / "index.jsonl"
CONFIG_PATH = OUTPUT_ROOT / "config.json"

PROMPT_SUFFIX = (
    "\nPlease put your final answer inside `\\boxed{}`. "
    "For example: `\\boxed{your answer}`"
)

TOKEN_LIMIT = 8192
BATCH_SIZE = 32
MODEL_NAME = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16"
LOG_PATH = "nemotron"


@dataclass
class Row:
    problem_id: str
    suffixed_id: str
    category: str
    prompt: str
    answer: str
    generated_cot: str


def _load_rows() -> list[Row]:
    if not TRAIN_COT_CSV.exists():
        raise FileNotFoundError(
            f"Missing {TRAIN_COT_CSV.name}; run `uv run reasoning.py` first."
        )
    if not TRAIN_ORDER_PATH.exists():
        raise FileNotFoundError(
            f"Missing {TRAIN_ORDER_PATH.name}; this captures the training shuffle order."
        )

    suffixed_ids = [
        line.strip()
        for line in TRAIN_ORDER_PATH.read_text().splitlines()
        if line.strip()
    ]

    with TRAIN_COT_CSV.open(encoding="utf-8-sig", newline="") as f:
        csv_rows = list(csv.DictReader(f))

    if len(csv_rows) != len(suffixed_ids):
        raise ValueError(
            f"train_cot.csv has {len(csv_rows)} rows but train_order.txt has "
            f"{len(suffixed_ids)} entries; they must line up 1:1."
        )

    rows: list[Row] = []
    for csv_row, suffixed in zip(csv_rows, suffixed_ids):
        base_id = re.sub(r"-[a-z]\d+$", "", suffixed)
        if base_id != csv_row["id"]:
            raise ValueError(
                f"train_order entry {suffixed!r} (base={base_id}) does not match "
                f"train_cot.csv row id {csv_row['id']!r}."
            )
        rows.append(
            Row(
                problem_id=csv_row["id"],
                suffixed_id=suffixed,
                category=csv_row["type"],
                prompt=csv_row["prompt"],
                answer=csv_row["answer"],
                generated_cot=csv_row["generated_cot"],
            )
        )
    return rows


def _tokenize_prompt(prompt_text: str, chat_tokenizer: AutoTokenizer) -> list[int]:
    messages = [{"role": "user", "content": prompt_text + PROMPT_SUFFIX}]
    return chat_tokenizer.apply_chat_template(
        messages,
        tokenize=True,
        add_generation_prompt=True,
        enable_thinking=True,
    )


def _encode_row(
    row: Row,
    tokenizer: Tokenizer,
    chat_tokenizer: AutoTokenizer,
) -> tuple[list[int], list[int]]:
    prompt_ids = _tokenize_prompt(row.prompt, chat_tokenizer)
    completion_text = f"{row.generated_cot}<|im_end|>"
    completion_ids = tokenizer.encode(completion_text, add_special_tokens=False).ids

    tokens = prompt_ids + completion_ids
    mask = [0] * len(prompt_ids) + [1] * len(completion_ids)

    if len(tokens) > TOKEN_LIMIT:
        tokens = tokens[:TOKEN_LIMIT]
        mask = mask[:TOKEN_LIMIT]
    return tokens, mask


def _reset_output_dirs() -> None:
    if OUTPUT_ROOT.exists():
        shutil.rmtree(OUTPUT_ROOT)
    TOKENS_DIR.mkdir(parents=True)
    (LOGPROBS_DIR / "0").mkdir(parents=True)


def _write_tokens(suffixed_id: str, tokens: list[int], mask: list[int]) -> None:
    out_dir = TOKENS_DIR / suffixed_id
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "synthetic.json").open("w") as f:
        json.dump({"tokens": tokens, "mask": mask}, f)


def _write_logprob_placeholder(suffixed_id: str, num_loss_tokens: int) -> None:
    out_dir = LOGPROBS_DIR / "0" / suffixed_id
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "synthetic.jsonl").open("w") as f:
        json.dump({"logprobs": [0.0] * num_loss_tokens}, f)
        f.write("\n")


def main() -> None:
    rows = _load_rows()

    tokenizer = Tokenizer.from_file(str(TOKENIZER_PATH))
    chat_tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True)

    _reset_output_dirs()

    total_unmasked = 0
    total_masked = 0
    cat_unmasked: dict[str, int] = {}
    cat_count: dict[str, int] = {}
    index_entries: list[dict] = []
    n = len(rows)

    for step_global, row in enumerate(tqdm(rows, desc="corpus")):
        tokens, mask = _encode_row(row, tokenizer, chat_tokenizer)
        num_loss = sum(mask)
        num_pad = len(mask) - num_loss

        _write_tokens(row.suffixed_id, tokens, mask)
        _write_logprob_placeholder(row.suffixed_id, num_loss)

        total_unmasked += num_loss
        total_masked += num_pad
        cat_unmasked[row.category] = cat_unmasked.get(row.category, 0) + num_loss
        cat_count[row.category] = cat_count.get(row.category, 0) + 1

        index_entries.append(
            {
                "epoch": 0,
                "step": step_global // BATCH_SIZE,
                "problem_id": row.suffixed_id,
                "segment": "synthetic.jsonl",
                "category": row.category,
                "num_loss_tokens": num_loss,
            }
        )

    LOGPROBS_INDEX.parent.mkdir(parents=True, exist_ok=True)
    with LOGPROBS_INDEX.open("w") as f:
        for entry in index_entries:
            json.dump(entry, f)
            f.write("\n")

    total_steps = (n + BATCH_SIZE - 1) // BATCH_SIZE
    config = {
        "loss_config": {
            "name": "cross_entropy",
            "class_name": "CrossEntropyLossConfig",
        },
        "lr_schedule": {
            "learning_rate": 0.0002,
            "class_name": "StepLinearDecayLRSchedule",
        },
        "log_path": LOG_PATH,
        "model_name": MODEL_NAME,
        "batch_size": BATCH_SIZE,
        "num_epochs": 1,
        "lora_rank": 32,
        "max_length": TOKEN_LIMIT,
        "train_mlp": True,
        "train_attn": True,
        "train_unembed": True,
        "adam_config": {
            "beta1": 0.9,
            "beta2": 0.95,
            "eps": 1e-08,
            "weight_decay": 0.0,
            "grad_clip_norm": 1000000000.0,
        },
        "backend": "tinker",
        "micro_batch_size": 16,
        "time": LOG_PATH,
        "stats": {
            "num_examples": n,
            "total_masked_tokens": total_masked,
            "total_unmasked_tokens": total_unmasked,
            "total_steps": total_steps,
        },
    }
    with CONFIG_PATH.open("w") as f:
        json.dump(config, f, indent=2)

    print(f"\nCorpus: {n} entries written to {OUTPUT_ROOT}")
    print(f"Unmasked tokens: {total_unmasked:,}")
    print(f"Masked tokens:   {total_masked:,}")
    print(f"Total steps:     {total_steps}")
    print()
    for cat in sorted(cat_count):
        print(f"  {cat}: {cat_count[cat]} runs, {cat_unmasked[cat]:,} unmasked tokens")


if __name__ == "__main__":
    main()
