//! The world built from the plan (world.World, filter.Tracker._grid, _spread_sight,
//! _ld_tiles, sensormodel.SensorModel, tiling.Tiling.observed): the 0.1 m raster of places and rooms,
//! the distance to the walls, and per sensor its sight of a person and its LD2410C's - in steps, each a
//! function of what is built before.
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use super::raster::{CELL, Grid, Raster, RoomGrid};
use super::{LdSight, LdTiles, Sight, Tiles};
use crate::config::{Config, SensorConfig, TrackerParams};
use crate::geometry::{Point, Walls};
use crate::ld2410::profile::expected;
use crate::ld2410::{CELLS, FLOOR, GATE};
use crate::params::{Params, STILL, WALK};
use crate::plan::{Place, Plan, RoomIdx};
use crate::vmath::py_hypot;

/// The raster around the rooms (the outside rooms too): a cell beyond them on every side (World).
pub(super) fn raster_of(plan: &Plan) -> Raster {
    let bounds: Vec<[f64; 4]> = plan
        .zones
        .iter()
        .filter(|z| z.is_room())
        .map(|z| z.shape.bounds())
        .collect();
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

/// The centres of the cells, row-major (i, j).
fn cells(raster: &Raster) -> impl Iterator<Item = Point> + '_ {
    (0..raster.nx).flat_map(move |i| (0..raster.ny).map(move |j| raster.centre(i, j)))
}

/// Per cell its place: of the first room holding its centre, None in no room.
pub(super) fn labels_of(plan: &Plan, raster: &Raster) -> Vec<Option<Place>> {
    cells(raster).map(|c| plan.place_at(c)).collect()
}

/// Per cell in view its observed room (filter.Tracker.room_of: of overlapping rooms the last one wins,
/// as the Python tracker writes them in order; the outputs' room_at takes the first - rooms do not
/// overlap, the faces between the walls).
pub(super) fn rooms_of(plan: &Plan, raster: &Raster, labels: &[Option<Place>]) -> RoomGrid {
    let rooms: Vec<_> = plan.observed().collect();
    let of = |(c, &label): (Point, &Option<Place>)| {
        let k = rooms.iter().rposition(|z| z.shape.contains(c, 0.0));
        k.filter(|_| label == Some(Place::Observed)).map(RoomIdx)
    };
    RoomGrid::new(*raster, cells(raster).zip(labels).map(of).collect())
}

/// m: per cell a lower bound of the distance from anywhere in it to the nearest wall.
pub(super) fn wall_dist(raster: &Raster, walls: &[[Point; 2]]) -> Vec<f64> {
    cells(raster)
        .map(|p| {
            let d = walls.iter().fold(f64::INFINITY, |best, &[a, b]| {
                let d = [b[0] - a[0], b[1] - a[1]];
                let t = (((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]) / (d[0] * d[0] + d[1] * d[1]).max(1e-12))
                    .clamp(0.0, 1.0);
                best.min(py_hypot(p[0] - a[0] - t * d[0], p[1] - a[1] - t * d[1]))
            });
            d - CELL / 2f64.sqrt()
        })
        .collect()
}

/// The cells within a radius of each cell that a straight line from its centre reaches without crossing
/// a wall, one offset at a time (all at once took ~1.8 GB per sensor on the largest plan).
struct Reach<'a> {
    raster: &'a Raster,
    labels: &'a [Option<Place>],
    walls: &'a Walls,
    /// the offsets in cells
    offsets: Vec<(i64, i64)>,
    /// the cells in a place
    inside: Vec<(i64, i64)>,
}

impl<'a> Reach<'a> {
    fn new(raster: &'a Raster, labels: &'a [Option<Place>], walls: &'a Walls, radius: f64) -> Self {
        let n = (radius / CELL) as i64;
        let r2 = (radius / CELL) * (radius / CELL);
        let offsets: Vec<(i64, i64)> = (-n..=n)
            .flat_map(|di| (-n..=n).map(move |dj| (di, dj)))
            .filter(|&(di, dj)| ((di * di + dj * dj) as f64) <= r2)
            .collect();
        let ny = raster.ny as i64;
        let inside = (0..raster.nx as i64)
            .flat_map(|i| (0..ny).map(move |j| (i, j)))
            .filter(|&(i, j)| labels[(i * ny + j) as usize].is_some())
            .collect();
        Self {
            raster,
            labels,
            walls,
            offsets,
            inside,
        }
    }

    fn placed(&self, i: i64, j: i64) -> bool {
        let (nx, ny) = (self.raster.nx as i64, self.raster.ny as i64);
        i >= 0 && i < nx && j >= 0 && j < ny && self.labels[(i * ny + j) as usize].is_some()
    }

