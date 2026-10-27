//! A person in view with a measuring track, or the source of a ghost track (gauss.py, MODEL.md 5.2):
//! a mixture of two Gaussians, one per mode - standing and walking - each a Kalman filter over
//! position x, velocity v and, per track of theirs, the track's offset on them (constant c,
//! wandering o), per axis its own covariance. The standing component also holds the probability of
//! each kind of stay (3.1) and, jointly, of each level of the detectability and the LD2410C
//! amplitude (4.1, 4.3). A part of the person may have gone through a door into a region without a
//! sensor: a density over the tiles (densities.rs) with the weight a.

use crate::filter::densities::DensityId;
use crate::filter::pool::PoolId;
use crate::filter::small_map::SmallMap;
use crate::geometry::Point;
use crate::params::{STILL, WALK};
use crate::world::World;

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
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
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

/// log(exp(a) + exp(b)) by the larger one (-inf + -inf: NaN, as the Python filter).
pub fn log_add(a: f64, b: f64) -> f64 {
    let top = max2(a, b);
    top + ((a - top).exp() + (b - top).exp()).ln()
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
        let comps = if max2(lw[0], lw[1]) > f64::NEG_INFINITY {
            log_add(lw[0], lw[1])
        } else {
            f64::NEG_INFINITY
        };
        if self.a <= 0.0 {
            if comps > f64::NEG_INFINITY {
                self.logw = lw.map(|l| l - comps);
            }
            return comps;
        }
        let p0 = if self.a < 1.0 {
            (-self.a).ln_1p() + comps
        } else {
            f64::NEG_INFINITY
        };
        let p1 = self.a.ln() + d_away;
        if max2(p0, p1) == f64::NEG_INFINITY {
            return f64::NEG_INFINITY;
        }
        let total = log_add(p0, p1);
        self.a = (p1 - total).exp();
        if comps > f64::NEG_INFINITY {
            self.logw = lw.map(|l| l - comps);
        }
        total
    }
}
