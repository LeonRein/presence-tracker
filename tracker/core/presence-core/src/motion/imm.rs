//! One step of a Gaussian mixture (MODEL.md 3.1, 3.2, 5.2; gauss.Gauss.predict, the prediction of
//! kernels.sync_step): first the mode changes - standing -> walking at the stay's rate, walking ->
//! standing at speed / walk length; who stood on is more likely a long stay, who just stopped a
//! fresh one; the detectability changes now and then within a stay and is new for a new one, with
//! the LD2410C amplitude (4.3) - the incoming parts of each mode matched to one Gaussian (IMM,
//! Saerkkae & Svensson 2023 p. 352; walking -> standing: the velocity is 0 from now on; standing ->
//! walking: off in any direction at the walking speed), then each mode's linear prediction (exact
//! discretizations, Saerkkae & Solin 2019 ex. 6.2). With walls and doors also: walls reflect what
//! moves across them, and the walkers' share that walking takes through a door goes to the part
//! gone through a door.

use std::f64::consts::PI;

use crate::filter::densities::{Densities, Kind};
use crate::filter::mixture::{Kalman, Mixture, collapse, log_add, weights2};
use crate::geometry::Point;
use crate::params::{Params, STILL, Shapes, WALK};
use crate::vmath::pairwise_sum;
use crate::world::World;

/// The numbers of one step dt (gauss.step_params).
#[derive(Debug)]
pub struct ImmStep {
    dt: f64,
    /// walkers stop
    p_stop: f64,
    /// the walkers' stationary velocity variance per axis
    s2: f64,
    /// the standing position's diffusion per s
    still_q: f64,
    /// s: the velocity's correlation time
    tau: f64,
    /// decay of the tracks' wandering offsets
    ao: f64,
    /// the detectability is drawn anew within a stay
    q: f64,
    /// share of a track offset's variance that is constant over the track
    const_share: f64,
    /// per kind of stay, the probability that it goes on
    e_go: Vec<f64>,
}

impl ImmStep {
    pub fn new(p: &Params, shapes: &Shapes, dt: f64) -> Self {
        Self {
            dt,
            p_stop: -(-p.stop_rate() * dt).exp_m1(),
            s2: 0.5 * (p.speed.powf(2.0) + p.speed_spread.powf(2.0)),
            still_q: p.still_noise.powf(2.0),
            tau: 1.0 / p.turn_rate,
            ao: (-dt / p.tau).exp(),
            q: -(-p.kappa_switch * dt).exp_m1(),
            const_share: p.const_share,
            e_go: shapes.go.iter().map(|&go| (-go * dt).exp()).collect(),
        }
    }
}

/// A d x d matrix, row-major.
struct Square {
    d: usize,
    v: Vec<f64>,
}

impl Square {
    fn identity(d: usize) -> Self {
        let mut m = Self { d, v: vec![0.0; d * d] };
        for i in 0..d {
            m.set(i, i, 1.0);
        }
        m
    }

    fn zeros(d: usize) -> Self {
        Self { d, v: vec![0.0; d * d] }
    }

    fn at(&self, i: usize, j: usize) -> f64 {
        self.v[i * self.d + j]
    }

    fn set(&mut self, i: usize, j: usize, x: f64) {
        self.v[i * self.d + j] = x;
    }
}

/// F and Q of one step per mode (kernels.motion): standing, the position diffuses; walking, the OU
/// velocity, exactly discretized; the tracks' wandering offsets o decay and get their noise.
fn transition(mix: &Mixture, step: &ImmStep) -> ([Square; 2], [Square; 2]) {
    let d = mix.dim();
    let mut f = [Square::identity(d), Square::identity(d)];
    let mut q = [Square::zeros(d), Square::zeros(d)];
    let (dt, tau, s2) = (step.dt, step.tau, step.s2);
    f[STILL].set(1, 1, 0.0);
    q[STILL].set(0, 0, step.still_q * dt);
    let a = (-dt / tau).exp();
    f[WALK].set(0, 1, tau * (1.0 - a));
    f[WALK].set(1, 1, a);
    q[WALK].set(1, 1, s2 * (1.0 - a * a));
    q[WALK].set(0, 1, s2 * tau * ((1.0 - a) * (1.0 - a)));
    q[WALK].set(1, 0, q[WALK].at(0, 1));
    q[WALK].set(
        0,
        0,
        2.0 * s2 * tau * (dt - 2.0 * tau * (1.0 - a) + 0.5 * tau * (1.0 - a * a)),
    );
    for slot in mix.slots.values() {
        let k = slot.index + 1;
        let wander = slot.var * (1.0 - step.const_share) * (1.0 - step.ao * step.ao);
        for mode in [STILL, WALK] {
            f[mode].set(k, k, step.ao);
            q[mode].set(k, k, wander);
        }
    }
    (f, q)
}

