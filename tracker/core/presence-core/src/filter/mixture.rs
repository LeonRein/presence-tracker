//! A person in view with a measuring track, or the source of a ghost track (gauss.py, MODEL.md 5.2):
//! a mixture of two Gaussians, one per mode - standing and walking - each a Kalman filter over
//! position x, velocity v and, per track of theirs, the track's offset on them (constant c,
//! wandering o), per axis its own covariance. The standing component also holds the probability of
//! each kind of stay (3.1) and, jointly, of each level of the detectability and the LD2410C
//! amplitude (4.1, 4.3). A part of the person may have gone through a door into a region without a
//! sensor: a density over the tiles (densities.rs) with the weight a.

use std::f64::consts::PI;

use crate::filter::densities::{Amp, Densities, DensityId, Kind, TileFactors};
use crate::filter::pool::PoolId;
use crate::filter::small_map::SmallMap;
use crate::geometry::Point;
use crate::params::{Params, STILL, Shapes, WALK};
use crate::vmath::{logaddexp, nan_max, pairwise_sum};
use crate::world::World;

/// log(2 pi) (gauss.LOG2PI)
const LOG2PI: f64 = 1.8378770664093453;
/// state indices per axis: position, velocity; a track's c and o follow at its slot
const X: usize = 0;
const V: usize = 1;

/// A position cut to a sensor's sight (Mixture::in_sight): the 5-point Gauss-Hermite rule of N(0, 1)
/// per axis (numpy's hermegauss(5)), its product grid and weights; a cut keeps at least 5 cm of
/// spread per axis (or what it had) (gauss.SIGHT_GRID, SIGHT_W, SIGHT_MIN_VAR).
const SIGHT_NODES: [f64; 5] = [
    -2.8569700138728056,
    -1.355626179974266,
    0.0,
    1.355626179974266,
    2.8569700138728056,
];
const SIGHT_W: [f64; 25] = [
    0.0001267293098014938,
    0.002499999999999996,
    0.006003952708117697,
    0.002499999999999996,
    0.0001267293098014938,
    0.002499999999999996,
    0.04931771513464292,
    0.11844049173632676,
    0.04931771513464292,
    0.002499999999999996,
    0.006003952708117697,
    0.11844049173632676,
    0.28444444444444467,
    0.11844049173632676,
    0.006003952708117697,
    0.002499999999999996,
    0.04931771513464292,
    0.11844049173632676,
    0.04931771513464292,
    0.002499999999999996,
    0.0001267293098014938,
    0.002499999999999996,
    0.006003952708117697,
    0.002499999999999996,
    0.0001267293098014938,
];
const SIGHT_MIN_VAR: f64 = 0.05 * 0.05;

/// A Gaussian mixture's index in its pool.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MixtureId(u32);

impl PoolId for MixtureId {
    fn from_index(index: usize) -> Self {
        Self(u32::try_from(index).expect("too many Gaussian mixtures"))
    }
    fn index(self) -> usize {
        self.0 as usize
    }
}

/// A track of a sensor, as the filter names it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct SegId(pub u64);

/// Where a track's offset sits in the state (c at index, o at index + 1) and its variance per axis.
#[derive(Debug, Clone, Copy)]
pub struct Slot {
    pub index: usize,
    pub var: f64,
}

/// One mode's Kalman filter: per axis the mean (d) and the covariance (d x d), axis-major.
#[derive(Debug, Clone)]
pub struct Kalman {
    pub mean: Vec<f64>,
    pub cov: Vec<f64>,
}

impl Kalman {
    pub fn zeros(dim: usize) -> Self {
        Self {
            mean: vec![0.0; 2 * dim],
            cov: vec![0.0; 2 * dim * dim],
        }
    }

    /// One axis: its mean (d) and covariance (d x d, row-major).
    pub fn axis(&self, ax: usize) -> (&[f64], &[f64]) {
        let d = self.dim();
        (&self.mean[ax * d..][..d], &self.cov[ax * d * d..][..d * d])
    }

    pub fn axis_mut(&mut self, ax: usize) -> (&mut [f64], &mut [f64]) {
        let d = self.dim();
        (&mut self.mean[ax * d..][..d], &mut self.cov[ax * d * d..][..d * d])
    }

    /// The same Gaussian with the state indices `keep` only, in that order (marginal, reordered).
    fn select(&self, keep: &[usize]) -> Self {
        let (d, e) = (self.dim(), keep.len());
        let mut out = Self::zeros(e);
        for ax in 0..2 {
            let (mean, cov) = self.axis(ax);
            let (out_mean, out_cov) = out.axis_mut(ax);
            for ((m, row), &ki) in out_mean.iter_mut().zip(out_cov.chunks_exact_mut(e)).zip(keep) {
                *m = mean[ki];
                for (c, &kj) in row.iter_mut().zip(keep) {
                    *c = cov[ki * d + kj];
                }
            }
        }
        out
    }

    /// The Gaussian with two more states per axis, a priori independent of the rest: variances
    /// v_c and v_o (Gauss.add_track).
    fn extended(&self, v_c: f64, v_o: f64) -> Self {
        let (d, e) = (self.dim(), self.dim() + 2);
        let mut out = Self::zeros(e);
        for ax in 0..2 {
            let (mean, cov) = self.axis(ax);
            let (out_mean, out_cov) = out.axis_mut(ax);
            out_mean[..d].copy_from_slice(mean);
            for (row, from) in out_cov.chunks_exact_mut(e).zip(cov.chunks_exact(d)) {
                row[..d].copy_from_slice(from);
            }
            out_cov[d * e + d] = v_c;
            out_cov[(d + 1) * e + d + 1] = v_o;
        }
        out
    }

    /// The position and velocity alone, per axis (without the tracks' offsets).
    pub fn motion(&self) -> Kalman {
        let d = self.dim();
        let mut out = Kalman::zeros(2);
        for ax in 0..2 {
            out.mean[ax * 2..][..2].copy_from_slice(&self.mean[ax * d..][..2]);
            for i in 0..2 {
                out.cov[(ax * 2 + i) * 2..][..2].copy_from_slice(&self.cov[(ax * d + i) * d..][..2]);
            }
        }
        out
    }

    /// d: position, velocity and two per track
    pub fn dim(&self) -> usize {
        self.mean.len() / 2
    }

    pub fn pos(&self) -> Point {
        [self.mean[0], self.mean[self.dim()]]
    }

    pub fn pos_var(&self) -> Point {
        let d = self.dim();
        [self.cov[0], self.cov[d * d]]
    }
}

