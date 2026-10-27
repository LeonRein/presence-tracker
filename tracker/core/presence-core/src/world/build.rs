//! The world built from the plan (world.World, filter.Tracker._grid, _spread_sight, _ld_view,
//! _ld_tiles, sensormodel.SensorModel, tiling.Tiling.observed): the 0.1 m raster of places and rooms,
//! the distance to the walls, and per sensor its sight of a person and its LD2410C's.

use super::raster::{CELL, Grid, Raster};
use super::{LdSight, LdTiles, Sight, Tiles};
use crate::config::{Config, SensorConfig};
use crate::geometry::{Point, Walls};
use crate::ld2410::profile::expected;
use crate::ld2410::{CELLS, FLOOR, GATE};
use crate::params::{Params, STILL, WALK};
use crate::plan::Plan;
use crate::vmath::py_hypot;

/// The raster around the rooms (the outside rooms too): a cell beyond them on every side (World).
pub(super) fn raster_of(config: &Config) -> Raster {
    let bounds: Vec<[f64; 4]> = config.rooms().map(|z| z.shape.bounds()).collect();
    if bounds.is_empty() {
        return Raster {
            x0: 0.0,
            y0: 0.0,
            nx: 1,
            ny: 1,
        };
    }
    let lo = |k: usize| bounds.iter().map(|b| b[k]).fold(f64::INFINITY, f64::min);
    let hi = |k: usize| bounds.iter().map(|b| b[k]).fold(f64::NEG_INFINITY, f64::max);
    let (x0, y0) = (lo(0) - CELL, lo(1) - CELL);
    Raster {
        x0,
        y0,
        nx: ((hi(2) - x0) / CELL).ceil() as usize + 2,
        ny: ((hi(3) - y0) / CELL).ceil() as usize + 2,
    }
}

/// Per cell its place (World.labels): the first room holding its centre - the observed area (0), a
/// region (1..), outside for an outside room -, None in no room.
pub(super) fn labels_of(plan: &Plan, raster: &Raster) -> Vec<Option<usize>> {
    cells(raster)
        .map(|c| {
            plan.all_rooms
                .iter()
                .find(|(z, _)| z.contains(c, 0.0))
                .map(|&(_, place)| place)
        })
        .collect()
}

/// The centres of the cells, row-major (i, j).
fn cells(raster: &Raster) -> impl Iterator<Item = Point> + '_ {
    (0..raster.nx).flat_map(move |i| (0..raster.ny).map(move |j| raster.centre(i, j)))
}

/// Per cell its observed room (filter.Tracker.room_of: the last observed room holding its centre among
/// the cells in view), the number of rooms for none.
pub(super) fn rooms_of(plan: &Plan, raster: &Raster, labels: &[Option<usize>]) -> Vec<f64> {
    let none = plan.rooms.len() as f64;
    cells(raster)
        .zip(labels)
        .map(|(c, &label)| {
            let k = plan.rooms.iter().rposition(|r| r.shape.contains(c, 0.0));
            match (label, k) {
                (Some(0), Some(k)) => k as f64,
                _ => none,
            }
        })
        .collect()
}

/// m: per cell a lower bound of the distance from anywhere in it to the nearest wall (World.wall_dist).
pub(super) fn wall_dist(raster: &Raster, walls: &[[Point; 2]]) -> Vec<f64> {
    cells(raster)
        .map(|p| {
            let d = walls.iter().fold(f64::INFINITY, |best, &[a, b]| {
                let d = [b[0] - a[0], b[1] - a[1]];
                let t = (((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]) / (d[0] * d[0] + d[1] * d[1]).max(1e-12))
                    .clamp(0.0, 1.0);
                best.min((p[0] - a[0] - t * d[0]).hypot(p[1] - a[1] - t * d[1]))
            });
            d - CELL / 2f64.sqrt()
        })
        .collect()
}

