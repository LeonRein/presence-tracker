//! What a person puts into the cells (ld2410.expected, MODEL.md 4.3): the profile measured 6.10.
//! per mode, times the beam.

use super::{CELLS, MOVING, centre};
use crate::params::Params;

/// The expected energy a person puts into the 16 cells at `angle` degrees off the axis, `slant` m
/// and with `sight` (0..1), standing or walking (mode; ld2410.expected_rows): per cell v x A r^-n x
/// (the Gaussian main lobe, and beyond the centre the tail mixed in).
pub fn expected(p: &Params, mode: usize, angle: f64, slant: f64, sight: f64) -> [f64; CELLS] {
    let [full, none] = p.ld_beam;
    let v = ((none - angle) / (none - full)).clamp(0.0, 1.0) * sight;
    let r = if slant < 0.3 { 0.3 } else { slant };
    let (pm, ps) = (&p.ld_moving[mode], &p.ld_still[mode]);
    let (wm, ws) = (r.powf(-pm.n), r.powf(-ps.n));
    std::array::from_fn(|c| {
        let (q, w) = if c < MOVING { (pm, wm) } else { (ps, ws) };
        let d = centre(c) - r - q.delta;
        let t = d / q.sigma;
        let main = (-0.5 * (t * t)).exp();
        let k = if d <= 0.0 {
            main
        } else {
            (1.0 - q.tail) * main + q.tail * (-d / q.ell).exp()
        };
        v * (q.a * w * k)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::params::{STILL, WALK};

    #[test]
    fn the_profile_peaks_at_the_persons_gate_and_falls_with_distance() {
        // test_ld2410: a person at 3 m puts most into gate 3 (moving) and falls off with distance
        let p = Params::default();
        for mode in [STILL, WALK] {
            let near = expected(&p, mode, 0.0, 2.0, 1.0);
            let far = expected(&p, mode, 0.0, 4.0, 1.0);
            let peak = (0..MOVING).max_by(|&a, &b| near[a].total_cmp(&near[b])).expect("cells");
            assert!(peak == 2 || peak == 3, "{peak}");
            assert!(far[..MOVING].iter().sum::<f64>() < near[..MOVING].iter().sum::<f64>());
        }
        // out of the beam: nothing
        assert!(expected(&p, STILL, 80.0, 2.0, 1.0).iter().all(|&x| x == 0.0));
        assert!(expected(&p, STILL, 0.0, 2.0, 0.0).iter().all(|&x| x == 0.0));
    }
}
