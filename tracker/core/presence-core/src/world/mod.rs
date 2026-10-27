//! The world the filter runs in (MODEL.md 2, 5.3): the plan's raster and walls, the tiles, the
//! sensors' sight, the model's numbers. Built once, unchanged until the configuration changes; every
//! step borrows it.

pub mod raster;
pub mod sight;
pub mod tiles;

pub use raster::{CELL, Grid, Raster};
pub use sight::Sight;
pub use tiles::{DoorTile, Tiles};

use crate::geometry::{Point, Walls};
use crate::ld2450::rates::RateModel;
use crate::params::{Params, Shapes};

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
    /// per sensor where it is and its sight per cell
    pub sensors: Vec<(Point, Vec<f64>)>,
}

impl World {
    pub fn from_export(e: WorldExport) -> Self {
        let rates = RateModel::new(&e.params);
        let raster = e.raster;
        let sights = e
            .sensors
            .into_iter()
            .map(|(at, grid)| {
                let mut s = Sight::new(at, Grid::new(raster, grid));
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

    /// No wall within r of the point (world.World.clear).
    pub fn clear(&self, p: Point, r: f64) -> bool {
        self.wall_dist.at(p) > r
    }
}