#[derive(Debug)]
pub struct Mixture {
    /// log weights of [STILL, WALK], normalized
    pub logw: [f64; 2],
    pub modes: [Kalman; 2],
    /// probability of each kind of stay (L), for the standing component
    pub stay: Vec<f64>,
    /// probability of each level of detectability and LD2410C amplitude together (K x A)
    pub ka: Vec<f64>,
    pub slots: SmallMap<SegId, Slot>,
    /// the source of a ghost track, not a person
    pub phantom: bool,
    /// weight of the part gone through a door (it can grow while that part is about to be dropped)
    pub a: f64,
    pub away: Option<DensityId>,
    /// the time it was moved to (None: at the filter's last move of everybody)
    pub t: Option<f64>,
}

/// Moment matching of weighted Gaussians per axis (gauss._collapse, kernels._collapse2): their
/// total weight and the Gaussian; (0, the first) if none has weight.
pub fn collapse(parts: &[(f64, &Kalman)]) -> (f64, Kalman) {
    let tot = parts.iter().fold(0.0, |s, (w, _)| s + w);
    if tot <= 0.0 {
        return (0.0, parts[0].1.clone()); // a copy: the same Gaussian goes on as another
    }
    let d = parts[0].1.dim();
    let mut out = Kalman::zeros(d);
    for (i, m) in out.mean.iter_mut().enumerate() {
        *m = parts.iter().fold(0.0, |s, (w, g)| s + w * g.mean[i]) / tot;
    }
    for &(w, g) in parts {
        if w <= 0.0 {
            continue;
        }
        for ax in 0..2 {
            let (gm, gc) = g.axis(ax);
            let (m, c) = out.axis_mut(ax);
            for (i, (row, grow)) in c.chunks_exact_mut(d).zip(gc.chunks_exact(d)).enumerate() {
                for (j, (x, gx)) in row.iter_mut().zip(grow).enumerate() {
                    *x += w * (gx + (gm[i] - m[i]) * (gm[j] - m[j]));
                }
            }
        }
    }
    for c in &mut out.cov {
        *c /= tot;
    }
    (tot, out)
}

/// Sigma points of a component's position (MODEL.md 5.2): the unscented transform of the 2-D
/// position with n + kappa = 3, the centre with 1/3 and +-sqrt(3) sigma along each axis with 1/6
/// each (Saerkkae & Svensson 2023, eq. 8.70-8.71; gauss.UNIT).
pub const SQRT_3: f64 = 1.7320508075688772;
pub const UNIT: [Point; 5] = [[0.0, 0.0], [SQRT_3, 0.0], [-SQRT_3, 0.0], [0.0, SQRT_3], [0.0, -SQRT_3]];
pub const UNIT_W: [f64; 5] = [1.0 / 3.0, 1.0 / 6.0, 1.0 / 6.0, 1.0 / 6.0, 1.0 / 6.0];
/// the farthest unit point from the centre
const UNIT_REACH: f64 = SQRT_3;

/// gauss.Gauss.weights from the log weights: numpy's max (NaN wins), exp, sum.
pub fn weights2(l0: f64, l1: f64) -> [f64; 2] {
    let top = if l0 >= l1 || l0.is_nan() { l0 } else { l1 };
    if top == f64::NEG_INFINITY {
        return [0.5, 0.5];
    }
    let (w0, w1) = ((l0 - top).exp(), (l1 - top).exp());
    let s = w0 + w1;
    [w0 / s, w1 / s]
}

/// The larger of two log weights, as numba's max (the first unless the second is larger).
pub fn max2(a: f64, b: f64) -> f64 {
    if b > a { b } else { a }
}

/// log(exp(a) + exp(b)) by the larger one, top + log(exp(a - top) + exp(b - top)) (numba's and
/// numpy's logsumexp of two; -inf + -inf: NaN, as the Python filter; NaN stays NaN). The Python
/// filter's other form, numpy's logaddexp (vmath::logaddexp, by log1p), differs in the last bit: each
/// place uses the one the Python filter used there.
pub fn log_add(a: f64, b: f64) -> f64 {
    let top = max2(a, b);
    top + ((a - top).exp() + (b - top).exp()).ln()
}

/// log_add of the components' log weights, -inf if both are.
fn log_add_or_none(lw: [f64; 2]) -> f64 {
    if max2(lw[0], lw[1]) > f64::NEG_INFINITY {
        log_add(lw[0], lw[1])
    } else {
        f64::NEG_INFINITY
    }
}

/// The sigma points (UNIT) of N(mean[k], diag var[k]) per mode k, with walls: those of a mode with
/// a wall within their reach, seen from where the component is (pos[k]), mirrored at the first wall
/// their move from pos[k] crosses (walls reflect, MODEL.md 3.2; kernels.sigma_points). All moves
/// of the modes near a wall are mirrored in one call: the walls tried are those near all of them.
pub fn sigma_points(world: &World, walls: bool, pos: [Point; 2], mean: [Point; 2], var: [Point; 2]) -> [[Point; 5]; 2] {
    let mut pts = [[[0.0; 2]; 5]; 2];
    let mut near = [false; 2];
    for k in 0..2 {
        let sx = if var[k][0] < 0.0 { 0.0 } else { var[k][0] }.sqrt();
        let sy = if var[k][1] < 0.0 { 0.0 } else { var[k][1] }.sqrt();
        for (p, u) in pts[k].iter_mut().zip(UNIT) {
            *p = [mean[k][0] + u[0] * sx, mean[k][1] + u[1] * sy];
        }
        let r = (mean[k][0] - pos[k][0]).abs() + (mean[k][1] - pos[k][1]).abs() + UNIT_REACH * sx.max(sy);
        // without walls the kernel had a grid of one cell with 0: every mode near, nothing to mirror
        near[k] = !walls || !world.clear(pos[k], r);
    }
    if !walls || !near.iter().any(|&n| n) {
        return pts;
    }
    let modes: Vec<usize> = (0..2).filter(|&k| near[k]).collect();
    let p0: Vec<Point> = modes.iter().flat_map(|&k| [pos[k]; 5]).collect();
    let p1: Vec<Point> = modes.iter().flat_map(|&k| pts[k]).collect();
    let mut moved = vec![[0.0; 2]; p1.len()];
    let mut normal = vec![[0.0; 2]; p1.len()];
    world.walls.reflect(&p0, &p1, &mut moved, &mut normal);
    for (&k, chunk) in modes.iter().zip(moved.as_chunks::<5>().0) {
        pts[k].copy_from_slice(chunk);
    }
    pts
}

impl Mixture {
    pub fn dim(&self) -> usize {
        self.modes[STILL].dim()
    }

