//! Where a sensor can see a person directly (the floor plan's evidence, (c) in mod.rs): in a room that is
//! not a closed room without a sensor, in its line of sight; on a grid, with the nearest free point of every
//! cell outside.

use super::{C, CELL, VIEW};
use crate::config::Config;
use crate::geometry::{Point, line_of_sight};
use crate::plan::Plan;

/// The free space of one sensor on a CELL grid around the rooms (2 m beyond them): per cell whether it
/// is free, and for a cell outside the nearest free cell on the edge.
#[derive(Debug)]
pub(super) struct FreeSpace {
    x0: f64,
    y0: f64,
    nx: usize,
    ny: usize,
    /// [i * ny + j]: x index i, y index j
    free: Vec<bool>,
    near: Vec<C>,
    /// m^2 of the free cells (at least 1)
    pub(super) area: f64,
}

impl FreeSpace {
    /// The free space of a sensor whose sight lines start at `origin`, in the rooms with these bounds.
    fn new(bounds: &[[f64; 4]], free: impl Fn(Point) -> bool) -> Self {
        let lo = |k: usize| bounds.iter().map(|b| b[k]).fold(f64::INFINITY, f64::min);
        let hi = |k: usize| bounds.iter().map(|b| b[k]).fold(f64::NEG_INFINITY, f64::max);
        let (x0, y0) = (lo(0) - 2.0, lo(1) - 2.0);
        let (x1, y1) = (hi(2) + 2.0, hi(3) + 2.0);
        let (nx, ny) = (((x1 - x0) / CELL) as usize + 1, ((y1 - y0) / CELL) as usize + 1);
        let gx: Vec<f64> = (0..nx).map(|i| x0 + CELL * (i as f64 + 0.5)).collect();
        let gy: Vec<f64> = (0..ny).map(|j| y0 + CELL * (j as f64 + 0.5)).collect();
        let cell = |k: usize| C::new(gx[k / ny], gy[k % ny]);
        let free: Vec<bool> = (0..nx * ny).map(|k| free([gx[k / ny], gy[k % ny]])).collect();
        let area = f64::max(free.iter().filter(|&&f| f).count() as f64 * CELL * CELL, 1.0);
        // the free cells on the edge: one of the four neighbours not free (the grid wraps around, as
        // numpy's roll; its border is 2 m outside every room)
        let at = |i: usize, j: usize| free[i * ny + j];
        let edge: Vec<usize> = (0..nx * ny)
            .filter(|&k| {
                let (i, j) = (k / ny, k % ny);
                free[k]
                    && !(at((i + nx - 1) % nx, j)
                        && at((i + 1) % nx, j)
                        && at(i, (j + ny - 1) % ny)
                        && at(i, (j + 1) % ny))
            })
            .collect();
        let (ex, ey): (Vec<f64>, Vec<f64>) = edge.iter().map(|&k| (gx[k / ny], gy[k % ny])).unzip();
        // for every cell outside the nearest edge cell (the first of equally near ones): by the squared
        // distance, then among those as near the true distance (|a - b| as numpy has it)
        let near = (0..nx * ny)
            .map(|k| {
                let c = cell(k);
                if free[k] || edge.is_empty() {
                    return c;
                }
                let sq = |e: usize| {
                    let (dx, dy) = (c.re - ex[e], c.im - ey[e]);
                    dx * dx + dy * dy
                };
                let least = (0..edge.len()).map(sq).fold(f64::INFINITY, f64::min);
                let mut best: Option<(f64, usize)> = None;
                for e in (0..edge.len()).filter(|&e| sq(e) <= least * (1.0 + 1e-9)) {
                    let d = (c - C::new(ex[e], ey[e])).abs();
                    if best.is_none_or(|(b, _)| d < b) {
                        best = Some((d, e));
                    }
                }
                best.map_or(c, |(_, e)| C::new(ex[e], ey[e]))
            })
            .collect();
        Self {
            x0,
            y0,
            nx,
            ny,
            free,
            near,
            area,
        }
    }

    /// The nearest free point of w and the distance to it (0 inside).
    pub(super) fn lookup(&self, w: C) -> (C, f64) {
        let index = |v: f64, n: usize| ((v / CELL) as i64).clamp(0, n as i64 - 1) as usize;
        let k = index(w.re - self.x0, self.nx) * self.ny + index(w.im - self.y0, self.ny);
        let b = if self.free[k] { w } else { self.near[k] };
        (b, (w - b).abs())
    }
}

/// What the floor plan says for every sensor: its free space (None without rooms in which the sensors may
/// see people of the home), and the area of its view for the echoes.
#[derive(Debug)]
pub(super) struct Space {
    pub(super) fields: Vec<Option<FreeSpace>>,
    /// m^2: the sensor's view out to VIEW
    pub(super) views: Vec<f64>,
    /// any room to judge by
    pub(super) rooms: bool,
}

impl Space {
    /// For the sensors `with` (indices into config.sensors): their free space.
    pub(super) fn new(config: &Config, plan: &Plan, with: &[usize]) -> Self {
        let open: Vec<_> = plan.open_rooms().collect();
        let bounds: Vec<[f64; 4]> = open.iter().map(|z| z.shape.bounds()).collect();
        let fields = (0..config.sensors.len())
            .map(|si| {
                if open.is_empty() || !with.contains(&si) {
                    return None;
                }
                let origin = config.sensors[si].sight_origin();
                Some(FreeSpace::new(&bounds, |p| {
                    open.iter().any(|z| z.shape.contains(p, 0.0)) && line_of_sight(origin, p, &plan.walls)
                }))
            })
            .collect();
        let views = config
            .sensors
            .iter()
            .map(|s| s.fov.to_radians() / 2.0 * VIEW * VIEW)
            .collect();
        Self {
            fields,
            views,
            rooms: !open.is_empty(),
        }
    }
}