/// The cells within `radius` of each cell that a straight line from its centre reaches without crossing
/// a wall (World.reach): the offsets in cells, and per offset and cell (row-major) whether it is reached.
fn reach(raster: &Raster, labels: &[Option<usize>], walls: &Walls, radius: f64) -> (Vec<(i64, i64)>, Vec<Vec<bool>>) {
    let n = (radius / CELL) as i64;
    let r2 = (radius / CELL) * (radius / CELL);
    let offsets: Vec<(i64, i64)> = (-n..=n)
        .flat_map(|di| (-n..=n).map(move |dj| (di, dj)))
        .filter(|&(di, dj)| ((di * di + dj * dj) as f64) <= r2)
        .collect();
    let (nx, ny) = (raster.nx as i64, raster.ny as i64);
    let inside: Vec<(i64, i64)> = (0..nx)
        .flat_map(|i| (0..ny).map(move |j| (i, j)))
        .filter(|&(i, j)| labels[(i * ny + j) as usize].is_some())
        .collect();
    let masks = offsets
        .iter()
        .map(|&(di, dj)| {
            let mut mask = vec![false; raster.cells()];
            let reached: Vec<(i64, i64)> = inside
                .iter()
                .copied()
                .filter(|&(i, j)| {
                    let (ti, tj) = (i + di, j + dj);
                    ti >= 0 && ti < nx && tj >= 0 && tj < ny && labels[(ti * ny + tj) as usize].is_some()
                })
                .collect();
            let starts: Vec<Point> = reached
                .iter()
                .map(|&(i, j)| raster.centre(i as usize, j as usize))
                .collect();
            let crossed = if (di, dj) == (0, 0) {
                vec![false; starts.len()]
            } else {
                let ends: Vec<Point> = starts
                    .iter()
                    .map(|p| [p[0] + di as f64 * CELL, p[1] + dj as f64 * CELL])
                    .collect();
                walls.crosses(&starts, &ends)
            };
            for (&(i, j), cross) in reached.iter().zip(crossed) {
                mask[(i * ny + j) as usize] = !cross;
            }
            mask
        })
        .collect();
    (offsets, masks)
}

// ------------------------------------------------------------------ the LD2450's sight

/// m: the sensor model's map is held on cells of this side, anchored here (sensormodel.CELL, ORIGIN)
const COARSE: f64 = 0.25;
const ORIGIN: f64 = -30.0;
const PD_MAX: f64 = 0.95;
/// degrees inside the field of view's edge where it starts to fall, the share at the edge, degrees
/// beyond it where it reaches 0; m beyond the range still seen fully, the fall beyond, the end
/// (sensormodel.py, measured 5.10.)
const EDGE_ANGLE: f64 = 15.0;
const PD_AT_EDGE: f64 = 0.5;
const BEYOND_ANGLE: f64 = 15.0;
const FULL_RANGE_EXTRA: f64 = 1.0;
const RANGE_TAIL: f64 = 1.5;
const RANGE_TAIL_MAX: f64 = 4.0;

fn coarse_cell(v: f64) -> i64 {
    ((v - ORIGIN) / COARSE) as i64
}

fn coarse_centre(i: i64) -> f64 {
    ORIGIN + (i as f64 + 0.5) * COARSE
}

/// How well the sensor sees the spot (SensorModel.prior_pd): full in its field of view, falling toward
/// its edge and beyond its range; 0 behind walls and in a closed room without a sensor.
fn prior_pd(s: &SensorConfig, plan: &Plan, p: Point) -> f64 {
    if plan.closed_rooms.iter().any(|z| z.contains(p, 0.0)) {
        return 0.0;
    }
    let [lx, ly] = s.to_local(p);
    let r = py_hypot(lx, ly);
    if ly <= 0.0 || r > s.range + RANGE_TAIL_MAX {
        return 0.0;
    }
    let az = lx.atan2(ly).to_degrees().abs();
    if az >= s.fov / 2.0 + BEYOND_ANGLE || !crate::geometry::line_of_sight(s.sight_origin(), p, &plan.walls) {
        return 0.0;
    }
    let edge = s.fov / 2.0;
    let side = if az <= edge - EDGE_ANGLE {
        1.0
    } else if az <= edge {
        1.0 - (1.0 - PD_AT_EDGE) * (az - edge + EDGE_ANGLE) / EDGE_ANGLE
    } else {
        PD_AT_EDGE * (1.0 - (az - edge) / BEYOND_ANGLE)
    };
    let far = (-(r - s.range - FULL_RANGE_EXTRA).max(0.0) / RANGE_TAIL).exp();
    PD_MAX * side * far
}

/// The sensor model's map of one sensor (SensorModel.rebuild): on the coarse cells over the rooms'
/// box, as float32; outside the box computed where asked (SensorModel.pd).
struct CoarseSight {
    lo: [i64; 2],
    hi: [i64; 2],
    grid: Vec<f32>,
}