    pub fn weights(&self) -> [f64; 2] {
        weights2(self.logw[0], self.logw[1])
    }

    /// The sigma points of each component's position (with walls: mirrored at them).
    pub fn sigma(&self, world: &World, walls: bool) -> [[Point; 5]; 2] {
        let pos = self.modes.each_ref().map(Kalman::pos);
        let var = self.modes.each_ref().map(Kalman::pos_var);
        sigma_points(world, walls, pos, pos, var)
    }

    /// Weighs the person by no track started on them in dt (kernels.sync_weigh): E[exp(-rate dt)]
    /// over each component's sigma points (rates per mode and point; for the standing one per level
    /// of detectability kappa), so the levels' probabilities (Gauss.kappa_weigh) and the
    /// components' weights change, and the part gone through a door by exp(d_away)
    /// (Gauss.reweigh). Returns the log of the factor of the whole person.
    pub fn weigh_no_track(&mut self, rates: &[[f64; 5]; 2], kappa: &[f64], dt: f64, d_away: f64) -> f64 {
        let expect =
            |mode: usize, scale: f64| (0..5).fold(0.0, |e, p| e + UNIT_W[p] * (-scale * rates[mode][p] * dt).exp());
        let f_walk = expect(WALK, 1.0);
        let f_still: Vec<f64> = kappa.iter().map(|&k| expect(STILL, k)).collect();
        let amps = self.ka.len() / kappa.len();
        let level = |i: usize| f_still[i / amps];
        let s = self.ka.iter().enumerate().fold(0.0, |s, (i, &v)| s + v * level(i));
        if s > 0.0 {
            for (i, v) in self.ka.iter_mut().enumerate() {
                *v = *v * level(i) / s;
            }
        }
        let ln_or_none = |x: f64| if x > 0.0 { x.ln() } else { f64::NEG_INFINITY };
        let lw = [self.logw[0] + ln_or_none(s), self.logw[1] + ln_or_none(f_walk)];
        self.with_away(lw, d_away, log_add_or_none)
    }

    /// The components' new log weights lw (before normalizing) and the factor d_away of the part
    /// gone through a door: the part's new weight a, the components' weights normalized, and the
    /// log factor of the whole person; `add` sums the two parts in the log (log_add, or logaddexp:
    /// where the Python filter used which). Nothing left (-inf): nothing changes. NaN: the weights
    /// are kept, the factor is NaN.
    fn with_away(&mut self, lw: [f64; 2], d_away: f64, add: fn([f64; 2]) -> f64) -> f64 {
        let comps = log_add_or_none(lw);
        let total = if self.a <= 0.0 {
            comps
        } else {
            let p0 = if self.a < 1.0 {
                (-self.a).ln_1p() + comps
            } else {
                f64::NEG_INFINITY
            };
            let p1 = self.a.ln() + d_away;
            let total = add([p0, p1]);
            if total == f64::NEG_INFINITY {
                return total;
            }
            self.a = (p1 - total).exp();
            total
        };
        if comps > f64::NEG_INFINITY {
            self.logw = lw.map(|l| l - comps);
        }
        total
    }

    pub fn walking(&self) -> f64 {
        self.weights()[WALK]
    }

    /// (K,) probability of each level of detectability (Gauss.kw)
    pub fn kw(&self, levels: usize) -> Vec<f64> {
        self.ka.chunks_exact(self.ka.len() / levels).map(pairwise_sum).collect()
    }

    /// (A,) probability of each level of the LD2410C amplitude (Gauss.amw)
    pub fn amw(&self, levels: usize) -> Vec<f64> {
        let amps = self.ka.len() / levels;
        (0..amps)
            .map(|a| (0..levels).fold(0.0, |s, k| s + self.ka[k * amps + a]))
            .collect()
    }

    /// A copy as a new object: the model branches (Gauss.copy). Its part gone through a door is
    /// copied too: that part is this mixture's alone.
    pub fn fork(&self, densities: &mut Densities) -> Mixture {
        Mixture {
            logw: self.logw,
            modes: self.modes.clone(),
            stay: self.stay.clone(),
            ka: self.ka.clone(),
            slots: self.slots.clone(),
            phantom: self.phantom,
            a: self.a,
            away: self.away.map(|id| densities.fork(id)),
            t: self.t,
        }
    }

    /// Multiplies the components' weights by exp(dlog) and the part gone through a door by
    /// exp(d_away); returns the log of the factor of the whole person (for the hypotheses) and
    /// normalizes (Gauss.reweigh).
    pub fn reweigh(&mut self, dlog: [f64; 2], d_away: f64) -> f64 {
        let lw = [self.logw[0] + dlog[0], self.logw[1] + dlog[1]];
        self.with_away(lw, d_away, |[p0, p1]| logaddexp(p0, p1))
    }

    /// The LD2410C's log factors of the standing component per level of its amplitude (A): the
    /// levels' probabilities change; returns the component's log factor (MODEL.md 4.3, _ld_apply).
    pub fn weigh_amplitude(&mut self, logf: &[f64]) -> f64 {
        let top = nan_max(logf);
        let f: Vec<f64> = logf.iter().map(|x| (x - top).exp()).collect();
        let s = self.weigh_ka(|i| f[i % f.len()]);
        if s > 0.0 { top + s.ln() } else { f64::NEG_INFINITY }
    }

    /// The levels of detectability and amplitude (K x A) times f(index), normalized if anything is
    /// left; returns their sum before.
    fn weigh_ka(&mut self, f: impl Fn(usize) -> f64) -> f64 {
        let mut ka: Vec<f64> = self.ka.iter().enumerate().map(|(i, v)| v * f(i)).collect();
        let s = pairwise_sum(&ka);
        if s > 0.0 {
            for v in &mut ka {
                *v /= s;
            }
            self.ka = ka;
        }
        s
    }

    /// A likelihood that depends on the standing component's detectability, f_still per level
    /// (and f_walk for the walking one): updates the levels' probabilities and returns the log
    /// factor per component, for reweigh (Gauss.kappa_weigh).
    pub fn kappa_weigh(&mut self, f_still: &[f64], f_walk: f64) -> [f64; 2] {
        let amps = self.ka.len() / f_still.len();
        let s = self.weigh_ka(|i| f_still[i / amps]);
        let ln = |x: f64| if x > 0.0 { x.ln() } else { f64::NEG_INFINITY };
        [ln(s), ln(f_walk)]
    }

    /// A new track on this person: its offset (c, o), a priori independent of the rest
    /// (Gauss.add_track).
    pub fn add_track(&mut self, seg: SegId, var: f64, const_share: f64) {
        let d = self.dim();
        let (v_c, v_o) = (var * const_share, var * (1.0 - const_share));
        self.modes = self.modes.each_ref().map(|g| g.extended(v_c, v_o));
        self.slots.insert(seg, Slot { index: d, var });
    }

