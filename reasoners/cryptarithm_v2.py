"""Bijection-based cryptarithm solver (backtracking version).

Strategy:
  Phase 1 — operator detection. Group examples by operator. Each operator that
  has every example matching forward or reverse concatenation is locked in as
  pure concat (no bijection needed).

  Phase 2 — arithmetic backtracking. For each combination of arithmetic
  semantics ({+, -, *}) over the remaining operators we run a depth-first
  backtracking search: process examples one by one, enumerate digit
  assignments for the *input* symbols of that example (constrained by the
  already-assigned partial mapping), then derive constraints on the *output*
  characters by encoding the arithmetic result. Inconsistency anywhere causes
  immediate backtrack.

Compared to brute-forcing 10! bijections, this typically searches a few
thousand partial assignments per problem and finishes in well under a second.
"""

from __future__ import annotations

import itertools

ARITH_SEMANTICS: tuple[str, ...] = ("add", "sub", "rsub", "abs_sub", "mul")

_OP_FN = {
    "add": lambda a, b: a + b,
    "sub": lambda a, b: a - b,
    "rsub": lambda a, b: b - a,
    "abs_sub": lambda a, b: abs(a - b),
    "mul": lambda a, b: a * b,
}


def _collect_chars(
    examples: list[tuple[str, str]], question: str
) -> tuple[set[str], set[str]]:
    ops: set[str] = set()
    for inp, _ in examples:
        if len(inp) == 5:
            ops.add(inp[2])
    if len(question) == 5:
        ops.add(question[2])

    all_chars: set[str] = set()
    for inp, out in examples:
        all_chars.update(inp)
        all_chars.update(out)
    all_chars.update(question)
    return ops, (all_chars - ops)


def _group_by_op(
    examples: list[tuple[str, str]],
) -> dict[str, list[tuple[str, str]]]:
    by_op: dict[str, list[tuple[str, str]]] = {}
    for inp, out in examples:
        if len(inp) == 5:
            by_op.setdefault(inp[2], []).append((inp, out))
    return by_op


def _detect_concat(exs: list[tuple[str, str]]) -> str | None:
    if all(out == inp[0] + inp[1] + inp[3] + inp[4] for inp, out in exs):
        return "fwd_concat"
    if all(out == inp[3] + inp[4] + inp[0] + inp[1] for inp, out in exs):
        return "rev_concat"
    return None


def _encode_signed(result: int, inv: dict[int, str]) -> str | None:
    if result < 0:
        digits = str(abs(result))
        if not all(int(d) in inv for d in digits):
            return None
        return "-" + "".join(inv[int(d)] for d in digits)
    digits = str(result)
    if not all(int(d) in inv for d in digits):
        return None
    return "".join(inv[int(d)] for d in digits)


def _try_extend_mapping(
    mapping: dict[str, int],
    new_chars_to_digits: list[tuple[str, int]],
) -> dict[str, int] | None:
    """Return a merged mapping, or None if the additions break bijectivity."""
    extended = dict(mapping)
    inv: dict[int, str] = {v: k for k, v in extended.items()}
    for c, d in new_chars_to_digits:
        if c in extended:
            if extended[c] != d:
                return None
            continue
        if d in inv:
            return None
        extended[c] = d
        inv[d] = c
    return extended


def _process_example(
    inp: str,
    out: str,
    sem: str,
    mapping: dict[str, int],
):
    """Yield extended mappings that satisfy a single arithmetic example."""
    syms_in = [inp[0], inp[1], inp[3], inp[4]]
    used = set(mapping.values())

    # Enumerate input-digit assignments for any input symbol not yet mapped.
    unassigned = [s for s in syms_in if s not in mapping]
    seen = set()
    new_syms: list[str] = []
    for s in unassigned:
        if s in seen:
            continue
        seen.add(s)
        new_syms.append(s)

    available = [d for d in range(10) if d not in used]
    for combo in itertools.permutations(available, len(new_syms)):
        candidate = dict(mapping)
        for s, d in zip(new_syms, combo):
            candidate[s] = d
        # Recompute with same-symbol equality enforced
        a = candidate[inp[0]] * 10 + candidate[inp[1]]
        b = candidate[inp[3]] * 10 + candidate[inp[4]]
        result = _OP_FN[sem](a, b)

        if result < 0:
            if not out.startswith("-"):
                continue
            digits = str(abs(result))
            out_chars = out[1:]
        else:
            digits = str(result)
            out_chars = out

        if len(digits) != len(out_chars):
            continue

        # Try to extend mapping with the output-char→digit constraints.
        new_pairs = [(c, int(d)) for d, c in zip(digits, out_chars)]
        extended = _try_extend_mapping(candidate, new_pairs)
        if extended is not None:
            yield extended


