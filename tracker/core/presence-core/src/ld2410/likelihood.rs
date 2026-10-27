//! The energies' likelihood (MODEL.md 4.3; ld2410.Stats, Likelihood, kernels.ld_rows, ld_ratio):
//! per cell Gamma(alpha, mean mu), an energy at 100 censored; the frames are correlated, so their
//! log-likelihoods count with dt / tau (composite likelihood, Varin, Reid & Firth 2011); per kind of
//! cell (moving, still) the block's common gain u is integrated out: 1/u ~ Gamma(k, rate k) mixed with
//! bursts (conjugate). The form is that of track-before-detect (Salmond & Birch 2001).

use super::{CAP, CELLS, MOVING, cell_values};
use crate::params::Params;
use crate::vmath::{lgamma, pairwise_sum, vexp, vexp1, vlog, vlog1};

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
/// numpy's interp (ld2410.log_censored, kernels._censored_at).
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

/// A kind of cell (moving, still) and the prior of its block's common gain: two components
/// (log weight + k log rate - lgamma k, shape k, rate r).
#[derive(Debug)]
struct Kind {
    cells: std::ops::Range<usize>,
    comps: [(f64, f64, f64); 2],
}

/// The numbers of the energies' likelihood (ld2410.Likelihood).
#[derive(Debug)]
pub struct Likelihood {
    pub alpha: [f64; CELLS],
    pub tau: [f64; CELLS],
    kinds: [Kind; 2],
    censored: Censored,
}

