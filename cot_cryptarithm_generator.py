"""Generate frozen-style cryptarithm CoTs for newly-solved problems.

The output mirrors the letter-assignment format already present in cot/<id>.txt
for the 54 existing rule_found cryptarithm_deduce entries, minimally extended
with arithmetic-operator semantics ("Operator x: addition" / subtraction /
multiplication, plus a digit-value reveal and the arithmetic apply step).

The CoT is only emitted for problems where:
  * the solver returns a non-None answer that matches the stored ground truth,
  * the bijection determined by the solver supplies digits for every character
    needed to encode both the example outputs and the question answer.

Every emitted CoT is round-tripped through the boxed-answer extractor and
required to match the stored answer; mismatches are dropped silently.
"""

from __future__ import annotations

import re
import string
from pathlib import Path

from reasoners.cryptarithm_v2 import solve, solve_guess

PROJECT_ROOT = Path(__file__).parent
COT_DIR = PROJECT_ROOT / "cot"


_SEMANTIC_NAMES = {
    "fwd_concat": "concatenation",
    "rev_concat": "reverse concatenation",
    "add": "addition",
    "sub": "subtraction",
    "rsub": "reverse subtraction",
    "abs_sub": "absolute difference",
    "mul": "multiplication",
}


def _semantic_op_symbol(sem: str) -> str:
    return {
        "add": "+",
        "sub": "-",
        "rsub": "-",
        "abs_sub": "-",
        "mul": "*",
    }.get(sem, "")


def _ordered_symbols(examples: list[tuple[str, str]], question: str) -> list[str]:
    """Return ALL non-operator-position chars in first-appearance order.

    The frozen-format CoT assigns a letter to every non-operator-position
    character (including operator glyphs that appear in output strings, e.g.
    '-' as the leading sign on a subtraction result). Only the *position-2*
    operator characters of inputs/question are excluded — they get x/y/z
    labels instead.
    """
    op_chars = {inp[2] for inp, _ in examples if len(inp) == 5}
    if len(question) == 5:
        op_chars.add(question[2])

    order: list[str] = []
    seen: set[str] = set()
    for inp, out in examples:
        # Input: positions 0,1,3,4 are digit slots; position 2 is operator.
        for i, c in enumerate(inp):
            if i == 2:
                continue
            if c in seen:
                continue
            seen.add(c)
            order.append(c)
        # Output: every char gets a letter, including operator-glyph signs.
        for c in out:
            if c in seen:
                continue
            seen.add(c)
            order.append(c)
    for i, c in enumerate(question):
        if i == 2:
            continue
        if c in seen:
            continue
        seen.add(c)
        order.append(c)
    return order


def _ordered_operators(examples: list[tuple[str, str]], question: str) -> list[str]:
    order: list[str] = []
    seen: set[str] = set()
    for inp, _ in examples:
        if len(inp) == 5 and inp[2] not in seen:
            seen.add(inp[2])
            order.append(inp[2])
    if len(question) == 5 and question[2] not in seen:
        seen.add(question[2])
        order.append(question[2])
    return order


_LETTER_POOL = string.ascii_uppercase
_OP_LABEL_POOL = "xyzwvutsrq"


def _convert_input_block(
    inp: str, sym_to_letter: dict[str, str], op_to_label: dict[str, str]
) -> str:
    """`% | * " |` → letter sequence string `AB x CB`. Mapping line is non-deduped.

    The character at position 2 is always the operator — even if the same glyph
    appears elsewhere as a digit symbol, we render its mapping using the
    operator label (x/y/z) at that position.
    """
    spelled_str = " ".join(inp)
    mapping_strs = []
    for i, c in enumerate(inp):
        if i == 2 and c in op_to_label:
            mapping_strs.append(f"{c} -> {op_to_label[c]}")
        elif c in sym_to_letter:
            mapping_strs.append(f"{c} -> {sym_to_letter[c]}")
        elif c in op_to_label:
            mapping_strs.append(f"{c} -> {op_to_label[c]}")
    abbr = (
        sym_to_letter[inp[0]]
        + sym_to_letter[inp[1]]
        + f" {op_to_label[inp[2]]} "
        + sym_to_letter[inp[3]]
        + sym_to_letter[inp[4]]
    )
    return f"    input:  {spelled_str} : {', '.join(mapping_strs)} -> {abbr}"


