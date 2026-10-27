//! How well a sensor sees each cell (filter.Tracker._grid, MODEL.md 4.1), and what follows from it
//! per tile.

use super::raster::{CELL, Grid};
use crate::geometry::Point;
use crate::ld2450::rates::RateModel;
use crate::world::Tiles;

/// A sensor's sight of a person, 0..1 per 0.1 m cell, and its rates per tile without live tracks.
#[derive(Debug)]
pub struct Sight {
    pub at: Point,
    /// where its sight lines start (SensorConfig.sight_origin)
    pub origin: Point,
    pub grid: Grid,
    /// how well it sees each tile, 0..1 (tiling.Tiling.seen)
    pub tiles: Vec<f64>,
    /// m^2: the area it sees (sight above 0.05; filter.Tracker._area)
    pub area: f64,
    /// [STILL, WALK] rates at the tiles' sample points and per tile, without live tracks
    base_points: [Vec<f64>; 2],
    base_tiles: [Vec<f64>; 2],
}

impl Sight {
    /// A sight without its rates per tile (set_rates).
    pub fn new(at: Point, origin: Point, grid: Grid) -> Self {
        let seen = grid.values().iter().filter(|&&v| v > 0.05).count();
        Self {
            at,
            origin,
            area: seen as f64 * CELL * CELL,
            grid,
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
