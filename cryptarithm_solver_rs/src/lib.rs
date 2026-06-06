//! Bijection-based cryptarithm solver (Rust port for speed).
//!
//! A cryptarithm problem is a set of examples `a op b = r` where each symbol is
//! a distinct decimal digit (a bijection), `op` is one of a small operator zoo
//! (add / sub / mul with +/-1 offsets, reverse-sub, abs-sub, neg-abs, maxmod),
//! and the result `r` is the arithmetic value re-encoded in the same symbol
//! alphabet. The question is another `a op b` expression; we must produce its
//! encoded result.
//!
//! Algorithm:
//!   1. Group examples by operator char.
//!   2. Pure-concat operators (fwd / rev) need no bijection — handled directly.
//!   3. For the remaining (arithmetic) operators, enumerate one semantic per
//!      distinct operator from the zoo (worst case 11^3 = 1331 combos in this
//!      dataset), and for each combo run a DFS over digit assignments:
//!      process examples most-constraining-first, assign unmapped input symbols,
//!      derive output-symbol constraints by encoding the arithmetic result, and
//!      backtrack on any bijection conflict.
//!
//! The Python equivalent timed out on the 11^n semantic sweep; in Rust the whole
//! 659-problem set runs in well under a second.

use pyo3::prelude::*;

/// Operator semantics in the zoo. Order matters only for which valid solution is
/// found first; we keep the cheap/common ones early.
const ZOO: [Op; 11] = [
    Op::Add,
    Op::Sub,
    Op::RSub,
    Op::AbsSub,
    Op::Mul,
    Op::AddP1,
    Op::AddM1,
    Op::MulP1,
    Op::MulM1,
    Op::NegAbs,
    Op::MaxMod,
];

#[derive(Clone, Copy, PartialEq)]
enum Op {
    Add,
    Sub,
    RSub,
    AbsSub,
    NegAbs,
    Mul,
    AddP1,
    AddM1,
    MulP1,
    MulM1,
    MaxMod,
}

impl Op {
    fn name(self) -> &'static str {
        match self {
            Op::Add => "add",
            Op::Sub => "sub",
            Op::RSub => "rsub",
            Op::AbsSub => "abs_sub",
            Op::NegAbs => "neg_abs",
            Op::Mul => "mul",
            Op::AddP1 => "add+1",
            Op::AddM1 => "add-1",
            Op::MulP1 => "mul+1",
            Op::MulM1 => "mul-1",
            Op::MaxMod => "maxmod",
        }
    }

    #[inline]
    fn eval(self, a: i64, b: i64) -> Option<i64> {
        match self {
            Op::Add => Some(a + b),
            Op::Sub => Some(a - b),
            Op::RSub => Some(b - a),
            Op::AbsSub => Some((a - b).abs()),
            Op::NegAbs => Some(-((a - b).abs())),
            Op::Mul => Some(a * b),
            Op::AddP1 => Some(a + b + 1),
            Op::AddM1 => Some(a + b - 1),
            Op::MulP1 => Some(a * b + 1),
            Op::MulM1 => Some(a * b - 1),
            Op::MaxMod => {
                let lo = a.min(b);
                if lo == 0 {
                    None
                } else {
                    Some(a.max(b) % lo)
                }
            }
        }
    }
}

/// A single example decomposed into its two operand symbols, operator, and the
/// output symbol sequence (with a sign flag).
struct Ex {
    a0: char,
    a1: char,
    op: char,
    b0: char,
    b1: char,
    out: Vec<char>,
    out_neg: bool,
}

fn split5(s: &[char]) -> Option<(char, char, char, char, char)> {
    if s.len() != 5 {
        return None;
    }
    Some((s[0], s[1], s[2], s[3], s[4]))
}

