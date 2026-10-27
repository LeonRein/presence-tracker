//! The shapes derived from the model (filtermodel.Shapes): the stays as a mixture of exponentials, the
//! detectability and the LD2410C amplitude on grids of their Gamma priors, joined by a Gaussian copula.
//! Built as filtermodel.py builds them; numpy's sums and BLAS round otherwise in the last bits.

use super::Params;
use crate::vmath::{lanczos_lgamma, pairwise_sum};

/// How long a stay lasts and how well it is seen (filtermodel.Shapes): every stay draws its rate of
/// getting up go_l with weight go_w_l (L kinds), its detectability kappa_k with kappa_w_k (K
/// levels), and with it the LD2410C amplitude (A levels; ka_w the joint prior, K x A).
#[derive(Debug)]
#[cfg_attr(test, derive(serde::Deserialize))]
pub struct Shapes {
    pub go: Vec<f64>,
    pub go_w: Vec<f64>,
    /// the kinds of the stays going on at a random moment (longer ones more likely)
    pub go_w_ongoing: Vec<f64>,
    pub kappa: Vec<f64>,
    pub kappa_w: Vec<f64>,
    /// the levels of the LD2410C amplitude g (A)
    pub amp: Vec<f64>,
    pub ka_w: Vec<f64>,
    /// P(level of the amplitude | detectability), K x A
    pub amp_given_kappa: Vec<f64>,
}

impl Shapes {
    pub fn kinds(&self) -> usize {
        self.go.len()
    }

    pub fn levels(&self) -> usize {
        self.kappa.len()
    }

    /// levels of the LD2410C amplitude (A)
    pub fn amps(&self) -> usize {
        self.ka_w.len() / self.kappa.len()
    }

    /// The prior of a stay's kind and detectability, L x K (independent a priori; Shapes.stay_prior).
    pub fn stay_prior(&self, ongoing: bool) -> Vec<f64> {
        let go_w = if ongoing { &self.go_w_ongoing } else { &self.go_w };
        go_w.iter()
            .flat_map(|&g| self.kappa_w.iter().map(move |&k| g * k))
            .collect()
    }
}

impl Shapes {
    /// The shapes of a model (Shapes.__init__).
    pub fn new(p: &Params) -> Self {
        let (go_w, go) = stay_rates(p.go_share, p.go_time, &p.stay_points);
        // a stay seen at a random moment is longer than a fresh one (renewal theory): its rate of
        // getting up weighted by its mean duration
        let ongoing: Vec<f64> = go_w.iter().zip(&go).map(|(w, g)| w / g).collect();
        let total = pairwise_sum(&ongoing);
        let (kappa, kappa_w) = gamma_log_grid(p.kappa_shape, p.kappa_levels);
        let (amp, amp_w) = gamma_log_grid(p.ld_amp_shape, p.ld_amp_levels);
        let ka_w = copula_cells(&kappa_w, &amp_w, p.kappa_amp_corr);
        let amp_given_kappa = ka_w
            .chunks_exact(amp.len())
            .flat_map(|row| {
                let s = pairwise_sum(row);
                row.iter().map(move |x| x / s)
            })
            .collect();
        Self {
            go_w_ongoing: ongoing.iter().map(|x| x / total).collect(),
            go,
            go_w,
            kappa,
            kappa_w,
            amp,
            ka_w,
            amp_given_kappa,
        }
    }
}

/// A mixture of exponentials (weights, rates) for the heavy-tailed stays with survival
/// (scale / (t + scale))^share: Feldmann & Whitt (1998), matched at the points (filtermodel.stay_rates).
fn stay_rates(share: f64, scale: f64, points: &[f64]) -> (Vec<f64>, Vec<f64>) {
    let surv = |t: f64| (scale / (t + scale)).powf(share);
    let ratio = 2.0;
    let (mut p, mut lam): (Vec<f64>, Vec<f64>) = (Vec::new(), Vec::new());
    let rest =
        |p: &[f64], lam: &[f64], t: f64| surv(t) - p.iter().zip(lam).fold(0.0, |s, (pj, lj)| s + pj * (-lj * t).exp());
    for &c in &points[..points.len() - 1] {
        let rate = (rest(&p, &lam, c) / rest(&p, &lam, ratio * c)).ln() / ((ratio - 1.0) * c);
        p.push(rest(&p, &lam, c) * (rate * c).exp());
        lam.push(rate);
    }
    let last = 1.0 - p.iter().fold(0.0, |s, x| s + x);
    let t = points[points.len() - 1];
    let r = rest(&p, &lam, t);
    lam.push((last / r).ln() / t);
    p.push(last);
    (p, lam)
}

/// numpy's linspace(start, stop, num): i x step + start, the last exactly stop.
fn linspace(start: f64, stop: f64, num: usize) -> Vec<f64> {
    let step = (stop - start) / (num - 1) as f64;
    let mut out: Vec<f64> = (0..num).map(|i| i as f64 * step + start).collect();
    out[num - 1] = stop;
    out
}