/// The Gaussian with its velocity set to 0 (and that of the standing component given variance v),
/// per axis.
fn without_velocity(g: &Kalman, v: f64) -> Kalman {
    let d = g.dim();
    let mut out = g.clone(); // the incoming part of the other mode: a new Gaussian
    for ax in 0..2 {
        out.mean[ax * d + 1] = 0.0;
        for i in 0..d {
            out.cov[(ax * d + 1) * d + i] = 0.0;
            out.cov[(ax * d + i) * d + 1] = 0.0;
        }
        out.cov[(ax * d + 1) * d + 1] = v;
    }
    out
}

/// The linear prediction F, Q of one mode per axis (kernels.imm_predict).
fn linear(g: &Kalman, f: &Square, q: &Square) -> Kalman {
    let d = g.dim();
    let mut out = Kalman::zeros(d);
    let mut fp = Square::zeros(d);
    for ax in 0..2 {
        let m0 = &g.mean[ax * d..][..d];
        let p0 = &g.cov[ax * d * d..][..d * d];
        for i in 0..d {
            out.mean[ax * d + i] = (0..d).fold(0.0, |s, j| s + f.at(i, j) * m0[j]);
        }
        for i in 0..d {
            for l in 0..d {
                fp.set(i, l, (0..d).fold(0.0, |s, j| s + f.at(i, j) * p0[j * d + l]));
            }
        }
        for i in 0..d {
            for n in 0..d {
                let s = (0..d).fold(0.0, |s, l| s + fp.at(i, l) * f.at(n, l));
                out.cov[(ax * d + i) * d + n] = s + q.at(i, n);
            }
        }
    }
    out
}

/// One step of the mixture (module doc). With walls_and_doors (tiles there, a person and not a
/// ghost's source) walls reflect and walkers go through doors; keep_away false: the part gone
/// through a door is dropped next (a measurement follows), only its weight a changes. A part made
/// here is a new density.
pub fn predict(
    world: &World,
    mix: &mut Mixture,
    densities: &mut Densities,
    step: &ImmStep,
    walls_and_doors: bool,
    keep_away: bool,
) {
    let shapes = &world.shapes;
    let w = mix.weights();
    // the stays that go on, and the IMM's mode changes
    let stayed: Vec<f64> = mix.stay.iter().zip(&step.e_go).map(|(g, e)| g * e).collect();
    let p_stay = pairwise_sum(&stayed);
    let (f, q) = transition(mix, step);
    let (w_ss, w_sw) = (w[STILL] * p_stay, w[STILL] * (1.0 - p_stay));
    let (w_ww, w_ws) = (w[WALK] * (1.0 - step.p_stop), w[WALK] * step.p_stop);
    let [still, walking] = &mix.modes;
    let (tot_s, still0) = collapse(&[(w_ss, still), (w_ws, &without_velocity(walking, 0.0))]);
    let (tot_w, walk0) = collapse(&[(w_ww, walking), (w_sw, &without_velocity(still, step.s2))]);
    mix.modes = [
        linear(&still0, &f[STILL], &q[STILL]),
        linear(&walk0, &f[WALK], &q[WALK]),
    ];
    if tot_s > 0.0 {
        for ((g, &kept), &go_w) in mix.stay.iter_mut().zip(&stayed).zip(&shapes.go_w) {
            let kept = if p_stay > 0.0 { kept / p_stay } else { go_w };
            *g = (w_ss * kept + w_ws * go_w) / tot_s;
        }
        let q = step.q;
        for (ka, &prior) in mix.ka.iter_mut().zip(&shapes.ka_w) {
            *ka = (w_ss * ((1.0 - q) * *ka + q * prior) + w_ws * prior) / tot_s;
        }
    }
    let ln_or_none = |x: f64| if x > 0.0 { x.ln() } else { f64::NEG_INFINITY };
    let (mut l0, mut l1) = (ln_or_none(tot_s), ln_or_none(tot_w));
    let tot = log_add(l0, l1);
    (l0, l1) = (l0 - tot, l1 - tot);
    if walls_and_doors {
        reflect_at_walls(world, mix, &walk0, &f[WALK], &q[WALK]);
        let walking = weights2(l0, l1)[WALK];
        if let Some(rate) = door_rates(world, &mix.modes[WALK], walking) {
            let total = rate.iter().fold(0.0, |s, &x| s + x);
            let frac = -(-total * step.dt).exp_m1();
            let moved = (1.0 - mix.a) * walking * frac;
            if total > 0.0 && moved > 0.0 {
                if keep_away {
                    go_through_doors(densities, mix, moved, &rate, total);
                }
                l1 += if frac < 1.0 { (-frac).ln_1p() } else { f64::NEG_INFINITY };
                let tot = log_add(l0, l1);
                (l0, l1) = (l0 - tot, l1 - tot);
                mix.a += moved;
            }
        }
    }
    mix.logw = [l0, l1];
}