/// Detect whether every example in a group is forward / reverse concatenation.
/// Returns Some(true) for fwd, Some(false) for rev, None otherwise.
fn detect_concat(group: &[&Ex]) -> Option<bool> {
    let fwd = group.iter().all(|e| {
        !e.out_neg && e.out.len() == 4 && e.out[0] == e.a0 && e.out[1] == e.a1 && e.out[2] == e.b0 && e.out[3] == e.b1
    });
    if fwd {
        return Some(true);
    }
    let rev = group.iter().all(|e| {
        !e.out_neg && e.out.len() == 4 && e.out[0] == e.b0 && e.out[1] == e.b1 && e.out[2] == e.a0 && e.out[3] == e.a1
    });
    if rev {
        return Some(false);
    }
    None
}

/// Bijection state: sym->digit (256-wide for ASCII fast path is not enough since
/// symbols are arbitrary Unicode, so we use a small Vec of (char,digit) plus a
/// digit-used bitmask). Symbol counts are <= 10, so linear scans are tiny.
#[derive(Clone)]
struct Mapping {
    pairs: Vec<(char, u8)>,
    used: u16, // bit d set if digit d is taken
}

impl Mapping {
    fn new() -> Self {
        Mapping {
            pairs: Vec::with_capacity(10),
            used: 0,
        }
    }
    #[inline]
    fn get(&self, c: char) -> Option<u8> {
        self.pairs.iter().find(|(k, _)| *k == c).map(|(_, v)| *v)
    }
    #[inline]
    fn digit_used(&self, d: u8) -> bool {
        (self.used >> d) & 1 == 1
    }
    /// Try to bind c->d; returns false on conflict (c already other digit, or d taken).
    #[inline]
    fn try_bind(&mut self, c: char, d: u8) -> bool {
        if let Some(existing) = self.get(c) {
            return existing == d;
        }
        if self.digit_used(d) {
            return false;
        }
        self.pairs.push((c, d));
        self.used |= 1 << d;
        true
    }
    #[inline]
    fn inv(&self, d: u8) -> Option<char> {
        self.pairs.iter().find(|(_, v)| *v == d).map(|(k, _)| *k)
    }
}

fn two_digit(m: &Mapping, c0: char, c1: char) -> Option<i64> {
    Some(m.get(c0)? as i64 * 10 + m.get(c1)? as i64)
}

/// Encode an integer result back into symbols via the inverse mapping.
/// Returns the encoded char sequence (without sign) and whether it was negative,
/// or None if any digit has no symbol assigned.
fn encode(result: i64, m: &Mapping) -> Option<(Vec<char>, bool)> {
    let neg = result < 0;
    let mag = result.unsigned_abs().to_string();
    let mut out = Vec::with_capacity(mag.len());
    for ch in mag.chars() {
        let d = ch as u8 - b'0';
        out.push(m.inv(d)?);
    }
    Some((out, neg))
}

/// Process one example under a chosen semantic, extending the mapping in every
/// consistent way; each consistent extension is pushed to `out`.
fn process_example(e: &Ex, op: Op, base: &Mapping, out: &mut Vec<Mapping>) {
    // Collect distinct unassigned input symbols (deduped, base-relative).
    let in_syms = [e.a0, e.a1, e.b0, e.b1];
    let mut unassigned: Vec<char> = Vec::with_capacity(4);
    for &s in &in_syms {
        if base.get(s).is_none() && !unassigned.contains(&s) {
            unassigned.push(s);
        }
    }
    let mut cand = base.clone();
    assign_inputs(&unassigned, 0, &mut cand, e, op, out);
}