    /// Per cell (row-major) whether the offset is reached from it.
    fn mask(&self, (di, dj): (i64, i64)) -> Vec<bool> {
        let ny = self.raster.ny as i64;
        let mut mask = vec![false; self.raster.cells()];
        let reached: Vec<(i64, i64)> = self
            .inside
            .iter()
            .copied()
            .filter(|&(i, j)| self.placed(i + di, j + dj))
            .collect();
        let starts: Vec<Point> = reached
            .iter()
            .map(|&(i, j)| self.raster.centre(i as usize, j as usize))
            .collect();
        let crossed = if (di, dj) == (0, 0) {
            vec![false; starts.len()]
        } else {
            let ends: Vec<Point> = starts
                .iter()
                .map(|p| [p[0] + di as f64 * CELL, p[1] + dj as f64 * CELL])
                .collect();
            self.walls.crosses(&starts, &ends)
        };
        for (&(i, j), cross) in reached.iter().zip(crossed) {
            mask[(i * ny + j) as usize] = !cross;
        }
        mask
    }
}

// ------------------------------------------------------------------ the LD2450's sight

/// m: the sensor model's map is held on cells of this side, anchored here (ORIGIN)
const COARSE: f64 = 0.25;
const ORIGIN: f64 = -30.0;
const PD_MAX: f64 = 0.95;
/// degrees inside the field of view's edge where it starts to fall, the share at the edge, degrees
/// beyond it where it reaches 0; m beyond the range still seen fully, the fall beyond, the end
/// (measured 5.10.)
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

