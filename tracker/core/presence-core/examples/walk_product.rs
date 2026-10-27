//! Measures one tick of walking for m densities (docs/rust-core.md 3: faer against matrixmultiply
//! and a sparse matrix with the densities as the inner dimension).
//!
//! cargo run --release --example walk_product -- WALK_T.f64 N
//! WALK_T.f64: the matrix of walking [from][to] (tiling.T_walk_t) as raw little-endian f64.

use std::time::Instant;

use faer::linalg::matmul::matmul;
use faer::{Accum, MatMut, MatRef, Par};

/// The matrix by columns ("to"): per column its rows ("from") and values.
struct Sparse {
    ptr: Vec<usize>,
    from: Vec<usize>,
    val: Vec<f64>,
}

impl Sparse {
    fn new(t: &[f64], n: usize) -> Self {
        let mut s = Sparse {
            ptr: vec![0],
            from: Vec::new(),
            val: Vec::new(),
        };
        for to in 0..n {
            for from in 0..n {
                let v = t[from * n + to];
                if v != 0.0 {
                    s.from.push(from);
                    s.val.push(v);
                }
            }
            s.ptr.push(s.from.len());
        }
        s
    }

    /// out (n x m) = T^T applied to w (n x m): densities inner.
    fn apply<const M: usize>(&self, w: &[f64], out: &mut [f64]) {
        for (to, o) in out.as_chunks_mut::<M>().0.iter_mut().enumerate() {
            let mut acc = [0.0; M];
            for k in self.ptr[to]..self.ptr[to + 1] {
                let (v, src) = (self.val[k], &w[self.from[k] * M..][..M]);
                for (a, x) in acc.iter_mut().zip(src) {
                    *a += v * x;
                }
            }
            o.copy_from_slice(&acc);
        }
    }

    fn apply_dyn(&self, w: &[f64], out: &mut [f64], m: usize) {
        for (to, o) in out.chunks_exact_mut(m).enumerate() {
            o.fill(0.0);
            for k in self.ptr[to]..self.ptr[to + 1] {
                let (v, src) = (self.val[k], &w[self.from[k] * m..][..m]);
                for (a, x) in o.iter_mut().zip(src) {
                    *a += v * x;
                }
            }
        }
    }
}

fn time(label: &str, m: usize, reps: usize, mut f: impl FnMut()) {
    f();
    let t = Instant::now();
    for _ in 0..reps {
        f();
    }
    println!(
        "{label:>16} m={m:>2}: {:8.1} us",
        t.elapsed().as_secs_f64() * 1e6 / reps as f64
    );
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let bytes = std::fs::read(&args[1]).expect("the matrix");
    let n: usize = args[2].parse().expect("n");
    let t: Vec<f64> = bytes
        .as_chunks::<8>()
        .0
        .iter()
        .map(|b| f64::from_le_bytes(*b))
        .collect();
    assert_eq!(t.len(), n * n);
    let sparse = Sparse::new(&t, n);
    println!(
        "n {n}, filled {:.1} %",
        100.0 * sparse.val.len() as f64 / (n * n) as f64
    );
    let reps = 2000;
    for m in [1, 4, 8, 12, 16] {
        let w: Vec<f64> = (0..m * n).map(|i| ((i * 7919) % 1000) as f64 / 1000.0).collect();
        let mut out = vec![0.0; m * n];
        time("faer", m, reps, || {
            matmul(
                MatMut::from_row_major_slice_mut(&mut out, m, n),
                Accum::Replace,
                MatRef::from_row_major_slice(&w, m, n),
                MatRef::from_row_major_slice(&t, n, n),
                1.0,
                Par::Seq,
            )
        });
        time("matrixmultiply", m, reps, || unsafe {
            matrixmultiply::dgemm(
                m,
                n,
                n,
                1.0,
                w.as_ptr(),
                n as isize,
                1,
                t.as_ptr(),
                n as isize,
                1,
                0.0,
                out.as_mut_ptr(),
                n as isize,
                1,
            )
        });
        // the densities inner: w as n x m
        let wt: Vec<f64> = (0..n * m).map(|i| w[(i % m) * n + i / m]).collect();
        time("sparse inner", m, reps, || sparse.apply_dyn(&wt, &mut out, m));
        if m == 8 {
            time("sparse inner M=8", m, reps, || sparse.apply::<8>(&wt, &mut out));
        }
    }
}
