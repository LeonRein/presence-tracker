//! The energies' likelihood (MODEL.md 4.3, Likelihood, ld_ratio):
//! per cell Gamma(alpha, mean mu), an energy at 100 censored; the frames are correlated, so their
//! log-likelihoods count with dt / tau (composite likelihood, Varin, Reid & Firth 2011); per kind of
//! cell (moving, still) the block's common gain u is integrated out: 1/u ~ Gamma(k, rate k) mixed with
//! bursts (conjugate). The form is that of track-before-detect (Salmond & Birch 2001).
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use super::{CAP, CELLS, MOVING, cell_values};
use crate::params::Params;
use crate::vmath::{at_least, lgamma, pairwise_sum, vexp, vexp1, vlog, vlog1};

/// The number of points of the grid of log mean energies for the censored cells.
const GRID_POINTS: usize = 4001;

/// log of the regularized upper incomplete gamma function Q(a, x), x >= 0 (ld2410.log_gammaq;
/// Numerical Recipes 6.2: the series for x < a + 1, the continued fraction (Lentz) above).
pub fn log_gammaq(a: f64, x: f64) -> f64 {
    let lead = -x + a * x.max(1e-300).ln() - lgamma(a);
    if x < a + 1.0 {
        let mut term = 1.0 / a;
        let mut total = term;
        for k in 1..40 {
            term = term * x / (a + k as f64);
            total += term;
        }
        return (1.0 - lead.exp() * total).max(1e-300).ln();
    }
    let mut b = x + 1.0 - a;
    let mut c = 1e300;
    let mut d = 1.0 / b;
    let mut h = d;
    for i in 1..30 {
        let an = -(i as f64) * (i as f64 - a);
        b += 2.0;
        d = an * d + b;
        if d.abs() < 1e-300 {
            d = 1e-300;
        }
        c = b + an / c;
        if c.abs() < 1e-300 {
            c = 1e-300;
        }
        d = 1.0 / d;
        h = h * d * c;
    }
    lead + h.max(1e-300).ln()
}

/// log P(energy >= CAP) for Gamma(alpha, mean mu) on a uniform grid of log mu, interpolated as
/// numpy's interp.
#[derive(Debug)]
struct Censored {
    grid: Vec<f64>,
    /// per value of alpha its table on the grid
    tables: Vec<(f64, Vec<f64>)>,
}

impl Censored {
    fn new(alphas: &[f64]) -> Self {
        // numpy's linspace(log 0.05, log 1e5, 4001): start + i x step, the end exact
        let (lo, hi) = (0.05f64.ln(), 1e5f64.ln());
        let step = (hi - lo) / (GRID_POINTS - 1) as f64;
        let mut grid: Vec<f64> = (0..GRID_POINTS).map(|i| i as f64 * step + lo).collect();
        grid[GRID_POINTS - 1] = hi;
        let mut tables: Vec<(f64, Vec<f64>)> = Vec::new();
        for &a in alphas {
            if !tables.iter().any(|(x, _)| *x == a) {
                let table = grid.iter().map(|&g| log_gammaq(a, CAP * a / g.exp())).collect();
                tables.push((a, table));
            }
        }
        Self { grid, tables }
    }

    fn table(&self, alpha: f64) -> &[f64] {
        &self
            .tables
            .iter()
            .find(|(a, _)| *a == alpha)
            .expect("a table per alpha")
            .1
    }

    /// The table's value at the mean m (x = log m): a mean <= 0 as the grid's lowest, NaN stays NaN.
    fn at(&self, fp: &[f64], m: f64, x: f64) -> f64 {
        let grid = &self.grid;
        let n = grid.len();
        let (g0, g1) = (grid[0], grid[n - 1]);
        if m.is_nan() {
            return m;
        }
        if m <= 0.0 || x <= g0 {
            return fp[0];
        }
        if x >= g1 {
            return fp[n - 1];
        }
        let h = (g1 - g0) / (n - 1) as f64;
        let mut j = (((x - g0) / h) as usize).min(n - 2);
        while j > 0 && x < grid[j] {
            j -= 1;
        }
        while j < n - 2 && x >= grid[j + 1] {
            j += 1;
        }
        if x == grid[j] {
            return fp[j];
        }
        (fp[j + 1] - fp[j]) / (grid[j + 1] - grid[j]) * (x - grid[j]) + fp[j]
    }
}