/// How well the sensor sees the spot: full in its field of view, falling toward
/// its edge and beyond its range; 0 behind walls and in a closed room without a sensor.
fn prior_pd(s: &SensorConfig, plan: &Plan, p: Point) -> f64 {
    if plan.closed.iter().any(|z| plan.zones[z.0].shape.contains(p, 0.0)) {
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

/// The sensor model's map of one sensor: on the coarse cells over the rooms'
/// box, as float32; outside the box computed where asked.
struct CoarseSight {
    lo: [i64; 2],
    hi: [i64; 2],
    grid: Vec<f32>,
}

impl CoarseSight {
    fn new(s: &SensorConfig, plan: &Plan) -> Self {
        let corners: Vec<Point> = plan
            .zones
            .iter()
            .filter(|z| z.is_room())
            .flat_map(|z| z.shape.outline(8))
            .collect();
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

/// The sight of a person, not of a point (MODEL.md 4.1): the sight of the
/// points averaged over where the track sits on the person (Gaussian per axis, the offset's variance
/// at this distance) over what lies on the person's side of the walls; in the places without a sensor
/// as the points' sight. Both grids in one pass over the offsets (each offset's mask is made once).
fn spread<const G: usize>(reach: &Reach, var: &[f64], grids: [&[f64]; G]) -> [Vec<f64>; G] {
    let (raster, labels) = (reach.raster, reach.labels);
    let (nx, ny) = (raster.nx as i64, raster.ny as i64);
    let cells = raster.cells();
    let mut num: [Vec<f64>; G] = std::array::from_fn(|_| vec![0.0; cells]);
    let mut den = vec![0.0; cells];
    for &(di, dj) in &reach.offsets {
        let mask = reach.mask((di, dj));
        let d2 = -0.5 * (di * di + dj * dj) as f64 * CELL * CELL;
        for i in 0..nx {
            for j in 0..ny {
                let c = (i * ny + j) as usize;
                let w = if mask[c] { (d2 / var[c]).exp() } else { 0.0 };
                let (ti, tj) = (i + di, j + dj);
                let target = (ti >= 0 && ti < nx && tj >= 0 && tj < ny).then(|| (ti * ny + tj) as usize);
                for (num, grid) in num.iter_mut().zip(grids) {
                    num[c] += w * target.map_or(0.0, |t| grid[t]);
                }
                den[c] += w;
            }
        }
    }
    std::array::from_fn(|g| {
        (0..cells)
            .map(|c| {
                if labels[c] == Some(Place::Observed) && den[c] > 0.0 {
                    num[g][c] / den[c].max(1e-300)
                } else {
                    grids[g][c]
                }
            })
            .collect()
    })
}

/// What the build of a sensor needs of the world built so far.
pub(super) struct Built<'a> {
    pub config: &'a Config,
    pub params: &'a Params,
    pub plan: &'a Plan,
    pub raster: Raster,
    pub labels: &'a [Option<Place>],
    pub walls: &'a Walls,
    pub tiles: &'a Tiles,
}

/// A sensor's sight of a person per cell and its LD2410C (_export_sensor).
pub(super) fn sensor(b: &Built, s: &SensorConfig) -> (Sight, LdSight) {
    let coarse = CoarseSight::new(s, b.plan);
    let (points, los) = point_sight(b, s, &coarse);
    let reach = Reach::new(&b.raster, b.labels, b.walls, reach_of(b, s));
    let var = offset_var(&b.raster, &b.config.params, s);
    let [grid, los] = spread(&reach, &var, [&points, &los]);
    let (grid, los) = (Grid::new(b.raster, grid), Grid::new(b.raster, los));
    let dh = s.height - b.config.params.target_height;
    let mut ld = LdSight {
        at: s.at(),
        axis: s.axis(),
        dh2: dh * dh,
        los,
        tiles: LdTiles::default(),
    };
    ld.tiles = ld_tiles(b, &ld);
    let observed = observed(b, &grid, &ld);
    (Sight::new(s.at(), s.sight_origin(), grid, observed), ld)
}

/// The sight of the points of each cell in a place, 0..1 (Tracker._grid before it is spread), and where
/// it is above 0 the LD2410C in the same housing can see too: through no wall.
fn point_sight(b: &Built, s: &SensorConfig, coarse: &CoarseSight) -> (Vec<f64>, Vec<f64>) {
    let points: Vec<f64> = cells(&b.raster)
        .zip(b.labels)
        .map(|(c, l)| {
            if l.is_some() {
                coarse.pd(s, b.plan, c) / PD_MAX
            } else {
                0.0
            }
        })
        .collect();
    let los = points.iter().map(|&g| if g > 0.0 { 1.0 } else { 0.0 }).collect();
    (points, los)
}

/// Per cell the variance (per axis) of where a track sits on a person at this distance from the
/// sensor: the measured spread along and across the line of sight (MODEL.md 4.1).
fn offset_var(raster: &Raster, p: &TrackerParams, s: &SensorConfig) -> Vec<f64> {
    let reading = crate::frames::Reading::of(p);
    cells(raster)
        .map(|c| reading.offset_var(py_hypot(c[0] - s.x, c[1] - s.y)))
        .collect()
}

/// m: how far the spreading reaches: 2.5 of the largest offset's spread in view, at most 1.2 m.
fn reach_of(b: &Built, s: &SensorConfig) -> f64 {
    let var = offset_var(&b.raster, &b.config.params, s);
    let top = var
        .iter()
        .zip(b.labels)
        .filter(|(_, l)| **l == Some(Place::Observed))
        .map(|(&v, _)| v)
        .fold(None, |m: Option<f64>, v| Some(m.map_or(v, |m| m.max(v))));
    top.map_or(0.0, |v| 2.5 * v.sqrt()).min(1.2)
}

/// The tiles the LD2410C sees and what a person there puts into its cells, standing and walking,
/// averaged over each tile: those above FLOOR in a cell.
fn ld_tiles(b: &Built, ld: &LdSight) -> LdTiles {
    let tiles = b.tiles;
    let mut still = vec![[0.0; CELLS]; tiles.n];
    let mut walk = vec![[0.0; CELLS]; tiles.n];
    for (&q, &t) in tiles.points.iter().zip(&tiles.point_tile) {
        let at = ld.place(q);
        let (es, ew) = (
            expected(b.params, STILL, at.angle, at.slant, at.sight),
            expected(b.params, WALK, at.angle, at.slant, at.sight),
        );
        for k in 0..CELLS {
            still[t][k] += es[k];
            walk[t][k] += ew[k];
        }
    }
    let mut out = LdTiles::default();
    for (c, ((ss, sw), &count)) in still.iter_mut().zip(&mut walk).zip(&tiles.point_count).enumerate() {
        ss.iter_mut().chain(sw.iter_mut()).for_each(|v| *v /= count);
        if ss
            .iter()
            .zip(sw.iter())
            .map(|(a, w)| a.max(*w))
            .fold(f64::NEG_INFINITY, f64::max)
            > FLOOR
        {
            out.idx.push(c);
            out.still.push(*ss);
            out.walk.push(*sw);
        }
    }
    out
}

/// How well the sensor can test a person per tile: its LD2450's sight or its LD2410C's beam within its
/// last gate, whichever is better (Tiling.observed of this sensor alone, MODEL.md 5.5).
fn observed(b: &Built, grid: &Grid, ld: &LdSight) -> Vec<f64> {
    let tiles = b.tiles;
    let [full, none] = b.params.ld_beam;
    let beam: Vec<f64> = tiles
        .points
        .iter()
        .map(|&q| {
            let at = ld.place(q);
            let near = if at.slant <= 9.0 * GATE { 1.0 } else { 0.0 };
            ((none - at.angle) / (none - full)).clamp(0.0, 1.0) * at.sight * near
        })
        .collect();
    let seen = tiles.average(&tiles.points.iter().map(|&q| grid.at(q)).collect::<Vec<_>>());
    tiles
        .average(&beam)
        .iter()
        .zip(&seen)
        .map(|(&a, &g)| f64::max(0.0, g.max(a)).clamp(0.0, 1.0))
        .collect()
}
