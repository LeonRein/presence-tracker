//! The data of one fit: walking points per sensor (for the floor plan), pairs and handovers; the poses as
//! functions of the parameters (per fitted sensor the change of its heading in rad and its ln scale).

use super::space::Space;
use super::{
    C, END_SPAN, EVERY, HANDOVER, MAX_GAP, MIN_RANGE, MIN_SPEED, PRIOR_DEG, Row, TAU_V, VAR_V, heading_of, thin,
};
use crate::config::{Config, Mount};
use crate::vmath::{py_hypot, py_round};

/// The walking points of one sensor: one per EVERY s and LD2450 track, at least MIN_RANGE away on the floor.
#[derive(Debug)]
pub(super) struct Points {
    pub(super) si: usize,
    pub(super) t: Vec<f64>,
    /// on the floor in the sensor's frame (x + iy), before its scale
    pub(super) z: Vec<C>,
    /// the measurement error there (MODEL.md 4.1, the mean of along and across)
    pub(super) sigma: Vec<f64>,
    /// its track, 0..ntracks
    pub(super) track: Vec<usize>,
    pub(super) ntracks: usize,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum Kind {
    /// the same moving target in both sensors at once
    Pair,
    /// one sensor's track ended, the other's started
    Handover,
}

/// Points of two sensors that are one place (za of sensor a, zb of b), with their error.
#[derive(Debug)]
pub(super) struct Pair {
    pub(super) kind: Kind,
    pub(super) a: usize,
    pub(super) b: usize,
    pub(super) za: Vec<C>,
    pub(super) zb: Vec<C>,
    pub(super) t: Vec<f64>,
    pub(super) sigma: Vec<f64>,
}

/// The ends of a sensor's walking tracks: their starts by time, their ends with the velocity of their
/// last second.
struct Ends {
    t_start: Vec<f64>,
    z_start: Vec<C>,
    t_end: Vec<f64>,
    z_end: Vec<C>,
    v_end: Vec<C>,
}

#[derive(Debug)]
pub(super) struct Problem<'a> {
    pub(super) config: &'a Config,
    pub(super) space: &'a Space,
    /// the sensors whose heading and scale are fitted (indices into config.sensors), in this order in the
    /// parameters
    pub(super) fit: Vec<usize>,
    /// per sensor: its x axis to the left
    mirror: Vec<bool>,
    sigma0: f64,
    sigma1: f64,
    /// per sensor with data (by id), its rows in time order
    pub(super) data: &'a [(usize, Vec<Row>)],
    /// per sensor with data, in the same order
    pub(super) points: Vec<Points>,
    pub(super) pairs: Vec<Pair>,
}

/// The rows' points on the floor of a sensor with this mirror.
fn on_floor(config: &Config, si: usize, mirror: bool, rows: &[Row]) -> Vec<C> {
    let mount = Mount {
        mirror,
        ..config.sensors[si].mount()
    };
    let h = config.params.target_height;
    rows.iter()
        .map(|r| {
            let [x, y] = mount.on_floor(r.local, h);
            C::new(x, y)
        })
        .collect()
}

/// The indices of a sensor's walking points (walking, far enough, thinned per track), and their z.
fn walking(config: &Config, si: usize, mirror: bool, rows: &[Row]) -> (Vec<usize>, Vec<C>) {
    let z = on_floor(config, si, mirror, rows);
    let idx: Vec<usize> = (0..rows.len())
        .filter(|&i| rows[i].v.abs() >= MIN_SPEED && z[i].abs() >= MIN_RANGE)
        .collect();
    let t: Vec<f64> = idx.iter().map(|&i| rows[i].t).collect();
    let seg: Vec<f64> = idx.iter().map(|&i| rows[i].seg).collect();
    (thin(&t, &seg, EVERY).into_iter().map(|k| idx[k]).collect(), z)
}

