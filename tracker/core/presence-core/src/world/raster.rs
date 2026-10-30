//! The 0.1 m raster of the plan (MODEL.md 2): values per cell, looked up at points.
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use crate::geometry::Point;
use crate::plan::RoomIdx;

/// m: side of a raster cell
pub const CELL: f64 = 0.1;

#[derive(Debug, Clone, Copy)]
pub struct Raster {
    pub x0: f64,
    pub y0: f64,
    pub nx: usize,
    pub ny: usize,
}

impl Raster {
    /// The cell of a point, clipped to the raster, as an index into a grid.
    pub fn cell_of(&self, p: Point) -> usize {
        let clip = |v: f64, n: usize| (v as i64).clamp(0, n as i64 - 1) as usize;
        let i = clip((p[0] - self.x0) / CELL, self.nx);
        let j = clip((p[1] - self.y0) / CELL, self.ny);
        i * self.ny + j
    }

    /// The centre of cell (i, j).
    pub fn centre(&self, i: usize, j: usize) -> Point {
        [self.x0 + (i as f64 + 0.5) * CELL, self.y0 + (j as f64 + 0.5) * CELL]
    }

    pub fn cells(&self) -> usize {
        self.nx * self.ny
    }
}

/// A value per cell of the raster, (nx, ny) in row-major order like numpy's.
#[derive(Debug)]
pub struct Grid {
    raster: Raster,
    values: Vec<f64>,
}

impl Grid {
    /// Panics if the values do not fit the raster.
    pub fn new(raster: Raster, values: Vec<f64>) -> Self {
        assert_eq!(values.len(), raster.cells(), "a grid needs one value per cell");
        Self { raster, values }
    }

    pub fn raster(&self) -> &Raster {
        &self.raster
    }

    pub fn values(&self) -> &[f64] {
        &self.values
    }

    /// The value in the cell of a point.
    pub fn at(&self, p: Point) -> f64 {
        self.values[self.raster.cell_of(p)]
    }
}

/// Per cell the observed room its centre is in (filter.Tracker.room_of), None in none.
#[derive(Debug)]
pub struct RoomGrid {
    raster: Raster,
    cells: Vec<Option<RoomIdx>>,
}

impl RoomGrid {
    /// Panics if the cells do not fit the raster.
    pub fn new(raster: Raster, cells: Vec<Option<RoomIdx>>) -> Self {
        assert_eq!(cells.len(), raster.cells(), "a grid needs one value per cell");
        Self { raster, cells }
    }

    pub fn cells(&self) -> &[Option<RoomIdx>] {
        &self.cells
    }

    /// The room of the cell of a point.
    pub fn at(&self, p: Point) -> Option<RoomIdx> {
        self.cells[self.raster.cell_of(p)]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn points_outside_take_the_nearest_cell() {
        let r = Raster {
            x0: 0.0,
            y0: 0.0,
            nx: 3,
            ny: 2,
        };
        let g = Grid::new(r, (0..6).map(f64::from).collect());
        assert_eq!(g.at([0.15, 0.05]), 2.0);
        assert_eq!(g.at([-5.0, 0.15]), 1.0);
        assert_eq!(g.at([0.29, 9.0]), 5.0);
        assert_eq!(g.at([-0.05, -0.05]), 0.0); // truncated toward zero, as Python's int()
    }
}