impl CoarseSight {
    fn new(config: &Config, s: &SensorConfig, plan: &Plan) -> Self {
        let corners: Vec<Point> = config.rooms().flat_map(|z| z.shape.outline(8)).collect();
        if corners.is_empty() {
            return Self {
                lo: [0, 0],
                hi: [0, 0],
                grid: Vec::new(),
            };
        }
        let min = |k: usize| corners.iter().map(|c| c[k]).fold(f64::INFINITY, f64::min);
        let max = |k: usize| corners.iter().map(|c| c[k]).fold(f64::NEG_INFINITY, f64::max);
        let lo = [coarse_cell(min(0) - 0.5), coarse_cell(min(1) - 0.5)];
        let hi = [coarse_cell(max(0) + 0.5) + 1, coarse_cell(max(1) + 0.5) + 1];
        let grid = (lo[0]..hi[0])
            .flat_map(|i| (lo[1]..hi[1]).map(move |j| (i, j)))
            .map(|(i, j)| {
                if s.in_use() {
                    prior_pd(s, plan, [coarse_centre(i), coarse_centre(j)]) as f32
                } else {
                    0.0
                }
            })
            .collect();
        Self { lo, hi, grid }
    }

    fn pd(&self, s: &SensorConfig, plan: &Plan, p: Point) -> f64 {
        let (i, j) = (coarse_cell(p[0]), coarse_cell(p[1]));
        if self.lo[0] <= i && i < self.hi[0] && self.lo[1] <= j && j < self.hi[1] {
            let row = (self.hi[1] - self.lo[1]) as usize;
            return f64::from(self.grid[(i - self.lo[0]) as usize * row + (j - self.lo[1]) as usize]);
        }
        if s.in_use() { prior_pd(s, plan, p) } else { 0.0 }
    }
}

/// The sight of a person, not of a point (filter.Tracker._spread_sight, MODEL.md 4.1): the sight of the
/// points averaged over where the track sits on the person (Gaussian per axis, the offset's variance
/// at this distance) over what lies on the person's side of the walls; in the places without a sensor
/// as the points' sight.
fn spread(
    raster: &Raster,
    labels: &[Option<usize>],
    reached: &(Vec<(i64, i64)>, Vec<Vec<bool>>),
    var: &[f64],
    grid: &[f64],
) -> Vec<f64> {
    let (nx, ny) = (raster.nx as i64, raster.ny as i64);
    let (offsets, masks) = reached;
    let (mut num, mut den) = (vec![0.0; grid.len()], vec![0.0; grid.len()]);
    for (&(di, dj), mask) in offsets.iter().zip(masks) {
        let d2 = -0.5 * (di * di + dj * dj) as f64 * CELL * CELL;
        for i in 0..nx {
            for j in 0..ny {
                let c = (i * ny + j) as usize;
                let w = if mask[c] { (d2 / var[c]).exp() } else { 0.0 };
                let (ti, tj) = (i + di, j + dj);
                let g = if ti >= 0 && ti < nx && tj >= 0 && tj < ny {
                    grid[(ti * ny + tj) as usize]
                } else {
                    0.0
                };
                num[c] += w * g;
                den[c] += w;
            }
        }
    }
    (0..grid.len())
        .map(|c| {
            if labels[c] == Some(0) && den[c] > 0.0 {
                num[c] / den[c].max(1e-300)
            } else {
                grid[c]
            }
        })
        .collect()
}

/// What the build of a sensor needs of the world built so far.
pub(super) struct Built<'a> {
    pub config: &'a Config,
    pub params: &'a Params,
    pub plan: &'a Plan,
    pub raster: Raster,
    pub labels: &'a [Option<usize>],
    pub rooms: &'a Grid,
    pub walls: &'a Walls,
    pub tiles: &'a Tiles,
}

