//! The rates at which a sensor starts a track on somebody (MODEL.md 4.1): rho P_D(r) times its
//! sight, less next to the targets it tracks (filter.Tracker._rates, kernels.sensor_rates).

use std::f64::consts::{LN_2, PI};

use crate::geometry::Point;
use crate::params::{P_FA, Params, STILL, WALK};
use crate::sensors::SensorSet;
use crate::world::Sight;

/// A live track of a sensor as the rates see it: where it measures its target, or where it holds
/// it when it lost it.
#[derive(Debug, Clone, Copy)]
pub struct TrackSpot {
    pub held: bool,
    pub at: Point,
}

#[derive(Debug)]
pub struct RateModel {
    /// rho and r50 per mode [STILL, WALK]
    acquire: [[f64; 2]; 2],
    /// the SNR at which a Swerling I target is detected half the time (filtermodel.S50)
    s50: f64,
    /// m: two people this far apart are resolved half of the time (range, cross)
    res: [f64; 2],
    find_radius: f64,
}

fn sq(x: f64) -> f64 {
    x * x
}

impl RateModel {
    pub fn new(p: &Params) -> Self {
        Self {
            acquire: p.acquire,
            s50: P_FA.ln() / 0.5f64.ln() - 1.0,
            res: [p.res_range, p.res_cross],
            find_radius: p.find_radius,
        }
    }

    /// Swerling I detection probability at distance r, half at r50 (radar equation, SNR ~ r^-4;
    /// kernels._pd).
    fn pd(&self, r: f64, r50: f64) -> f64 {
        let r = r.max(0.3);
        P_FA.powf(1.0 / (1.0 + self.s50 * (r50 / r).powi(4)))
    }

    /// The rate without the sensor's live tracks at a point, for walkers or still people (mode).
    fn base(&self, sight: &Sight, p: Point, mode: usize) -> f64 {
        let r = (p[0] - sight.at[0]).hypot(p[1] - sight.at[1]);
        let [rho, r50] = self.acquire[mode];
        rho * self.pd(r, r50) * sight.grid.at(p)
    }

    /// The share left at a point given the sensor's live tracks: next to a target it measures,
    /// somebody is merged into it (resolution, Svensson 2012); near where it holds a lost target it
    /// finds that one again.
    fn mask(&self, sight: &Sight, p: Point, tracks: &[TrackSpot]) -> f64 {
        if tracks.is_empty() {
            return 1.0;
        }
        let [sx, sy] = sight.at;
        let (dx, dy) = (p[0] - sx, p[1] - sy);
        let r = dx.hypot(dy);
        let th = dy.atan2(dx);
        let mut mask = 1.0;
        for spot in tracks {
            if spot.held {
                let (ex, ey) = (p[0] - spot.at[0], p[1] - spot.at[1]);
                mask *= 1.0 - (-0.5 * (ex * ex + ey * ey) / sq(self.find_radius)).exp();
            } else {
                let (zx, zy) = (spot.at[0] - sx, spot.at[1] - sy);
                let (rz, tz) = (zx.hypot(zy), zy.atan2(zx));
                let dr = r - rz;
                let mut a = th - tz; // in (-2 pi, 2 pi): to (-pi, pi]
                if a > PI {
                    a -= 2.0 * PI;
                } else if a <= -PI {
                    a += 2.0 * PI;
                }
                let dc = 0.5 * (r + rz) * a;
                mask *= 1.0 - (-LN_2 * (sq(dr / self.res[0]) + sq(dc / self.res[1]))).exp();
            }
        }
        mask
    }

    /// The rate (1/s) at which the sensor starts a track on somebody at p, walking or still (mode).
    pub fn rate_at(&self, sight: &Sight, p: Point, mode: usize, tracks: &[TrackSpot]) -> f64 {
        self.base(sight, p, mode) * self.mask(sight, p, tracks)
    }

    /// The rates at which the sensors `users` start a track on a person (kernels.gauss_rates), summed
    /// over them in the order of their indices: the still people's at the standing component's sigma
    /// points, the walkers' at the walking one's.
    pub fn mixture_rates(
        &self,
        sights: &[Sight],
        spots: &[Vec<TrackSpot>],
        users: SensorSet,
        pts: &[[Point; 5]; 2],
    ) -> [[f64; 5]; 2] {
        let mut out = [[0.0; 5]; 2];
        for si in users.iter() {
            for mode in [STILL, WALK] {
                for (o, &p) in out[mode].iter_mut().zip(&pts[mode]) {
                    *o += self.rate_at(&sights[si], p, mode, &spots[si]);
                }
            }
        }
        out
    }

    /// [STILL, WALK] rates without the live tracks at the points.
    pub fn base_rates(&self, sight: &Sight, points: &[Point]) -> [Vec<f64>; 2] {
        [STILL, WALK].map(|mode| points.iter().map(|&p| self.base(sight, p, mode)).collect())
    }

    /// [STILL, WALK] rates at the points given the rates without the tracks there (base_rates).
    pub fn masked_rates(
        &self,
        sight: &Sight,
        points: &[Point],
        base: &[Vec<f64>; 2],
        tracks: &[TrackSpot],
    ) -> [Vec<f64>; 2] {
        let mask: Vec<f64> = points.iter().map(|&p| self.mask(sight, p, tracks)).collect();
        [STILL, WALK].map(|mode| base[mode].iter().zip(&mask).map(|(b, m)| b * m).collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::world::raster::{Grid, Raster};

    fn sight() -> Sight {
        let raster = Raster {
            x0: -5.0,
            y0: -5.0,
            nx: 100,
            ny: 100,
        };
        Sight::new([0.0, 0.0], [0.0, 0.0], Grid::new(raster, vec![1.0; 10_000]), Vec::new())
    }

    #[test]
    fn half_the_walkers_are_found_at_r50() {
        let m = RateModel::new(&Params::default());
        let s = sight();
        let r = m.rate_at(&s, [4.55, 0.0], WALK, &[]);
        assert!((r - 0.51 * 0.5).abs() < 1e-12, "{r}");
    }

    #[test]
    fn nobody_is_found_on_a_measured_target() {
        let m = RateModel::new(&Params::default());
        let s = sight();
        let on = [TrackSpot {
            held: false,
            at: [2.0, 1.0],
        }];
        assert_eq!(m.rate_at(&s, [2.0, 1.0], STILL, &on), 0.0);
        let far = m.rate_at(&s, [-2.0, -3.0], STILL, &on);
        assert!((far / m.rate_at(&s, [-2.0, -3.0], STILL, &[]) - 1.0).abs() < 1e-9);
    }
}