/// numpy's searchsorted (left): the first index whose value is not below v.
fn searchsorted(a: &[f64], v: f64) -> usize {
    a.partition_point(|&x| x < v)
}

/// Gamma(shape, rate shape) (mean 1) on a grid for its posterior: k cells evenly spaced in log x
/// between the prior's 1e-3 and 1 - 1e-3 quantiles (the outer cells reach to 0 and infinity), each at
/// its conditional mean with the prior's mass in it (filtermodel.gamma_log_grid). (nodes, weights)
fn gamma_log_grid(shape: f64, k: usize) -> (Vec<f64>, Vec<f64>) {
    let tail = 1e-3;
    let x: Vec<f64> = linspace(1e-8f64.ln(), (60.0 / shape + 60.0).ln(), 200_001)
        .iter()
        .map(|v| v.exp())
        .collect();
    let (scale, lgamma) = (shape * shape.ln(), lanczos_lgamma(shape));
    let pdf: Vec<f64> = x
        .iter()
        .map(|&v| ((shape - 1.0) * v.ln() - shape * v + scale - lgamma).exp())
        .collect();
    let (mut mass, mut first) = (vec![0.0; x.len()], vec![0.0; x.len()]);
    for i in 1..x.len() {
        let dx = x[i] - x[i - 1];
        mass[i] = mass[i - 1] + 0.5 * (pdf[i] + pdf[i - 1]) * dx;
        first[i] = first[i - 1] + 0.5 * (pdf[i] * x[i] + pdf[i - 1] * x[i - 1]) * dx;
    }
    let all = mass[x.len() - 1];
    let q: Vec<f64> = mass.iter().map(|m| m / all).collect();
    let (lo, hi) = (x[searchsorted(&q, tail)], x[searchsorted(&q, 1.0 - tail)]);
    let edges: Vec<f64> = linspace(lo.ln(), hi.ln(), k + 1).iter().map(|v| v.exp()).collect();
    let mut idx = vec![0];
    idx.extend(edges[1..k].iter().map(|&e| searchsorted(&x, e)));
    idx.push(x.len() - 1);
    let w: Vec<f64> = idx.windows(2).map(|ab| mass[ab[1]] - mass[ab[0]]).collect();
    let vals: Vec<f64> = idx
        .windows(2)
        .map(|ab| (first[ab[1]] - first[ab[0]]) / (mass[ab[1]] - mass[ab[0]]))
        .collect();
    let total = pairwise_sum(&w);
    let mean = w.iter().zip(&vals).fold(0.0, |s, (a, b)| s + a * b) / total;
    (
        vals.iter().map(|v| v / mean).collect(),
        w.iter().map(|v| v / total).collect(),
    )
}

/// The probability of each pair of cells (a-major) of two variables with these masses in their cells,
/// joined by a Gaussian copula with correlation rho of the normal scores (Nelsen 2006): the bivariate
/// normal's mass on the rectangles between the cells' quantiles, both marginals made exact by
/// iterative proportional fitting (filtermodel.copula_cells).
fn copula_cells(wa: &[f64], wb: &[f64], rho: f64) -> Vec<f64> {
    let (na, nb) = (wa.len(), wb.len());
    if rho == 0.0 {
        return wa.iter().flat_map(|a| wb.iter().map(move |b| a * b)).collect();
    }
    let edges = |w: &[f64]| -> Vec<f64> {
        let total = pairwise_sum(w);
        let mut c = 0.0;
        w[..w.len() - 1]
            .iter()
            .map(|x| {
                c += x;
                inv_normal_cdf((c / total).clamp(1e-15, 1.0 - 1e-15))
            })
            .collect()
    };
    let (za, zb) = (edges(wa), edges(wb));
    let x = linspace(-9.0, 9.0, 9001);
    let s = (1.0 - rho * rho).sqrt();
    let dx = x[1] - x[0];
    let mut p = vec![0.0; na * nb];
    for &xi in &x {
        let dens = (-0.5 * xi * xi).exp() / (2.0 * std::f64::consts::PI).sqrt() * dx;
        // the cdf of b given x at b's edges, 0 and 1 outside
        let mut cdf = vec![0.0];
        cdf.extend(
            zb.iter()
                .map(|&z| 0.5 * (1.0 + libm::erf((z - rho * xi) / (s * 2f64.sqrt())))),
        );
        cdf.push(1.0);
        let a = searchsorted(&za, xi);
        for (j, c) in cdf.windows(2).enumerate() {
            p[a * nb + j] += (c[1] - c[0]) * dens;
        }
    }
    for _ in 0..50 {
        for (row, &w) in p.chunks_exact_mut(nb).zip(wa) {
            let k = w / pairwise_sum(row);
            row.iter_mut().for_each(|v| *v *= k);
        }
        let mut cols = p[..nb].to_vec();
        for row in p.chunks_exact(nb).skip(1) {
            cols.iter_mut().zip(row).for_each(|(c, v)| *c += v);
        }
        for row in p.chunks_exact_mut(nb) {
            row.iter_mut().zip(&cols).zip(wb).for_each(|((v, c), w)| *v *= w / c);
        }
    }
    let total = pairwise_sum(&p);
    p.iter().map(|v| v / total).collect()
}