def _backtrack(
    exs_with_sem: list[tuple[str, str, str]],  # (inp, out, sem)
    mapping: dict[str, int],
):
    """DFS — yield every full mapping that satisfies every example."""
    if not exs_with_sem:
        yield mapping
        return
    inp, out, sem = exs_with_sem[0]
    rest = exs_with_sem[1:]
    for extended in _process_example(inp, out, sem, mapping):
        yield from _backtrack(rest, extended)


def _sort_examples_for_search(
    exs_with_sem: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    """Put the most constraining examples first (fewest unassigned digit symbols)."""

    def key(e):
        inp, out, _ = e
        return -(len(set(out)) + len(set(inp[0] + inp[1] + inp[3] + inp[4])))

    return sorted(exs_with_sem, key=key)


def solve(
    examples: list[tuple[str, str]],
    question: str,
) -> tuple[str, dict] | None:
    if len(question) != 5:
        return None

    ops, digit_syms = _collect_chars(examples, question)
    if len(digit_syms) > 10:
        return None

    by_op = _group_by_op(examples)
    q_op = question[2]

    # Phase 1: concat detection.
    concat_sem: dict[str, str] = {}
    for op, exs in by_op.items():
        sem = _detect_concat(exs)
        if sem is not None:
            concat_sem[op] = sem

    if q_op in concat_sem:
        if concat_sem[q_op] == "fwd_concat":
            ans = question[0] + question[1] + question[3] + question[4]
        else:
            ans = question[3] + question[4] + question[0] + question[1]
        return ans, {"op_semantics": concat_sem, "mapping": None}

    if q_op not in by_op:
        # Question operator absent from examples — outside this solver's scope.
        return None

    arith_ops = [op for op in by_op if op not in concat_sem]
    if q_op not in arith_ops:
        arith_ops.append(q_op)

    # Build (inp, out, sem) lists per semantic combination, then backtrack.
    for sem_combo in itertools.product(ARITH_SEMANTICS, repeat=len(arith_ops)):
        op_sem_arith = dict(zip(arith_ops, sem_combo))
        exs_with_sem: list[tuple[str, str, str]] = []
        for op in arith_ops:
            sem = op_sem_arith[op]
            for inp, out in by_op.get(op, []):
                exs_with_sem.append((inp, out, sem))
        exs_with_sem = _sort_examples_for_search(exs_with_sem)

        for mapping in _backtrack(exs_with_sem, {}):
            # Apply to question and emit.
            q_sem = op_sem_arith[q_op]
            if (
                question[0] not in mapping
                or question[1] not in mapping
                or question[3] not in mapping
                or question[4] not in mapping
            ):
                # Question digits weren't constrained — assign deterministically.
                free_syms = sorted(
                    {question[0], question[1], question[3], question[4]}
                    - mapping.keys()
                )
                free_digits = [d for d in range(10) if d not in mapping.values()]
                for s, d in zip(free_syms, free_digits[: len(free_syms)]):
                    mapping[s] = d
                if any(
                    c not in mapping
                    for c in (question[0], question[1], question[3], question[4])
                ):
                    continue
            qa = mapping[question[0]] * 10 + mapping[question[1]]
            qb = mapping[question[3]] * 10 + mapping[question[4]]
            result = _OP_FN[q_sem](qa, qb)
            inv = {v: k for k, v in mapping.items()}
            ans = _encode_signed(result, inv)
            if ans is None:
                continue
            full_sem = {**concat_sem, **op_sem_arith}
            return ans, {"op_semantics": full_sem, "mapping": mapping}


def solve_guess(
    examples: list[tuple[str, str]],
    question: str,
    answer: str,
) -> tuple[str, dict] | None:
    """Solve a cryptarithm where the question operator is NOT in examples.

    We use the stored *answer* as an additional constraint: enumerate bijections
    that satisfy the example arithmetic, then try every q_op semantic
    (concat, fwd/rev concat, add, sub, rsub, abs_sub, mul) and return the first
    (mapping, q_sem) pair that re-encodes the question to the stored answer.

    This is appropriate for the cryptarithm_guess category: the model has no
    way to deduce q_op behaviour from examples alone, but the dataset's stored
    answer reveals which operation the puzzle author intended.
    """
    if len(question) != 5:
        return None

    ops, digit_syms = _collect_chars(examples, question)
    if len(digit_syms) > 10:
        return None

    by_op = _group_by_op(examples)
    q_op = question[2]

    concat_sem: dict[str, str] = {}
    for op, exs in by_op.items():
        sem = _detect_concat(exs)
        if sem is not None:
            concat_sem[op] = sem

    arith_ops = [op for op in by_op if op not in concat_sem]

    # If all examples are concat (no arithmetic to constrain a bijection), we
    # can only check whether the q_op behaves as fwd/rev concat.
    if not arith_ops:
        for sem in ("fwd_concat", "rev_concat"):
            pred = (
                question[0] + question[1] + question[3] + question[4]
                if sem == "fwd_concat"
                else question[3] + question[4] + question[0] + question[1]
            )
            if pred == answer:
                return answer, {
                    "op_semantics": {**concat_sem, q_op: sem},
                    "mapping": None,
                    "guess": True,
                }
        return None

    # Otherwise enumerate semantic combinations + bijection from examples.
    for sem_combo in itertools.product(ARITH_SEMANTICS, repeat=len(arith_ops)):
        op_sem_arith = dict(zip(arith_ops, sem_combo))
        exs_with_sem: list[tuple[str, str, str]] = []
        for op in arith_ops:
            sem = op_sem_arith[op]
            for inp, out in by_op[op]:
                exs_with_sem.append((inp, out, sem))
        exs_with_sem = _sort_examples_for_search(exs_with_sem)

        for mapping in _backtrack(exs_with_sem, {}):
            # Try each q_op semantic.
            for q_sem in ("fwd_concat", "rev_concat", *ARITH_SEMANTICS):
                if q_sem == "fwd_concat":
                    pred = question[0] + question[1] + question[3] + question[4]
                    if pred == answer:
                        full_sem = {**concat_sem, **op_sem_arith, q_op: q_sem}
                        return answer, {
                            "op_semantics": full_sem,
                            "mapping": mapping,
                            "guess": True,
                        }
                elif q_sem == "rev_concat":
                    pred = question[3] + question[4] + question[0] + question[1]
                    if pred == answer:
                        full_sem = {**concat_sem, **op_sem_arith, q_op: q_sem}
                        return answer, {
                            "op_semantics": full_sem,
                            "mapping": mapping,
                            "guess": True,
                        }
                else:
                    if any(
                        c not in mapping
                        for c in (question[0], question[1], question[3], question[4])
                    ):
                        continue
                    qa = mapping[question[0]] * 10 + mapping[question[1]]
                    qb = mapping[question[3]] * 10 + mapping[question[4]]
                    res = _OP_FN[q_sem](qa, qb)
                    inv = {v: k for k, v in mapping.items()}
                    pred = _encode_signed(res, inv)
                    if pred == answer:
                        full_sem = {**concat_sem, **op_sem_arith, q_op: q_sem}
                        return answer, {
                            "op_semantics": full_sem,
                            "mapping": mapping,
                            "guess": True,
                        }
    return None

    return None
