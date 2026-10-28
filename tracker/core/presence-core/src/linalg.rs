//! Dense linear algebra of the build (tiling.expm, Tiling.walk_end) with faer on one thread
//! (docs/rust-core.md 3). Matrices are row-major `Vec<f64>`, N x N.

use faer::linalg::matmul::matmul;
use faer::linalg::solvers::Solve;
use faer::{Accum, Mat, MatMut, MatRef, Par};

/// a @ b of two N x N matrices.
fn product(a: &[f64], b: &[f64], n: usize) -> Vec<f64> {
    let mut out = vec![0.0; n * n];
    matmul(
        MatMut::from_row_major_slice_mut(&mut out, n, n),
        Accum::Replace,
        MatRef::from_row_major_slice(a, n, n),
        MatRef::from_row_major_slice(b, n, n),
        1.0,
        Par::Seq,
    );
    out
}

/// The matrix exponential by scaling and squaring of the Taylor series (tiling.expm): scaled to a
/// 1-norm of at most 0.25, 15 terms, squared back. For a rate matrix times a time; Err if its norm is
/// no finite number (scaling it would never end).
pub fn expm(a: &[f64], n: usize) -> Result<Vec<f64>, String> {
    let norm = (0..n)
        .map(|j| (0..n).fold(0.0, |s, i| s + a[i * n + j].abs()))
        .fold(0.0, f64::max);
    if !norm.is_finite() || a.iter().any(|x| x.is_nan()) {
        return Err(format!("the walking model's rates are no numbers (norm {norm})"));
    }
    let s = if norm > 0.25 {
        (norm / 0.25).log2().ceil() as i32
    } else {
        0
    };
    let scale = 2f64.powi(s);
    let b: Vec<f64> = a.iter().map(|x| x / scale).collect();
    let mut identity = vec![0.0; n * n];
    for i in 0..n {
        identity[i * n + i] = 1.0;
    }
    let (mut out, mut term) = (identity.clone(), identity); // the sum and its term, from 1
    for k in 1..16 {
        term = product(&term, &b, n).iter().map(|x| x / k as f64).collect();
        out.iter_mut().zip(&term).for_each(|(o, t)| *o += t);
    }
    for _ in 0..s {
        out = product(&out, &out, n);
    }
    Ok(out)
}

/// The solution X of a X = b (a N x N, b N x m, both row-major), by LU with partial pivoting
/// (np.linalg.solve).
pub fn solve(a: &[f64], b: &[f64], n: usize, m: usize) -> Vec<f64> {
    let lu = Mat::from_fn(n, n, |i, j| a[i * n + j]).partial_piv_lu();
    let x = lu.solve(Mat::from_fn(n, m, |i, j| b[i * m + j]));
    (0..n)
        .flat_map(|i| (0..m).map(move |j| (i, j)))
        .map(|(i, j)| x[(i, j)])
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_exponential_of_a_rate_matrix_is_the_series() {
        // two states, rates 3 and 1 per unit: exp(Q t) = stationary + e^(-4t) (I - stationary)
        let t = 0.7;
        let q = [-3.0 * t, t, 3.0 * t, -t];
        let e = expm(&q, 2).expect("finite");
        let decay = (-4.0 * t).exp();
        let want = [
            0.25 + 0.75 * decay,
            0.25 - 0.25 * decay,
            0.75 - 0.75 * decay,
            0.75 + 0.25 * decay,
        ];
        assert!(e.iter().zip(want).all(|(a, b)| (a - b).abs() < 1e-14), "{e:?}");
    }

    #[test]
    fn a_matrix_without_a_finite_norm_is_refused() {
        assert!(expm(&[f64::INFINITY, 0.0, 0.0, 1.0], 2).is_err());
        assert!(expm(&[f64::NAN, 0.0, 0.0, 1.0], 2).is_err());
    }

    #[test]
    fn a_solution_solves() {
        let a = [4.0, 1.0, 0.5, 2.0, 5.0, 1.0, 0.0, 1.0, 3.0];
        let b = [1.0, 0.0, 2.0, 1.0, 0.5, 3.0];
        let x = solve(&a, &b, 3, 2);
        for i in 0..3 {
            for j in 0..2 {
                let ax: f64 = (0..3).map(|k| a[i * 3 + k] * x[k * 2 + j]).sum();
                assert!((ax - b[i * 2 + j]).abs() < 1e-14);
            }
        }
    }
}