/// A sensor's sight of a person per cell and its LD2410C (filter.Tracker._grid, _export_sensor).
pub(super) fn sensor(b: &Built, s: &SensorConfig) -> (Sight, LdSight) {
    let (raster, labels) = (b.raster, b.labels);
    let coarse = CoarseSight::new(b.config, s, b.plan);
    let centres: Vec<Point> = cells(&raster).collect();
    let points: Vec<f64> = centres
        .iter()
        .zip(labels)
        .map(|(&c, l)| {
            if l.is_some() {
                coarse.pd(s, b.plan, c) / PD_MAX
            } else {
                0.0
            }
        })
        .collect();
    // the LD2410C sees where the LD2450 in the same housing could see at all: through no wall
    let los: Vec<f64> = points.iter().map(|&g| if g > 0.0 { 1.0 } else { 0.0 }).collect();
    let tp = b.config.params.target_height;
    let p = &b.config.params;
    let var: Vec<f64> = centres
        .iter()
        .map(|&c| {
            let dist = (c[0] - s.x).hypot(c[1] - s.y);
            let (sr, st) = (
                p.range_sigma_base + p.range_sigma_slope * dist,
                p.lateral_sigma_base + p.lateral_sigma_slope * dist,
            );
            0.5 * (sr * sr + st * st)
        })
        .collect();
    let top = var
        .iter()
        .zip(labels)
        .filter(|(_, l)| **l == Some(0))
        .map(|(&v, _)| v)
        .fold(None, |m: Option<f64>, v| Some(m.map_or(v, |m| m.max(v))));
    let radius = top.map_or(0.0, |v| 2.5 * v.sqrt()).min(1.2);
    let reached = reach(&raster, labels, b.walls, radius);
    let grid = Grid::new(raster, spread(&raster, labels, &reached, &var, &points));
    let los = Grid::new(raster, spread(&raster, labels, &reached, &var, &los));
    let dh = s.height - tp;
    let mut ld = LdSight {
        at: s.at(),
        axis: s.axis(),
        dh2: dh * dh,
        los,
        view: ld_view(b, s),
        tiles: LdTiles::default(),
    };
    let tiles = b.tiles;
    let places: Vec<_> = tiles.points.iter().map(|&q| ld.place(q, b.rooms)).collect();
    // the tiles its LD2410C sees and what a person there puts into its cells (Tracker._ld_tiles)
    let mut still = vec![[0.0; CELLS]; tiles.n];
    let mut walk = vec![[0.0; CELLS]; tiles.n];
    for (q, &t) in places.iter().zip(&tiles.point_tile) {
        let (es, ew) = (
            expected(b.params, STILL, q.angle, q.slant, q.sight),
            expected(b.params, WALK, q.angle, q.slant, q.sight),
        );
        for k in 0..CELLS {
            still[t][k] += es[k];
            walk[t][k] += ew[k];
        }
    }
    for ((ss, sw), &count) in still.iter_mut().zip(&mut walk).zip(&tiles.point_count) {
        ss.iter_mut().chain(sw.iter_mut()).for_each(|v| *v /= count);
    }
    for (c, (ss, sw)) in still.iter().zip(&walk).enumerate() {
        if ss
            .iter()
            .zip(sw)
            .map(|(a, w)| a.max(*w))
            .fold(f64::NEG_INFINITY, f64::max)
            > FLOOR
        {
            ld.tiles.idx.push(c);
            ld.tiles.still.push(*ss);
            ld.tiles.walk.push(*sw);
        }
    }
    // how well it can test a person per tile: its LD2450's sight or its LD2410C's beam (Tiling.observed)
    let [full, none] = b.params.ld_beam;
    let beam: Vec<f64> = places
        .iter()
        .map(|q| {
            let near = if q.slant <= 9.0 * GATE { 1.0 } else { 0.0 };
            ((none - q.angle) / (none - full)).clamp(0.0, 1.0) * q.sight * near
        })
        .collect();
    let seen = tiles.average(&tiles.points.iter().map(|&q| grid.at(q)).collect::<Vec<_>>());
    let observed = tiles
        .average(&beam)
        .iter()
        .zip(&seen)
        .map(|(&a, &g)| f64::max(0.0, g.max(a)).clamp(0.0, 1.0))
        .collect();
    (Sight::new(s.at(), s.sight_origin(), grid, observed), ld)
}

/// Per room (the last for none) how much of what the geometric sight expects the sensor's LD2410C
/// gets from a person there, against its own room (filter.Tracker._ld_view, measured: ld_view); None
/// if nothing is measured for it.
fn ld_view(b: &Built, s: &SensorConfig) -> Option<Vec<f64>> {
    if b.params.ld_view.is_empty() {
        return None;
    }
    let none = b.plan.rooms.len();
    let own = b.rooms.at(s.sight_origin()) as usize;
    if own == none {
        return None;
    }
    let own_id = &b.plan.rooms[own].id;
    let mut v: Vec<f64> = b
        .plan
        .rooms
        .iter()
        .map(|r| {
            b.params
                .ld_view
                .get(&format!("{own_id}<-{}", r.id))
                .copied()
                .unwrap_or(1.0)
        })
        .chain([1.0])
        .collect();
    v[own] = 1.0;
    v.iter().any(|&x| x != 1.0).then_some(v)
}
