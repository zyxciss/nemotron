"""Ultra-robust patcher for s-baseline.ipynb.

Normalizes cells in s-baseline.ipynb by joining and splitlining their sources.
Injects a global `VALIDATE_ON_HOLDOUT = False` toggle into the configuration cell,
and wraps the holdout exclusion block in the training loop with a conditional check.
"""

from __future__ import annotations

import json
from pathlib import Path

NOTEBOOK_PATH = Path(__file__).parent / "s-baseline.ipynb"

def get_clean_lines(source: str | list[str]) -> list[str]:
    # Joining any string or list of lines/characters into a single string
    # and splitting it into standard lines preserves line structures perfectly in all formats.
    if isinstance(source, list):
        joined = "".join(source)
    else:
        joined = source
    return joined.splitlines(keepends=True)

def main() -> None:
    if not NOTEBOOK_PATH.exists():
        print(f"Missing {NOTEBOOK_PATH.name}")
        return

    with NOTEBOOK_PATH.open() as f:
        nb = json.load(f)

    # 1. Patch Cell 1 (Shared config)
    cell_1 = nb["cells"][1]
    lines_1 = get_clean_lines(cell_1["source"])
    
    # Check if VALIDATE_ON_HOLDOUT is already in there
    if not any("VALIDATE_ON_HOLDOUT" in line for line in lines_1):
        new_lines_1 = []
        for line in lines_1:
            new_lines_1.append(line)
            if "# ── Shared config" in line:
                new_lines_1.append("VALIDATE_ON_HOLDOUT = False  # Set to False to train on 100% of the dataset (for final submission)\n")
        cell_1["source"] = "".join(new_lines_1)
        print("Successfully added VALIDATE_ON_HOLDOUT global toggle to config cell.")
    else:
        # Re-save with clean string representation
        cell_1["source"] = "".join(lines_1)
        print("VALIDATE_ON_HOLDOUT already exists in config cell. Normalized cell source.")

    # 2. Patch Cell 4 (Training loop)
    cell_4 = nb["cells"][4]
    lines_4 = get_clean_lines(cell_4["source"])
    source_text = "".join(lines_4)

    # Find the start and end of the holdout exclusion block
    start_marker = "# ── Exclude held-out problems from training (clean local validation) ──"
    end_marker = "total_unmasked = sum(sum(e[\"weights\"]) for e in examples)"

    if "if VALIDATE_ON_HOLDOUT:" in source_text:
        # Re-save with clean string representation
        cell_4["source"] = "".join(lines_4)
        print("Holdout block is already wrapped in VALIDATE_ON_HOLDOUT. Normalized cell source.")
    elif start_marker in source_text and end_marker in source_text:
        new_lines_4 = []
        in_holdout_block = False
        holdout_block_lines = []

        for line in lines_4:
            if start_marker in line:
                new_lines_4.append(line)
                new_lines_4.append("    global VALIDATE_ON_HOLDOUT\n")
                new_lines_4.append("    if VALIDATE_ON_HOLDOUT:\n")
                in_holdout_block = True
                continue
            
            if in_holdout_block:
                if "total_unmasked = sum(" in line or "total_unmasked = sum(sum(e" in line:
                    # End of holdout block reached!
                    # Indent all lines inside the block by 4 spaces
                    for h_line in holdout_block_lines:
                        if h_line.strip():
                            new_lines_4.append("        " + h_line)
                        else:
                            new_lines_4.append(h_line)
                    
                    new_lines_4.append("    else:\n")
                    new_lines_4.append("        print('VALIDATE_ON_HOLDOUT=False: training on 100% full dataset (including holdout problems).')\n")
                    new_lines_4.append("\n")
                    new_lines_4.append(line)
                    in_holdout_block = False
                else:
                    holdout_block_lines.append(line)
            else:
                new_lines_4.append(line)

        cell_4["source"] = "".join(new_lines_4)
        print("Successfully wrapped holdout exclusion block with VALIDATE_ON_HOLDOUT check.")
    else:
        # Re-save with clean string representation
        cell_4["source"] = "".join(lines_4)
        print("Normalized cell source (did not find marker).")

    # 3. Save the notebook
    with NOTEBOOK_PATH.open("w") as f:
        json.dump(nb, f, indent=1)
    print("Notebook successfully saved.")

if __name__ == "__main__":
    main()
