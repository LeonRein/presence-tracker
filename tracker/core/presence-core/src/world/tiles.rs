//! The observed area as square tiles (tiling.Tiling, MODEL.md 5.3): where a person without a track
//! can be, and how walkers move between the tiles, into the regions without a sensor and out.

use crate::geometry::Point;

/// A tile next to a door into a region or out of the house: as its small Gaussian (centre, spread
/// per axis, area) with its rates of walking through each door (tiling.Tiling.near_doors).
#[derive(Debug)]
pub struct DoorTile {
    pub center: Point,
    pub var: Point,
    pub area: f64,
    /// 1/s into each region, then out of the house (R + 1)
    pub rates: Vec<f64>,
}

/// m: the sample points of the rates are this far apart (tiling.POINT)
pub const POINT: f64 = 0.2;

#[derive(Debug)]
pub struct Tiles {
    /// number of tiles
    pub n: usize,
    /// per tile the centroid, the spread of its cells per axis (+ a cell's own) and the area
    pub centers: Vec<Point>,
    pub var: Vec<Point>,
    pub area: Vec<f64>,
    /// per 0.1 m cell in view its tile and centre (tiling.Tiling.fine, fine_xy)
    pub fine: Vec<usize>,
    pub fine_xy: Vec<Point>,
    /// regions without a sensor (R)
    pub regions: usize,
    /// age bins of a stay in a region (A)
    pub ages: usize,
    /// s: walking moves the masses in steps of this length
    pub tick: f64,
    /// one tick of walking from tile to tile, n x n as [from][to] (tiling.T_walk_t)
    pub walk_t: Vec<f64>,
    /// one tick of walking from a tile into each region and out, n x (R + 1) (tiling.T_in_t)
    pub into_t: Vec<f64>,
    pub door_tiles: Vec<DoorTile>,
    /// per region the tiles where somebody coming out of it appears
    pub region_doors: Vec<Vec<usize>>,
    /// per region its ways out of the house
    pub exits: Vec<usize>,
    /// the tiles where the ways in from outside lead straight into view
    pub entries: Vec<usize>,
    /// 1/s at which a stay in each region and age bin ends, R x A
    pub region_rates: Vec<f64>,
    /// s: width of each age bin (A)
    pub widths: Vec<f64>,
    /// sample points of the rates (0.2 m raster), their tile, and the points per tile
    pub points: Vec<Point>,
    pub point_tile: Vec<usize>,
    pub point_count: Vec<f64>,
    /// where a walk that begins in a tile ends - stopped in a tile, in a region, out - whatever its
    /// length, (n + R + 1) x n (tiling.Tiling.walk_end; for leaps over a gap in the data)
    pub walk_end: Vec<f64>,
}

impl Tiles {
    /// All ways into the house: the regions' ways out and the entries.
    pub fn ways(&self) -> usize {
        self.exits.iter().sum::<usize>() + self.entries.len()
    }

    /// The tile of a point, or the nearest one (tiling.Tiling.cell_near); None without tiles.
    pub fn cell_near(&self, p: Point) -> Option<usize> {
        let mut best: Option<(f64, usize)> = None;
        for (&[x, y], &tile) in self.fine_xy.iter().zip(&self.fine) {
            let d = (x - p[0]).powi(2) + (y - p[1]).powi(2);
            if best.is_none_or(|(b, _)| d < b) {
                best = Some((d, tile));
            }
        }
        best.map(|(_, tile)| tile)
    }

    /// Per tile the mean of a function given at the sample points (tiling.Tiling.average): summed in
    /// the order of the points, as numpy's bincount.
    pub fn average(&self, at_points: &[f64]) -> Vec<f64> {
        let mut out = vec![0.0; self.n];
        for (&tile, &v) in self.point_tile.iter().zip(at_points) {
            out[tile] += v;
        }
        for (o, &count) in out.iter_mut().zip(&self.point_count) {
            *o /= count;
        }
        out
    }
}