def _convert_output_block(out: str, sym_to_letter: dict[str, str]) -> str:
    """Non-deduped output mapping line."""
    spelled = " ".join(out)
    mapping_strs = []
    for c in out:
        if c in sym_to_letter:
            mapping_strs.append(f"{c} -> {sym_to_letter[c]}")
    abbr = "".join(sym_to_letter.get(c, "?") for c in out)
    return f"    output: {spelled} : {', '.join(mapping_strs)} -> {abbr}"


def _build_letter_assignment_block(
    digit_syms: list[str], sym_to_letter: dict[str, str]
) -> str:
    lines = ["First, let me assign letters to each symbol:"]
    for s in digit_syms:
        lines.append(f"{s} -> {sym_to_letter[s]}")
    return "\n".join(lines)


def _build_operators_block(ops: list[str], op_to_label: dict[str, str]) -> str:
    lines = ["Operators:"]
    for o in ops:
        lines.append(f"{o} -> {op_to_label[o]}")
    return "\n".join(lines)


def _format_question_apply(
    question: str,
    sem: str,
    mapping: dict[str, int],
    sym_to_letter: dict[str, str],
    op_to_label: dict[str, str],
    answer: str,
) -> str:
    """Build the 'Applying to ...' section of the CoT."""
    a_letter = sym_to_letter[question[0]] + sym_to_letter[question[1]]
    b_letter = sym_to_letter[question[3]] + sym_to_letter[question[4]]
    op_label = op_to_label[question[2]]
    inv = {v: k for k, v in mapping.items()}

    lines = [f"Applying to {a_letter} {op_label} {b_letter}:"]

    if sem in ("fwd_concat", "rev_concat"):
        if sem == "fwd_concat":
            res_letters = a_letter + b_letter
            lines.append(
                f"  concatenation({a_letter}, {b_letter}) = {a_letter} || {b_letter} = {res_letters}"
            )
        else:
            res_letters = b_letter + a_letter
            lines.append(
                f"  reverse concatenation({a_letter}, {b_letter}) = {b_letter} || {a_letter} = {res_letters}"
            )
        # Convert letters back to symbols. Mapping line is non-deduped.
        symbol_strs = " ".join(res_letters)
        letter_to_sym = {letter: s for s, letter in sym_to_letter.items()}
        mapping_back = ", ".join(f"{ch} -> {letter_to_sym[ch]}" for ch in res_letters)
        lines.append(f"  Converting back: {symbol_strs} : {mapping_back} -> {answer}")
        return "\n".join(lines)

    # Arithmetic semantic
    a_val = mapping[question[0]] * 10 + mapping[question[1]]
    b_val = mapping[question[3]] * 10 + mapping[question[4]]
    op_name = _SEMANTIC_NAMES[sem]
    op_glyph = _semantic_op_symbol(sem)

    lines.append(
        "  Digit values from the bijection: "
        + ", ".join(
            f"{ch} -> {sym_to_letter[ch]} -> {mapping[ch]}"
            for ch in (question[0], question[1], question[3], question[4])
            if ch in mapping
        )
    )
    lines.append(f"  {a_letter} = {a_val}, {b_letter} = {b_val}")

    if sem == "add":
        result = a_val + b_val
        lines.append(
            f"  {op_name}({a_letter}, {b_letter}) = {a_val} {op_glyph} {b_val} = {result}"
        )
    elif sem == "sub":
        result = a_val - b_val
        lines.append(
            f"  {op_name}({a_letter}, {b_letter}) = {a_val} - {b_val} = {result}"
        )
    elif sem == "rsub":
        result = b_val - a_val
        lines.append(
            f"  {op_name}({a_letter}, {b_letter}) = {b_val} - {a_val} = {result}"
        )
    elif sem == "abs_sub":
        result = abs(a_val - b_val)
        lines.append(
            f"  {op_name}({a_letter}, {b_letter}) = |{a_val} - {b_val}| = {result}"
        )
    elif sem == "mul":
        result = a_val * b_val
        lines.append(
            f"  {op_name}({a_letter}, {b_letter}) = {a_val} * {b_val} = {result}"
        )
    else:
        return None  # type: ignore[return-value]

    # Encode result back
    if result < 0:
        digits = str(abs(result))
        signed = "-"
    else:
        digits = str(result)
        signed = ""

    digit_to_sym_msg = ", ".join(
        f"{d} -> {inv[int(d)]}" for d in dict.fromkeys(digits) if int(d) in inv
    )
    encoded = signed + "".join(inv[int(d)] for d in digits)
    lines.append(
        f"  Encoding result: {' '.join(digits)} : {digit_to_sym_msg} -> {encoded}"
    )
    return "\n".join(lines)