/// A component of the prior of a block's common gain: 1/u ~ Gamma(shape, rate), with
/// c = log weight + shape log rate - lgamma shape.
#[derive(Debug, Clone, Copy)]
struct Gain {
    c: f64,
    shape: f64,
    rate: f64,
}

/// A kind of cell (moving, still) and the prior of its block's common gain: usual and bursts.
#[derive(Debug)]
struct CellKind {
    cells: std::ops::Range<usize>,
    gain: [Gain; 2],
}

/// The numbers of the energies' likelihood.
#[derive(Debug)]
pub struct Likelihood {
    pub alpha: [f64; CELLS],
    pub tau: [f64; CELLS],
    kinds: [CellKind; 2],
    censored: Censored,
}

impl Likelihood {
    pub fn new(p: &Params) -> Self {
        let alpha = cell_values(p.ld_shape[0], p.ld_shape[1]);
        let burst = p.ld_burst;
        let gain = |w: f64, shape: f64, rate: f64| Gain {
            c: w.ln() + shape * rate.ln() - lgamma(shape),
            shape,
            rate,
        };
        let kind = |cells, k: f64| CellKind {
            cells,
            gain: [
                gain(1.0 - burst.weight, k, k),
                gain(burst.weight, burst.shape, burst.shape * burst.gain),
            ],
        };
        Self {
            alpha,
            tau: cell_values(p.ld_tau[0], p.ld_tau[1]),
            kinds: [kind(0..MOVING, p.ld_gain[0]), kind(MOVING..CELLS, p.ld_gain[1])],
            censored: Censored::new(&alpha),
        }
    }
}

/// The frames of one sensor since the last weighing, as the sufficient statistics of the Gamma
/// likelihood per cell: time (s) of uncensored frames, time x energy, time x log
/// energy, time of censored ones (each frame for the time it stands for); and the time in which the
/// LD2450 in the same housing had a target in sight.
#[derive(Debug, Clone, Default)]
pub struct Stats {
    pub time: f64,
    pub t_target: f64,
    pub t_unc: [f64; CELLS],
    pub t_e: [f64; CELLS],
    pub t_le: [f64; CELLS],
    pub t_cens: [f64; CELLS],
}

/// A component of a gain's prior given N looks: the rate r, c + lgamma(N + k), N + k.
#[derive(Debug, Clone, Copy)]
struct Posterior {
    rate: f64,
    c: f64,
    n: f64,
}

/// A kind of cell the frames looked at: its cells, and its gain's prior given the looks.
#[derive(Debug)]
struct Looked {
    cells: std::ops::Range<usize>,
    gain: [Posterior; 2],
}

/// What the likelihood takes from the frames, once for all the means asked about.
#[derive(Debug)]
pub struct Prepared<'a> {
    lik: &'a Likelihood,
    /// alpha x looks per cell
    look: [f64; CELLS],
    /// alpha x energy per cell
    ae: [f64; CELLS],
    /// per kind of cell with looks: its cells, and per component its rate, c + lgamma(N + k), N + k
    kinds: Vec<Looked>,
    /// the censored cells and their weights
    censored: Vec<(usize, f64)>,
}

impl Stats {
    /// A frame's energies (16) standing for dt s; target: the housing's LD2450 had one in sight.
    pub fn add(&mut self, e: &[f64; CELLS], dt: f64, target: bool) {
        self.time += dt;
        self.t_target += dt * if target { 1.0 } else { 0.0 };
        for (c, &x) in e.iter().enumerate() {
            let cens = x >= CAP;
            let ee = at_least(x, 0.5); // integer energies: 0 as half a unit
            self.t_unc[c] += dt * if cens { 0.0 } else { 1.0 };
            self.t_e[c] += dt * if cens { 0.0 } else { ee };
            self.t_le[c] += dt * if cens { 0.0 } else { ee.ln() };
            self.t_cens[c] += dt * if cens { 1.0 } else { 0.0 };
        }
    }

