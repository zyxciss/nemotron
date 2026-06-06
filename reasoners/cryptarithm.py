"""Cryptarithm reasoning generator (bijection CSP, frozen-CoT style).

A cryptarithm problem gives examples `a op b = r` where each symbol is a distinct
decimal digit (a bijection), `op` is an arithmetic or concatenation operator, and
`r` is the value re-encoded in the same symbol alphabet. We must produce the
encoded result of the question expression.

The heavy lifting (find a bijection + per-operator semantic consistent with every
example) is done by the Rust crate `cryptarithm_solver_rs` (see
`cryptarithm_solver_rs/`), which returns the answer, the deduced semantics, and
the digit mapping. This module renders that deduction as a compact
letter-assignment chain-of-thought that mirrors the format in `cot/<id>.txt` that
the 0.86 LoRA was trained on (≈60 lines), deliberately avoiding the 500-line
reject-spam style that over-memorises and caps the leaderboard.

The solver never sees the ground-truth answer, so this is genuine deduction, not
answer leakage. A run only emits a CoT when the deduced answer round-trips back to
itself through the boxed-answer extractor.
"""

from __future__ import annotations

import re
import string

from reasoners.store_types import Problem

try:
    from cryptarithm_solver_rs import solve_detail as _rs_solve_detail
except ImportError as exc:  # pragma: no cover - build-time guard
    raise ImportError(
        "cryptarithm_solver_rs is not built. Run:\n"
        "  cd cryptarithm_solver_rs && maturin develop --release"
    ) from exc


_SEMANTIC_NAMES = {
    "fwd_concat": "concatenation",
    "rev_concat": "reverse concatenation",
    "add": "addition",
    "add+1": "addition plus one",
    "add-1": "addition minus one",
    "sub": "subtraction",
    "rsub": "reverse subtraction",
    "abs_sub": "absolute difference",
    "neg_abs": "negated absolute difference",
    "mul": "multiplication",
    "mul+1": "multiplication plus one",
    "mul-1": "multiplication minus one",
    "maxmod": "max mod min",
}

_LETTER_POOL = string.ascii_uppercase
_OP_LABEL_POOL = "xyzwvutsrq"


def _arith(sem: str, a: int, b: int) -> tuple[int, str]:
    """Return (result, formula_string) for an arithmetic semantic over a, b."""
    if sem == "add":
        return a + b, f"{a} + {b}"
    if sem == "add+1":
        return a + b + 1, f"{a} + {b} + 1"
    if sem == "add-1":
        return a + b - 1, f"{a} + {b} - 1"
    if sem == "sub":
        return a - b, f"{a} - {b}"
    if sem == "rsub":
        return b - a, f"{b} - {a}"
    if sem == "abs_sub":
        return abs(a - b), f"|{a} - {b}|"
    if sem == "neg_abs":
        return -abs(a - b), f"-|{a} - {b}|"
    if sem == "mul":
        return a * b, f"{a} * {b}"
    if sem == "mul+1":
        return a * b + 1, f"{a} * {b} + 1"
    if sem == "mul-1":
        return a * b - 1, f"{a} * {b} - 1"
    if sem == "maxmod":
        return max(a, b) % min(a, b), f"max({a},{b}) mod min({a},{b})"
    raise KeyError(sem)


def _ordered_symbols(examples: list[tuple[str, str]], question: str) -> list[str]:
    """All non-operator-position characters in first-appearance order.

    Every such character (digit slots in inputs/question + every output char,
    including a leading '-' sign) gets a display letter; only the position-2
    operator characters are excluded (they get x/y/z labels instead).
    """
    order: list[str] = []
    seen: set[str] = set()

    def add(c: str) -> None:
        if c not in seen:
            seen.add(c)
            order.append(c)

    for inp, out in examples:
        for i, c in enumerate(inp):
            if i != 2:
                add(c)
        for c in out:
            add(c)
    for i, c in enumerate(question):
        if i != 2:
            add(c)
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


def _input_block(
    inp: str, sym_to_letter: dict[str, str], op_to_label: dict[str, str]
) -> str:
    spelled = " ".join(inp)
    parts = []
    for i, c in enumerate(inp):
        if i == 2:
            parts.append(f"{c} -> {op_to_label[c]}")
        elif c in sym_to_letter:
            parts.append(f"{c} -> {sym_to_letter[c]}")
    abbr = (
        sym_to_letter[inp[0]]
        + sym_to_letter[inp[1]]
        + f" {op_to_label[inp[2]]} "
        + sym_to_letter[inp[3]]
        + sym_to_letter[inp[4]]
    )
    return f"    input:  {spelled} : {', '.join(parts)} -> {abbr}"


