//! What a sensor's LD2410C sees (MODEL.md 4.3, _ld_tiles): where a
//! point is off its axis and how far, how much of a person there is in its line of sight; and what a
//! person in each tile it sees puts into its cells.
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use super::raster::Grid;
use crate::geometry::Point;
use crate::ld2410::CELLS;

/// The tiles an LD2410C sees and what a person there puts in, standing and walking (averaged over
/// each tile).
#[derive(Debug, Default)]
pub struct LdTiles {
    pub idx: Vec<usize>,
    pub still: Vec<[f64; CELLS]>,
    pub walk: Vec<[f64; CELLS]>,
}

#[derive(Debug)]
pub struct LdSight {
    pub at: Point,
    /// its axis: cos and sin of its heading
    pub axis: Point,
    /// m^2: (its height - a person's target height)^2
    pub dh2: f64,
    /// how much of a person at a cell is in its line of sight (0..1)
    pub los: Grid,
    pub tiles: LdTiles,
}

/// Where a point is for an LD2410C: degrees off its axis, slant distance, in sight (0..1).
#[derive(Debug, Clone, Copy)]
pub struct LdPlace {
    pub angle: f64,
    pub slant: f64,
    pub sight: f64,
}

impl LdSight {
    /// Where the point p is for this LD2410C.
    pub fn place(&self, p: Point) -> LdPlace {
        let (dx, dy) = (p[0] - self.at[0], p[1] - self.at[1]);
        let ahead = dx * self.axis[0] + dy * self.axis[1];
        let side = dx * self.axis[1] - dy * self.axis[0];
        let angle = side.atan2(ahead.max(1e-9)).abs().to_degrees();
        let slant = (dx * dx + dy * dy + self.dh2).sqrt();
        let sight = if ahead > 0.0 { self.los.at(p) } else { 0.0 };
        LdPlace { angle, slant, sight }
    }
}
