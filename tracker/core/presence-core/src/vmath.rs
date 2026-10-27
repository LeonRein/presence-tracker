//! Arithmetic that has to round as numpy does (MODEL.md 10 "Rechenzeit"): the filter is chaotic, a
//! sum in another order can decide a hypothesis differently, so sums of densities run in numpy's order.

/// numpy's sum of a contiguous array: its pairwise summation (numpy/_core/src/umath/loops_utils.h.src),
/// blocks of up to 128 with 8 accumulators, above that the halves (at multiples of 8) summed apart and
/// added. The same sum bit for bit as `a.sum()` (kernels._pairwise).
pub fn pairwise_sum(a: &[f64]) -> f64 {
    let n = a.len();
    if n < 8 {
        return a.iter().fold(0.0, |s, &x| s + x);
    }
    if n <= 128 {
        let mut r = [0.0; 8];
        r.copy_from_slice(&a[..8]);
        let whole = n - n % 8;
        for block in a[8..whole].as_chunks::<8>().0 {
            for (acc, &x) in r.iter_mut().zip(block) {
                *acc += x;
            }
        }
        let head = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        return a[whole..].iter().fold(head, |s, &x| s + x);
    }
    let mut half = n / 2;
    half -= half % 8;
    pairwise_sum(&a[..half]) + pairwise_sum(&a[half..])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn small_sums_run_in_a_row() {
        let a = [0.1, 0.2, 0.3];
        assert_eq!(pairwise_sum(&a), (0.0 + 0.1 + 0.2) + 0.3);
        assert_eq!(pairwise_sum(&[]), 0.0);
    }

    #[test]
    fn blocks_use_eight_accumulators() {
        // 1e16 + 1 + ... - 1e16: in a row the ones vanish, with 8 accumulators some survive
        let mut a = vec![1.0; 16];
        a[0] = 1e16;
        a[8] = -1e16;
        let r0 = 1e16 - 1e16;
        let expect = ((r0 + 2.0) + (2.0 + 2.0)) + ((2.0 + 2.0) + (2.0 + 2.0));
        assert_eq!(pairwise_sum(&a), expect);
    }

    #[test]
    fn long_sums_split_at_multiples_of_eight() {
        let a: Vec<f64> = (0..1000).map(|i| 1.0 / (i as f64 + 1.0)).collect();
        let half = 496; // 500 rounded down to a multiple of 8
        assert_eq!(pairwise_sum(&a), pairwise_sum(&a[..half]) + pairwise_sum(&a[half..]));
        let exact: f64 = a.iter().sum();
        assert!((pairwise_sum(&a) - exact).abs() < 1e-12);
    }
}