/// Recursively assign digits to the unassigned input symbols in place (push/pop
/// backtracking, no per-level allocation). At the leaf, evaluate the operator and
/// bind the result symbols, pushing any consistent full extension to `out`.
fn assign_inputs(un: &[char], k: usize, cand: &mut Mapping, e: &Ex, op: Op, out: &mut Vec<Mapping>) {
    if k == un.len() {
        let a = cand.get(e.a0).unwrap() as i64 * 10 + cand.get(e.a1).unwrap() as i64;
        let b = cand.get(e.b0).unwrap() as i64 * 10 + cand.get(e.b1).unwrap() as i64;
        let r = match op.eval(a, b) {
            Some(v) => v,
            None => return,
        };
        if (r < 0) != e.out_neg {
            return;
        }
        let mag = r.unsigned_abs().to_string();
        if mag.len() != e.out.len() {
            return;
        }
        let mut ext = cand.clone();
        for (ch, &osym) in mag.chars().zip(e.out.iter()) {
            if !ext.try_bind(osym, ch as u8 - b'0') {
                return;
            }
        }
        out.push(ext);
        return;
    }
    let s = un[k];
    for d in 0u8..10 {
        if cand.digit_used(d) {
            continue;
        }
        cand.pairs.push((s, d));
        cand.used |= 1 << d;
        assign_inputs(un, k + 1, cand, e, op, out);
        cand.pairs.pop();
        cand.used &= !(1 << d);
    }
}

/// Does some standalone bijection satisfy every example in `group` under `sem`?
/// Used to pre-filter the per-operator semantic search: a semantic infeasible for
/// an operator in isolation can never be part of a global solution, so skipping
/// it is answer-preserving — it only avoids dead branches of the cartesian sweep.
fn group_solvable(group: &[usize], sem: Op, exs: &[Ex]) -> bool {
    group_dfs(group, 0, sem, exs, &Mapping::new())
}

fn group_dfs(group: &[usize], start: usize, sem: Op, exs: &[Ex], mapping: &Mapping) -> bool {
    if start == group.len() {
        return true;
    }
    let mut exts: Vec<Mapping> = Vec::new();
    process_example(&exs[group[start]], sem, mapping, &mut exts);
    for ext in exts {
        if group_dfs(group, start + 1, sem, exs, &ext) {
            return true;
        }
    }
    false
}

/// DFS over the ordered (example, semantic) list, yielding the first full mapping
/// that satisfies all of them.
fn backtrack(items: &[(usize, Op)], exs: &[Ex], base: Mapping) -> Option<Mapping> {
    if items.is_empty() {
        return Some(base);
    }
    let (ex_idx, op) = items[0];
    let mut extensions: Vec<Mapping> = Vec::new();
    process_example(&exs[ex_idx], op, &base, &mut extensions);
    for ext in extensions {
        if let Some(sol) = backtrack(&items[1..], exs, ext) {
            return Some(sol);
        }
    }
    None
}

/// Full result of a solve: the encoded answer, the deduced semantic per operator,
/// and the symbol->digit bijection (empty for pure-concat questions).
struct SolveResult {
    answer: String,
    semantics: Vec<(String, String)>,
    mapping: Vec<(String, u8)>,
}