def _output_block(out: str, sym_to_letter: dict[str, str]) -> str:
    spelled = " ".join(out)
    parts = [f"{c} -> {sym_to_letter[c]}" for c in out if c in sym_to_letter]
    abbr = "".join(sym_to_letter.get(c, "?") for c in out)
    return f"    output: {spelled} : {', '.join(parts)} -> {abbr}"


def _question_apply(
    question: str,
    sem: str,
    mapping: dict[str, int],
    sym_to_letter: dict[str, str],
    op_to_label: dict[str, str],
    answer: str,
) -> str | None:
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
        letter_to_sym = {letter: s for s, letter in sym_to_letter.items()}
        back = ", ".join(f"{ch} -> {letter_to_sym[ch]}" for ch in res_letters)
        lines.append(f"  Converting back: {' '.join(res_letters)} : {back} -> {answer}")
        return "\n".join(lines)

    # Arithmetic semantic — every operand symbol must have a digit.
    for ch in (question[0], question[1], question[3], question[4]):
        if ch not in mapping:
            return None
    a_val = mapping[question[0]] * 10 + mapping[question[1]]
    b_val = mapping[question[3]] * 10 + mapping[question[4]]
    op_name = _SEMANTIC_NAMES[sem]

    lines.append(
        "  Digit values from the bijection: "
        + ", ".join(
            f"{ch} -> {sym_to_letter[ch]} -> {mapping[ch]}"
            for ch in (question[0], question[1], question[3], question[4])
        )
    )
    lines.append(f"  {a_letter} = {a_val}, {b_letter} = {b_val}")

    result, formula = _arith(sem, a_val, b_val)
    lines.append(f"  {op_name}({a_letter}, {b_letter}) = {formula} = {result}")

    digits = str(abs(result))
    signed = "-" if result < 0 else ""
    if any(int(d) not in inv for d in digits):
        return None
    encoded = signed + "".join(inv[int(d)] for d in digits)
    reveal = ", ".join(f"{d} -> {inv[int(d)]}" for d in dict.fromkeys(digits))
    lines.append(f"  Encoding result: {' '.join(digits)} : {reveal} -> {encoded}")
    if encoded != answer:
        return None
    return "\n".join(lines)


def _concat_default_cot(examples: list[tuple[str, str]], question: str) -> str | None:
    """Concatenation-default CoT for the cryptarithm_guess case (novel q-op).

    The question operator never appears in the examples, so its semantics can't
    be deduced. We state that explicitly and default to forward concatenation —
    the dataset's most common operator. Only scores when the answer genuinely is
    the forward concatenation of the question operands.
    """
    answer = question[0] + question[1] + question[3] + question[4]
    if "}" in answer:
        return None

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
    lines.append("First, let me assign letters to each symbol:")
    for s in digit_syms:
        lines.append(f"{s} -> {sym_to_letter[s]}")
    lines.append("Operators:")
    for o in ops:
        lines.append(f"{o} -> {op_to_label[o]}")
    lines.append("")
    lines.append("Converting examples to letter form:")
    lines.append("")
    for inp, out in examples:
        lines.append(f"  「{inp}」 = 「{out}」:")
        lines.append(_input_block(inp, sym_to_letter, op_to_label))
        lines.append(_output_block(out, sym_to_letter))
        lines.append("")

    a_letter = sym_to_letter[question[0]] + sym_to_letter[question[1]]
    b_letter = sym_to_letter[question[3]] + sym_to_letter[question[4]]
    lines.append(
        f"The question operator {op_to_label[question[2]]} does not appear in the examples."
    )
    lines.append(
        "When the operator cannot be determined, the default transformation is concatenation."
    )
    lines.append("")
    lines.append(
        f'Converting question "{question}": -> {a_letter} {op_to_label[question[2]]} {b_letter}'
    )
    lines.append(f"Applying to {a_letter} {op_to_label[question[2]]} {b_letter}:")
    res_letters = a_letter + b_letter
    lines.append(
        f"  concatenation({a_letter}, {b_letter}) = {a_letter} || {b_letter} = {res_letters}"
    )
    letter_to_sym = {letter: s for s, letter in sym_to_letter.items()}
    back = ", ".join(f"{ch} -> {letter_to_sym[ch]}" for ch in res_letters)
    lines.append(f"  Converting back: {' '.join(res_letters)} : {back} -> {answer}")
    lines.append("")
    lines.append("I will now return the answer in \\boxed{}")
    lines.append("The answer in \\boxed is")
    lines.append(f"\\boxed{{{answer}}}")

    text = "\n".join(lines)
    matches = re.findall(r"\\boxed\{([^}]*)(?:\}|$)", text)
    extracted = matches[-1].strip() if matches else ""
    if extracted != answer.strip():
        return None
    return text


