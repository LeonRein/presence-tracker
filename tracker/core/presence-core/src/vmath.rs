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

/// numpy's max of an array: NaN if any is NaN; -inf if empty.
pub fn nan_max(a: &[f64]) -> f64 {
    let mut top = f64::NEG_INFINITY;
    for &x in a {
        if x.is_nan() {
            return x;
        }
        if x > top {
            top = x;
        }
    }
    top
}

/// log(sum(exp(a))) as filter._logsumexp: by the largest (numpy's max, NaN wins), the exponentials
/// summed in numpy's order; -inf if empty or all -inf.
pub fn logsumexp(a: &[f64]) -> f64 {
    let top = nan_max(a);
    if top == f64::NEG_INFINITY {
        return top;
    }
    let e: Vec<f64> = a.iter().map(|&x| (x - top).exp()).collect();
    top + pairwise_sum(&e).ln()
}

/// numpy's logaddexp of two (npy_logaddexp).
pub fn logaddexp(x: f64, y: f64) -> f64 {
    if x == y {
        return x + std::f64::consts::LN_2;
    }
    let d = x - y;
    if d > 0.0 {
        x + (-d).exp().ln_1p()
    } else if d <= 0.0 {
        y + d.exp().ln_1p()
    } else {
        d // NaN
    }
}

// The logarithm and the exponential of kernels.vlog and vexp: fdlibm's polynomials (e_log.c and
// e_exp.c, Sun Microsystems 1993) with a simpler reduction - the mantissa into [sqrt(2)/2, sqrt(2)),
// none of fdlibm's special cases near 1 - below 1 ulp (tested). Over a slice in two passes as
// kernels.py: first every number without a branch (the loop vectorizes), then the few that are no
// positive normal number (log) or beyond -708..709 (exp) by libm.
/// The constants as fdlibm publishes them (more digits than a double holds: quoted, not rounded).
#[allow(clippy::excessive_precision)]
mod fdlibm {
    pub const LG1: f64 = 6.666666666666735130e-01;
    pub const LG2: f64 = 3.999999999940941908e-01;
    pub const LG3: f64 = 2.857142874366239149e-01;
    pub const LG4: f64 = 2.222219843214978396e-01;
    pub const LG5: f64 = 1.818357216161805012e-01;
    pub const LG6: f64 = 1.531383769920937332e-01;
    pub const LG7: f64 = 1.479819860511658591e-01;
    pub const LN2_HI: f64 = 6.93147180369123816490e-01;
    pub const LN2_LO: f64 = 1.90821492927058770002e-10;
    /// 1 / ln 2 (fdlibm's 1.44269504088896338700e+00 is this double)
    pub const INV_LN2: f64 = std::f64::consts::LOG2_E;
    pub const P1: f64 = 1.66666666666666019037e-01;
    pub const P2: f64 = -2.77777777770155933842e-03;
    pub const P3: f64 = 6.61375632143793436117e-05;
    pub const P4: f64 = -1.65339022054652515390e-06;
    pub const P5: f64 = 4.13813679705723846039e-08;
    pub const TINY: f64 = 2.2250738585072014e-308;
}
use fdlibm::*;

/// 2^52: a double below it plus this holds the integer in its mantissa (exact both ways, and
/// without AVX-512's conversions between packed integers and doubles).
const TWO52: f64 = 4_503_599_627_370_496.0;

fn log_normal(x: f64) -> bool {
    (TINY..f64::INFINITY).contains(&x)
}

fn exp_normal(x: f64) -> bool {
    x > -708.0 && x < 709.0
}