fn solve_inner(examples: &[(String, String)], question: &str) -> Option<SolveResult> {
    let q: Vec<char> = question.chars().collect();
    let (qa0, qa1, qop, qb0, qb1) = split5(&q)?;

    // Parse examples.
    let mut exs: Vec<Ex> = Vec::new();
    for (inp, out) in examples {
        let ic: Vec<char> = inp.chars().collect();
        if let Some((a0, a1, op, b0, b1)) = split5(&ic) {
            let oc: Vec<char> = out.chars().collect();
            let (out_neg, out_syms) = if oc.first() == Some(&'-') {
                (true, oc[1..].to_vec())
            } else {
                (false, oc)
            };
            exs.push(Ex {
                a0,
                a1,
                op,
                b0,
                b1,
                out: out_syms,
                out_neg,
            });
        }
    }
    if exs.is_empty() {
        return None;
    }

    // Distinct operators.
    let mut ops: Vec<char> = Vec::new();
    for e in &exs {
        if !ops.contains(&e.op) {
            ops.push(e.op);
        }
    }

    // Group indices by operator.
    let group_of = |op: char| -> Vec<&Ex> { exs.iter().filter(|e| e.op == op).collect() };

    // Phase 1: concat detection per operator.
    let mut concat_fwd: Vec<(char, bool)> = Vec::new();
    for &op in &ops {
        let g = group_of(op);
        if let Some(fwd) = detect_concat(&g) {
            concat_fwd.push((op, fwd));
        }
    }
    let is_concat = |op: char| concat_fwd.iter().find(|(o, _)| *o == op).map(|(_, f)| *f);

    // If the question operator is pure concat, answer directly.
    if let Some(fwd) = is_concat(qop) {
        let ans: String = if fwd {
            [qa0, qa1, qb0, qb1].iter().collect()
        } else {
            [qb0, qb1, qa0, qa1].iter().collect()
        };
        let semantics = concat_fwd
            .iter()
            .map(|(op, f)| {
                (
                    op.to_string(),
                    if *f { "fwd_concat" } else { "rev_concat" }.to_string(),
                )
            })
            .collect();
        return Some(SolveResult {
            answer: ans,
            semantics,
            mapping: Vec::new(),
        });
    }

    // Question operator must appear in examples to be deducible.
    if !ops.contains(&qop) {
        return None;
    }

    // Symbol-count guard: a 10-digit bijection can't cover >10 distinct symbols.
    let mut syms: Vec<char> = Vec::new();
    let push_sym = |c: char, v: &mut Vec<char>| {
        if !v.contains(&c) {
            v.push(c);
        }
    };
    for e in &exs {
        for &c in &[e.a0, e.a1, e.b0, e.b1] {
            push_sym(c, &mut syms);
        }
        for &c in &e.out {
            push_sym(c, &mut syms);
        }
    }
    for &c in &[qa0, qa1, qb0, qb1] {
        push_sym(c, &mut syms);
    }
    if syms.len() > 10 {
        return None;
    }

    // Arithmetic operators = all ops that aren't pure concat. The question op is
    // included (it's in `ops` and not concat here).
    let arith: Vec<char> = ops.iter().copied().filter(|o| is_concat(*o).is_none()).collect();
    if arith.is_empty() {
        return None;
    }

    // Precompute, per arithmetic operator, the example indices (most-constraining
    // first: more distinct symbols in the example => earlier).
    let arith_ex: Vec<Vec<usize>> = arith
        .iter()
        .map(|&op| {
            let mut idxs: Vec<usize> = exs
                .iter()
                .enumerate()
                .filter(|(_, e)| e.op == op)
                .map(|(i, _)| i)
                .collect();
            idxs.sort_by_key(|&i| {
                let e = &exs[i];
                let mut s: Vec<char> = Vec::new();
                for &c in &[e.a0, e.a1, e.b0, e.b1] {
                    if !s.contains(&c) {
                        s.push(c);
                    }
                }
                for &c in &e.out {
                    if !s.contains(&c) {
                        s.push(c);
                    }
                }
                -(s.len() as i64)
            });
            idxs
        })
        .collect();

    // Per-operator feasibility: bit j of feasible[oi] is set iff ZOO[j] admits a
    // standalone bijection for operator arith[oi]'s example group. The global
    // cartesian sweep skips any combo using an infeasible semantic — sound because
    // a global solution restricts to a standalone solution for every operator.
    let n = arith.len();
    let mut feasible: Vec<u16> = vec![0u16; n];
    for oi in 0..n {
        let mut mask = 0u16;
        for (j, &sem) in ZOO.iter().enumerate() {
            if group_solvable(&arith_ex[oi], sem, &exs) {
                mask |= 1 << j;
            }
        }
        if mask == 0 {
            return None; // this operator admits no semantic at all
        }
        feasible[oi] = mask;
    }

    // Enumerate one semantic per arithmetic operator (cartesian product over ZOO).
    let mut combo_idx = vec![0usize; n];
    loop {
        // Skip combos whose semantic is infeasible for some operator (cheap O(n)).
        let feasible_combo = (0..n).all(|oi| (feasible[oi] >> combo_idx[oi]) & 1 == 1);
        if !feasible_combo {
            if advance_odometer(&mut combo_idx, ZOO.len()) {
                return None;
            }
            continue;
        }

        // Build the ordered (example, semantic) work list for this combo.
        // Order operators by how constraining their hardest example is.
        let mut items: Vec<(usize, Op)> = Vec::new();
        for (oi, _) in arith.iter().enumerate() {
            let op = ZOO[combo_idx[oi]];
            for &ex_idx in &arith_ex[oi] {
                items.push((ex_idx, op));
            }
        }
        // Most-constraining example first across the whole list.
        items.sort_by_key(|&(ex_idx, _)| {
            let e = &exs[ex_idx];
            let mut s: Vec<char> = Vec::new();
            for &c in &[e.a0, e.a1, e.b0, e.b1] {
                if !s.contains(&c) {
                    s.push(c);
                }
            }
            for &c in &e.out {
                if !s.contains(&c) {
                    s.push(c);
                }
            }
            -(s.len() as i64)
        });

        if let Some(m) = backtrack(&items, &exs, Mapping::new()) {
            // Apply the question operator's semantic.
            let qop_pos = arith.iter().position(|&o| o == qop).unwrap();
            let q_sem = ZOO[combo_idx[qop_pos]];
            if let (Some(a), Some(b)) = (two_digit(&m, qa0, qa1), two_digit(&m, qb0, qb1)) {
                if let Some(r) = q_sem.eval(a, b) {
                    if let Some((syms_out, neg)) = encode(r, &m) {
                        let mut ans = String::new();
                        if neg {
                            ans.push('-');
                        }
                        ans.extend(syms_out);

                        // Assemble deduced semantics (concat + arithmetic) and mapping.
                        let mut semantics: Vec<(String, String)> = concat_fwd
                            .iter()
                            .map(|(op, f)| {
                                (
                                    op.to_string(),
                                    if *f { "fwd_concat" } else { "rev_concat" }.to_string(),
                                )
                            })
                            .collect();
                        for (oi, &op_char) in arith.iter().enumerate() {
                            semantics.push((op_char.to_string(), ZOO[combo_idx[oi]].name().to_string()));
                        }
                        let mut mapping: Vec<(String, u8)> =
                            m.pairs.iter().map(|(c, d)| (c.to_string(), *d)).collect();
                        mapping.sort_by_key(|(_, d)| *d);

                        return Some(SolveResult {
                            answer: ans,
                            semantics,
                            mapping,
                        });
                    }
                }
            }
        }

        // Advance the mixed-radix combo counter; stop when it wraps.
        if advance_odometer(&mut combo_idx, ZOO.len()) {
            return None; // exhausted all semantic combinations
        }
    }
}

