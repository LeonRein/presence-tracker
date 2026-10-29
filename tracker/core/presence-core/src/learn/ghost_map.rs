//! Where each sensor starts ghost tracks (MODEL.md 4.2): per sensor and cell of its own
//! frame a Poisson rate with a Gamma prior (Luber, Tipaldi & Arras 2011, eq. 6.8-6.13), learned online
//! by EM (Kantas et al. 2015): each track that ends counts at the cell where it began with the filter's
//! P(ghost) - judged without what the map said at the birth (Park et al. 2020, eq. 37-41) - against
//! the time the sensor watched. Counts fade with FORGET. A sensor's part is in its own frame (on the
//! floor, x to its right, y forward, its scale taken out): it follows a recalibrated sensor; only one
//! mounted elsewhere starts its part over.
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use serde_json::{Map, Value, json};

use super::LearnedError;
use crate::formats::{self, field, number, object};
use crate::geometry::Point;
use crate::params::Params;
use crate::plan::Plan;
use crate::plan::Pose;
use crate::vmath::pairwise_sum;
use crate::world::{Sight, raster};

/// m: side of a cell, in the sensor's frame (its scale taken out)
pub const CELL: f64 = 0.4;
/// m: the cells reach this far to either side and forward (the LD2450 reports up to 8 m)
pub const RANGE: f64 = 8.0;
const NX: usize = 40; // 2 RANGE / CELL
const NY: usize = 20; // RANGE / CELL
/// m: where a track begins scatters around its source with the spread of the sensor's offset
/// (MODEL.md 4.1): the counts are spread over the cells with this much (Park et al. 2020, sec. 1)
const SPREAD: f64 = 0.3;
/// s: what was learned fades with this time constant (assumed)
pub const FORGET: f64 = 14.0 * 86400.0;
/// s between two fadings
const DECAY_EVERY: f64 = 60.0;
/// sight above this counts as watched
const WATCHED: f64 = 0.05;

/// The expected number of ghost births per cell (NX x NY, row-major) and the same spread with SPREAD.
#[derive(Debug, Clone, PartialEq)]
struct Counts {
    raw: Vec<f64>,
    smooth: Vec<f64>,
}

impl Counts {
    fn new(raw: Vec<f64>) -> Self {
        let smooth = spread(&raw);
        Self { raw, smooth }
    }

    fn scale(&mut self, f: f64) {
        self.raw.iter_mut().for_each(|c| *c *= f);
        self.smooth = spread(&self.raw);
    }
}

/// The counts spread with a Gaussian of SPREAD, separable, the sum kept (GhostMap.smoothed:
/// np.convolve "same" along x, then along y).
fn spread(raw: &[f64]) -> Vec<f64> {
    let r = (3.0 * SPREAD / CELL).ceil() as i64;
    let k: Vec<f64> = (-r..=r)
        .map(|d| (-0.5 * (d as f64 * CELL / SPREAD).powi(2)).exp())
        .collect();
    let total: f64 = k.iter().sum();
    let k: Vec<f64> = k.iter().map(|v| v / total).collect();
    // along x (each column j), then along y (each row i)
    let mut along_x = vec![0.0; NX * NY];
    for j in 0..NY {
        let column: Vec<f64> = (0..NX).map(|i| raw[i * NY + j]).collect();
        for (i, v) in convolve_same(&column, &k).into_iter().enumerate() {
            along_x[i * NY + j] = v;
        }
    }
    along_x.chunks(NY).flat_map(|row| convolve_same(row, &k)).collect()
}

/// np.convolve(v, k, "same") of a symmetric kernel of odd length, summed in the order of v.
fn convolve_same(v: &[f64], k: &[f64]) -> Vec<f64> {
    let r = k.len() / 2;
    (0..v.len())
        .map(|at| {
            let (first, last) = (at.saturating_sub(r), (at + r).min(v.len() - 1));
            (first..=last).fold(0.0, |s, m| s + v[m] * k[m + r - at])
        })
        .collect()
}

/// One sensor's part of the map.
#[derive(Debug, Clone, PartialEq)]
struct Part {
    id: String,
    /// where its frame lies in the house now
    pose: Pose,
    /// s watched
    time: f64,
    /// none before its first counted birth
    counts: Option<Counts>,
    /// the rate anywhere it sees, while nothing changed
    total: Option<f64>,
}