    /// A track ended, or is no longer this person's: its offset is marginalized out
    /// (Gauss.drop_track).
    pub fn drop_track(&mut self, seg: SegId) {
        let k = self.slots.remove(&seg).expect("a track the person has").index;
        let keep: Vec<usize> = (0..self.dim()).filter(|&i| i != k && i != k + 1).collect();
        self.modes = self.modes.each_ref().map(|g| g.select(&keep));
        for slot in self.slots.values_mut() {
            if slot.index > k {
                slot.index -= 2;
            }
        }
    }

    /// The Kalman update of both components with the track's position z. Measured, the person is in
    /// view: the part gone through a door is dropped and released. Returns the log of the mixture's
    /// factor, the predictive density of z (Gauss.update).
    pub fn update(&mut self, densities: &mut Densities, seg: SegId, z: Point, white: f64) -> f64 {
        let k = self.slots.get(&seg).expect("a track the person has").index;
        let d = self.dim();
        // the track measures x + c + o: per axis H = 1 at these states
        let idx = [X, k, k + 1];
        let mut ll = [0.0; 2];
        let mut ph = vec![0.0; d]; // P H'
        for (mode, g) in self.modes.iter_mut().enumerate() {
            for (ax, &za) in z.iter().enumerate() {
                let (mean, cov) = g.axis_mut(ax);
                for (p, row) in ph.iter_mut().zip(cov.chunks_exact(d)) {
                    *p = idx.iter().fold(0.0, |s, &j| s + row[j]);
                }
                let s = idx.iter().fold(0.0, |s, &j| s + ph[j]) + white.powf(2.0);
                let e = za - idx.iter().fold(0.0, |s, &j| s + mean[j]);
                ll[mode] += -0.5 * e * e / s - 0.5 * s.ln() - 0.5 * LOG2PI;
                for ((m, row), &p_i) in mean.iter_mut().zip(cov.chunks_exact_mut(d)).zip(&ph) {
                    let gain = p_i / s;
                    *m += e * gain;
                    for (c, &p_j) in row.iter_mut().zip(&ph) {
                        *c -= gain * p_j;
                    }
                }
            }
        }
        let mut lead = 0.0;
        if self.a > 0.0 {
            lead = if self.a < 1.0 {
                (-self.a).ln_1p()
            } else {
                f64::NEG_INFINITY
            };
            self.a = 0.0;
            if let Some(away) = self.away.take() {
                densities.release(away);
            }
        }
        lead + self.reweigh(ll, 0.0)
    }

    /// A sensor measured this person: they are in its sight - the radar can't see through walls
    /// (MODEL.md 4.1; Gauss.in_sight). `at`: where its sight lines start. The position per mode is
    /// cut to what the sensor sees: N(mean, diag var) on a grid of Gauss-Hermite points, the points
    /// whose sight line crosses a wall dropped, matched again; the rest of the state follows by its
    /// regression on position. If it sees none of them, they are mirrored at the wall instead. The
    /// weights stay.
    pub fn in_sight(&mut self, world: &World, at: Point) {
        if self.phantom {
            return;
        }
        let var = self.modes.each_ref().map(|g| g.pos_var().map(|v| v.max(0.0)));
        let mut pts = [[[0.0; 2]; 25]; 2];
        for (k, g) in self.modes.iter().enumerate() {
            let (pos, sd) = (g.pos(), var[k].map(f64::sqrt));
            for (p, q) in pts[k].iter_mut().enumerate() {
                *q = [pos[0] + SIGHT_NODES[p / 5] * sd[0], pos[1] + SIGHT_NODES[p % 5] * sd[1]];
            }
        }
        let flat: Vec<Point> = pts.iter().flatten().copied().collect();
        let from = vec![at; flat.len()];
        let hidden = world.walls.crosses(&from, &flat);
        if !hidden.iter().any(|&h| h) {
            return;
        }
        let (mut mirrored, mut normal) = (vec![[0.0; 2]; flat.len()], vec![[0.0; 2]; flat.len()]);
        world.walls.reflect(&from, &flat, &mut mirrored, &mut normal);
        for (k, g) in self.modes.iter_mut().enumerate() {
            let hid = &hidden[k * 25..][..25];
            if !hid.iter().any(|&h| h) {
                continue;
            }
            let (q, w): (Vec<Point>, Vec<f64>) = if hid.iter().all(|&h| h) {
                (mirrored[k * 25..][..25].to_vec(), SIGHT_W.to_vec())
            } else {
                (0..25).filter(|&p| !hid[p]).map(|p| (pts[k][p], SIGHT_W[p])).unzip()
            };
            let total = pairwise_sum(&w);
            let w: Vec<f64> = w.iter().map(|x| x / total).collect();
            let mut mx = [0.0; 2];
            for (wi, p) in w.iter().zip(&q) {
                mx = [mx[0] + wi * p[0], mx[1] + wi * p[1]];
            }
            let mut px = [0.0; 2];
            for (wi, p) in w.iter().zip(&q) {
                px = [
                    px[0] + wi * (p[0] - mx[0]).powf(2.0),
                    px[1] + wi * (p[1] - mx[1]).powf(2.0),
                ];
            }
            let d = g.dim();
            for ax in 0..2 {
                let px = px[ax].max(var[k][ax].min(SIGHT_MIN_VAR));
                let (mean, cov) = g.axis_mut(ax);
                let pxx = cov[X * d + X];
                if !(pxx > 0.0) {
                    continue;
                }
                // the rest of the state by its regression on the position
                let gain: Vec<f64> = cov.chunks_exact(d).map(|row| row[X] / pxx).collect();
                let shift = mx[ax] - mean[X];
                for ((m, row), &gi) in mean.iter_mut().zip(cov.chunks_exact_mut(d)).zip(&gain) {
                    *m += gi * shift;
                    for (c, &gj) in row.iter_mut().zip(&gain) {
                        *c += gi * gj * (px - pxx);
                    }
                }
            }
        }
    }