/// The times of a sensor's walking points with its configured mirror (the progress before a solve).
pub(super) fn walking_times(config: &Config, si: usize, rows: &[Row]) -> Vec<f64> {
    let (idx, _) = walking(config, si, config.sensors[si].mirror, rows);
    idx.into_iter().map(|i| rows[i].t).collect()
}

impl<'a> Problem<'a> {
    /// The problem of the data (per sensor in time order) for the sensors `fit`; `mirrors`: (sensor,
    /// mirror) instead of the configured ones.
    pub(super) fn new(
        config: &'a Config,
        space: &'a Space,
        data: &'a [(usize, Vec<Row>)],
        fit: Vec<usize>,
        mirrors: &[(usize, bool)],
    ) -> Self {
        let p = &config.params;
        let mirror = (0..config.sensors.len())
            .map(|si| {
                mirrors
                    .iter()
                    .find(|&&(s, _)| s == si)
                    .map_or(config.sensors[si].mirror, |&(_, m)| m)
            })
            .collect();
        let mut prob = Self {
            config,
            space,
            fit,
            mirror,
            // MODEL.md 4.1: the error along and across the line of sight, per axis their mean
            sigma0: (p.range_sigma_base + p.lateral_sigma_base) / 2.0,
            sigma1: (p.range_sigma_slope + p.lateral_sigma_slope) / 2.0,
            data,
            points: Vec::new(),
            pairs: Vec::new(),
        };
        prob.points = data.iter().map(|(si, rows)| prob.points_of(*si, rows)).collect();
        for (k, (a, _)) in data.iter().enumerate() {
            for (b, _) in &data[k + 1..] {
                if let Some(q) = prob.pairs_of(*a, *b) {
                    prob.pairs.push(q);
                }
            }
        }
        let ends: Vec<Ends> = data.iter().map(|(si, rows)| prob.ends(*si, rows)).collect();
        for (ka, (a, _)) in data.iter().enumerate() {
            for (kb, (b, _)) in data.iter().enumerate() {
                if ka != kb
                    && let Some(q) = prob.handovers(&ends[ka], &ends[kb], *a, *b)
                {
                    prob.pairs.push(q);
                }
            }
        }
        prob
    }

    /// The rows' points on the floor with this problem's mirror of the sensor.
    pub(super) fn z(&self, si: usize, rows: &[Row]) -> Vec<C> {
        on_floor(self.config, si, self.mirror[si], rows)
    }

    pub(super) fn sigma(&self, z: C) -> f64 {
        self.sigma0 + self.sigma1 * z.abs()
    }

    fn points_of(&self, si: usize, rows: &[Row]) -> Points {
        let (idx, z) = walking(self.config, si, self.mirror[si], rows);
        let mut segs: Vec<f64> = idx.iter().map(|&i| rows[i].seg).collect();
        segs.sort_by(f64::total_cmp);
        segs.dedup();
        let z: Vec<C> = idx.iter().map(|&i| z[i]).collect();
        Points {
            si,
            t: idx.iter().map(|&i| rows[i].t).collect(),
            sigma: z.iter().map(|&z| self.sigma(z)).collect(),
            z,
            track: idx
                .iter()
                .map(|&i| segs.partition_point(|&s| s < rows[i].seg))
                .collect(),
            ntracks: segs.len(),
        }
    }

