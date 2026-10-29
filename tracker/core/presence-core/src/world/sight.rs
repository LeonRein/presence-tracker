//! How well a sensor sees each cell (MODEL.md 4.1), and what follows from it
//! per tile.
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use super::raster::{CELL, Grid};
use crate::geometry::Point;
use crate::ld2450::rates::RateModel;
use crate::world::Tiles;

/// The sensor model's coarse map of a sensor, for the web UI: its sight
/// of a point on square cells of side `cell` over the rooms' box, column by column from `corner`.
#[derive(Debug, Default)]
pub struct SensorMap {
    pub corner: Point,
    pub cell: f64,
    pub cols: usize,
    pub rows: usize,
    pub prior: Vec<f32>,
}

/// A sensor's sight of a person, 0..1 per 0.1 m cell, and its rates per tile without live tracks.
#[derive(Debug)]
pub struct Sight {
    pub at: Point,
    /// where its sight lines start (SensorConfig.sight_origin)
    pub origin: Point,
    pub grid: Grid,
    /// how well it sees each tile, 0..1
    pub tiles: Vec<f64>,
    /// m^2: the area it sees (sight above 0.05)
    pub area: f64,
    /// how well it can test a person in each tile, 0..1: its LD2450's sight or its LD2410C's beam,
    /// whichever is better (tiling.Tiling.observed of this sensor alone, MODEL.md 5.5)
    pub observed: Vec<f64>,
    /// the coarse map the sight of a point comes from (set by the build)
    pub map: SensorMap,
    /// [STILL, WALK] rates at the tiles' sample points and per tile, without live tracks
    base_points: [Vec<f64>; 2],
    base_tiles: [Vec<f64>; 2],
}

impl Sight {
    /// A sight without its rates per tile (set_rates).
    pub fn new(at: Point, origin: Point, grid: Grid, observed: Vec<f64>) -> Self {
        let seen = grid.values().iter().filter(|&&v| v > 0.05).count();
        Self {
            at,
            origin,
            area: seen as f64 * CELL * CELL,
            observed,
            grid,
            map: SensorMap::default(),
            tiles: Vec::new(),
            base_points: [Vec::new(), Vec::new()],
            base_tiles: [Vec::new(), Vec::new()],
        }
    }

    pub(super) fn set_rates(&mut self, rates: &RateModel, tiles: &Tiles) {
        let at: Vec<f64> = tiles.points.iter().map(|&p| self.grid.at(p)).collect();
        self.tiles = tiles.average(&at);
        self.base_points = rates.base_rates(self, &tiles.points);
        self.base_tiles = self.base_points.each_ref().map(|r| tiles.average(r));
    }

    pub fn base_points(&self) -> &[Vec<f64>; 2] {
        &self.base_points
    }

    pub fn base_tiles(&self) -> &[Vec<f64>; 2] {
        &self.base_tiles
    }
}