def reasoning_cryptarithm(problem: Problem) -> str | None:
    """Generate a frozen-style CoT for a cryptarithm problem, or None."""
    examples = [(str(ex.input_value), str(ex.output_value)) for ex in problem.examples]
    question = str(problem.question)
    if len(question) != 5 or any(len(i) != 5 for i, _ in examples):
        return None

    detail = _rs_solve_detail(examples, question)
    if detail is None:
        # The solver only commits when the question operator appears in the
        # examples (a determinable cryptarithm_deduce). When it does NOT — the
        # cryptarithm_guess case — the operator's behaviour can't be deduced, so
        # we fall back to the dataset's most common transformation, concatenation.
        # This is an explicit, stated prior (not answer leakage): it only scores
        # on problems whose hidden operator really is forward concatenation.
        example_ops = {inp[2] for inp, _ in examples}
        if question[2] not in example_ops:
            return _concat_default_cot(examples, question)
        return None
    answer, sem_list, mapping_list = detail

    # The kaggle metric extracts the final \boxed{...} with `\\boxed\{([^}]*)\}`,
    # which truncates at the first '}'. Answers containing '}' can't be scored.
    if "}" in answer:
        return None

    op_sem = dict(sem_list)
    mapping = {sym: int(d) for sym, d in mapping_list}
    q_sem = op_sem.get(question[2])
    if q_sem is None:
        return None

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
    lines.append("First, let me assign letters to each symbol:")
    for s in digit_syms:
        lines.append(f"{s} -> {sym_to_letter[s]}")
    lines.append("Operators:")
    for o in ops:
        lines.append(f"{o} -> {op_to_label[o]}")
    lines.append("")
    lines.append("Converting examples to letter form:")
    lines.append("")
    for inp, out in examples:
        lines.append(f"  「{inp}」 = 「{out}」:")
        lines.append(_input_block(inp, sym_to_letter, op_to_label))
        lines.append(_output_block(out, sym_to_letter))
        lines.append("")

    lines.append(
        "Each input is 5 characters: two symbol-digits, an operator, two more symbol-digits."
    )
    for o in sorted(ops):
        sem = op_sem.get(o)
        label = _SEMANTIC_NAMES[sem] if sem else "unknown"
        lines.append(f"Operator {op_to_label[o]}: {label}")
    lines.append("")
    lines.append(
        f"The question operator is {op_to_label[question[2]]}, which is {_SEMANTIC_NAMES[q_sem]}."
    )
    lines.append("")

    q_parts = []
    for i, c in enumerate(question):
        if i == 2:
            q_parts.append(f"{c} -> {op_to_label[c]}")
        elif c in sym_to_letter:
            q_parts.append(f"{c} -> {sym_to_letter[c]}")
    q_abbr = (
        sym_to_letter[question[0]]
        + sym_to_letter[question[1]]
        + f" {op_to_label[question[2]]} "
        + sym_to_letter[question[3]]
        + sym_to_letter[question[4]]
    )
    lines.append(
        f'Converting question "{question}": {" ".join(question)} : '
        + ", ".join(q_parts)
        + f" -> {q_abbr}"
    )
    lines.append("")

    apply_block = _question_apply(
        question, q_sem, mapping, sym_to_letter, op_to_label, answer
    )
    if apply_block is None:
        return None
    lines.append(apply_block)
    lines.append("")
    lines.append("I will now return the answer in \\boxed{}")
    lines.append("The answer in \\boxed is")
    lines.append(f"\\boxed{{{answer}}}")

    text = "\n".join(lines)

    # Round-trip: the extractor's final \boxed{} must equal the deduced answer.
    matches = re.findall(r"\\boxed\{([^}]*)(?:\}|$)", text)
    extracted = matches[-1].strip() if matches else ""
    if extracted != answer.strip():
        return None
    return text
