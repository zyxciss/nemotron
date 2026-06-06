use pyo3::prelude::*;

const N: usize = 8;
const N_KEYS: usize = 24; // 3 kinds (ROT, SHL, SHR) × 8 k
const N_VAR: usize = 48;  // keys + NOT variants
const PAIR_ORDER: [&str; 6] = ["AND", "OR", "XOR", "AND-NOT", "OR-NOT", "XOR-NOT"];

fn bits(s: &str) -> [u8; N] {
    let mut out = [0u8; N];
    for (i, b) in s.bytes().enumerate().take(N) {
        out[i] = b - b'0';
    }
    out
}

fn bitstr(bits: &[u8; N]) -> String {
    let mut s = String::with_capacity(N);
    for &b in bits {
        s.push(if (b & 1) == 1 { '1' } else { '0' });
    }
    s
}

fn matches(masks: &[u16; N], full: u16, out_targets: &[u16; N]) -> bool {
    for j in 0..N {
        if (masks[j] & full) != out_targets[j] {
            return false;
        }
    }
    true
}

fn add_answer(existing: &mut Option<[u8; N]>, qrow: &[u8; N]) -> bool {
    let mut bits = [0u8; N];
    bits.copy_from_slice(qrow);
    match existing {
        None => {
            *existing = Some(bits);
            false
        }
        Some(ref saved) => saved != &bits,
    }
}

fn eval_pair(fam: &str, a: u8, b: u8) -> Option<u8> {
    match fam {
        "AND" => Some(a & b),
        "OR" => Some(a | b),
        "XOR" => Some(a ^ b),
        "AND-NOT" => Some(a & (1 - b)),
        "OR-NOT" => Some(a | (1 - b)),
        "XOR-NOT" => Some(a ^ (1 - b)),
        _ => None,
    }
}

#[pyfunction]
fn solve(examples: Vec<(String, String)>, question: String) -> Option<String> {
    if question.len() != N {
        return None;
    }
    let n_ex = examples.len();
    if n_ex == 0 {
        return None;
    }

    // Validate and parse inputs
    let inputs_bits: Vec<[u8; N]> = examples.iter().map(|(i, _)| bits(i)).collect();
    for (_, o) in &examples {
        if o.len() != N {
            return None;
        }
    }
    let full: u16 = (1u16 << n_ex) - 1;

    // out_targets[j] = bitmask of which examples have output bit j = 1
    let mut out_targets = [0u16; N];
    for j in 0..N {
        let mut m = 0u16;
        for (e, (_, o)) in examples.iter().enumerate() {
            if o.as_bytes()[j] == b'1' {
                m |= 1 << e;
            }
        }
        out_targets[j] = m;
    }

    let qin = bits(&question);

    // Precompute view masks and query rows for all 24 (kind, k) combinations
    let mut view_masks = [[0u16; N]; N_KEYS];
    let mut view_qrows = [[0u8; N]; N_KEYS];

    for ki in 0..N_KEYS {
        let kind = ki / N; // 0=ROT, 1=SHL, 2=SHR
        let k = ki % N;

        for j in 0..N {
            let (src, valid) = match kind {
                0 => {
                    // ROT
                    let src = (j + N - k) % N;
                    (src, true)
                }
                1 => {
                    // SHL
                    let sj = j + k;
                    if sj < N {
                        (sj, true)
                    } else {
                        (0, false)
                    }
                }
                _ => {
                    // SHR
                    if j >= k {
                        let src = j - k;
                        (src, true)
                    } else {
                        (0, false)
                    }
                }
            };

            // View masks: for each example e, check if input bit `src` = 1
            if valid {
                let mut m = 0u16;
                for e in 0..n_ex {
                    if inputs_bits[e][src] == 1 {
                        m |= 1 << e;
                    }
                }
                view_masks[ki][j] = m;
            }

            // Query row: the query bit for this view
            view_qrows[ki][j] = if valid { qin[src] } else { 0 };
        }
    }

    // Build var: 48 entries = 24 views + 24 NOT variants (view applied to full)
    // Stored as parallel arrays (masks, qrows) for cache-friendly iteration
    let mut var_masks = [[0u16; N]; N_VAR];
    let mut var_qrows = [[0u8; N]; N_VAR];

    for ki in 0..N_KEYS {
        let vi = ki * 2;
        // Original
        var_masks[vi] = view_masks[ki];
        var_qrows[vi] = view_qrows[ki];

        // NOT variant: invert mask & full, invert query bit
        for j in 0..N {
            var_masks[vi + 1][j] = (!view_masks[ki][j]) & full;
            var_qrows[vi + 1][j] = 1 - view_qrows[ki][j];
        }
    }

    let mut answer: Option<[u8; N]> = None;

    // 1-transform
    for vi in 0..N_VAR {
        if matches(&var_masks[vi], full, &out_targets) {
            if add_answer(&mut answer, &var_qrows[vi]) {
                return None;
            }
        }
    }

    // 2-transform — populate `two` list with ALL combos (not just matching)
    let cap = N_VAR * N_VAR * 3;
    let mut two_masks: Vec<[u16; N]> = Vec::with_capacity(cap);
    let mut two_qrows: Vec<[u8; N]> = Vec::with_capacity(cap);

    for ai in 0..N_VAR {
        let a_masks = &var_masks[ai];
        let a_qrows = &var_qrows[ai];
        for bi in 0..N_VAR {
            let b_masks = &var_masks[bi];
            let b_qrows = &var_qrows[bi];
            for op in 0..3u8 {
                let mut vec = [0u16; N];
                let mut qrow = [0u8; N];
                match op {
                    0 => {
                        for j in 0..N {
                            vec[j] = a_masks[j] & b_masks[j];
                            qrow[j] = a_qrows[j] & b_qrows[j];
                        }
                    }
                    1 => {
                        for j in 0..N {
                            vec[j] = a_masks[j] | b_masks[j];
                            qrow[j] = a_qrows[j] | b_qrows[j];
                        }
                    }
                    _ => {
                        for j in 0..N {
                            vec[j] = (a_masks[j] ^ b_masks[j]) & full;
                            qrow[j] = a_qrows[j] ^ b_qrows[j];
                        }
                    }
                }
                if matches(&vec, full, &out_targets) {
                    if add_answer(&mut answer, &qrow) {
                        return None;
                    }
                }
                two_masks.push(vec);
                two_qrows.push(qrow);
            }
        }
    }

    // 3-transform
    let n_two = two_masks.len();
    for ti in 0..n_two {
        let inner_masks = &two_masks[ti];
        let inner_qrows = &two_qrows[ti];
        for ci in 0..N_VAR {
            let c_masks = &var_masks[ci];
            let c_qrows = &var_qrows[ci];
            for op in 0..3u8 {
                let mut vec = [0u16; N];
                match op {
                    0 => {
                        for j in 0..N {
                            vec[j] = inner_masks[j] & c_masks[j];
                        }
                    }
                    1 => {
                        for j in 0..N {
                            vec[j] = inner_masks[j] | c_masks[j];
                        }
                    }
                    _ => {
                        for j in 0..N {
                            vec[j] = (inner_masks[j] ^ c_masks[j]) & full;
                        }
                    }
                }
                if matches(&vec, full, &out_targets) {
                    let mut qrow = [0u8; N];
                    match op {
                        0 => {
                            for j in 0..N {
                                qrow[j] = inner_qrows[j] & c_qrows[j];
                            }
                        }
                        1 => {
                            for j in 0..N {
                                qrow[j] = inner_qrows[j] | c_qrows[j];
                            }
                        }
                        _ => {
                            for j in 0..N {
                                qrow[j] = inner_qrows[j] ^ c_qrows[j];
                            }
                        }
                    }
                    if add_answer(&mut answer, &qrow) {
                        return None;
                    }
                }
            }
        }
    }

    answer.map(|a| bitstr(&a))
}