    pub fn prepare<'a>(&self, lik: &'a Likelihood) -> Prepared<'a> {
        let look: [f64; CELLS] = std::array::from_fn(|c| lik.alpha[c] * self.t_unc[c] / lik.tau[c]);
        let ae = std::array::from_fn(|c| lik.alpha[c] * self.t_e[c] / lik.tau[c]);
        let kinds = lik
            .kinds
            .iter()
            .filter_map(|kind| {
                let n = pairwise_sum(&look[kind.cells.clone()]);
                (n > 0.0).then(|| Looked {
                    cells: kind.cells.clone(),
                    gain: kind.gain.map(|g| Posterior {
                        rate: g.rate,
                        c: g.c + lgamma(n + g.shape),
                        n: n + g.shape,
                    }),
                })
            })
            .collect();
        let censored = (0..CELLS)
            .filter(|&c| self.t_cens[c] > 0.0)
            .map(|c| (c, self.t_cens[c] / lik.tau[c]))
            .collect();
        Prepared {
            lik,
            look,
            ae,
            kinds,
            censored,
        }
    }
}

/// The buffers of the likelihood's rows, kept from call to call (one per weighing).
#[derive(Debug, Default)]
pub struct Scratch {
    mu: Vec<f64>,
    lm: Vec<f64>,
    out: Vec<f64>,
    a: Vec<f64>,
    t0: Vec<f64>,
    t1: Vec<f64>,
    top: Vec<f64>,
    e: Vec<f64>,
}

impl Prepared<'_> {
    /// The part of the tempered log-likelihood that depends on the means (Stats._mu_part,
    /// kernels._ld_cols), per row into `scratch.out`: mu by cell (`scratch.mu`, `mu[c * k + i]` the
    /// mean of cell c in row i); per row -sum log(mu) x looks, per kind of cell log sum_c exp(c +
    /// lgamma(N + k) - (N + k) log(r + A)), A the sum of alpha x energy / mean over its cells, and
    /// the censored cells. Cell by cell over all rows: the loops over the rows vectorize.
    fn mu_part(&self, scratch: &mut Scratch, k: usize) {
        let Scratch {
            mu,
            lm,
            out,
            a,
            t0,
            t1,
            top,
            ..
        } = scratch;
        lm.clone_from(mu);
        vlog(lm);
        out.clear();
        out.resize(k, 0.0);
        for c in 0..CELLS {
            for (o, l) in out.iter_mut().zip(&lm[c * k..][..k]) {
                *o -= l * self.look[c];
            }
        }
        for buf in [&mut *a, &mut *t0, &mut *t1, &mut *top] {
            buf.resize(k, 0.0);
        }
        for Looked { cells, gain } in &self.kinds {
            a.fill(0.0);
            for c in cells.clone() {
                for (x, m) in a.iter_mut().zip(&mu[c * k..][..k]) {
                    *x += self.ae[c] / m;
                }
            }
            let [g0, g1] = *gain;
            for i in 0..k {
                let (x, y) = (g0.c - g0.n * vlog1(g0.rate + a[i]), g1.c - g1.n * vlog1(g1.rate + a[i]));
                let m = if x.is_nan() || (!y.is_nan() && x >= y) { x } else { y }; // NaN wins
                (top[i], t0[i], t1[i]) = (m, x - m, y - m);
            }
            vexp(&mut t0[..k]);
            vexp(&mut t1[..k]);
            for i in 0..k {
                out[i] += top[i] + vlog1(t0[i] + t1[i]);
            }
        }
        let censored = &self.lik.censored;
        for &(c, w) in &self.censored {
            let fp = censored.table(self.lik.alpha[c]);
            for i in 0..k {
                out[i] += censored.at(fp, mu[c * k + i], lm[c * k + i]) * w;
            }
        }
    }

    /// The tempered log-likelihood of the frames given the means mu (16).
    pub fn loglik(&self, stats: &Stats, mu: &[f64; CELLS]) -> f64 {
        let lik = self.lik;
        let terms: Vec<f64> = (0..CELLS)
            .map(|c| {
                let a = lik.alpha[c];
                (stats.t_unc[c] * (a * a.ln() - lgamma(a)) + (a - 1.0) * stats.t_le[c]) / lik.tau[c]
            })
            .collect();
        let mut scratch = Scratch {
            mu: mu.to_vec(),
            ..Scratch::default()
        };
        self.mu_part(&mut scratch, 1);
        pairwise_sum(&terms) + scratch.out[0]
    }

    /// Masses m (k) putting S (k x 16) on top of the means mu0: the log
    /// likelihood ratio per point against mu0 (capped; a point without mass gets -cap, not computed),
    /// the log of the normalizer - the factor of the whole person, or for the unknown ones
    /// (intensity) log((1 + int lambda L) / (1 + Lambda)): at most one of them in view - and what
    /// they put in afterwards (16).
    pub fn ratio(
        &self,
        scratch: &mut Scratch,
        m: &[f64],
        s: &[[f64; CELLS]],
        mu0: &[f64; CELLS],
        intensity: bool,
    ) -> Ratio {
        let n = m.iter().filter(|&&x| x > 0.0).count();
        scratch.mu.clear();
        scratch.mu.resize(CELLS * (n + 1), 0.0);
        for c in 0..CELLS {
            let row = &mut scratch.mu[c * (n + 1)..][..n + 1];
            for (x, (si, _)) in row.iter_mut().zip(s.iter().zip(m).filter(|(_, mi)| **mi > 0.0)) {
                *x = mu0[c] + si[c];
            }
            row[n] = mu0[c];
        }
        self.mu_part(scratch, n + 1);
        let both = &scratch.out;
        let mut ratios = vec![-super::LOG_CAP; m.len()];
        for (x, j) in ratios
            .iter_mut()
            .zip(m)
            .filter(|(_, mi)| **mi > 0.0)
            .map(|(x, _)| x)
            .zip(0..)
        {
            *x = both[j] - both[n];
        }
        ratio_tail(ratios, m, s, intensity, &mut scratch.e)
    }
}