    /// One moving target in both sensors at once: a interpolated to b's frame times within one of a's
    /// tracks (frames at most MAX_GAP apart), one pair per EVERY s.
    fn pairs_of(&self, a: usize, b: usize) -> Option<Pair> {
        let single = |si: usize| -> Option<Vec<Row>> {
            let rows = &self.data.iter().find(|(s, _)| *s == si)?.1;
            Some(rows.iter().filter(|r| r.single).copied().collect())
        };
        let (rows_a, rows_b) = (single(a)?, single(b)?);
        if rows_a.len() < 2 || rows_b.is_empty() {
            return None;
        }
        let (za_all, zb_all) = (self.z(a, &rows_a), self.z(b, &rows_b));
        let (mut za, mut zb, mut tb) = (Vec::new(), Vec::new(), Vec::new());
        for (rb, &z) in rows_b.iter().zip(&zb_all) {
            let k = rows_a.partition_point(|r| r.t < rb.t).clamp(1, rows_a.len() - 1);
            let (t0, t1) = (rows_a[k - 1].t, rows_a[k].t);
            if rb.t > t0 && rb.t <= t1 && t1 - t0 <= MAX_GAP && rows_a[k - 1].seg == rows_a[k].seg {
                let w = (rb.t - t0) / f64::max(t1 - t0, 1e-6);
                za.push(za_all[k - 1] * (1.0 - w) + za_all[k] * w);
                zb.push(z);
                tb.push(rb.t);
            }
        }
        if za.len() < 3 {
            return None;
        }
        let keep: Vec<usize> = thin(&tb, &vec![0.0; tb.len()], EVERY)
            .into_iter()
            .filter(|&i| za[i].abs() >= MIN_RANGE && zb[i].abs() >= MIN_RANGE)
            .collect();
        if keep.len() < 3 {
            return None;
        }
        let za: Vec<C> = keep.iter().map(|&i| za[i]).collect();
        let zb: Vec<C> = keep.iter().map(|&i| zb[i]).collect();
        Some(Pair {
            kind: Kind::Pair,
            a,
            b,
            sigma: za
                .iter()
                .zip(&zb)
                .map(|(&x, &y)| py_hypot(self.sigma(x), self.sigma(y)))
                .collect(),
            t: keep.iter().map(|&i| tb[i]).collect(),
            za,
            zb,
        })
    }

    /// Per walking LD2450 track of the sensor: its first point, its last point and the velocity over its
    /// last second (in the sensor frame).
    fn ends(&self, si: usize, rows: &[Row]) -> Ends {
        let z = self.z(si, rows);
        let mut ok: Vec<usize> = (0..rows.len())
            .filter(|&i| rows[i].v.abs() >= MIN_SPEED && z[i].abs() >= MIN_RANGE)
            .collect();
        ok.sort_by(|&i, &j| {
            rows[i]
                .seg
                .total_cmp(&rows[j].seg)
                .then(rows[i].t.total_cmp(&rows[j].t))
        });
        let (t, z): (Vec<f64>, Vec<C>) = ok.iter().map(|&i| (rows[i].t, z[i])).unzip();
        let mut runs: Vec<(usize, usize)> = Vec::new(); // (first, last) per track
        for (k, &i) in ok.iter().enumerate() {
            match runs.last_mut() {
                Some(run) if rows[ok[run.0]].seg == rows[i].seg => run.1 = k,
                _ => runs.push((k, k)),
            }
        }
        let mut starts: Vec<usize> = runs.iter().map(|&(f, _)| f).collect();
        starts.sort_by(|&a, &b| t[a].total_cmp(&t[b]));
        let mut ends = Ends {
            t_start: starts.iter().map(|&f| t[f]).collect(),
            z_start: starts.iter().map(|&f| z[f]).collect(),
            t_end: Vec::new(),
            z_end: Vec::new(),
            v_end: Vec::new(),
        };
        for &(f, j) in &runs {
            let i = f + t[f..=j].partition_point(|&x| x < t[j] - END_SPAN.1);
            if t[j] - t[i] >= END_SPAN.0 {
                ends.t_end.push(t[j]);
                ends.z_end.push(z[j]);
                ends.v_end.push((z[j] - z[i]) / (t[j] - t[i]));
            }
        }
        ends
    }

