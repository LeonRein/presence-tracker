//! The world the filter runs in (MODEL.md 2, 5.3): the plan's raster and walls, the tiles, the
//! sensors' sight, the model's numbers. Built once, unchanged until the configuration changes; every
//! step borrows it.

pub mod raster;
pub mod sight;
pub mod tiles;

pub use raster::{CELL, Grid, Raster};
pub use sight::Sight;
pub use tiles::{DoorTile, POINT, Tiles};

use crate::filter::densities::DensityShape;
use crate::geometry::{Point, Walls};
use crate::ld2450::rates::RateModel;
use crate::params::{Params, Shapes};
use crate::sensors::SensorSet;
use crate::vmath::pairwise_sum;

#[derive(Debug)]
pub struct World {
    pub params: Params,
    pub shapes: Shapes,
    pub rates: RateModel,
    pub raster: Raster,
    pub walls: Walls,
    /// m: per cell a lower bound of the distance from anywhere in it to the nearest wall
    pub wall_dist: Grid,
    pub tiles: Tiles,
    /// per sensor (in the order of the configuration)
    pub sights: Vec<Sight>,
}

/// What the Python tracker built (docs/rust-core.md 6): the world handed over while the build is
/// still Python, and later the aid that checks the build in Rust against it.
#[derive(Debug)]
pub struct WorldExport {
    pub params: Params,
    pub shapes: Shapes,
    pub raster: Raster,
    pub walls: Vec<[f64; 4]>,
    pub wall_dist: Vec<f64>,
    pub tiles: Tiles,
    pub sensors: Vec<SensorExport>,
}

/// A sensor as the Python tracker built it: where it is, where its sight lines start, its sight per
/// cell and how well it can test a person per tile.
#[derive(Debug)]
pub struct SensorExport {
    pub at: Point,
    pub origin: Point,
    pub grid: Vec<f64>,
    pub observed: Vec<f64>,
}

impl World {
    pub fn from_export(e: WorldExport) -> Self {
        let rates = RateModel::new(&e.params);
        let raster = e.raster;
        let sights = e
            .sensors
            .into_iter()
            .map(|sensor| {
                let mut s = Sight::new(
                    sensor.at,
                    sensor.origin,
                    Grid::new(raster, sensor.grid),
                    sensor.observed,
                );
                s.set_rates(&rates, &e.tiles);
                s
            })
            .collect();
        Self {
            params: e.params,
            shapes: e.shapes,
            rates,
            raster,
            walls: Walls::new(e.walls),
            wall_dist: Grid::new(raster, e.wall_dist),
            tiles: e.tiles,
            sights,
        }
    }

    /// The shape of a density over these tiles.
    pub fn density_shape(&self) -> DensityShape {
        DensityShape {
            tiles: self.tiles.n,
            kinds: self.shapes.kinds(),
            levels: self.shapes.levels(),
            regions: self.tiles.regions,
            ages: self.tiles.ages,
            amps: self.shapes.amps(),
        }
    }

    /// How well the best of these sensors can test a person in each tile, 0..1 (tiling.Tiling.
    /// observed, MODEL.md 5.5): 0 where none of them sees, everywhere if none watches.
    pub fn observed(&self, sensors: SensorSet) -> Vec<f64> {
        let mut out = vec![0.0; self.tiles.n];
        for si in sensors.iter() {
            for (o, &x) in out.iter_mut().zip(&self.sights[si].observed) {
                *o = f64::max(*o, x);
            }
        }
        out
    }

    /// No wall within r of the point (world.World.clear).
    pub fn clear(&self, p: Point, r: f64) -> bool {
        self.wall_dist.at(p) > r
    }

    /// The share of N(mean, diag var) at each sample point of the tiles (summing to 1), the points
    /// behind a wall seen from the mean given nothing - those with a density above 1e-3 of the peak
    /// (nobody walks through a wall, the Gaussian itself does not know them); None if the Gaussian
    /// has nothing in view (tiling.Tiling.gauss_points).
    pub fn gauss_points(&self, mean: Point, var: Point) -> Option<Vec<f64>> {
        let tiles = &self.tiles;
        let cell = POINT * POINT / 12.0;
        let (vx, vy) = (var[0] + cell, var[1] + cell);
        let mut d: Vec<f64> = tiles
            .points
            .iter()
            .map(|&[x, y]| {
                let (dx, dy) = (x - mean[0], y - mean[1]);
                (-0.5 * (dx * dx / vx + dy * dy / vy)).exp()
            })
            .collect();
        let reach = 3.72 * (var[0].max(var[1]) + cell).sqrt();
        if !self.clear(mean, reach) {
            let near: Vec<usize> = (0..d.len()).filter(|&i| d[i] > 1e-3).collect();
            if !near.is_empty() {
                let to: Vec<Point> = near.iter().map(|&i| tiles.points[i]).collect();
                let behind = self.walls.crosses(&vec![mean; near.len()], &to);
                if behind.iter().any(|&b| b) && !behind.iter().all(|&b| b) {
                    for (&i, _) in near.iter().zip(&behind).filter(|(_, b)| **b) {
                        d[i] = 0.0;
                    }
                }
            }
        }
        let s = pairwise_sum(&d);
        (s > 0.0).then(|| d.iter().map(|x| x / s).collect())
    }

    /// The share of N(mean, diag var) in each tile (tiling.Tiling.gauss_mass): by the sample points,
    /// or all of it in the nearest tile if it has nothing in view.
    pub fn gauss_mass(&self, mean: Point, var: Point) -> Vec<f64> {
        let tiles = &self.tiles;
        let mut out = vec![0.0; tiles.n];
        match self.gauss_points(mean, var) {
            Some(d) => {
                for (&tile, &v) in tiles.point_tile.iter().zip(&d) {
                    out[tile] += v;
                }
            }
            None => {
                if let Some(c) = tiles.cell_near(mean) {
                    out[c] = 1.0;
                }
            }
        }
        out
    }
}