/// Walls reflect the walking component's motion (kernels.wall_moves; MODEL.md 3.2, 5.2): the
/// augmented unscented transform with the 16 cubature points of position, velocity and noise per
/// axis (Saerkkae & Svensson 2023 alg. 8.16 / 8.11), moved, mirrored at the walls (the velocity
/// too), matched again; the offsets follow by regression. Where the component already was stays as
/// it is, only the motion meets the walls. Only if a wall is within reach of the motion.
fn reflect_at_walls(world: &World, mix: &mut Mixture, before: &Kalman, f: &Square, q: &Square) {
    let d = before.dim();
    let after = &mix.modes[WALK];
    let from = before.pos();
    let to = after.pos();
    let p0 = |ax: usize, i: usize, j: usize| before.cov[(ax * d + i) * d + j];
    let (vx, vv) = (p0(0, 0, 0).max(p0(1, 0, 0)), p0(0, 1, 1).max(p0(1, 1, 1)));
    let s = 8f64.sqrt();
    let reach = (to[0] - from[0]).hypot(to[1] - from[1])
        + s * (vx.max(0.0).sqrt() + f.at(0, 1).abs() * vv.max(0.0).sqrt() + q.at(0, 0).max(0.0).sqrt());
    if world.clear(from, reach) {
        return;
    }
    // per point and axis (position, velocity) before the step and the noise
    let mut pre = [[[0.0; 2]; 2]; 16];
    let mut noise = [[[0.0; 2]; 2]; 16];
    for p in &mut pre {
        for (ax, v) in p.iter_mut().enumerate() {
            *v = [before.mean[ax * d], before.mean[ax * d + 1]];
        }
    }
    let chol = |c00: f64, c10: f64, c11: f64| {
        let l00 = c00.max(1e-24).sqrt();
        let l10 = c10 / l00;
        (l00, l10, (c11 - l10 * l10).max(0.0).sqrt())
    };
    let (l00, l10, l11) = chol(q.at(0, 0), q.at(1, 0), q.at(1, 1));
    let mut i = 0;
    for ax in 0..2 {
        let (c00, c10, c11) = chol(p0(ax, 0, 0), p0(ax, 1, 0), p0(ax, 1, 1));
        for j in 0..2 {
            for sign in [s, -s] {
                if j == 0 {
                    pre[i][ax][0] += sign * c00;
                    pre[i][ax][1] += sign * c10;
                    noise[i + 4][ax][0] += sign * l00;
                    noise[i + 4][ax][1] += sign * l10;
                } else {
                    pre[i][ax][1] += sign * c11;
                    noise[i + 4][ax][1] += sign * l11;
                }
                i += 1;
            }
        }
        i += 4;
    }
    let mut post = [[[0.0; 2]; 2]; 16];
    for ((post, pre), noise) in post.iter_mut().zip(&pre).zip(&noise) {
        for ax in 0..2 {
            for r in 0..2 {
                post[ax][r] = f.at(r, 0) * pre[ax][0] + f.at(r, 1) * pre[ax][1] + noise[ax][r];
            }
        }
    }
    let starts: [Point; 16] = pre.map(|p| [p[0][0], p[1][0]]);
    let ends: [Point; 16] = post.map(|p| [p[0][0], p[1][0]]);
    let (mut moved, mut normal) = ([[0.0; 2]; 16], [[0.0; 2]; 16]);
    if !world.walls.reflect(&starts, &ends, &mut moved, &mut normal) {
        return;
    }
    for ((post, moved), normal) in post.iter_mut().zip(&moved).zip(&normal) {
        let vn = (0..2).fold(0.0, |s, ax| s + post[ax][1] * normal[ax]);
        for ax in 0..2 {
            post[ax][0] = moved[ax];
            post[ax][1] -= 2.0 * vn * normal[ax];
        }
    }
    let after = &mut mix.modes[WALK];
    for ax in 0..2 {
        let mut mu = [0.0; 2];
        for p in &post {
            mu = [mu[0] + p[ax][0], mu[1] + p[ax][1]];
        }
        mu = mu.map(|m| m / 16.0);
        let mut cn = [[0.0; 2]; 2];
        for p in &post {
            let e = [p[ax][0] - mu[0], p[ax][1] - mu[1]];
            for (r, row) in cn.iter_mut().enumerate() {
                for (c, v) in row.iter_mut().enumerate() {
                    *v += e[r] * e[c];
                }
            }
        }
        cn = cn.map(|row| row.map(|v| v / 16.0));
        let (mean, cov) = (&after.mean[ax * d..][..d], &after.cov[ax * d * d..][..d * d]);
        let at = |i: usize, j: usize| cov[i * d + j];
        let (a00, a01, a10, a11) = (at(0, 0) + 1e-12, at(0, 1), at(1, 0), at(1, 1) + 1e-12);
        let det = a00 * a11 - a01 * a10;
        // G = (C^-1 P[core, :])^T, (d, 2)
        let g: Vec<[f64; 2]> = (0..d)
            .map(|k| {
                [
                    (a11 * at(0, k) - a01 * at(1, k)) / det,
                    (a00 * at(1, k) - a10 * at(0, k)) / det,
                ]
            })
            .collect();
        let dm = [mu[0] - mean[0], mu[1] - mean[1]];
        let new_mean: Vec<f64> = g
            .iter()
            .zip(mean)
            .map(|(gk, m)| m + (gk[0] * dm[0] + gk[1] * dm[1]))
            .collect();
        let dc = [
            [cn[0][0] - at(0, 0), cn[0][1] - at(0, 1)],
            [cn[1][0] - at(1, 0), cn[1][1] - at(1, 1)],
        ];
        let new_cov: Vec<f64> = (0..d * d)
            .map(|kl| {
                let (gk, gl) = (g[kl / d], g[kl % d]);
                cov[kl]
                    + (gk[0] * (dc[0][0] * gl[0] + dc[0][1] * gl[1]) + gk[1] * (dc[1][0] * gl[0] + dc[1][1] * gl[1]))
            })
            .collect();
        after.mean[ax * d..][..d].copy_from_slice(&new_mean);
        after.cov[ax * d * d..][..d * d].copy_from_slice(&new_cov);
    }
}