    /// E[exp(-|x - z|^2 / (2 radius^2))] per component, and the sigma points of the position
    /// weighted by that kernel (a Gaussian again: z as a measurement with variance radius^2), with
    /// walls - for the expectation of something else times the kernel (Gauss.near_sigma,
    /// kernels.near_parts).
    pub fn near_sigma(&self, world: &World, z: Point, radius: f64) -> ([f64; 2], [[Point; 5]; 2]) {
        let r2 = radius.powf(2.0);
        let mut near = [0.0; 2];
        let (mut mean, mut var) = ([[0.0; 2]; 2], [[0.0; 2]; 2]);
        for (k, g) in self.modes.iter().enumerate() {
            let (pos, p) = (g.pos(), g.pos_var());
            let mut f = [0.0; 2];
            for a in 0..2 {
                let v = r2 + p[a];
                let d = pos[a] - z[a];
                f[a] = (r2 / v).sqrt() * (-0.5 * (d * d) / v).exp();
                mean[k][a] = (pos[a] * r2 + z[a] * p[a]) / (p[a] + r2);
                var[k][a] = p[a] * r2 / (p[a] + r2);
            }
            near[k] = f[0] * f[1];
        }
        let pos = self.modes.each_ref().map(Kalman::pos);
        (near, sigma_points(world, true, pos, mean, var))
    }

    /// The density of this person over the tiles (MODEL.md 5.4), when their last track ends: each
    /// component spread over the tiles, standing ones with their kinds of stay and detectability
    /// and the amplitude of the stay where it stands (the end of a track is not the end of the
    /// stay, Swerling III); with the part gone through a door. The velocity and the offsets are
    /// lost. A new density (Gauss.to_tiles).
    pub fn to_tiles(&self, world: &World, densities: &mut Densities) -> DensityId {
        let (tiles, shapes) = (&world.tiles, &world.shapes);
        let h = densities.alloc(Kind::Person);
        if tiles.n == 0 {
            *densities.get_mut(h).out = 1.0;
            return h;
        }
        let w = self.weights();
        let mut d = densities.get_mut(h);
        let s = d.shape;
        if w[STILL] > 0.0 {
            let g = &self.modes[STILL];
            let mass = world.gauss_mass(g.pos(), g.pos_var());
            let kw = self.kw(s.levels);
            for (l, &go) in self.stay.iter().enumerate() {
                for (k, &kk) in kw.iter().enumerate() {
                    let f = w[STILL] * (go * kk);
                    for (x, &m) in d.still[s.still_row(l, k)].iter_mut().zip(&mass) {
                        *x += f * m;
                    }
                }
            }
            let mut amp = s.amp_prior();
            for (k, &kk) in kw.iter().enumerate() {
                for a in 0..=s.amps {
                    // P(level | detectability) of this stay; g = 1 (the last level) not
                    let agk = match a {
                        _ if a == s.amps => 0.0,
                        _ if kk > 0.0 => self.ka[k * s.amps + a] / kk,
                        _ => shapes.amp_given_kappa[k * s.amps + a],
                    };
                    for (g, &m) in amp[s.amp_row(k, a)].iter_mut().zip(&mass) {
                        if m > 0.0 {
                            *g = agk;
                        }
                    }
                }
            }
            *d.amp = Some(Amp {
                levels: amp,
                keep: None,
            });
        }
        if w[WALK] > 0.0 {
            let g = &self.modes[WALK];
            let mass = world.gauss_mass(g.pos(), g.pos_var());
            for (x, &m) in d.walk.iter_mut().zip(&mass) {
                *x += w[WALK] * m;
            }
        }
        d.normalize();
        match self.away {
            Some(away) if self.a > 0.0 => {
                let out = if self.a < 1.0 {
                    densities.mixture(&[(1.0 - self.a, h), (self.a, away)])
                } else {
                    densities.fork(away)
                };
                densities.release(h);
                out
            }
            _ => h,
        }
    }
}

/// A Gaussian mixture of a person or a ghost's source with exactly the tracks `segs` (the others
/// marginalized out), in that order (Gauss._ordered): its modes.
fn ordered_modes(mix: &Mixture, segs: &[SegId]) -> [Kalman; 2] {
    let mut keep = vec![X, V];
    for seg in segs {
        let k = mix.slots.get(seg).expect("a track all of them have").index;
        keep.extend([k, k + 1]);
    }
    mix.modes.each_ref().map(|g| g.select(&keep))
}

/// sum_i w_i x mixture_i for [(w_i, mixture_i)], weights summing to 1, as a new mixture
/// (Gauss.mixture): per mode matched to one Gaussian; offsets of tracks that not all of them hold
/// are marginalized out; the parts gone through a door mixed (a new density).
pub fn mix_mixtures(parts: &[(f64, &Mixture)], densities: &mut Densities) -> Mixture {
    let first = parts[0].1;
    let common: Vec<SegId> = first
        .slots
        .keys()
        .copied()
        .filter(|s| parts.iter().all(|(_, g)| g.slots.contains_key(s)))
        .collect();
    let modes: Vec<[Kalman; 2]> = parts.iter().map(|(_, g)| ordered_modes(g, &common)).collect();
    let weights: Vec<[f64; 2]> = parts.iter().map(|(_, g)| g.weights()).collect();
    let comps = [STILL, WALK].map(|k| {
        let ps: Vec<(f64, &Kalman)> = parts
            .iter()
            .zip(&modes)
            .zip(&weights)
            .map(|(((w, g), m), wg)| (w * (1.0 - g.a) * wg[k], &m[k]))
            .collect();
        collapse(&ps)
    });
    let ws: Vec<f64> = parts
        .iter()
        .zip(&weights)
        .map(|((w, g), wg)| w * (1.0 - g.a) * wg[STILL])
        .collect();
    let aw: Vec<(f64, DensityId)> = parts
        .iter()
        .filter_map(|(w, g)| g.away.filter(|_| g.a > 0.0).map(|id| (w * g.a, id)))
        .collect();
    let a = aw.iter().fold(0.0, |s, (x, _)| s + x);
    let away = (a > 0.0).then(|| match aw.as_slice() {
        [(_, only)] => densities.fork(*only),
        _ => densities.mixture(&aw.iter().map(|&(x, id)| (x / a, id)).collect::<Vec<_>>()),
    });
    let wsum = pairwise_sum(&ws);
    let (stay, ka) = if wsum > 0.0 {
        let mix = |field: fn(&Mixture) -> &Vec<f64>| -> Vec<f64> {
            let len = field(first).len();
            (0..len)
                .map(|i| parts.iter().zip(&ws).fold(0.0, |s, ((_, g), wi)| s + wi * field(g)[i]) / wsum)
                .collect()
        };
        (mix(|g| &g.stay), mix(|g| &g.ka))
    } else {
        (first.stay.clone(), first.ka.clone())
    };
    let tot = [comps[0].0, comps[1].0];
    let sum = tot[0] + tot[1];
    let logw = if sum > 0.0 {
        tot.map(|t| (t / sum).ln())
    } else {
        [0.5f64.ln(); 2]
    };
    let [(_, still), (_, walk)] = comps;
    let slots = common
        .iter()
        .enumerate()
        .map(|(j, &s)| {
            (
                s,
                Slot {
                    index: 2 + 2 * j,
                    var: first.slots.get(&s).expect("a common track").var,
                },
            )
        })
        .collect();
    Mixture {
        logw,
        modes: [still, walk],
        stay,
        ka,
        slots,
        phantom: first.phantom,
        a,
        away,
        t: first.t,
    }
}

