//! Standing and getting up over the tiles (MODEL.md 3.1, 4.1).
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use crate::params::Shapes;

/// What a step dt changes in the stays, the same for every tile: walkers stop with stop_p, still
/// people get up with up_l per kind of stay, the detectability is drawn anew within a stay with q.
#[derive(Debug)]
pub struct StayStep {
    pub stop: f64,
    pub up: Vec<f64>,
    pub switch: f64,
}

impl StayStep {
    pub fn new(stop_rate: f64, kappa_switch: f64, shapes: &Shapes, dt: f64) -> Self {
        Self {
            stop: -(-stop_rate * dt).exp_m1(),
            up: shapes.go.iter().map(|&go| -(-go * dt).exp_m1()).collect(),
            switch: -(-kappa_switch * dt).exp_m1(),
        }
    }
}

/// One step of the stays in place: walkers stop, still people get up, the
/// detectability changes now and then within a stay; who stops begins a fresh stay (its kind from
/// go_w, its detectability from kappa_w). walk (n), still (L x K x n). With a measured amplitude,
/// keep (K x n) is multiplied by the share of the still mass after the step that is no fresh draw.
/// Row by row over the tiles: per tile the same arithmetic in the same order as the kernel.
pub fn stays(walk: &mut [f64], still: &mut [f64], keep: Option<&mut [f64]>, step: &StayStep, shapes: &Shapes) {
    let (kinds, levels, n) = (shapes.kinds(), shapes.levels(), walk.len());
    let row = |l: usize, k: usize| (l * levels + k) * n..(l * levels + k + 1) * n;
    let (q, up) = (step.switch, &step.up);
    // per kind of stay the still mass per tile
    let mut s = vec![0.0; kinds * n];
    for (l, sl) in s.chunks_exact_mut(n.max(1)).enumerate() {
        for k in 0..levels {
            for (t, &x) in sl.iter_mut().zip(&still[row(l, k)]) {
                *t += x;
            }
        }
    }
    let stop: Vec<f64> = walk.iter().map(|w| w * step.stop).collect();
    let (mut rise, mut fresh_all) = (vec![0.0; n], vec![0.0; n]);
    for (l, sl) in s.chunks_exact(n.max(1)).enumerate() {
        for c in 0..n {
            rise[c] += up[l] * sl[c];
            fresh_all[c] += q * (sl[c] - up[l] * sl[c]) + shapes.go_w[l] * stop[c];
        }
    }
    for c in 0..n {
        walk[c] = walk[c] - stop[c] + rise[c];
    }
    if let Some(keep) = keep {
        for (k, kr) in keep.chunks_exact_mut(n.max(1)).enumerate() {
            let mut kept = vec![0.0; n];
            for l in 0..kinds {
                for (t, &x) in kept.iter_mut().zip(&still[row(l, k)]) {
                    *t += x * (1.0 - up[l]) * (1.0 - q);
                }
            }
            for c in 0..n {
                let tot = kept[c] + shapes.kappa_w[k] * fresh_all[c];
                if tot > 0.0 {
                    kr[c] *= kept[c] / tot;
                }
            }
        }
    }
    let mut fresh = vec![0.0; n];
    for (l, sl) in s.chunks_exact(n.max(1)).enumerate() {
        let f = (1.0 - up[l]) * (1.0 - q);
        for c in 0..n {
            fresh[c] = q * (sl[c] - up[l] * sl[c]) + shapes.go_w[l] * stop[c];
        }
        for k in 0..levels {
            for (x, &fr) in still[row(l, k)].iter_mut().zip(&fresh) {
                *x = *x * f + shapes.kappa_w[k] * fr;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::params::Params;

    const STOP: f64 = 0.425;
    const SWITCH: f64 = 0.05;
    const DT: f64 = 0.7;

    fn close(a: f64, b: f64) -> bool {
        (a - b).abs() <= 1e-12 * b.abs().max(1e-300)
    }

    #[test]
    fn on_one_tile_a_walker_stops_into_a_fresh_stay_as_worked_out_by_hand() {
        // MODEL.md 3.1: walking goes on with exp(-mu dt); who stopped begins a stay of kind l with
        // go_w[l] and detectability k with kappa_w[k]
        let s = Shapes::new(&Params::default()); // weights that differ by kind and level
        let step = StayStep::new(STOP, SWITCH, &s, DT);
        let (mut walk, mut still) = (vec![1.0], vec![0.0; s.kinds() * s.levels()]);
        stays(&mut walk, &mut still, None, &step, &s);
        let stopped = 1.0 - (-STOP * DT).exp();
        assert!(close(walk[0], (-STOP * DT).exp()), "{walk:?}");
        for l in 0..s.kinds() {
            for k in 0..s.levels() {
                let want = s.go_w[l] * s.kappa_w[k] * stopped;
                assert!(close(still[l * s.levels() + k], want), "{l} {k}: {still:?}");
            }
        }
    }

    #[test]
    fn on_one_tile_a_still_person_gets_up_by_their_kind_and_redraws_their_detectability() {
        // MODEL.md 3.1, 5.3: of a stay of kind l at level k, 1 - exp(-go_l dt) gets up; of the rest
        // q = 1 - exp(-kappa_switch dt) draws its level anew from kappa_w, the kind stays
        let s = Shapes::new(&Params::default()); // weights that differ by kind and level
        let step = StayStep::new(STOP, SWITCH, &s, DT);
        let (l, k) = (1, 0);
        let mut still = vec![0.0; s.kinds() * s.levels()];
        still[l * s.levels() + k] = 1.0;
        let mut walk = vec![0.0];
        stays(&mut walk, &mut still, None, &step, &s);
        let (sits, q) = ((-s.go[l] * DT).exp(), 1.0 - (-SWITCH * DT).exp());
        assert!(close(walk[0], 1.0 - sits), "{walk:?}");
        for j in 0..s.levels() {
            let kept = if j == k { sits * (1.0 - q) } else { 0.0 };
            assert!(
                close(still[l * s.levels() + j], kept + sits * q * s.kappa_w[j]),
                "{j}: {still:?}"
            );
        }
        let others = still.iter().enumerate().filter(|(i, _)| i / s.levels() != l);
        assert!(
            others.into_iter().all(|(_, &x)| x == 0.0),
            "the other kinds get nothing"
        );
    }
}