/// The walkers' rates (1/s) of walking into each region and out of the house (tiling.Tiling.
/// near_doors): their mass in the tiles next to a door, each tile as its small Gaussian, times the
/// tiles' rates. None if hardly anybody walks or there are no doors.
fn door_rates(world: &World, walking: &Kalman, share: f64) -> Option<Vec<f64>> {
    let tiles = &world.tiles;
    if share < 1e-9 || tiles.door_tiles.is_empty() {
        return None;
    }
    let ([mx, my], [vx, vy]) = (walking.pos(), walking.pos_var());
    let mut rate = vec![0.0; tiles.regions + 1];
    for door in &tiles.door_tiles {
        let (v0, v1) = (vx + door.var[0], vy + door.var[1]);
        let (e0, e1) = (door.center[0] - mx, door.center[1] - my);
        let dens = (-0.5 * (e0 * e0 / v0 + e1 * e1 / v1)).exp() / (2.0 * PI * (v0 * v1).sqrt());
        let f = (dens * door.area).min(1.0);
        for (r, &dr) in rate.iter_mut().zip(&door.rates) {
            *r += f * dr;
        }
    }
    Some(rate)
}

/// The share `moved` of the person goes to the part gone through a door: into the region behind the
/// door, its stay just begun, or out of the house. The part there is scaled to its weight a first;
/// without one, a new density is made.
fn go_through_doors(densities: &mut Densities, mix: &mut Mixture, moved: f64, rate: &[f64], total: f64) {
    let a = mix.a;
    let id = match mix.away {
        Some(id) => {
            densities.get_mut(id).scale(a / (a + moved));
            id
        }
        None => *mix.away.insert(densities.alloc(Kind::Person)),
    };
    let away = densities.get_mut(id);
    let (regions, ages) = (rate.len() - 1, away.shape.ages);
    for (r, &rate_r) in rate[..regions].iter().enumerate() {
        away.region[r * ages] += moved / (a + moved) * rate_r / total;
    }
    *away.out += moved / (a + moved) * rate[regions] / total;
}