/// A track that begins on somebody: its id, where it began, and the variance per axis of where it
/// sits on a person.
#[derive(Debug, Clone, Copy)]
pub struct NewTrack {
    pub seg: SegId,
    pub z: Point,
    pub var: f64,
}

/// Per mode its weight and the position's posterior N(mean, diag var) given the track.
#[derive(Debug, Clone, Copy)]
struct Moments {
    w: [f64; 2],
    mean: [Point; 2],
    var: [Point; 2],
}

/// Given the position's posterior N(mx, px) per axis and a track's first position z: the joint
/// moments of (x, c, o) per axis - given x, c + o + w = z - x splits by the variances (MODEL.md
/// 5.4; Gauss._offsets). Returns [mean (3), cov (3 x 3)] per axis.
fn offsets(track: NewTrack, mx: Point, px: Point, p: &Params) -> [([f64; 3], [f64; 9]); 2] {
    let var = track.var;
    let (vc, vo) = (var * p.const_share, var * (1.0 - p.const_share));
    let s2 = var + p.white.powf(2.0);
    let (ac, ao) = (vc / s2, vo / s2);
    [0, 1].map(|a| {
        let r = track.z[a] - mx[a];
        let mean = [mx[a], ac * r, ao * r];
        let c11 = ac * ac * px[a] + vc - vc * vc / s2;
        let c22 = ao * ao * px[a] + vo - vo * vo / s2;
        let c12 = ac * ao * px[a] - vc * vo / s2;
        let (c01, c02) = (-ac * px[a], -ao * px[a]);
        (mean, [px[a], c01, c02, c01, c11, c12, c02, c12, c22])
    })
}

/// Both components from the position's posterior per mode, with the new track measured; a walker
/// without a track goes in any direction (Gauss._make).
fn make(track: NewTrack, m: Moments, stay: Vec<f64>, ka: Vec<f64>, p: &Params, phantom: bool) -> Mixture {
    // per axis: x, v, then the track's c and o
    let idx = [X, 2, 3];
    let modes = [STILL, WALK].map(|k| {
        let mut g = Kalman::zeros(4);
        for (a, (mo, co)) in offsets(track, m.mean[k], m.var[k], p).into_iter().enumerate() {
            let (mean, cov) = g.axis_mut(a);
            for (i, &ii) in idx.iter().enumerate() {
                mean[ii] = mo[i];
                for (j, &jj) in idx.iter().enumerate() {
                    cov[ii * 4 + jj] = co[i * 3 + j];
                }
            }
            if k == WALK {
                (mean[V], cov[V * 4 + V]) = (0.0, p.walk_var());
            }
        }
        g
    });
    let sum = m.w[0] + m.w[1];
    Mixture {
        logw: m.w.map(|x| (x / sum).ln()),
        modes,
        stay,
        ka,
        slots: [(
            track.seg,
            Slot {
                index: 2,
                var: track.var,
            },
        )]
        .into_iter()
        .collect(),
        phantom,
        a: 0.0,
        away: None,
        t: None,
    }
}

/// The source of a new ghost track (Gauss.source): anywhere a ghost may be born (evenly), so given
/// the first position x = z - c - o - w; standing or walking alike, a stay seen at a random time.
pub fn ghost_source(track: NewTrack, p: &Params, shapes: &Shapes) -> Mixture {
    let (z, var) = (track.z, track.var);
    let s2 = var + p.white.powf(2.0);
    let moments = Moments {
        w: [0.5, 0.5],
        mean: [z, z],
        var: [[s2; 2]; 2],
    };
    let mut g = make(
        track,
        moments,
        shapes.go_w_ongoing.clone(),
        shapes.ka_w.clone(),
        p,
        true,
    );
    // with an even prior x carries all of z's uncertainty: Var x = vc + vo + w^2, Cov(x, c) = -vc
    let (vc, vo) = (var * p.const_share, var * (1.0 - p.const_share));
    for m in &mut g.modes {
        for (a, &za) in z.iter().enumerate() {
            let (mean, c) = m.axis_mut(a);
            c[0] = s2;
            (c[2 * 4 + 2], c[3 * 4 + 3], c[2 * 4 + 3], c[3 * 4 + 2]) = (vc, vo, 0.0, 0.0);
            (c[2], c[2 * 4]) = (-vc, -vc);
            (c[3], c[3 * 4]) = (-vo, -vo);
            (mean[X], mean[2], mean[3]) = (za, 0.0, 0.0);
        }
    }
    g
}