#[pyfunction]
fn per_bit_ops(
    examples: Vec<(String, String)>,
    question: String,
    answer: String,
) -> Option<Vec<(String, Option<u8>, Option<u8>)>> {
    let inputs: Vec<[u8; N]> = examples.iter().map(|(i, _)| bits(i)).collect();
    let outs: Vec<[u8; N]> = examples.iter().map(|(_, o)| bits(o)).collect();
    let q = bits(&question);
    let a = bits(&answer);

    let mut result = Vec::with_capacity(N);

    for j in 0..N {
        let col: Vec<u8> = outs.iter().map(|o| o[j]).collect();
        let aj = a[j];
        let mut pick: Option<(String, Option<u8>, Option<u8>)> = None;

        // Identity
        'ident: for p in 0..N {
            for (inp, &c) in inputs.iter().zip(col.iter()) {
                if inp[p] != c {
                    continue 'ident;
                }
            }
            if q[p] == aj {
                pick = Some(("I".to_string(), Some(p as u8), None));
                break;
            }
        }

        // NOT
        if pick.is_none() {
            'not: for p in 0..N {
                for (inp, &c) in inputs.iter().zip(col.iter()) {
                    if (1 - inp[p]) != c {
                        continue 'not;
                    }
                }
                if (1 - q[p]) == aj {
                    pick = Some(("NOT".to_string(), Some(p as u8), None));
                    break;
                }
            }
        }

        // Constant
        if pick.is_none() {
            if col.iter().all(|&c| c == 0) && aj == 0 {
                pick = Some(("0".to_string(), None, None));
            } else if col.iter().all(|&c| c == 1) && aj == 1 {
                pick = Some(("1".to_string(), None, None));
            }
        }

        // 2-input families
        if pick.is_none() {
            'fam: for &fam in &PAIR_ORDER {
                for p in 0..N {
                    for r in 0..N {
                        if p == r {
                            continue;
                        }
                        let mut ok = true;
                        for (inp, &c) in inputs.iter().zip(col.iter()) {
                            match eval_pair(fam, inp[p], inp[r]) {
                                Some(bit) if bit == c => {}
                                _ => {
                                    ok = false;
                                    break;
                                }
                            }
                        }
                        if ok {
                            if let Some(bit) = eval_pair(fam, q[p], q[r]) {
                                if bit == aj {
                                    pick = Some((fam.to_string(), Some(p as u8), Some(r as u8)));
                                    break 'fam;
                                }
                            }
                        }
                    }
                }
            }
        }

        if let Some(pick) = pick {
            result.push(pick);
        } else {
            return None;
        }
    }

    Some(result)
}

#[pymodule]
fn bitword_solver_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(solve, m)?)?;
    m.add_function(wrap_pyfunction!(per_bit_ops, m)?)?;
    Ok(())
}