/// The inverse of the standard normal's cdf (statistics.NormalDist.inv_cdf): Wichura's AS241.
#[allow(clippy::excessive_precision)]
fn inv_normal_cdf(p: f64) -> f64 {
    let q = p - 0.5;
    if q.abs() <= 0.425 {
        let r = 0.180625 - q * q;
        let num = (((((((2.5090809287301226727e+3 * r + 3.3430575583588128105e+4) * r + 6.7265770927008700853e+4)
            * r
            + 4.5921953931549871457e+4)
            * r
            + 1.3731693765509461125e+4)
            * r
            + 1.9715909503065514427e+3)
            * r
            + 1.3314166789178437745e+2)
            * r
            + 3.3871328727963666080e+0)
            * q;
        let den = ((((((5.2264952788528545610e+3 * r + 2.8729085735721942674e+4) * r + 3.9307895800092710610e+4)
            * r
            + 2.1213794301586595867e+4)
            * r
            + 5.3941960214247511077e+3)
            * r
            + 6.8718700749205790830e+2)
            * r
            + 4.2313330701600911252e+1)
            * r
            + 1.0;
        return num / den;
    }
    let r = if q <= 0.0 { p } else { 1.0 - p };
    let r = (-r.ln()).sqrt();
    let (num, den) = if r <= 5.0 {
        let r = r - 1.6;
        (
            ((((((7.74545014278341407640e-4 * r + 2.27238449892691845833e-2) * r + 2.41780725177450611770e-1) * r
                + 1.27045825245236838258e+0)
                * r
                + 3.64784832476320460504e+0)
                * r
                + 5.76949722146069140550e+0)
                * r
                + 4.63033784615654529590e+0)
                * r
                + 1.42343711074968357734e+0,
            ((((((1.05075007164441684324e-9 * r + 5.47593808499534494600e-4) * r + 1.51986665636164571966e-2) * r
                + 1.48103976427480074590e-1)
                * r
                + 6.89767334985100004550e-1)
                * r
                + 1.67638483018380384940e+0)
                * r
                + 2.05319162663775882187e+0)
                * r
                + 1.0,
        )
    } else {
        let r = r - 5.0;
        (
            ((((((2.01033439929228813265e-7 * r + 2.71155556874348757815e-5) * r + 1.24266094738807843860e-3) * r
                + 2.65321895265761230930e-2)
                * r
                + 2.96560571828504891230e-1)
                * r
                + 1.78482653991729133580e+0)
                * r
                + 5.46378491116411436990e+0)
                * r
                + 6.65790464350110377720e+0,
            ((((((2.04426310338993978564e-15 * r + 1.42151175831644588870e-7) * r + 1.84631831751005468180e-5) * r
                + 7.86869131145613259100e-4)
                * r
                + 1.48753612908506148525e-2)
                * r
                + 1.36929880922735805310e-1)
                * r
                + 5.99832206555887937690e-1)
                * r
                + 1.0,
        )
    };
    let x = num / den;
    if q < 0.0 { -x } else { x }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing::hexf;

    #[test]
    fn the_normal_quantiles_are_pythons() {
        // statistics.NormalDist().inv_cdf(p).hex() of CPython 3.13
        for (p, want) in [
            (1e-15, "-0x1.fc3f007789667p+2"),
            (0.01, "-0x1.29c5c4630ff0ep+1"),
            (0.2, "-0x1.aee8fa73a1333p-1"),
            (0.5, "0x0.0p+0"),
            (0.6, "0x1.036d6c4a04b5ap-2"),
            (0.93, "0x1.79cd70d9c27f3p+0"),
            (0.999999, "0x1.30381a97985f1p+2"),
        ] {
            assert_eq!(inv_normal_cdf(p), hexf(want), "{p}");
        }
    }

    #[test]
    fn the_shapes_are_distributions_with_their_marginals() {
        let sh = Shapes::new(&Params::default());
        let one = |v: &[f64]| (v.iter().sum::<f64>() - 1.0).abs() < 1e-12;
        assert!(one(&sh.go_w) && one(&sh.go_w_ongoing) && one(&sh.kappa_w) && one(&sh.ka_w));
        assert_eq!((sh.kinds(), sh.levels(), sh.amps()), (5, 5, 9));
        // the copula keeps both marginals; the grids have mean 1
        for (k, &w) in sh.kappa_w.iter().enumerate() {
            assert!((sh.ka_w[k * 9..][..9].iter().sum::<f64>() - w).abs() < 1e-12);
        }
        let mean: f64 = sh.kappa.iter().zip(&sh.kappa_w).map(|(a, b)| a * b).sum();
        assert!((mean - 1.0).abs() < 1e-12);
        // a stay of a day is in the slowest kind (rate ~ 1 / day)
        assert!(sh.go[0] < 1e-4 && sh.go[4] > 0.1);
    }
}