/// A person without a track gets one (MODEL.md 5.4; Gauss.from_tiles): their density times f (the
/// rate or chance of this track starting / being found on somebody in each tile) times the density
/// of z, each tile as the Gaussian with its mean and spread, matched per mode to one Gaussian.
/// Returns the mixture and the log of the mass - this alternative's factor for the hypothesis - or
/// None if there is no mass. Reading the density's amplitude settles it.
pub fn from_tiles(
    world: &World,
    densities: &mut Densities,
    id: DensityId,
    f: TileFactors,
    track: NewTrack,
) -> Option<(Mixture, f64)> {
    let (tiles, p, shapes) = (&world.tiles, &world.params, &world.shapes);
    let (z, n) = (track.z, tiles.n);
    let s2 = track.var + p.white.powf(2.0);
    // per tile the density of z, and the position given the tile and z
    let mut dens = vec![0.0; n];
    let mut post = vec![([0.0; 2], [0.0; 2]); n];
    for (c, (e, (pm, pv))) in dens.iter_mut().zip(&mut post).enumerate() {
        let (tv, cen) = (tiles.var[c], tiles.centers[c]);
        let v = [tv[0] + s2, tv[1] + s2];
        let dz = [z[0] - cen[0], z[1] - cen[1]];
        *e = (-0.5 * (dz[0] * dz[0] / v[0] + dz[1] * dz[1] / v[1])).exp() / (2.0 * PI * (v[0] * v[1]).sqrt());
        for a in 0..2 {
            pm[a] = cen[a] + tv[a] / v[a] * dz[a];
            pv[a] = tv[a] * s2 / v[a];
        }
    }
    let s = densities.shape();
    let d = densities.get(id);
    let qw: Vec<f64> = d
        .walk
        .iter()
        .zip(f.walk)
        .zip(&dens)
        .map(|((w, x), e)| w * x * e)
        .collect();
    let mut qs = vec![0.0; s.still_len()];
    for l in 0..s.kinds {
        for k in 0..s.levels {
            let fk = f.still.level(k, n);
            let row = s.still_row(l, k);
            for (((q, x), fx), e) in qs[row.clone()].iter_mut().zip(&d.still[row]).zip(fk).zip(&dens) {
                *q = x * (fx * e);
            }
        }
    }
    let mass = pairwise_sum(&qw) + pairwise_sum(&qs);
    let r = d.r;
    if !(mass > 0.0 && r > 0.0) {
        return None;
    }
    // per tile the still mass over the kinds and levels, in numpy's order
    let mut q_still = vec![0.0; n];
    for row in qs.chunks_exact(n) {
        for (acc, x) in q_still.iter_mut().zip(row) {
            *acc += x;
        }
    }
    let mut m = Moments {
        w: [0.0; 2],
        mean: [[0.0; 2]; 2],
        var: [[0.0; 2]; 2],
    };
    for (k, q) in [(STILL, &q_still), (WALK, &qw)] {
        m.w[k] = pairwise_sum(q);
        if m.w[k] <= 0.0 {
            (m.mean[k], m.var[k]) = (z, [s2; 2]);
            continue;
        }
        for a in 0..2 {
            let mx = q.iter().zip(&post).fold(0.0, |acc, (qi, (pm, _))| acc + qi * pm[a]) / m.w[k];
            let px = q
                .iter()
                .zip(&post)
                .fold(0.0, |acc, (qi, (pm, pv))| acc + qi * (pv[a] + (pm[a] - mx).powf(2.0)))
                / m.w[k];
            (m.mean[k][a], m.var[k][a]) = (mx, px);
        }
    }
    let per_kind: Vec<f64> = qs.chunks_exact(s.levels * n).map(pairwise_sum).collect();
    let total = pairwise_sum(&per_kind);
    let stay = if total > 0.0 {
        per_kind.iter().map(|g| g / total).collect()
    } else {
        shapes.go_w_ongoing.clone()
    };
    let qk = s.still_per_level(&qs);
    let ka = if pairwise_sum(&qk) > 0.0 {
        // the amplitude of the stay as the tiles carry it (4.3), where the measurement puts them
        let mut d = densities.get_mut(id);
        d.settle_amp();
        let prior = s.amp_prior();
        let amp = d.amp.as_ref().map_or(&prior, |a| &a.levels);
        let mut ka = vec![0.0; s.levels * s.amps];
        for k in 0..s.levels {
            let q = &qk[s.level_row(k)];
            let dot = |a: usize| q.iter().zip(&amp[s.amp_row(k, a)]).fold(0.0, |acc, (x, g)| acc + x * g);
            let fresh = dot(s.amps);
            for a in 0..s.amps {
                ka[k * s.amps + a] = dot(a) + fresh * shapes.amp_given_kappa[k * s.amps + a];
            }
        }
        let total = pairwise_sum(&ka);
        ka.iter().map(|v| v / total).collect()
    } else {
        shapes.ka_w.clone()
    };
    // times the probability that the person exists (a known person without a track is a
    // Bernoulli; the track says they do)
    Some((make(track, m, stay, ka, p, false), mass.ln() + r.ln()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::motion::imm::{ImmStep, predict};
    use crate::testing::{self, TILE};

    /// A walker at the origin with velocity v, as test_gauss.walker.
    fn walker(v: Point) -> Mixture {
        let s = testing::shapes();
        let mut modes = [Kalman::zeros(2), Kalman::zeros(2)];
        for (k, g) in modes.iter_mut().enumerate() {
            for (ax, &vx) in v.iter().enumerate() {
                let (mean, cov) = g.axis_mut(ax);
                cov[0] = 0.01;
                if k == WALK {
                    (mean[V], cov[3]) = (vx, 0.05);
                }
            }
        }
        Mixture {
            logw: [1e-300f64.ln(), 0.0],
            modes,
            stay: s.go_w,
            ka: s.ka_w,
            slots: SmallMap::default(),
            phantom: false,
            a: 0.0,
            away: None,
            t: None,
        }
    }

    fn steps(world: &World, p: &Params, mix: &mut Mixture, dt: f64, n: usize, walls: bool) {
        let mut densities = Densities::new(DensityShape {
            tiles: world.tiles.n,
            kinds: 2,
            levels: 2,
            regions: 0,
            ages: 18,
            amps: 2,
        });
        for _ in 0..n {
            predict(
                world,
                mix,
                &mut densities,
                &ImmStep::new(p, &world.shapes, dt),
                walls,
                true,
            );
        }
    }

    use crate::filter::densities::DensityShape;

    #[test]
    fn a_walk_does_not_depend_on_how_time_is_cut() {
        // test_gauss: the walk's prediction is an exact discretization (MODEL.md 3.2), one step of
        // 0.2 s is two of 0.1 s (no mode changes: walks of infinite length); and it forgets its
        // direction at the measured rate, E[v(t)] = v(0) exp(-turn_rate t)
        let world = testing::flat();
        let p = Params {
            walk_length: 1e12,
            ..Params::default()
        };
        let (mut one, mut two) = (walker([0.8, 0.0]), walker([0.8, 0.0]));
        steps(&world, &p, &mut one, 0.2, 1, false);
        steps(&world, &p, &mut two, 0.1, 2, false);
        for (a, b) in one.modes[WALK].mean.iter().zip(&two.modes[WALK].mean) {
            assert!((a - b).abs() < 1e-12);
        }
        for (a, b) in one.modes[WALK].cov.iter().zip(&two.modes[WALK].cov) {
            assert!((a - b).abs() < 1e-12);
        }
        let mut g = walker([0.8, 0.0]);
        steps(&world, &p, &mut g, 2.0, 1, false);
        let v = g.modes[WALK].axis(0).0[V];
        assert!((v / (0.8 * (-2.0 * p.turn_rate).exp()) - 1.0).abs() < 1e-9);
    }

    #[test]
    fn walls_turn_a_walk_but_leave_who_stands_at_one() {
        // test_gauss: a walker heading for the wall stays in the room, while somebody standing
        // right at a wall is not pushed off it - only the step's motion meets the walls
        let world = testing::flat();
        let p = Params {
            walk_length: 1e12,
            ..Params::default()
        };
        let mut g = walker([0.0, 0.8]);
        for m in &mut g.modes {
            m.mean[0] = 3.0;
            m.mean[2] = 4.5;
        }
        let mut free = walker([0.0, 0.8]);
        free.modes = g.modes.clone(); // the same start, without walls
        steps(&world, &p, &mut g, 0.2, 10, true);
        steps(&world, &p, &mut free, 0.2, 10, false);
        assert!(free.modes[WALK].pos()[1] > 5.2);
        assert!(g.modes[WALK].pos()[1] < 4.9 && g.modes[WALK].axis(1).0[V] < 0.0);
        let mut s = walker([0.0, 0.0]);
        s.logw = [0.0, 1e-300f64.ln()];
        for m in &mut s.modes {
            m.mean[0] = 3.0;
            m.mean[2] = 4.95;
        }
        steps(&world, &Params::default(), &mut s, 0.2, 25, true);
        assert!((s.modes[STILL].pos()[1] - 4.95).abs() < 0.06);
    }

    #[test]
    fn rates_are_expected_over_the_component() {
        // test_gauss: the rates of a wide component are the mean over its sigma points, not the
        // value at its mean, and points across a wall are mirrored into the room
        let world = testing::flat();
        let mut g = walker([0.8, 0.0]);
        for m in &mut g.modes {
            (m.mean[0], m.mean[2]) = (4.5, 3.5);
            (m.cov[0], m.cov[4]) = (2.25, 2.25);
        }
        let pts = g.sigma(&world, true);
        assert!(
            pts.iter()
                .flatten()
                .all(|p| p[0] > 0.0 && p[0] < 6.0 && p[1] > 0.0 && p[1] < 5.0)
        );
        let users = [0].into_iter().collect();
        let r = world
            .rates
            .mixture_rates(&world.sights, &[Vec::new(), Vec::new()], users, &pts);
        let expected = r[WALK].iter().zip(UNIT_W).fold(0.0, |s, (x, w)| s + x * w);
        let at_mean = world.rates.rate_at(&world.sights[0], g.modes[WALK].pos(), WALK, &[]);
        assert!((expected / at_mean - 1.0).abs() > 0.05, "{expected} {at_mean}");
    }

    #[test]
    fn an_update_is_the_kalman_update_of_x_plus_the_offsets() {
        let world = testing::flat();
        let p = &world.params;
        let mut densities = Densities::new(DensityShape {
            tiles: 0,
            kinds: 2,
            levels: 2,
            regions: 0,
            ages: 18,
            amps: 2,
        });
        let mut g = walker([0.3, 0.0]);
        g.logw = [0.5f64.ln(); 2];
        g.add_track(SegId(4), 0.04, p.const_share);
        assert_eq!(g.dim(), 4);
        let z = [0.1, -0.05];
        let before = g.modes.clone();
        let l = g.update(&mut densities, SegId(4), z, p.white);
        // per axis H = [1, 0, 1, 1]; the offsets independent of x a priori
        let s = 0.01 + 0.04 + p.white.powf(2.0);
        let mut ll = [0.0; 2];
        for (k, (old, new)) in before.iter().zip(&g.modes).enumerate() {
            for (ax, &za) in z.iter().enumerate() {
                let e = za - old.axis(ax).0[X];
                ll[k] += -0.5 * e * e / s - 0.5 * s.ln() - 0.5 * LOG2PI;
                assert!((new.axis(ax).0[X] - 0.01 / s * e).abs() < 1e-15);
                assert!((new.axis(ax).1[0] - (0.01 - 0.01 * 0.01 / s)).abs() < 1e-15);
            }
        }
        let want = 0.5f64.ln() + (ll[0].exp() + ll[1].exp()).ln();
        assert!((l - want).abs() < 1e-12, "{l} {want}");
        g.drop_track(SegId(4));
        assert_eq!(g.dim(), 2);
        assert!(g.slots.is_empty());
    }

    #[test]
    fn a_track_behind_a_wall_puts_the_person_in_sight() {
        // Gauss.in_sight: all of the position behind the wall seen from the sensor: mirrored
        let world = testing::flat();
        let mut g = walker([0.0, 0.0]);
        for m in &mut g.modes {
            (m.mean[0], m.mean[2]) = (6.3, 2.5);
            (m.cov[0], m.cov[4]) = (4e-4, 4e-4);
        }
        g.in_sight(&world, world.sights[0].origin);
        for m in &g.modes {
            let [x, y] = m.pos();
            assert!(5.6 < x && x < 6.0 && (y - 2.5).abs() < 0.05, "{x} {y}");
        }
        // in sight already: nothing changes
        let seen = g.modes.clone();
        g.in_sight(&world, world.sights[0].origin);
        assert_eq!(g.modes[0].mean, seen[0].mean);
    }

    #[test]
    fn from_the_tiles_a_track_takes_the_person_where_they_stand() {
        let world = testing::flat();
        let s = &world.shapes;
        let mut densities = Densities::new(DensityShape {
            tiles: world.tiles.n,
            kinds: 2,
            levels: 2,
            regions: 0,
            ages: 18,
            amps: 2,
        });
        let id = densities.alloc(Kind::Person);
        let c = world.tiles.cell_near([3.25, 2.25]).expect("a tile");
        {
            let d = densities.get_mut(id);
            let shape = d.shape;
            for (l, k) in [(0, 0), (0, 1), (1, 0), (1, 1)] {
                d.still[shape.still_row(l, k)][c] = 0.25;
            }
        }
        let ones = vec![1.0; world.tiles.n];
        let track = NewTrack {
            seg: SegId(1),
            z: world.tiles.centers[c],
            var: 0.04,
        };
        let (mix, l) = from_tiles(&world, &mut densities, id, TileFactors::same(&ones), track).expect("mass");
        let v = TILE * TILE / 12.0 + 0.04 + world.params.white.powf(2.0);
        assert!((l - (1.0 / (2.0 * PI * v)).ln()).abs() < 1e-12);
        assert!(mix.weights()[STILL] > 1.0 - 1e-12);
        let [x, y] = mix.modes[STILL].pos();
        assert!((x - 3.25).abs() < 1e-12 && (y - 2.25).abs() < 1e-12);
        assert_eq!(mix.stay, vec![0.5, 0.5]);
        assert!((mix.ka.iter().sum::<f64>() - 1.0).abs() < 1e-12 && mix.ka.len() == s.ka_w.len());
        // nobody there: no mass
        let far = NewTrack {
            z: [-30.0, -30.0],
            ..track
        };
        let none = from_tiles(&world, &mut densities, id, TileFactors::same(&ones), far);
        assert!(none.is_none_or(|(_, l)| l < -1000.0));
    }
}
