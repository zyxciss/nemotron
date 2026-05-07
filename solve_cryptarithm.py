"""Fast cryptarithm solver using Z3 SMT solver.

Each cryptarithm problem uses base-N arithmetic where:
- Each symbol maps to a unique digit 0..N-1
- Each operator at position [2] maps to an arithmetic operation (add, sub, mul, concat)
- '-' prefix in output indicates negative result (only from subtraction)
- '-' never appears as a value symbol

Usage: uv run solve_cryptarithm.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from itertools import product as iproduct

import z3


def _parse_problem(data: dict) -> tuple | None:
    """Parse a problem into structured form."""
    examples = []
    all_ops = set()
    all_val_chars = set()  # Characters that appear in value positions

    for ex in data["examples"]:
        inp = ex["input_value"]
        out = ex["output_value"]
        if len(inp) != 5:
            return None
        a1, a2, op, b1, b2 = inp
        all_ops.add(op)
        all_val_chars.update([a1, a2, b1, b2])
        out_clean = out.lstrip("-")
        for c in out_clean:
            all_val_chars.add(c)
        is_neg = out.startswith("-") and len(out) > 1
        examples.append((a1, a2, op, b1, b2, out_clean, is_neg))

    q = data["question"]
    if len(q) != 5:
        return None
    qa1, qa2, qop, qb1, qb2 = q
    all_val_chars.update([qa1, qa2, qb1, qb2])
    ans = data["answer"]
    ans_neg = ans.startswith("-") and len(ans) > 1
    ans_clean = ans.lstrip("-")
    for c in ans_clean:
        all_val_chars.add(c)

    # A character is a "pure operator" only if it appears at position 2
    # but NEVER appears as a value character
    pure_ops = all_ops - all_val_chars
    # '-' is always treated as a sign indicator, never as a value symbol
    # (confirmed: '-' never appears in operand positions)
    value_symbols = sorted(all_val_chars - {"-"})
    n = len(value_symbols)

    return examples, all_ops, pure_ops, value_symbols, n, (qa1, qa2, qop, qb1, qb2), ans_clean, ans_neg


def _get_op_candidates(all_ops, pure_ops, examples):
    """Determine possible operations for each operator."""
    op_candidates = {}
    for op in sorted(all_ops):
        # Check if any example with this op has '-' prefix in output (negative)
        op_exs = [(a1, a2, b1, b2, out, is_neg)
                   for a1, a2, opc, b1, b2, out, is_neg in examples if opc == op]
        has_neg = any(is_neg for _, _, _, _, _, is_neg in op_exs)
        has_concat = any(not is_neg and out == a1 + a2 + b1 + b2
                         for a1, a2, b1, b2, out, is_neg in op_exs)

        if has_concat:
            op_candidates[op] = ["concat"]
        elif has_neg:
            op_candidates[op] = ["sub"]
        elif op == "-":
            op_candidates[op] = ["sub"]
        elif op in pure_ops:
            # Pure operator (never a value symbol) - standard meanings
            if op == "+":
                op_candidates[op] = ["add", "concat"]
            elif op == "*":
                op_candidates[op] = ["mul", "concat"]
            else:
                op_candidates[op] = ["concat", "add", "sub", "mul"]
        else:
            # Custom operator that also appears as a value
            op_candidates[op] = ["concat", "add", "sub", "mul"]

    return op_candidates


def _to_number(chars: str, sym_vars: dict, base: int):
    """Convert a string of symbol characters to a z3 integer expression."""
    result = z3.IntVal(0)
    for c in chars:
        result = result * base + sym_vars[c]
    return result


def solve_with_z3(data: dict, max_time: float = 5.0) -> dict | None:
    """Solve a cryptarithm problem using Z3."""
    parsed = _parse_problem(data)
    if parsed is None:
        return None

    examples, all_ops, pure_ops, value_symbols, n, question, ans_clean, ans_neg = parsed
    qa1, qa2, qop, qb1, qb2 = question

    if n > 11:
        return None

    op_candidates = _get_op_candidates(all_ops, pure_ops, examples)
    op_list = sorted(all_ops)
    op_combos = list(iproduct(*[op_candidates[op] for op in op_list]))

    for base in [n]:
        if n > base:
            continue

        for op_combo in op_combos:
            op_map = dict(zip(op_list, op_combo))

            solver = z3.Solver()
            solver.set("timeout", int(max_time * 1000))

            # Create symbol variables
            sym_vars = {}
            for s in value_symbols:
                v = z3.Int(f"s_{ord(s)}")
                sym_vars[s] = v
                solver.add(v >= 0, v < base)

            # All-different constraint
            if len(value_symbols) > 1:
                solver.add(z3.Distinct(*sym_vars.values()))

            valid = True
            # Add example constraints
            for a1, a2, op, b1, b2, out_clean, is_neg in examples:
                left = sym_vars[a1] * base + sym_vars[a2]
                right = sym_vars[b1] * base + sym_vars[b2]

                op_name = op_map[op]

                if op_name == "concat":
                    # Concat: left * base^2 + right (always 2+2 digit symbols)
                    result_expr = left * (base * base) + right
                elif op_name == "add":
                    result_expr = left + right
                elif op_name == "sub":
                    if is_neg:
                        result_expr = right - left  # |left - right| when result is negative
                    else:
                        result_expr = left - right
                elif op_name == "mul":
                    result_expr = left * right
                else:
                    valid = False
                    break

                if is_neg and op_name != "sub":
                    valid = False
                    break

                if is_neg:
                    solver.add(left - right < 0)

                # Check all output chars are in our symbol set
                if not all(c in sym_vars for c in out_clean):
                    valid = False
                    break

                # Encode the output
                out_expr = _to_number(out_clean, sym_vars, base)

                # The output must equal the result
                solver.add(result_expr == out_expr)

                # Output length constraint
                out_len = len(out_clean)
                if out_len == 1:
                    solver.add(result_expr >= 0)
                    solver.add(result_expr < base)
                elif out_len >= 2:
                    solver.add(result_expr >= base ** (out_len - 1))
                    solver.add(result_expr < base ** out_len)

            if not valid:
                continue

            while solver.check() == z3.sat:
                model = solver.model()
                mapping = {s: model.eval(v).as_long() for s, v in sym_vars.items()}
                
                # Block this solution for the next iteration
                block = []
                for s, v in sym_vars.items():
                    block.append(v != mapping[s])
                solver.add(z3.Or(*block))

                rev_map = {v: k for k, v in mapping.items()}

                # Verify answer chars are in mapping
                if not all(c in mapping for c in [qa1, qa2, qb1, qb2]):
                    continue

                left_val = mapping[qa1] * base + mapping[qa2]
                right_val = mapping[qb1] * base + mapping[qb2]

                qop_name = op_map.get(qop)
                ops_to_try = [qop_name] if qop_name else ["concat", "add", "sub", "mul"]

                for try_op in ops_to_try:
                    if try_op == "concat":
                        res = left_val * base * base + right_val
                    elif try_op == "add":
                        res = left_val + right_val
                    elif try_op == "sub":
                        res = left_val - right_val
                    elif try_op == "mul":
                        res = left_val * right_val
                    else:
                        continue

                    neg_prefix = ""
                    if res < 0:
                        neg_prefix = "-"
                        res = -res

                    if res == 0:
                        if 0 in rev_map:
                            encoded = neg_prefix + rev_map[0]
                        else:
                            continue
                    else:
                        digits = []
                        temp = res
                        ok = True
                        while temp > 0:
                            d = temp % base
                            if d not in rev_map:
                                ok = False
                                break
                            digits.append(rev_map[d])
                            temp //= base
                        if not ok:
                            continue
                        encoded = neg_prefix + "".join(reversed(digits))

                    correct = encoded == data["answer"]
                    if correct:
                        return {
                            "predicted": encoded,
                            "actual": data["answer"],
                            "correct": correct,
                            "ops": dict(op_map),
                            "base": base,
                            "mapping": mapping,
                            "qop_name": try_op
                        }

    return None


def main():
    problems_dir = Path("problems")
    with open("problems.jsonl") as f:
        entries = [json.loads(l) for l in f if l.strip()]

    crypto_d = [e for e in entries if e["category"] == "cryptarithm_deduce"]
    crypto_g = [e for e in entries if e["category"] == "cryptarithm_guess"]

    t0 = time.time()
    correct = 0
    wrong = 0
    unsolvable = 0

    for i, entry in enumerate(crypto_d):
        pid = entry["id"]
        p = problems_dir / f"{pid}.jsonl"
        with open(p) as f:
            data = json.loads(f.readline())

        result = solve_with_z3(data, max_time=5.0)
        if result:
            if result["correct"]:
                correct += 1
            else:
                wrong += 1
                if wrong <= 5:
                    print(
                        f"WRONG: {pid} pred={result['predicted']} actual={result['actual']} "
                        f"ops={result['ops']} base={result['base']}"
                    )
        else:
            unsolvable += 1

        if (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            print(
                f"[{elapsed:.0f}s] {i+1}/{len(crypto_d)}: "
                f"correct={correct} wrong={wrong} unsolvable={unsolvable}"
            )

    elapsed = time.time() - t0
    print(f"\n=== CRYPTARITHM DEDUCE ===")
    print(f"Correct: {correct}/{len(crypto_d)} ({correct/len(crypto_d)*100:.1f}%)")
    print(f"Wrong: {wrong}")
    print(f"Unsolvable: {unsolvable}")
    print(f"Time: {elapsed:.1f}s")

    # Also test on cryptarithm_guess
    t0g = time.time()
    correct_g = 0
    wrong_g = 0
    unsolvable_g = 0

    for entry in crypto_g:
        pid = entry["id"]
        p = problems_dir / f"{pid}.jsonl"
        with open(p) as f:
            data = json.loads(f.readline())

        result = solve_with_z3(data, max_time=5.0)
        if result:
            if result["correct"]:
                correct_g += 1
            else:
                wrong_g += 1
        else:
            unsolvable_g += 1

    elapsed_g = time.time() - t0g
    print(f"\n=== CRYPTARITHM GUESS ===")
    print(f"Correct: {correct_g}/{len(crypto_g)} ({correct_g/len(crypto_g)*100:.1f}%)")
    print(f"Wrong: {wrong_g}")
    print(f"Unsolvable: {unsolvable_g}")
    print(f"Time: {elapsed_g:.1f}s")


if __name__ == "__main__":
    main()