def generate_cot(
    examples: list[tuple[str, str]],
    question: str,
    answer: str,
) -> str | None:
    """Return the CoT text, or None if unable to produce a verified one."""
    # The kaggle metric extracts the final \boxed{...} with regex `\\boxed\{([^}]*)\}`,
    # which truncates at the first `}` inside. Any answer containing `}` would score
    # as the (possibly empty) prefix, so training on those is counterproductive.
    if "}" in answer:
        return None

    result = solve(examples, question)
    if result is None:
        # Fall back to the guess solver (q_op not in examples).
        result = solve_guess(examples, question, answer)
    if result is None:
        return None
    predicted, info = result
    if predicted != answer:
        return None

    op_sem = info["op_semantics"]
    mapping = info["mapping"] or {}

    digit_syms = _ordered_symbols(examples, question)
    if len(digit_syms) > len(_LETTER_POOL):
        return None
    sym_to_letter = {s: _LETTER_POOL[i] for i, s in enumerate(digit_syms)}

    ops = _ordered_operators(examples, question)
    if len(ops) > len(_OP_LABEL_POOL):
        return None
    op_to_label = {o: _OP_LABEL_POOL[i] for i, o in enumerate(ops)}

    lines: list[str] = []
    lines.append("We need to infer the transformation rule from the examples.")
    lines.append("")
    lines.append(_build_letter_assignment_block(digit_syms, sym_to_letter))
    lines.append(_build_operators_block(ops, op_to_label))
    lines.append("")
    lines.append("Converting examples to letter form:")
    lines.append("")
    for inp, out in examples:
        if len(inp) != 5:
            continue
        lines.append(f"  「{inp}」 = 「{out}」:")
        lines.append(_convert_input_block(inp, sym_to_letter, op_to_label))
        lines.append(_convert_output_block(out, sym_to_letter))
        lines.append("")

    lines.append(
        "Each input is 5 characters: two symbol-digits, an operator, two more symbol-digits."
    )
    # The frozen format iterates the operator-summary lines in alphabetical order
    # of the operator character (independent of the assignment order x/y/z).
    for o in sorted(ops):
        sem = op_sem.get(o)
        if sem is None:
            lines.append(f"Operator {op_to_label[o]}: unknown")
        else:
            lines.append(f"Operator {op_to_label[o]}: {_SEMANTIC_NAMES[sem]}")
    lines.append("")
    q_sem = op_sem.get(question[2])
    sem_label = _SEMANTIC_NAMES[q_sem] if q_sem else "unknown"
    lines.append(
        f"The question operator is {op_to_label[question[2]]}, which is {sem_label}."
    )
    lines.append("")

    q_mapping_strs = []
    for i, c in enumerate(question):
        if i == 2 and c in op_to_label:
            q_mapping_strs.append(f"{c} -> {op_to_label[c]}")
        elif c in sym_to_letter:
            q_mapping_strs.append(f"{c} -> {sym_to_letter[c]}")
        elif c in op_to_label:
            q_mapping_strs.append(f"{c} -> {op_to_label[c]}")
    q_abbr = (
        sym_to_letter[question[0]]
        + sym_to_letter[question[1]]
        + f" {op_to_label[question[2]]} "
        + sym_to_letter[question[3]]
        + sym_to_letter[question[4]]
    )
    lines.append(
        f'Converting question "{question}": {" ".join(question)} : '
        + ", ".join(q_mapping_strs)
        + f" -> {q_abbr}"
    )
    lines.append("")

    apply_block = _format_question_apply(
        question, q_sem, mapping, sym_to_letter, op_to_label, answer
    )
    if apply_block is None:
        return None
    lines.append(apply_block)
    lines.append("")
    lines.append("I will now return the answer in \\boxed{}")
    lines.append("The answer in \\boxed is")
    lines.append(f"\\boxed{{{answer}}}")
    lines.append("</think>")
    lines.append(f"\\boxed{{{answer}}}")
    text = "\n".join(lines)

    # Final round-trip check: last \boxed{} must equal the answer.
    matches = re.findall(r"\\boxed\{([^}]*)(?:\}|$)", text)
    extracted = matches[-1].strip() if matches else ""
    if extracted != answer.strip():
        return None
    return text