impl Part {
    fn new(id: &str, pose: Pose) -> Self {
        Self {
            id: id.to_string(),
            pose,
            time: 0.0,
            counts: None,
            total: None,
        }
    }

    /// The cell of a house point in the sensor's frame (SensorConfig::to_local).
    fn cell(&self, [px, py]: Point) -> usize {
        let p = &self.pose;
        let h = p.heading.to_radians();
        let (dx, dy) = ((px - p.x) / p.scale, (py - p.y) / p.scale);
        let gy = dx * h.cos() + dy * h.sin();
        let gx = dx * h.sin() - dy * h.cos();
        let lx = if p.mirror { -gx } else { gx };
        let clip = |v: f64, n: usize| (v.floor() as i64).clamp(0, n as i64 - 1) as usize;
        clip((lx + RANGE) / CELL, NX) * NY + clip(gy / CELL, NY)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct GhostMap {
    /// per m^2 (of the floor) and s
    prior_rate: f64,
    /// s: weight of the prior, as watching time of a cell of CELL^2 m^2
    prior_time: f64,
    faded: Option<f64>,
    /// per sensor of the world, in its order (after follow)
    parts: Vec<Part>,
}

impl GhostMap {
    /// The map of the prior: the model's rate of ghost tracks everywhere, in each
    /// cell with the weight of ghost_prior_ghosts ghosts.
    pub fn from_prior(p: &Params) -> Self {
        let prior_rate = p.ghost_types.iter().map(|&[rate, _]| rate).sum::<f64>();
        Self {
            prior_rate,
            prior_time: p.ghost_prior_ghosts / (prior_rate * CELL * CELL),
            faded: None,
            parts: Vec::new(),
        }
    }

    /// Whether it was learned with the prior the model has now (Tracker.use_ghost_map).
    pub fn learned_with(&self, p: &Params) -> bool {
        let new = Self::from_prior(p);
        let close = |a: f64, b: f64| (a - b).abs() <= 1e-9 * a.abs().max(b.abs());
        close(self.prior_rate, new.prior_rate) && close(self.prior_time, new.prior_time)
    }

    pub fn prior_rate(&self) -> f64 {
        self.prior_rate
    }

    /// Follow the sensors of the plan: a recalibrated one keeps its part (in its own
    /// frame it moves with the new pose), one mounted elsewhere or new starts from the prior, the part
    /// of one no longer there goes.
    pub fn follow(&mut self, plan: &Plan) {
        let mut old = std::mem::take(&mut self.parts);
        self.parts = plan
            .sensors
            .iter()
            .map(|s| match old.iter().position(|p| p.id == s.id) {
                Some(k) if !s.pose.remounted(&old[k].pose) => Part {
                    pose: s.pose,
                    total: None,
                    ..old.swap_remove(k)
                },
                _ => Part::new(&s.id, s.pose),
            })
            .collect();
    }

    /// Posterior mean rate of ghost births per m^2 and s at a point for sensor si; a
    /// cell of its frame covers (CELL scale)^2 of the floor.
    pub fn rate(&self, si: usize, at: Point) -> f64 {
        let part = &self.parts[si];
        let n = part.counts.as_ref().map_or(0.0, |c| c.smooth[part.cell(at)]);
        let k0 = self.prior_rate * CELL * CELL * self.prior_time; // the prior's weight per cell, in ghosts
        (k0 + n) / (k0 / self.prior_rate + (CELL * part.pose.scale).powi(2) * part.time)
    }

    /// The rate anywhere sensor si sees (per s), kept until the map
    /// changes.
    pub fn total(&mut self, si: usize, sight: &Sight) -> f64 {
        if let Some(t) = self.parts[si].total {
            return t;
        }
        let r = sight.grid.raster();
        let rates: Vec<f64> = (0..r.nx)
            .flat_map(|i| (0..r.ny).map(move |j| (i, j)))
            .filter(|&(i, j)| sight.grid.values()[i * r.ny + j] > WATCHED)
            .map(|(i, j)| self.rate(si, r.centre(i, j)))
            .collect();
        let t = pairwise_sum(&rates) * raster::CELL * raster::CELL;
        self.parts[si].total = Some(t);
        t
    }

    /// s sensor si watched.
    pub fn watched(&self, si: usize) -> f64 {
        self.parts[si].time
    }

    pub fn add_watch(&mut self, si: usize, seconds: f64) {
        self.parts[si].time += seconds;
    }

    /// What was learned fades with FORGET, applied every DECAY_EVERY s; whether it did.
    pub fn fade(&mut self, t: f64) -> bool {
        let faded = *self.faded.get_or_insert(t);
        if t - faded < DECAY_EVERY {
            return false;
        }
        let f = (-(t - faded) / FORGET).exp();
        self.faded = Some(t);
        for part in &mut self.parts {
            part.time *= f;
            if let Some(c) = &mut part.counts {
                c.scale(f);
            }
            part.total = None;
        }
        true
    }

    /// A track of sensor si that began at a house point (read with the sensor's pose now), a ghost
    /// with this probability.
    pub fn add_birth(&mut self, si: usize, at: Point, p_ghost: f64) {
        let part = &mut self.parts[si];
        let cell = part.cell(at);
        let mut raw = part.counts.take().map_or_else(|| vec![0.0; NX * NY], |c| c.raw);
        raw[cell] += p_ghost;
        part.counts = Some(Counts::new(raw));
        part.total = None;
    }

    // ------------------------------------------------------------ the format

    pub fn to_json(&self) -> Value {
        let mut time = Map::new();
        let mut poses = Map::new();
        let mut count = Map::new();
        for p in &self.parts {
            let q = &p.pose;
            time.insert(p.id.clone(), json!(p.time));
            poses.insert(p.id.clone(), json!([q.x, q.y, q.heading, q.height, q.mirror, q.scale]));
            if let Some(c) = &p.counts {
                let rows: Vec<Vec<f64>> = c
                    .raw
                    .chunks(NY)
                    .map(|row| row.iter().map(|&v| formats::round_numpy(v, 4)).collect())
                    .collect();
                count.insert(p.id.clone(), json!(rows));
            }
        }
        json!({"cell": CELL, "range": RANGE, "prior_rate": self.prior_rate, "prior_time": self.prior_time,
               "time": time, "poses": poses, "count": count})
    }

    /// A saved map for the sensors of the plan (it follows them at once).
    pub fn from_json(v: &Value, plan: &Plan) -> Result<Self, LearnedError> {
        let m = object(v, "ghost_map")?;
        let close = |a: f64, b: f64| (a - b).abs() <= 1e-9 * a.abs().max(b.abs());
        if !(close(number(field(m, "cell")?, "cell", 0.0)?, CELL)
            && close(number(field(m, "range")?, "range", 0.0)?, RANGE))
        {
            return Err(LearnedError::Grid);
        }
        let positive = |name| number(field(m, name)?, name, f64::MIN_POSITIVE);
        let (prior_rate, prior_time) = (positive("prior_rate")?, positive("prior_time")?);
        let time = formats::per_sensor(m, "time", |_, x| number(x, "time", 0.0))?;
        let mut counts = formats::per_sensor(m, "count", counts_of)?;
        let parts = formats::per_sensor(m, "poses", |_, x| pose_of(x))?
            .into_iter()
            .map(|(id, pose)| Part {
                time: time.iter().find(|(k, _)| *k == id).map_or(0.0, |&(_, t)| t),
                counts: counts
                    .iter()
                    .position(|(k, _)| *k == id)
                    .map(|k| Counts::new(counts.swap_remove(k).1)),
                total: None,
                pose,
                id,
            })
            .collect();
        let mut map = Self {
            prior_rate,
            prior_time,
            faded: None,
            parts,
        };
        map.follow(plan);
        Ok(map)
    }
}

fn pose_of(v: &Value) -> Result<Pose, LearnedError> {
    let bad = LearnedError::Field("poses");
    let a = v.as_array().filter(|a| a.len() == 6).ok_or(bad.clone())?;
    let n = |k: usize| a[k].as_f64().filter(|x| x.is_finite()).ok_or(bad.clone());
    Ok(Pose {
        x: n(0)?,
        y: n(1)?,
        heading: n(2)?,
        height: n(3)?,
        mirror: a[4].as_bool().ok_or(bad.clone())?,
        scale: n(5).and_then(|s| if s > 0.0 { Ok(s) } else { Err(bad.clone()) })?,
    })
}

/// NX lists of NY counts, finite and not negative.
fn counts_of(id: &str, v: &Value) -> Result<Vec<f64>, LearnedError> {
    let size = || LearnedError::Part {
        field: "count",
        sensor: id.to_string(),
    };
    let rows = v.as_array().filter(|r| r.len() == NX).ok_or_else(size)?;
    let mut out = Vec::with_capacity(NX * NY);
    for row in rows {
        out.extend(formats::numbers(row, "count", NY, 0.0).map_err(|_| size())?);
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing;

    fn map() -> (GhostMap, crate::world::World) {
        let world = testing::flat();
        let mut g = GhostMap::from_prior(&world.params);
        g.follow(&world.plan);
        (g, world)
    }

    #[test]
    fn without_anything_learned_the_rate_is_the_priors() {
        let (g, w) = map();
        let prior: f64 = w.params.ghost_types.iter().map(|t| t[0]).sum();
        assert!((g.rate(0, [1.0, 2.0]) - prior).abs() < 1e-18);
        assert!((g.prior_time - w.params.ghost_prior_ghosts / (prior * 0.16)).abs() < 1e-6);
    }

    #[test]
    fn a_birth_counts_in_the_sensors_own_frame_spread_over_its_neighbours() {
        let (mut g, w) = map();
        let s = &w.plan.sensors[0].pose;
        // 2 m straight ahead of sensor 0
        let h = s.heading.to_radians();
        let ahead = [s.x + 2.0 * s.scale * h.cos(), s.y + 2.0 * s.scale * h.sin()];
        let before = g.rate(0, ahead);
        g.add_birth(0, ahead, 1.0);
        let c = g.parts[0].counts.as_ref().expect("counted");
        assert_eq!(c.raw[(RANGE / CELL) as usize * NY + 5], 1.0, "x 0, y 2 m: cell (20, 5)");
        assert!(
            (c.smooth.iter().sum::<f64>() - 1.0).abs() < 1e-12,
            "spreading keeps the sum"
        );
        assert!(c.smooth[20 * NY + 5] < 0.5 && c.smooth[21 * NY + 5] > 0.0);
        assert!(g.rate(0, ahead) > before);
        // a recalibrated sensor keeps its part, one mounted elsewhere starts over
        let mut plan = testing::flat().plan;
        plan.sensors[0].pose.scale = 1.1;
        g.follow(&plan);
        assert!(g.parts[0].counts.is_some());
        plan.sensors[0].pose.x += 1.0;
        g.follow(&plan);
        assert!(g.parts[0].counts.is_none());
    }

    #[test]
    fn it_fades_once_a_minute_and_only_then() {
        let (mut g, _) = map();
        g.add_watch(0, 100.0);
        g.add_birth(0, [1.0, 1.0], 0.5);
        assert!(!g.fade(1000.0) && !g.fade(1059.0));
        assert!(g.fade(1060.0));
        let f = (-60.0 / FORGET).exp();
        assert_eq!(g.watched(0), 100.0 * f);
        assert_eq!(
            g.parts[0].counts.as_ref().map(|c| c.raw.iter().sum::<f64>()),
            Some(0.5 * f)
        );
    }

    #[test]
    fn the_format_comes_back_and_a_broken_one_is_named() {
        let (mut g, w) = map();
        g.add_watch(0, 3600.0);
        g.add_birth(0, [1.0, 1.0], 0.75);
        let back = GhostMap::from_json(&g.to_json(), &w.plan).expect("readable");
        assert_eq!(back, g);
        let mut v = g.to_json();
        v["cell"] = json!(0.3);
        assert_eq!(GhostMap::from_json(&v, &w.plan), Err(LearnedError::Grid));
        let mut v = g.to_json();
        let id = w.plan.sensors[0].id.clone();
        v["count"][&id][3] = json!([1.0]);
        assert_eq!(
            GhostMap::from_json(&v, &w.plan),
            Err(LearnedError::Part {
                field: "count",
                sensor: id.clone()
            })
        );
        let mut v = g.to_json();
        v["time"][&id] = json!(-1.0);
        assert_eq!(GhostMap::from_json(&v, &w.plan), Err(LearnedError::Field("time")));
        let mut v = g.to_json();
        v["poses"][&id][4] = json!(1);
        assert_eq!(GhostMap::from_json(&v, &w.plan), Err(LearnedError::Field("poses")));
    }
}