impl Likelihood {
    pub fn new(p: &Params) -> Self {
        let alpha = cell_values(p.ld_shape[0], p.ld_shape[1]);
        let [bk, bu, bw] = p.ld_burst;
        let br = bk * bu;
        let kind = |cells, k: f64| Kind {
            cells,
            comps: [
                ((1.0 - bw).ln() + k * k.ln() - lgamma(k), k, k),
                (bw.ln() + bk * br.ln() - lgamma(bk), bk, br),
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
/// likelihood per cell (ld2410.Stats): time (s) of uncensored frames, time x energy, time x log
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

/// A kind of cell the frames looked at: its cells, and per component of its gain's prior the rate
/// r, c + lgamma(N + k) and N + k (N the looks).
#[derive(Debug)]
struct Looked {
    cells: std::ops::Range<usize>,
    comps: [(f64, f64, f64); 2],
}

/// What the likelihood takes from the frames, once for all the means asked about (Stats._prepared).
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
            let ee = x.max(0.5); // integer energies: 0 as half a unit
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
                    comps: kind.comps.map(|(c, k, r)| (r, c + lgamma(n + k), n + k)),
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

impl Prepared<'_> {
    /// The part of the tempered log-likelihood that depends on the means (Stats._mu_part,
    /// kernels._ld_cols): mu by cell, `mu[c * k + i]` the mean of cell c in row i; per row -sum
    /// log(mu) x looks, per kind of cell log sum_c exp(c + lgamma(N + k) - (N + k) log(r + A)), A the
    /// sum of alpha x energy / mean over its cells, and the censored cells. Cell by cell over all
    /// rows: the loops over the rows vectorize.
    pub fn mu_part(&self, mu: &[f64], k: usize) -> Vec<f64> {
        let mut lm = mu.to_vec();
        vlog(&mut lm);
        let mut out = vec![0.0; k];
        for c in 0..CELLS {
            for (o, l) in out.iter_mut().zip(&lm[c * k..][..k]) {
                *o -= l * self.look[c];
            }
        }
        let (mut a, mut t0, mut t1, mut top) = (vec![0.0; k], vec![0.0; k], vec![0.0; k], vec![0.0; k]);
        for Looked { cells, comps } in &self.kinds {
            a.fill(0.0);
            for c in cells.clone() {
                for (x, m) in a.iter_mut().zip(&mu[c * k..][..k]) {
                    *x += self.ae[c] / m;
                }
            }
            let [(r0, c0, n0), (r1, c1, n1)] = *comps;
            for i in 0..k {
                let (x, y) = (c0 - n0 * vlog1(r0 + a[i]), c1 - n1 * vlog1(r1 + a[i]));
                let m = if x.is_nan() || (!y.is_nan() && x >= y) { x } else { y }; // NaN wins
                (top[i], t0[i], t1[i]) = (m, x - m, y - m);
            }
            vexp(&mut t0);
            vexp(&mut t1);
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
        out
    }

    /// The tempered log-likelihood of the frames given the means mu (16) (Stats.loglik).
    pub fn loglik(&self, stats: &Stats, mu: &[f64; CELLS]) -> f64 {
        let lik = self.lik;
        let terms: Vec<f64> = (0..CELLS)
            .map(|c| {
                let a = lik.alpha[c];
                (stats.t_unc[c] * (a * a.ln() - lgamma(a)) + (a - 1.0) * stats.t_le[c]) / lik.tau[c]
            })
            .collect();
        pairwise_sum(&terms) + self.mu_part(mu, 1)[0]
    }

    /// Masses m (k) putting S (k x 16) on top of the means mu0 (kernels.ld_ratio): the log
    /// likelihood ratio per point against mu0 (capped; a point without mass gets -cap, not computed),
    /// the log of the normalizer - the factor of the whole person, or for the unknown ones
    /// (intensity) log((1 + int lambda L) / (1 + Lambda)): at most one of them in view - and what
    /// they put in afterwards (16).
    pub fn ratio(&self, m: &[f64], s: &[[f64; CELLS]], mu0: &[f64; CELLS], intensity: bool) -> Ratio {
        let with: Vec<usize> = (0..m.len()).filter(|&i| m[i] > 0.0).collect();
        let n = with.len();
        let mut mu = vec![0.0; CELLS * (n + 1)];
        for c in 0..CELLS {
            for (j, &i) in with.iter().enumerate() {
                mu[c * (n + 1) + j] = mu0[c] + s[i][c];
            }
            mu[c * (n + 1) + n] = mu0[c];
        }
        let both = self.mu_part(&mu, n + 1);
        let mut ratios = vec![-super::LOG_CAP; m.len()];
        for (j, &i) in with.iter().enumerate() {
            ratios[i] = both[j] - both[n];
        }
        ratio_tail(ratios, m, s, intensity)
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
fn ratio_tail(mut lr: Vec<f64>, m: &[f64], s: &[[f64; CELLS]], intensity: bool) -> Ratio {
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
    let mut e: Vec<f64> = lr.iter().map(|x| x - top).collect();
    vexp(&mut e);
    let inside = m.iter().zip(&e).fold(0.0, |acc, (mi, ei)| acc + mi * ei);
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
    for ((mi, ei), si) in m.iter().zip(&e).zip(s) {
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
            let r = prep.ratio(&m, &s, &mu0, intensity);
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

    #[test]
    fn a_person_explains_what_the_background_does_not() {
        // test_ld2410: frames with more energy near a gate favour a person there
        let p = Params::default();
        let lik = Likelihood::new(&p);
        let b = [
            13.0, 9.0, 4.5, 4.5, 4.5, 4.5, 4.5, 4.5, 4.5, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0,
        ];
        let person = super::super::profile::expected(&p, crate::params::STILL, 0.0, 2.2, 1.0);
        let mut st = Stats::default();
        let e: [f64; CELLS] = std::array::from_fn(|c| (b[c] + person[c]).round().min(100.0));
        for _ in 0..20 {
            st.add(&e, 0.1, false);
        }
        let prep = st.prepare(&lik);
        let mu: [f64; CELLS] = std::array::from_fn(|c| b[c] + person[c]);
        assert!(prep.loglik(&st, &mu) > prep.loglik(&st, &b) + 5.0);
    }
}