/// log x for a positive normal number, without a branch (anything else gives a wrong number).
#[inline(always)]
fn log_poly(x: f64) -> f64 {
    let b = x.to_bits();
    let frac = b & 0x000f_ffff_ffff_ffff;
    let big = u64::from(frac > 0x6_a09e_667f_3bcd); // the mantissa above sqrt(2): halved, the exponent + 1
    let m = f64::from_bits((frac | 0x3ff0_0000_0000_0000) - (big << 52));
    // the exponent ((b >> 52) & 0x7ff) - 1023 + big, as a double
    let e = f64::from_bits(TWO52.to_bits() | (((b >> 52) & 0x7ff) + big)) - (TWO52 + 1023.0);
    let f = m - 1.0;
    let s = f / (2.0 + f);
    let z = s * s;
    let w = z * z;
    let t1 = w * (LG2 + w * (LG4 + w * LG6));
    let t2 = z * (LG1 + w * (LG3 + w * (LG5 + w * LG7)));
    let hfsq = 0.5 * f * f;
    e * LN2_HI - ((hfsq - (s * (hfsq + (t2 + t1)) + e * LN2_LO)) - f)
}

/// exp x within -708..709, without a branch (beyond it a wrong number).
#[inline(always)]
fn exp_poly(x: f64) -> f64 {
    let k = (x * INV_LN2 + 0.5).floor();
    let hi = x - k * LN2_HI;
    let lo = k * LN2_LO;
    let r = hi - lo;
    let t = r * r;
    let c = r - t * (P1 + t * (P2 + t * (P3 + t * (P4 + t * P5))));
    let y = 1.0 - ((lo - (r * c) / (2.0 - c)) - hi);
    // k as an integer (|k| < 1100) shifted into the exponent
    let kb = (k + 1.5 * TWO52).to_bits().wrapping_sub((1.5 * TWO52).to_bits());
    f64::from_bits(y.to_bits().wrapping_add(kb << 52))
}

/// log x (kernels.vlog): what is no positive normal number (0, subnormal, negative, inf, NaN) by libm.
pub fn vlog1(x: f64) -> f64 {
    if log_normal(x) {
        log_poly(x)
    } else if x > 0.0 {
        x.ln()
    } else if x == 0.0 {
        f64::NEG_INFINITY
    } else {
        f64::NAN
    }
}

/// exp x (kernels.vexp): beyond -708..709 (underflow, overflow, inf, NaN) by libm.
pub fn vexp1(x: f64) -> f64 {
    if exp_normal(x) { exp_poly(x) } else { x.exp() }
}

/// log of each, in place (vlog1).
#[inline(never)]
pub fn vlog(x: &mut [f64]) {
    let odd = x.iter().filter(|&&v| !log_normal(v)).count(); // usually none
    let odd: Vec<(usize, f64)> = if odd == 0 {
        Vec::new()
    } else {
        x.iter().copied().enumerate().filter(|&(_, v)| !log_normal(v)).collect()
    };
    for v in x.iter_mut() {
        *v = log_poly(*v);
    }
    for (i, v) in odd {
        x[i] = vlog1(v);
    }
}

/// exp of each, in place (vexp1).
#[inline(never)]
pub fn vexp(x: &mut [f64]) {
    let odd = x.iter().filter(|&&v| !exp_normal(v)).count(); // usually none
    let odd: Vec<(usize, f64)> = if odd == 0 {
        Vec::new()
    } else {
        x.iter().copied().enumerate().filter(|&(_, v)| !exp_normal(v)).collect()
    };
    for v in x.iter_mut() {
        *v = exp_poly(*v);
    }
    for (i, v) in odd {
        x[i] = vexp1(v);
    }
}

/// The larger of x and lo, NaN staying NaN (numpy's maximum; f64::max would give lo).
pub fn at_least(x: f64, lo: f64) -> f64 {
    if x.is_nan() { x } else { x.max(lo) }
}

/// log Gamma(x) (math.lgamma; libm's: CPython's own may differ in the last bit).
pub fn lgamma(x: f64) -> f64 {
    libm::lgamma(x)
}

/// log k! for k < n as the outputs take them (filter._lgamma_table: CPython's math.lgamma(k + 1),
/// its own Lanczos sum, which differs from libm's lgamma in the last bit for 690 of the first 2000).
pub fn log_factorials(n: usize) -> Vec<f64> {
    (0..n).map(|k| lanczos_lgamma(k as f64 + 1.0)).collect()
}