    /// A track of a ends, one of b starts within HANDOVER s: b's start against a's end carried forward by
    /// the walking model. Ornstein-Uhlenbeck velocity with the measured velocity at the end (Sarkka &
    /// Solin 2019, ex. 6.2): mean shift v tau (1 - e^(-dt/tau)), variance per axis VAR_V tau^2 (2 dt/tau -
    /// 3 + 4 e^(-dt/tau) - e^(-2 dt/tau)).
    fn handovers(&self, ea: &Ends, eb: &Ends, a: usize, b: usize) -> Option<Pair> {
        let (mut za, mut zb, mut tt, mut var) = (Vec::new(), Vec::new(), Vec::new(), Vec::new());
        for ((&te, &ze), &ve) in ea.t_end.iter().zip(&ea.z_end).zip(&ea.v_end) {
            let lo = eb.t_start.partition_point(|&s| s <= te);
            let hi = eb.t_start.partition_point(|&s| s < te + HANDOVER);
            for j in lo..hi {
                let x = (eb.t_start[j] - te) / TAU_V;
                za.push(ze + ve * TAU_V * (1.0 - (-x).exp()));
                zb.push(eb.z_start[j]);
                tt.push(eb.t_start[j]);
                var.push(VAR_V * TAU_V.powi(2) * (2.0 * x - 3.0 + 4.0 * (-x).exp() - (-2.0 * x).exp()));
            }
        }
        if za.len() < 3 {
            return None;
        }
        let sigma = (0..za.len())
            .map(|i| (self.sigma(za[i]).powi(2) + self.sigma(zb[i]).powi(2) + var[i]).sqrt())
            .collect();
        Some(Pair {
            kind: Kind::Handover,
            a,
            b,
            za,
            zb,
            t: tt,
            sigma,
        })
    }

    /// The position of a fitted sensor among the parameters (2 per sensor).
    pub(super) fn slot(&self, si: usize) -> Option<usize> {
        self.fit.iter().position(|&s| s == si).map(|k| 2 * k)
    }

    /// (c, p): a floor point z in the sensor's frame lies at p + c z in the house; `scale` instead of the
    /// one of phi.
    pub(super) fn pose(&self, phi: &[f64], si: usize, scale: Option<f64>) -> (C, C) {
        let s = &self.config.sensors[si];
        let (mut h, mut k) = ((s.heading - 90.0).to_radians(), s.scale);
        if let Some(i) = self.slot(si) {
            h += phi[i];
            k = phi[i + 1].exp();
        }
        let k = scale.unwrap_or(k);
        (C::new(k * h.cos(), k * h.sin()), C::new(s.x, s.y))
    }

    /// The points z of a sensor in the house.
    pub(super) fn world(&self, phi: &[f64], si: usize, z: &[C], scale: Option<f64>) -> Vec<C> {
        let (c, p) = self.pose(phi, si, scale);
        z.iter().map(|&z| p + c * z).collect()
    }

    pub(super) fn lnscale(&self, phi: &[f64], si: usize) -> f64 {
        self.slot(si)
            .map_or_else(|| self.config.sensors[si].scale.ln(), |i| phi[i + 1])
    }

    /// Heading around the drawn one; ln scale ~ N(mu, tau^2), the scales of the LD2450s.
    pub(super) fn prior_residuals(&self, phi: &[f64], mu: f64, tau: f64) -> Vec<f64> {
        let heading = PRIOR_DEG.to_radians();
        phi.as_chunks::<2>()
            .0
            .iter()
            .flat_map(|q| [q[0] / heading, (q[1] - mu) / tau])
            .collect()
    }

    /// The drawn poses.
    pub(super) fn start(&self) -> Vec<f64> {
        self.fit
            .iter()
            .flat_map(|&si| [0.0, self.config.sensors[si].scale.ln()])
            .collect()
    }

    /// The heading (0.01 deg) and scale (1e-4) of a sensor at phi. Rounded for the model: taken over, they are
    /// the configuration's values (the geometry key reads them), and the plan's and the pairs' headings are
    /// compared at this resolution.
    pub(super) fn describe(&self, phi: &[f64], si: usize) -> (f64, f64) {
        let (c, _) = self.pose(phi, si, None);
        (py_round(heading_of(c), 2), py_round(c.abs(), 4))
    }
}
