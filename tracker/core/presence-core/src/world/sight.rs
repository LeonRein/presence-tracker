//! How well a sensor sees each cell (filter.Tracker._grid, MODEL.md 4.1), and what follows from it
//! per tile.

use super::raster::Grid;
use crate::geometry::Point;
use crate::ld2450::rates::RateModel;
use crate::world::Tiles;

/// A sensor's sight of a person, 0..1 per 0.1 m cell, and its rates per tile without live tracks.
#[derive(Debug)]
pub struct Sight {
    pub at: Point,
    pub grid: Grid,
    /// [STILL, WALK] rates at the tiles' sample points and per tile, without live tracks
    base_points: [Vec<f64>; 2],
    base_tiles: [Vec<f64>; 2],
}

impl Sight {
    /// A sight without its rates per tile (set_rates).
    pub fn new(at: Point, grid: Grid) -> Self {
        Self {
            at,
            grid,
            base_points: [Vec::new(), Vec::new()],
            base_tiles: [Vec::new(), Vec::new()],
        }
    }

    pub(super) fn set_rates(&mut self, rates: &RateModel, tiles: &Tiles) {
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