/// A person's (or the unknown ones') log likelihood ratio per point, the log of its normalizer, and
/// what they put in afterwards.
#[derive(Debug, Clone)]
pub struct Ratio {
    pub lr: Vec<f64>,
    pub norm: f64,
    pub post: [f64; CELLS],
}

/// kernels.ld_ratio_tail: the ratios capped at LOG_CAP (NaN wins), the normalizer, and sum_i m_i
/// exp(lr_i - norm) S_i.
fn ratio_tail(mut lr: Vec<f64>, m: &[f64], s: &[[f64; CELLS]], intensity: bool, e: &mut Vec<f64>) -> Ratio {
    let cap = super::LOG_CAP;
    let mut top = f64::NEG_INFINITY;
    let mut nan = false;
    let mut msum = 0.0;
    for (x, &mi) in lr.iter_mut().zip(m) {
        if !(*x <= cap) && !x.is_nan() {
            *x = cap;
        }
        if x.is_nan() {
            nan = true;
        } else if *x > top {
            top = *x;
        }
        msum += mi;
    }
    if nan {
        top = f64::NAN;
    } else if top < 0.0 {
        top = 0.0;
    }
    e.clear();
    e.extend(lr.iter().map(|x| x - top));
    vexp(e);
    let inside = m.iter().zip(e.iter()).fold(0.0, |acc, (mi, ei)| acc + mi * ei);
    let norm = if intensity {
        top + ((-top).exp() + inside).ln() - msum.ln_1p()
    } else {
        let rest = (1.0 - msum).max(0.0);
        let total = rest * (-top).exp() + inside;
        if total > 0.0 {
            top + total.ln()
        } else {
            // every ratio below e^-745 and all of the person in view: the same in the log space
            let mut hi = if rest > 0.0 { rest.ln() } else { f64::NEG_INFINITY };
            for (x, &mi) in lr.iter().zip(m) {
                if mi > 0.0 && x + mi.ln() > hi {
                    hi = x + mi.ln();
                }
            }
            if hi > f64::NEG_INFINITY {
                let mut sum = if rest > 0.0 { (rest.ln() - hi).exp() } else { 0.0 };
                for (x, &mi) in lr.iter().zip(m) {
                    if mi > 0.0 {
                        sum += (x + mi.ln() - hi).exp();
                    }
                }
                hi + sum.ln()
            } else {
                hi
            }
        }
    };
    for (ei, x) in e.iter_mut().zip(&lr) {
        *ei = vexp1(x - norm);
    }
    let mut post = [0.0; CELLS];
    for ((mi, ei), si) in m.iter().zip(e.iter()).zip(s) {
        let wi = mi * ei;
        for (p, x) in post.iter_mut().zip(si) {
            *p += wi * x;
        }
    }
    Ratio { lr, norm, post }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing::Rng;

    fn stats(rng: &mut Rng, frames: usize) -> Stats {
        let mut st = Stats::default();
        for _ in 0..frames {
            let mut e = [0.0; CELLS];
            for x in &mut e {
                *x = if rng.uniform() < 0.1 {
                    100.0
                } else {
                    rng.below(101) as f64
                };
            }
            st.add(&e, rng.range(0.05, 1.0), false);
        }
        st
    }

    #[test]
    fn the_upper_incomplete_gamma() {
        // test_ld2410.test_upper_incomplete_gamma: Q(1, x) = exp(-x), Q(a, 0) = 1
        for x in [0.0, 0.3, 1.0, 2.5, 7.0, 30.0] {
            assert!((log_gammaq(1.0, x) + x).abs() < 1e-9, "{x}");
        }
        assert!(log_gammaq(2.5, 0.0).abs() < 1e-12);
        // Q(2, x) = (1 + x) exp(-x)
        for x in [0.5, 3.0, 12.0] {
            assert!((log_gammaq(2.0, x) - ((1.0 + x).ln() - x)).abs() < 1e-9, "{x}");
        }
    }

    #[test]
    fn censored_cells_interpolate_as_numpy() {
        // test_kernels.test_the_censored_cells_kernel_interpolates_as_np_interp_on_the_grid
        let c = Censored::new(&[2.0]);
        let fp = c.table(2.0);
        let g = &c.grid;
        for i in [0, 1, 17, 2000, 3999, 4000] {
            assert_eq!(c.at(fp, g[i].exp(), g[i]), fp[i]);
        }
        assert_eq!(c.at(fp, 1e-9, g[0] - 5.0), fp[0]);
        assert_eq!(c.at(fp, 1e9, g[4000] + 5.0), fp[4000]);
        assert_eq!(c.at(fp, 0.0, f64::NEG_INFINITY), fp[0]);
        assert!(c.at(fp, f64::NAN, f64::NAN).is_nan());
        let x = 0.5 * (g[17] + g[18]);
        let want = (fp[18] - fp[17]) / (g[18] - g[17]) * (x - g[17]) + fp[17];
        assert_eq!(c.at(fp, x.exp(), x), want);
    }

    #[test]
    fn the_ratio_is_the_difference_of_the_log_likelihoods() {
        // test_kernels.test_the_ld2410_ratio_is_the_numpy: ratios, normalizer and what is put in
        let p = Params::default();
        let lik = Likelihood::new(&p);
        let mut rng = Rng::new(13);
        for trial in 0..60 {
            let frames = 1 + rng.below(19);
            let st = stats(&mut rng, frames);
            let prep = st.prepare(&lik);
            let mu0: [f64; CELLS] = std::array::from_fn(|_| rng.range(0.0, 3.0).exp());
            let k = 1 + rng.below(40);
            let s: Vec<[f64; CELLS]> = (0..k)
                .map(|_| std::array::from_fn(|_| rng.range(-3.0, 6.0).exp()))
                .collect();
            let m: Vec<f64> = (0..k)
                .map(|_| {
                    if rng.uniform() < 0.7 {
                        rng.uniform() / k as f64
                    } else {
                        0.0
                    }
                })
                .collect();
            let intensity = trial % 3 == 0;
            let r = prep.ratio(&mut Scratch::default(), &m, &s, &mu0, intensity);
            let ll0 = prep.loglik(&st, &mu0);
            for i in 0..k {
                if m[i] > 0.0 {
                    let mu: [f64; CELLS] = std::array::from_fn(|c| mu0[c] + s[i][c]);
                    let want = (prep.loglik(&st, &mu) - ll0).min(super::super::LOG_CAP);
                    assert!(
                        (r.lr[i] - want).abs() < 1e-9 * want.abs().max(1.0),
                        "{} {want}",
                        r.lr[i]
                    );
                } else {
                    assert_eq!(r.lr[i], -super::super::LOG_CAP);
                }
            }
            let top = r.lr.iter().fold(0.0f64, |a, &b| a.max(b));
            let inside: f64 = m.iter().zip(&r.lr).map(|(mi, l)| mi * (l - top).exp()).sum();
            let msum: f64 = m.iter().sum();
            let want = if intensity {
                top + ((-top).exp() + inside).ln() - msum.ln_1p()
            } else {
                top + ((1.0 - msum).max(0.0) * (-top).exp() + inside).ln()
            };
            assert!((r.norm - want).abs() < 1e-9 * want.abs().max(1.0));
        }
    }

    /// The prior background of ld2410.prior.
    const PRIOR: [f64; CELLS] = [
        13.0, 9.0, 4.5, 4.5, 4.5, 4.5, 4.5, 4.5, 4.5, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0,
    ];

    /// n frames of 0.09 s with the energies e capped.
    fn frames(e: &[f64; CELLS], n: usize) -> Stats {
        let mut st = Stats::default();
        let e = e.map(|x| x.min(100.0));
        for _ in 0..n {
            st.add(&e, 0.09, false);
        }
        st
    }

    /// log L(mu1) - log L(mu0).
    fn log_ratio(lik: &Likelihood, st: &Stats, mu1: &[f64; CELLS], mu0: &[f64; CELLS]) -> f64 {
        let prep = st.prepare(lik);
        prep.loglik(st, mu1) - prep.loglik(st, mu0)
    }

    #[test]
    fn a_person_explains_what_the_background_does_not() {
        // test_ld2410: 10 s of frames, they count by their correlation time (MODEL.md 4.3)
        let p = Params::default();
        let lik = Likelihood::new(&p);
        let b = PRIOR;
        let person = super::super::profile::expected(&p, crate::params::STILL, 0.0, 2.0, 1.0);
        let with: [f64; CELLS] = std::array::from_fn(|c| b[c] + person[c]);
        assert!(log_ratio(&lik, &frames(&with, 110), &with, &b) > 5.0);
        assert!(log_ratio(&lik, &frames(&b, 110), &with, &b) < -5.0);
        // the same excess in every gate of a kind is the common gain, not somebody
        let far = super::super::profile::expected(&p, crate::params::STILL, 0.0, 5.5, 1.0);
        let uniform = b.map(|x| 1.5 * x);
        let there: [f64; CELLS] = std::array::from_fn(|c| b[c] + far[c]);
        assert!(log_ratio(&lik, &frames(&uniform, 110), &there, &b) < 1.0);
    }

    #[test]
    fn the_amplitude_grid_carries_a_weak_reflector() {
        // test_ld2410 (MODEL.md 4.3): a person giving back a third of the profile (7.10. 22:12, Bad)
        // is explained by a low amplitude, not by the mean one; the grid and its prior mass per cell
        // of filtermodel.Shapes
        let amp = [
            0.20706513064584572,
            0.2994790765419169,
            0.40251942333763985,
            0.5401115683948937,
            0.7231709221427172,
            0.965436006366314,
            1.2839010492633798,
            1.700062508468409,
            2.2833850758714704,
        ];
        let w = [
            0.0043779612552276555,
            0.012810939634038468,
            0.04137409372931396,
            0.10770102862305887,
            0.2106563056807754,
            0.2815908551173566,
            0.22785168997043798,
            0.0956287125712516,
            0.018008413418539475,
        ];
        let p = Params::default();
        let lik = Likelihood::new(&p);
        let b = PRIOR;
        let person = super::super::profile::expected(&p, crate::params::STILL, 0.0, 1.8, 1.0);
        let st = frames(&std::array::from_fn(|c| b[c] + 0.3 * person[c]), 330); // 30 s
        let lr: Vec<f64> = amp
            .iter()
            .map(|g| log_ratio(&lik, &st, &std::array::from_fn(|c| b[c] + g * person[c]), &b))
            .collect();
        let top = lr.iter().fold(f64::NEG_INFINITY, |a, &x| a.max(x));
        let post: Vec<f64> = w.iter().zip(&lr).map(|(w, l)| w * (l - top).exp()).collect();
        let mean = post.iter().zip(&amp).map(|(p, g)| p * g).sum::<f64>() / post.iter().sum::<f64>();
        assert!(mean < 0.45, "{mean}");
    }

    #[test]
    fn the_factor_of_a_person_all_in_view_never_takes_log_0() {
        // test_robust: all of a person in view (masses summing to 1) and every ratio below e^-745:
        // until 0.9.3 math.log(0) ("math domain error")
        let s = [[0.0; CELLS]; 2];
        let r = ratio_tail(vec![-1000.0, -2000.0], &[0.5, 0.5], &s, false, &mut Vec::new());
        assert!((r.norm - (-1000.0 + 0.5f64.ln())).abs() < 1e-12 * 1000.0);
        // the usual case as before
        let r = ratio_tail(vec![1.0, -1.0], &[0.3, 0.2], &s, false, &mut Vec::new());
        let e = std::f64::consts::E;
        assert!((r.norm - (0.5 + 0.3 * e + 0.2 / e).ln()).abs() < 1e-15);
    }

    /// log of the integral of exp(g(s)) over s, g given on a uniform grid with step h (Simpson).
    fn log_integral(g: &[f64], h: f64) -> f64 {
        let top = g.iter().fold(f64::NEG_INFINITY, |a, &b| a.max(b));
        let n = g.len() - 1;
        let sum: f64 = g
            .iter()
            .enumerate()
            .map(|(i, x)| {
                (x - top).exp()
                    * if i == 0 || i == n {
                        1.0
                    } else if i % 2 == 1 {
                        4.0
                    } else {
                        2.0
                    }
            })
            .sum();
        top + (sum * h / 3.0).ln()
    }

    #[test]
    fn the_likelihood_integrates_the_gain_out() {
        // per kind of cell the common gain 1/v, v from the mixed Gamma prior, integrated numerically
        // over log v; a censored cell P(energy >= CAP) integrated numerically over the energy
        let p = Params::default();
        let lik = Likelihood::new(&p);
        let crate::params::Burst {
            shape: bk,
            gain: bu,
            weight: bw,
        } = p.ld_burst;
        let mut rng = Rng::new(5);
        for trial in 0..8 {
            let mut st = Stats::default();
            for _ in 0..1 + rng.below(12) {
                let e: [f64; CELLS] = std::array::from_fn(|_| rng.below(99) as f64);
                st.add(&e, rng.range(0.05, 0.5), false);
            }
            let censored = trial % 2 == 1;
            if censored {
                let mut e = [5.0; CELLS];
                e[3] = 100.0;
                e[12] = 100.0;
                st.add(&e, 0.2, false);
            }
            let mu: [f64; CELLS] = std::array::from_fn(|_| rng.range(1.0, 40.0));
            let mut want = 0.0;
            for (c, m) in mu.iter().enumerate() {
                let (a, tau) = (lik.alpha[c], lik.tau[c]);
                want += (st.t_unc[c] * (a * a.ln() - lgamma(a)) + (a - 1.0) * st.t_le[c]) / tau;
                want -= a * st.t_unc[c] / tau * m.ln();
            }
            for (cells, k) in [(0..MOVING, p.ld_gain[0]), (MOVING..CELLS, p.ld_gain[1])] {
                let look: f64 = cells.clone().map(|c| lik.alpha[c] * st.t_unc[c] / lik.tau[c]).sum();
                let ae: f64 = cells.map(|c| lik.alpha[c] * st.t_e[c] / lik.tau[c] / mu[c]).sum();
                if look == 0.0 {
                    continue;
                }
                let gamma = |v: f64, shape: f64, rate: f64| {
                    shape * rate.ln() - lgamma(shape) + (shape - 1.0) * v.ln() - rate * v
                };
                let (lo, hi, n) = (-20.0, 12.0, 200_000);
                let h = (hi - lo) / n as f64;
                let g: Vec<f64> = (0..=n)
                    .map(|i| {
                        let s = lo + i as f64 * h;
                        let v = s.exp();
                        let prior = ((1.0 - bw).ln() + gamma(v, k, k)).max(bw.ln() + gamma(v, bk, bk * bu));
                        let other = ((1.0 - bw).ln() + gamma(v, k, k)).min(bw.ln() + gamma(v, bk, bk * bu));
                        let prior = prior + (other - prior).exp().ln_1p();
                        prior + look * s - v * ae + s
                    })
                    .collect();
                want += log_integral(&g, h);
            }
            for c in (0..CELLS).filter(|&c| st.t_cens[c] > 0.0) {
                let (a, rate) = (lik.alpha[c], lik.alpha[c] / mu[c]);
                let (n, top) = (200_000, CAP + 80.0 * mu[c]);
                let h = (top - CAP) / n as f64;
                let g: Vec<f64> = (0..=n)
                    .map(|i| {
                        let x = CAP + i as f64 * h;
                        a * rate.ln() - lgamma(a) + (a - 1.0) * x.ln() - rate * x
                    })
                    .collect();
                want += st.t_cens[c] / lik.tau[c] * log_integral(&g, h);
            }
            let got = st.prepare(&lik).loglik(&st, &mu);
            let tol = if censored { 1e-5 } else { 1e-8 };
            assert!(
                (got - want).abs() < tol * want.abs().max(1.0),
                "{trial}: {got} against {want}"
            );
        }
    }
}