/// Increment a mixed-radix counter (least-significant digit first). Returns true
/// when the counter wraps past its maximum (i.e. enumeration is exhausted).
fn advance_odometer(idx: &mut [usize], radix: usize) -> bool {
    let mut k = 0;
    loop {
        if k == idx.len() {
            return true;
        }
        idx[k] += 1;
        if idx[k] < radix {
            return false;
        }
        idx[k] = 0;
        k += 1;
    }
}

/// Solve a cryptarithm. Returns the predicted answer string, or None if the
/// problem is out of scope (non-5-char, >10 symbols, q-op absent, no consistent
/// assignment found).
#[pyfunction]
fn solve(examples: Vec<(String, String)>, question: String) -> Option<String> {
    solve_inner(&examples, &question).map(|r| r.answer)
}

/// Solve a cryptarithm and return the full deduction for CoT generation:
/// (answer, [(operator_char, semantic_name), ...], [(symbol, digit), ...]).
/// The mapping is empty when the question operator is pure concatenation.
#[pyfunction]
fn solve_detail(
    examples: Vec<(String, String)>,
    question: String,
) -> Option<(String, Vec<(String, String)>, Vec<(String, u8)>)> {
    solve_inner(&examples, &question).map(|r| (r.answer, r.semantics, r.mapping))
}

#[pymodule]
fn cryptarithm_solver_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(solve, m)?)?;
    m.add_function(wrap_pyfunction!(solve_detail, m)?)?;
    Ok(())
}
