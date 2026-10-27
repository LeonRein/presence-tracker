//! The 0.1 m raster of the plan (world.World, MODEL.md 2): values per cell, looked up at points.

use crate::geometry::Point;

/// m: side of a raster cell (world.CELL)
pub const CELL: f64 = 0.1;

#[derive(Debug, Clone, Copy)]
#[cfg_attr(test, derive(serde::Deserialize))]
pub struct Raster {
    pub x0: f64,
    pub y0: f64,
    pub nx: usize,
    pub ny: usize,
}

impl Raster {
    /// The cell of a point, clipped to the raster (world.World.cell_of), as an index into a grid.
    pub fn cell_of(&self, p: Point) -> usize {
        let clip = |v: f64, n: usize| (v as i64).clamp(0, n as i64 - 1) as usize;
        let i = clip((p[0] - self.x0) / CELL, self.nx);
        let j = clip((p[1] - self.y0) / CELL, self.ny);
        i * self.ny + j
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

    pub fn values(&self) -> &[f64] {
        &self.values
    }

    /// The value in the cell of a point (kernels.grid_at).
    pub fn at(&self, p: Point) -> f64 {
        self.values[self.raster.cell_of(p)]
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
