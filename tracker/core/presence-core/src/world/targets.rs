//! The zones "wird betreten" is computed for (filter.Tracker._entry_targets): the observed rooms, then
//! the areas - per zone its cells, and which sample points of the tiles lie in it.

use crate::plan::Plan;
use crate::world::raster::{Grid, Raster};
use crate::world::tiles::Tiles;

#[derive(Debug, Default)]
pub struct EntryTargets {
    /// per zone, per cell of the raster whether it is in the zone
    cells: Vec<Vec<bool>>,
    /// (sample point, zone) for every point in a zone, in the order of the points
    points: Vec<(usize, usize)>,
}

impl EntryTargets {
    /// The rooms' cells are those of `rooms` (per cell its observed room, the last index for none), the
    /// areas' those whose centres they hold (world.World.zone_mask); a sample point is in its tile's room.
    pub fn new(plan: &Plan, raster: Raster, rooms: &Grid, tiles: &Tiles) -> Self {
        let mut cells: Vec<Vec<bool>> = (0..plan.rooms.len())
            .map(|k| rooms.values().iter().map(|&r| r == k as f64).collect())
            .collect();
        let centres: Vec<_> = (0..raster.nx)
            .flat_map(|i| (0..raster.ny).map(move |j| raster.centre(i, j)))
            .collect();
        cells.extend(
            plan.areas
                .iter()
                .map(|shape| centres.iter().map(|&c| shape.contains(c, 0.0)).collect()),
        );
        let mut points = Vec::new();
        for (i, (&p, &tile)) in tiles.points.iter().zip(&tiles.point_tile).enumerate() {
            if let Some(k) = tiles.room[tile] {
                points.push((i, k));
            }
            let cell = raster.cell_of(p);
            for (z, area) in cells.iter().enumerate().skip(plan.rooms.len()) {
                if area[cell] {
                    points.push((i, z));
                }
            }
        }
        Self { cells, points }
    }

    pub fn len(&self) -> usize {
        self.cells.len()
    }

    pub fn is_empty(&self) -> bool {
        self.cells.is_empty()
    }

    /// Whether a cell of the raster is in zone z.
    pub fn holds(&self, z: usize, cell: usize) -> bool {
        self.cells[z][cell]
    }

    /// Per zone the sum of a value per sample point over the points in it, in the order of the points.
    pub fn sums(&self, at_points: &[f64]) -> Vec<f64> {
        let mut out = vec![0.0; self.len()];
        for &(i, z) in &self.points {
            out[z] += at_points[i];
        }
        out
    }
}
