//! The LD2410C's echo sources (MODEL.md 4.3): energy that is not a person's. Per
//! sensor off, or one source on - the profile of a standing person on the axis at a slant, times an
//! amplitude - in one of K stages of one of its lives: it begins at a rate (uniform over the sources),
//! is short-lived or (with the share ld_echo_long[0]) long-lived, lives an Erlang(K) time of that mean
//! and ends. What tells it from a person is how it begins and ends: a person must come in, and
//! stays.
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use super::CELLS;
use super::profile::expected;
use crate::params::{Params, STILL, Slants};
use crate::vmath::{nan_max, pairwise_sum};

#[derive(Debug, Clone)]
pub struct Echoes {
    /// what each source puts in (J)
    src: Vec<[f64; CELLS]>,
    /// stages
    k: usize,
    /// per life: its share of the sources that begin, its mean (s)
    lives: Vec<(f64, f64)>,
    /// [off, (life, stage, source) ...]
    p: Vec<f64>,
    /// per life and source what began in the last step, and the first stage after it (for learning the rate)
    fresh: Vec<f64>,
    first: Vec<f64>,
}

impl Echoes {
    pub fn new(p: &Params) -> Self {
        let Slants { from: lo, to: hi, step } = p.ld_echo_ranges;
        // numpy's arange(lo, hi + 1e-9, step)
        let n = ((hi + 1e-9 - lo) / step).ceil() as usize;
        let profiles: Vec<[f64; CELLS]> = (0..n)
            .map(|i| expected(p, STILL, 0.0, lo + i as f64 * step, 1.0))
            .collect();
        let src: Vec<[f64; CELLS]> = p
            .ld_echo_amps
            .iter()
            .flat_map(|&amp| profiles.iter().map(move |row| row.map(|x| amp * x)))
            .collect();
        let k = p.ld_echo_stages;
        let [long, long_life] = p.ld_echo_long;
        let lives: Vec<(f64, f64)> = [(1.0 - long, p.ld_echo_life), (long, long_life)]
            .into_iter()
            .filter(|&(share, _)| share > 0.0)
            .collect();
        let mut probs = vec![0.0; 1 + lives.len() * k * src.len()];
        probs[0] = 1.0;
        Self {
            src,
            k,
            fresh: vec![0.0; lives.len()],
            lives,
            p: probs,
            first: Vec::new(),
        }
    }

    fn sources(&self) -> usize {
        self.src.len()
    }

    /// Where the states of a life's stage begin in p.
    fn at(&self, life: usize, stage: usize) -> usize {
        1 + (life * self.k + stage) * self.sources()
    }

    /// Moves on by dt: sources begin with `rate` (per s), age, end.
    pub fn predict(&mut self, _params: &Params, dt: f64, rate: f64) {
        let j = self.sources();
        let p = &self.p;
        let mut q = vec![0.0; p.len()];
        let on = p[0] * -(-rate * dt).exp_m1();
        q[0] = p[0] - on;
        let mut first = Vec::with_capacity(self.lives.len() * j);
        for (life, &(share, mean)) in self.lives.iter().enumerate() {
            let fresh = on * share / j as f64;
            self.fresh[life] = fresh;
            let start = self.at(life, 0);
            for x in &mut q[start..start + j] {
                *x += fresh;
            }
            let go = -(-(self.k as f64) / mean * dt).exp_m1();
            for stage in 0..self.k {
                let from = self.at(life, stage);
                let part = &p[from..from + j];
                for (x, &v) in q[from..from + j].iter_mut().zip(part) {
                    *x += v * (1.0 - go);
                }
                if stage + 1 < self.k {
                    let next = self.at(life, stage + 1);
                    for (x, &v) in q[next..next + j].iter_mut().zip(part) {
                        *x += v * go;
                    }
                } else {
                    q[0] += pairwise_sum(part) * go;
                }
            }
            first.extend_from_slice(&q[start..start + j]);
        }
        self.first = first;
        self.p = q;
    }

    /// After weigh: the expected number of sources that began in the last step.
    pub fn began(&self) -> f64 {
        let j = self.sources();
        (0..self.lives.len()).fold(0.0, |s, life| {
            let start = self.at(life, 0);
            self.p[start..start + j]
                .iter()
                .zip(&self.first[life * j..][..j])
                .fold(s, |s, (p, &f)| s + p * if f > 0.0 { self.fresh[life] / f } else { 0.0 })
        })
    }

    /// The masses of the states that are on, and what each puts into the cells x scale.
    pub fn points(&self, scale: &[f64; CELLS]) -> (Vec<f64>, Vec<[f64; CELLS]>) {
        let put = (0..self.lives.len() * self.k)
            .flat_map(|_| self.src.iter().map(|row| std::array::from_fn(|c| row[c] * scale[c])))
            .collect();
        (self.p[1..].to_vec(), put)
    }

    /// Multiplies the states that are on by exp(logf) (off by 1) and normalizes.
    pub fn weigh(&mut self, logf: &[f64]) {
        // Python's max(numpy's max, 0.0): NaN stays NaN
        let top = nan_max(logf);
        let top = if top.is_nan() { top } else { top.max(0.0) };
        let on: Vec<f64> = self.p[1..].iter().zip(logf).map(|(p, l)| p * (l - top).exp()).collect();
        let off = self.p[0] * (-top).exp();
        let z = off + pairwise_sum(&on);
        self.p = std::iter::once(off).chain(on).map(|x| x / z).collect();
    }

    /// The probabilities of off, and of each (stage, source) on.
    pub fn probs(&self) -> &[f64] {
        &self.p
    }

    /// P(no source is on)
    pub fn off(&self) -> f64 {
        self.p[0]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn echo_sources_begin_live_end_and_are_counted() {
        // test_ld2410.test_echo_sources_begin_live_end_and_are_counted
        let p = Params::default();
        let mut e = Echoes::new(&p);
        assert_eq!(e.sources(), 24 * 8); // slants 0.75-6.5 m every 0.25 m, 8 amplitudes
        let rate = 1.0 / 3600.0;
        e.predict(&p, 1.0, rate);
        let on = 1.0 - e.off();
        assert!((on - -(-rate).exp_m1()).abs() < 1e-15);
        assert!((e.began() - on).abs() < 1e-15);
        // a source on, then nothing: after 600 s only the long-lived ones (mean 245 s) may be on,
        // after many of their lives it is over
        let n = e.p.len();
        e.weigh(&vec![50.0; n - 1]);
        assert!(e.off() < 1e-6);
        for _ in 0..600 {
            e.predict(&p, 1.0, 0.0);
        }
        let short_on: f64 = e.p[1..e.at(1, 0)].iter().sum();
        assert!(short_on < 1e-9 && e.off() < 0.999, "{short_on} {}", e.off());
        for _ in 0..6000 {
            e.predict(&p, 1.0, 0.0);
        }
        assert!(e.off() > 0.999);
        assert!((e.p.iter().sum::<f64>() - 1.0).abs() < 1e-12);
    }
}
