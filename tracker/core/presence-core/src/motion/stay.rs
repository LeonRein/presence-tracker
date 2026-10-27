//! Standing and getting up over the tiles (MODEL.md 3.1, 4.1; hidden.Hidden.move).

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

/// One step of the stays in place (kernels.hidden_stays): walkers stop, still people get up, the
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