/// CPython's m_lgamma (Modules/mathmodule.c) for x > 0: Lanczos' approximation with g = 6.0247 and 13
/// terms (Boost's; Godfrey's coefficients), the sum as a rational function in x (in 1/x from 5 on).
/// The constants as CPython quotes them.
#[allow(clippy::excessive_precision)]
fn lanczos_lgamma(x: f64) -> f64 {
    const G: f64 = 6.024680040776729583740234375;
    const NUM: [f64; 13] = [
        23531376880.410759688572007674451636754734846804940,
        42919803642.649098768957899047001988850926355848959,
        35711959237.355668049440185451547166705960488635843,
        17921034426.037209699919755754458931112671403265390,
        6039542586.3520280050642916443072979210699388420708,
        1439720407.3117216736632230727949123939715485786772,
        248874557.86205415651146038641322942321632125127801,
        31426415.585400194380614231628318205362874684987640,
        2876370.6289353724412254090516208496135991145378768,
        186056.26539522349504029498971604569928220784236328,
        8071.6720023658162106380029022722506138218516325024,
        210.82427775157934587250973392071336271166969580291,
        2.5066282746310002701649081771338373386264310793408,
    ];
    // x (x + 1) ... (x + 11)
    const DEN: [f64; 13] = [
        0.0,
        39916800.0,
        120543840.0,
        150917976.0,
        105258076.0,
        45995730.0,
        13339535.0,
        2637558.0,
        357423.0,
        32670.0,
        1925.0,
        66.0,
        1.0,
    ];
    if x.is_nan() {
        return x;
    }
    if x.is_infinite() {
        return f64::INFINITY;
    }
    if x == x.floor() && x <= 2.0 {
        return if x <= 0.0 { f64::INFINITY } else { 0.0 };
    }
    if x < 1e-20 {
        return -x.ln();
    }
    let (mut num, mut den) = (0.0, 0.0);
    if x < 5.0 {
        for (n, d) in NUM.iter().zip(&DEN).rev() {
            num = num * x + n;
            den = den * x + d;
        }
    } else {
        for (n, d) in NUM.iter().zip(&DEN) {
            num = num / x + n;
            den = den / x + d;
        }
    }
    let r = (num / den).ln() - G;
    r + (x - 0.5) * ((x + G - 0.5).ln() - 1.0)
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

    #[test]
    fn logsumexp_is_by_the_largest() {
        assert_eq!(logsumexp(&[]), f64::NEG_INFINITY);
        assert_eq!(logsumexp(&[f64::NEG_INFINITY, f64::NEG_INFINITY]), f64::NEG_INFINITY);
        assert!(logsumexp(&[1.0, f64::NAN]).is_nan());
        assert!((logsumexp(&[0.0, 0.0]) - 2f64.ln()).abs() < 1e-15);
        assert_eq!(logaddexp(f64::NEG_INFINITY, f64::NEG_INFINITY), f64::NEG_INFINITY);
        assert!((logaddexp(1.0, 0.0) - (1.0 + (-1f64).exp().ln_1p())).abs() == 0.0);
    }

    #[test]
    fn the_vectorized_log_and_exp_are_within_one_ulp() {
        // test_kernels.test_the_vectorized_log_and_exp_are_numpys_within_one_ulp, against libm
        let mut rng = crate::testing::Rng::new(15);
        let ulp = |v: f64| (f64::from_bits(v.abs().to_bits() + 1) - v.abs()).abs();
        for _ in 0..20000 {
            let x = rng.range(-700.0, 700.0).exp();
            assert!((vlog1(x) - x.ln()).abs() <= ulp(x.ln()), "{x}");
            let y = rng.range(-745.0, 709.8);
            assert!((vexp1(y) - y.exp()).abs() <= ulp(y.exp()), "{y}");
        }
        assert_eq!(vlog1(0.0), f64::NEG_INFINITY);
        assert!(vlog1(-1.0).is_nan() && vlog1(f64::NAN).is_nan());
        assert_eq!(vlog1(f64::INFINITY), f64::INFINITY);
        assert_eq!(vlog1(1.0), 0.0);
        assert_eq!(vlog1(5e-324), 5e-324f64.ln());
        assert_eq!(vexp1(0.0), 1.0);
        assert_eq!(vexp1(f64::NEG_INFINITY), 0.0);
        assert_eq!(vexp1(f64::INFINITY), f64::INFINITY);
        assert!(vexp1(f64::NAN).is_nan());
        assert_eq!(vexp1(710.0), f64::INFINITY);
        assert_eq!(vexp1(-746.0), 0.0);
        // near 1, and on both sides of the mantissa's threshold sqrt(2)
        let at = |bits: u64| f64::from_bits(bits);
        let sqrt2 = 0x3ff6_a09e_667f_3bcd_u64;
        let near: Vec<f64> = (0..64u64)
            .flat_map(|k| {
                [
                    at(1f64.to_bits() + k),
                    at(1f64.to_bits() - k),
                    at(sqrt2 + k),
                    at(sqrt2 - k),
                ]
            })
            .chain((0..64).map(|k| at(sqrt2 + (k << 40))))
            .collect();
        for &x in &near {
            assert!((vlog1(x) - x.ln()).abs() <= ulp(x.ln()).max(f64::MIN_POSITIVE), "{x}");
        }
    }

    #[test]
    fn over_a_slice_as_one_by_one() {
        let mut rng = crate::testing::Rng::new(16);
        let mut x: Vec<f64> = (0..999).map(|_| rng.range(-800.0, 800.0)).collect();
        x.extend([
            0.0,
            -0.0,
            -1.0,
            5e-324,
            f64::INFINITY,
            f64::NEG_INFINITY,
            f64::NAN,
            709.5,
            -708.5,
            1.0,
        ]);
        let mut e = x.clone();
        vexp(&mut e);
        let mut l = x.clone();
        vlog(&mut l);
        for (i, &v) in x.iter().enumerate() {
            assert_eq!(e[i].to_bits(), vexp1(v).to_bits(), "exp {v}");
            assert_eq!(l[i].to_bits(), vlog1(v).to_bits(), "log {v}");
        }
    }

    #[test]
    fn log_factorials_are_cpythons_lgamma_bit_for_bit() {
        // math.lgamma(k + 1).hex() of CPython 3.13: its own Lanczos sum, not libm's
        let want = [
            (0, "0x0.0p+0"),
            (2, "0x1.62e42fefa39ecp-1"),
            (3, "0x1.cab0bfa2a2004p+0"),
            (4, "0x1.96ca77c922cf7p+1"),
            (5, "0x1.326643c4479cap+2"),
            (9, "0x1.99a8921a7f7cep+3"),
            (13, "0x1.68d5a9c3b32cdp+4"),
            (22, "0x1.83c4faba84f05p+5"),
            (40, "0x1.b94855c702ba1p+6"),
            (63, "0x1.9204c51e7c761p+7"),
            (64, "0x1.9a561e3e1a4bep+7"),
            (99, "0x1.67225b4879462p+8"),
            (170, "0x1.61495a1a8a1d5p+9"),
            (500, "0x1.466a931d9ee1dp+11"),
            (1999, "0x1.9c776338bbc50p+13"),
        ];
        let table = log_factorials(2000);
        for (k, hex) in want {
            assert_eq!(table[k], hexf(hex), "log {k}!");
        }
        assert_eq!(table[1], 0.0);
        // not integers (math.lgamma(0.5), (4.75), (1e-21), (7.25))
        for (x, hex) in [
            (0.5, "0x1.250d048e7a1c0p-1"),
            (4.75, "0x1.677f44aba1adep+1"),
            (1e-21, "0x1.82d59465dffacp+5"),
            (7.25, "0x1.c35701a50ff06p+2"),
        ] {
            assert_eq!(lanczos_lgamma(x), hexf(hex), "lgamma {x}");
        }
    }

    /// A number written as Python's float.hex().
    fn hexf(s: &str) -> f64 {
        let (mant, exp) = s.trim_start_matches("0x").split_once('p').expect("p");
        let (int, frac) = mant.split_once('.').expect(".");
        let digits = u64::from_str_radix(&format!("{int}{frac}"), 16).expect("hex");
        let e: i32 = exp.parse().expect("exponent");
        digits as f64 * 2f64.powi(e - 4 * frac.len() as i32)
    }
}
