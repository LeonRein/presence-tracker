//! The observed area as square tiles (MODEL.md 5.3): where a person without a track
//! can be, and how walkers move between the tiles, into the regions without a sensor and out.
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use std::sync::OnceLock;

use crate::config::TrackerParams;
use crate::geometry::{Point, Walls};
use crate::linalg::{expm, solve};
use crate::params::Params;
use crate::plan::{Place, Plan, RegionIdx, RoomIdx};
use crate::vmath::{max2, pairwise_sum, py_hypot};
use crate::world::raster::{CELL, Raster, RoomGrid};

/// A tile next to a door into a region or out of the house: as its small Gaussian (centre, spread
/// per axis, area) with its rates of walking through each door.
#[derive(Debug)]
pub struct DoorTile {
    pub center: Point,
    pub var: Point,
    pub area: f64,
    /// 1/s into each region, then out of the house (R + 1)
    pub rates: Vec<f64>,
}

/// m: the sample points of the rates are this far apart
pub const POINT: f64 = 0.2;

#[derive(Debug)]
pub struct Tiles {
    /// number of tiles
    pub n: usize,
    /// per tile the centroid, the spread of its cells per axis (+ a cell's own) and the area
    pub centers: Vec<Point>,
    pub var: Vec<Point>,
    pub area: Vec<f64>,
    /// per 0.1 m cell in view its tile and centre (fine_xy)
    pub fine: Vec<usize>,
    pub fine_xy: Vec<Point>,
    /// regions without a sensor (R)
    pub regions: usize,
    /// age bins of a stay in a region (A)
    pub ages: usize,
    /// s: walking moves the masses in steps of this length
    pub tick: f64,
    /// one tick of walking from tile to tile, n x n as [from][to]
    pub walk_t: Vec<f64>,
    /// one tick of walking from a tile into each region and out, n x (R + 1)
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
    /// per tile its observed room (None: in none)
    pub room: Vec<Option<RoomIdx>>,
    /// per tile the lower left corner of its square of SIZE m, which its room cuts (for the display)
    pub squares: Vec<Point>,
    /// R x A: per region the age distribution of a stay seen at a random time
    pub region_start: Vec<f64>,
    /// the generator of walking (n + R + 1)^2, [to][from], for walk_end
    pub generator: Vec<f64>,
    /// walk_end, filled once when it is first asked for (a OnceLock is set only once: the tiles stay
    /// as built)
    pub walk_end: OnceLock<Vec<f64>>,
}

/// m: side of a tile
pub const SIZE: f64 = 0.4;
/// tiles at most (the live house has ~700; 4000 are 640 m^2 in view): the walking matrix is dense
pub const MAX_TILES: usize = 4000;
/// s: walking moves the tiles' masses in steps of this length
pub const TICK: f64 = 0.1;
/// s: the starts of the age bins of a stay in a region: 0, 2 s ... 36 h; each bin
/// reaches to the next start, the last to twice its own
pub const AGE_EDGES: [f64; 18] = [
    0.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0, 128.0, 256.0, 512.0, 1024.0, 2048.0, 4096.0, 8192.0, 16384.0, 32768.0,
    65536.0, 131072.0,
];
/// the age bins of a stay in a region (A)
pub const AGES: usize = AGE_EDGES.len();

/// What the tiles are built on: the raster, the place of each cell, the walls.
struct Floor<'a> {
    raster: Raster,
    labels: &'a [Option<Place>],
    walls: &'a Walls,
}

/// The cells in view, row-major, and their centres.
struct Cells {
    at: Vec<(usize, usize)>,
    centre: Vec<Point>,
}

/// The tiles: per cell in view its tile, per tile its square and room (np.unique's order of
/// (square i, square j, room; -1 none)).
struct Layout {
    tile: Vec<usize>,
    keys: Vec<(usize, usize, i64)>,
    squares: Vec<Point>,
}

/// Per tile its centroid, the spread of its cells per axis (+ a cell's own) and its area.
struct Moments {
    centers: Vec<Point>,
    var: Vec<Point>,
    area: Vec<f64>,
}

/// The sample points of the rates: the cells on a POINT raster, and each tile's most central one if it
/// has none; their tile; the points per tile.
struct Samples {
    points: Vec<Point>,
    point_tile: Vec<usize>,
    point_count: Vec<f64>,
}

/// Per stay in a region its rates of ending per age bin and the age seen at a random time (from the
/// log-normal stays of unobserved.Dwell, MODEL.md 3.3).
struct RegionStays {
    widths: Vec<f64>,
    rates: Vec<f64>,
    start: Vec<f64>,
}

/// The tile of the cell nearest to p, None without cells.
fn nearest_tile(centres: &[Point], tile: &[usize], p: Point) -> Option<usize> {
    let mut best: Option<(f64, usize)> = None;
    for (&[x, y], &t) in centres.iter().zip(tile) {
        let d = (x - p[0]).powi(2) + (y - p[1]).powi(2);
        if best.is_none_or(|(b, _)| d < b) {
            best = Some((d, t));
        }
    }
    best.map(|(_, t)| t)
}

fn cells_in_view(raster: Raster, labels: &[Option<Place>]) -> Cells {
    let at: Vec<(usize, usize)> = (0..raster.nx)
        .flat_map(|i| (0..raster.ny).map(move |j| (i, j)))
        .filter(|&(i, j)| labels[i * raster.ny + j] == Some(Place::Observed))
        .collect();
    let centre = at.iter().map(|&(i, j)| raster.centre(i, j)).collect();
    Cells { at, centre }
}

fn layout(cells: &Cells, raster: Raster, rooms: &RoomGrid) -> Layout {
    let b = ((SIZE / CELL).round() as usize).max(1);
    let i0 = cells.at.iter().map(|c| c.0).min().unwrap_or(0);
    let j0 = cells.at.iter().map(|c| c.1).min().unwrap_or(0);
    let key = |&(i, j): &(usize, usize)| {
        let room = rooms.cells()[i * raster.ny + j].map_or(-1, |RoomIdx(k)| k as i64);
        ((i - i0) / b, (j - j0) / b, room)
    };
    let mut keys: Vec<(usize, usize, i64)> = cells.at.iter().map(key).collect();
    keys.sort_unstable();
    keys.dedup();
    let tile = cells
        .at
        .iter()
        .map(|c| keys.binary_search(&key(c)).expect("a key"))
        .collect();
    let corner = [raster.x0 + i0 as f64 * CELL, raster.y0 + j0 as f64 * CELL];
    let squares = keys
        .iter()
        .map(|&(bi, bj, _)| [corner[0] + (bi * b) as f64 * CELL, corner[1] + (bj * b) as f64 * CELL])
        .collect();
    Layout { tile, keys, squares }
}

fn moments(cells: &Cells, layout: &Layout) -> Moments {
    let n = layout.keys.len();
    let mut count = vec![0.0; n];
    let mut sum = vec![[0.0; 2]; n];
    for (&t, p) in layout.tile.iter().zip(&cells.centre) {
        count[t] += 1.0;
        sum[t] = [sum[t][0] + p[0], sum[t][1] + p[1]];
    }
    let centers: Vec<Point> = sum.iter().zip(&count).map(|(s, c)| [s[0] / c, s[1] / c]).collect();
    let mut spread = vec![[0.0; 2]; n];
    for (&t, p) in layout.tile.iter().zip(&cells.centre) {
        let d = [p[0] - centers[t][0], p[1] - centers[t][1]];
        spread[t] = [spread[t][0] + d[0] * d[0], spread[t][1] + d[1] * d[1]];
    }
    let var = spread
        .iter()
        .zip(&count)
        .map(|(s, c)| [s[0] / c + CELL * CELL / 12.0, s[1] / c + CELL * CELL / 12.0])
        .collect();
    Moments {
        area: count.iter().map(|c| c * CELL * CELL).collect(),
        centers,
        var,
    }
}

fn samples(cells: &Cells, layout: &Layout, centers: &[Point]) -> Samples {
    let n = layout.keys.len();
    let p = ((POINT / CELL).round() as usize).max(1);
    let on: Vec<usize> = (0..cells.at.len())
        .filter(|&k| cells.at[k].0 % p == p / 2 && cells.at[k].1 % p == p / 2)
        .collect();
    let covered: std::collections::BTreeSet<usize> = on.iter().map(|&k| layout.tile[k]).collect();
    let mut pick = on; // the points on the raster, then the extra ones
    for t in (0..n).filter(|t| !covered.contains(t)) {
        let dist = |k: usize| {
            let (dx, dy) = (cells.centre[k][0] - centers[t][0], cells.centre[k][1] - centers[t][1]);
            (dx * dx + dy * dy) + if layout.tile[k] != t { 1e9 } else { 0.0 }
        };
        pick.push((0..cells.at.len()).fold(0, |best, k| if dist(k) < dist(best) { k } else { best }));
    }
    let point_tile: Vec<usize> = pick.iter().map(|&k| layout.tile[k]).collect();
    let mut point_count = vec![0.0; n];
    for &t in &point_tile {
        point_count[t] += 1.0;
    }
    Samples {
        points: pick.iter().map(|&k| cells.centre[k]).collect(),
        point_tile,
        point_count,
    }
}

/// The walkers' diffusion (MODEL.md 3.2: D = E[s^2] / (2 lambda) per axis).
fn diffusion(params: &Params) -> f64 {
    (params.speed * params.speed + params.speed_spread * params.speed_spread) / (2.0 * params.turn_rate)
}

/// m: per pair of tiles the border they share, n x n (neighbouring cells of two tiles not across a wall,
/// a cell's side each, in the order of the pairs of cells).
fn borders(cells: &Cells, layout: &Layout, floor: &Floor) -> Vec<f64> {
    let (raster, walls) = (floor.raster, floor.walls);
    let (nx, ny, n) = (raster.nx, raster.ny, layout.keys.len());
    let mut index = vec![usize::MAX; nx * ny];
    for (k, &(i, j)) in cells.at.iter().enumerate() {
        index[i * ny + j] = k;
    }
    let mut pairs: Vec<(usize, usize)> = Vec::new();
    for (di, dj) in [(1, 0), (0, 1)] {
        for i in 0..nx - di {
            for j in 0..ny - dj {
                let (a, c) = (index[i * ny + j], index[(i + di) * ny + j + dj]);
                if a != usize::MAX && c != usize::MAX {
                    pairs.push((a, c));
                }
            }
        }
    }
    let crossed = walls.crosses(
        &pairs.iter().map(|&(a, _)| cells.centre[a]).collect::<Vec<_>>(),
        &pairs.iter().map(|&(_, c)| cells.centre[c]).collect::<Vec<_>>(),
    );
    let mut border = vec![0.0; n * n];
    for (&(a, c), cross) in pairs.iter().zip(crossed) {
        let (ta, tc) = (layout.tile[a], layout.tile[c]);
        if !cross && ta != tc {
            border[ta * n + tc] += CELL;
        }
    }
    border
}

/// 1/s: per tile its rates of walking through a door into each region and out of the house (R + 1): a
/// cell in view next to one of a region or an outside room, not across a wall, its side twice as far
/// as the door from the tile's centre.
fn door_rates(cells: &Cells, layout: &Layout, m: &Moments, floor: &Floor, d: f64, regions: usize) -> Vec<Vec<f64>> {
    let (raster, labels, walls) = (floor.raster, floor.labels, floor.walls);
    let ny = raster.ny;
    let mut doors: Vec<((usize, usize), Vec<Point>)> = Vec::new();
    for (di, dj) in [(1i64, 0i64), (-1, 0), (0, 1), (0, -1)] {
        let next: Vec<(usize, usize, usize)> = (0..cells.at.len())
            .filter_map(|k| {
                let (ti, tj) = (cells.at[k].0 as i64 + di, cells.at[k].1 as i64 + dj);
                let inside = ti >= 0 && ti < raster.nx as i64 && tj >= 0 && tj < ny as i64;
                // a region's or outside: its door is the place's index among the places but observed
                let place = inside.then(|| labels[ti as usize * ny + tj as usize]).flatten()?;
                (place != Place::Observed).then(|| (k, place.index(regions) - 1, ti as usize * ny + tj as usize))
            })
            .collect();
        let other: Vec<Point> = next.iter().map(|&(_, _, c)| raster.centre(c / ny, c % ny)).collect();
        let starts: Vec<Point> = next.iter().map(|&(k, _, _)| cells.centre[k]).collect();
        for (&(k, door, _), cross) in next.iter().zip(walls.crosses(&starts, &other)) {
            if cross {
                continue;
            }
            let mid = [
                cells.centre[k][0] + 0.5 * CELL * di as f64,
                cells.centre[k][1] + 0.5 * CELL * dj as f64,
            ];
            let key = (layout.tile[k], door);
            match doors.iter_mut().find(|(d, _)| *d == key) {
                Some((_, mids)) => mids.push(mid),
                None => doors.push((key, vec![mid])),
            }
        }
    }
    let mut rate = vec![vec![0.0; regions + 1]; layout.keys.len()];
    for ((t, r), mids) in &doors {
        let count = mids.len() as f64;
        let sum = mids.iter().fold([0.0, 0.0], |s, p| [s[0] + p[0], s[1] + p[1]]);
        let mean = [sum[0] / count, sum[1] / count];
        let dd = 2.0 * max2(py_hypot(mean[0] - m.centers[*t][0], mean[1] - m.centers[*t][1]), CELL);
        rate[*t][*r] = d * count * CELL / (m.area[*t] * dd);
    }
    rate
}

/// The generator of walking, (n + R + 1)^2 as [to][from] (tiles, regions, outside): between
/// tiles D L / (A d) by finite volumes (L the border, A the area, d the centres' distance), through the
/// doors at their rates; its diagonal what leaves each tile.
fn generator(m: &Moments, border: &[f64], door_rate: &[Vec<f64>], d: f64, regions: usize) -> Vec<f64> {
    let n = m.area.len();
    let big = n + regions + 1;
    let mut q = vec![0.0; big * big];
    for i in 0..n {
        for j in 0..n {
            let l = border[i * n + j] + border[j * n + i];
            if l != 0.0 {
                let dist = py_hypot(m.centers[i][0] - m.centers[j][0], m.centers[i][1] - m.centers[j][1]);
                q[j * big + i] = d * l / (m.area[i] * dist.max(CELL));
            }
        }
    }
    for (t, rates) in door_rate.iter().enumerate() {
        for (r, &rate) in rates.iter().enumerate() {
            if rate != 0.0 {
                q[(n + r) * big + t] += rate;
            }
        }
    }
    for t in 0..n {
        let out = (0..big).fold(0.0, |s, i| s + q[i * big + t]);
        q[t * big + t] = -out;
    }
    q
}

/// One tick of walking from the generator (tiling: expm(Q TICK)), shares below 1e-12 as 0: tile to
/// tile as [from][to], n x n, and tile into each region and out, n x (R + 1).
fn walk_tick(q: &[f64], n: usize, regions: usize) -> Result<(Vec<f64>, Vec<f64>), String> {
    let big = n + regions + 1;
    let scaled: Vec<f64> = q.iter().map(|x| x * TICK).collect();
    let step: Vec<f64> = expm(&scaled, big)?
        .iter()
        .map(|&x| if x < 1e-12 { 0.0 } else { x })
        .collect();
    let walk_t = (0..n * n).map(|k| step[(k % n) * big + k / n]).collect();
    let into_t = (0..n * (regions + 1))
        .map(|k| step[(n + k % (regions + 1)) * big + k / (regions + 1)])
        .collect();
    Ok((walk_t, into_t))
}

impl RegionStays {
    /// Share of the visits to a region without a sensor that last longer than dt: log-normal with the
    /// configuration's median and spread (MODEL.md 3.3).
    fn survival(p: &TrackerParams, dt: f64) -> f64 {
        if dt <= 0.0 {
            return 1.0;
        }
        0.5 * libm::erfc((dt.ln() - p.dwell_median.ln()) / (p.dwell_spread * 2f64.sqrt()))
    }

    fn new(p: &TrackerParams) -> Self {
        let ends = AGE_EDGES.iter().skip(1).copied().chain([AGE_EDGES[AGES - 1] * 2.0]);
        let widths: Vec<f64> = AGE_EDGES.iter().zip(ends).map(|(a, b)| b - a).collect();
        let rates = AGE_EDGES
            .iter()
            .zip(&widths)
            .map(|(&a, &w)| (Self::survival(p, a).max(1e-12) / Self::survival(p, a + w).max(1e-12)).ln() / w)
            .collect();
        let seen: Vec<f64> = AGE_EDGES
            .iter()
            .zip(&widths)
            .map(|(&a, &w)| Self::survival(p, a + 0.5 * w) * w)
            .collect();
        let total = pairwise_sum(&seen);
        Self {
            widths,
            rates,
            start: seen.iter().map(|s| s / total).collect(),
        }
    }
}

/// mu (mu I - Q)^-1, its first n columns ((n + R + 1) x n), shares below 1e-12 as 0: where a walk that
/// stops at the rate mu ends, of the generator q ([to][from], (n + R + 1)^2).
fn walk_end_of(q: &[f64], n: usize, regions: usize, mu: f64) -> Vec<f64> {
    let big = n + regions + 1;
    if n == 0 {
        return Vec::new();
    }
    let a: Vec<f64> = (0..big * big)
        .map(|k| if k / big == k % big { mu } else { 0.0 } - q[k])
        .collect();
    let b: Vec<f64> = (0..big * n).map(|k| if k / n == k % n { mu } else { 0.0 }).collect();
    solve(&a, &b, big, n)
        .into_iter()
        .map(|x| if x < 1e-12 { 0.0 } else { x })
        .collect()
}

impl Tiles {
    /// The tiles of the observed area: squares of SIZE cut at the rooms' borders, their
    /// sample points, and walking between them as the diffusion limit of the walking model (MODEL.md 3.2,
    /// finite volumes), into the regions across doors and out of the house; one tick of it by the
    /// matrix exponential. Err if the walking model has no finite generator.
    pub fn build(
        plan: &Plan,
        raster: Raster,
        labels: &[Option<Place>],
        rooms: &RoomGrid,
        walls: &Walls,
        params: &Params,
        dwell: &TrackerParams,
    ) -> Result<Self, String> {
        let regions = plan.regions.len();
        let cells = cells_in_view(raster, labels);
        let layout = layout(&cells, raster, rooms);
        if layout.keys.len() > MAX_TILES {
            // the walking matrix is n x n and its exponential O(n^3): refused before it takes all memory
            return Err(format!(
                "{} tiles, more than {MAX_TILES}: the plan is too large",
                layout.keys.len()
            ));
        }
        let m = moments(&cells, &layout);
        let samples = samples(&cells, &layout, &m.centers);
        let d = diffusion(params);
        let floor = Floor { raster, labels, walls };
        let door_rate = door_rates(&cells, &layout, &m, &floor, d, regions);
        let q = generator(&m, &borders(&cells, &layout, &floor), &door_rate, d, regions);
        let n = layout.keys.len();
        let (walk_t, into_t) = walk_tick(&q, n, regions)?;
        let door_tiles = (0..n)
            .filter(|&t| pairwise_sum(&door_rate[t]) > 0.0)
            .map(|t| DoorTile {
                center: m.centers[t],
                var: m.var[t],
                area: m.area[t],
                rates: door_rate[t].clone(), // the tile's rates, the generator keeps them too
            })
            .collect();
        // where somebody coming out of a region appears; the ways in from outside straight into view
        let near = |p: Point| nearest_tile(&cells.centre, &layout.tile, p);
        let mut region_doors = vec![Vec::new(); regions];
        let mut entries = Vec::new();
        for portal in &plan.portals {
            if let Some(c) = near(portal.watch) {
                match portal.place {
                    Place::Region(RegionIdx(r)) => region_doors[r].push(c),
                    Place::Outside => entries.push(c),
                    Place::Observed => {}
                }
            }
        }
        let stays = RegionStays::new(dwell);
        Ok(Self {
            n,
            room: layout
                .keys
                .iter()
                .map(|k| usize::try_from(k.2).ok().map(RoomIdx))
                .collect(),
            squares: layout.squares,
            centers: m.centers,
            var: m.var,
            area: m.area,
            fine: layout.tile,
            fine_xy: cells.centre,
            regions,
            ages: AGES,
            tick: TICK,
            walk_t,
            into_t,
            door_tiles,
            region_doors,
            exits: plan.regions.iter().map(|r| r.exits).collect(),
            entries,
            region_rates: stays.rates.repeat(regions),
            region_start: stays.start.repeat(regions),
            widths: stays.widths,
            points: samples.points,
            point_tile: samples.point_tile,
            point_count: samples.point_count,
            generator: q,
            walk_end: OnceLock::new(),
        })
    }

    /// Where a walk that begins in a tile ends - stopped in a tile, gone into a region or out - whatever
    /// its length, (n + R + 1) x n: mu (mu I - Q)^-1, its first n columns, shares
    /// below 1e-12 as 0. Solved when first needed (for the leaps of a long gap), then kept.
    pub fn walk_end(&self, params: &Params) -> &[f64] {
        self.walk_end
            .get_or_init(|| walk_end_of(&self.generator, self.n, self.regions, params.stop_rate()))
    }

    /// Whether these are the same tiles as other's: the same number, centres, areas, rooms, regions and
    /// ages bit for bit (a density over the one is one over the other; the people_state fingerprint's
    /// numbers, compared exactly).
    pub fn same_as(&self, other: &Self) -> bool {
        let bits = |v: &[Point]| v.iter().flatten().map(|x| x.to_bits()).collect::<Vec<_>>();
        self.n == other.n
            && self.regions == other.regions
            && self.ages == other.ages
            && self.room == other.room
            && self.exits == other.exits
            && bits(&self.centers) == bits(&other.centers)
            && self
                .area
                .iter()
                .map(|x| x.to_bits())
                .eq(other.area.iter().map(|x| x.to_bits()))
    }

    /// All ways into the house: the regions' ways out and the entries.
    pub fn ways(&self) -> usize {
        self.exits.iter().sum::<usize>() + self.entries.len()
    }

    /// The tile of a point, or the nearest one; None without tiles.
    pub fn cell_near(&self, p: Point) -> Option<usize> {
        nearest_tile(&self.fine_xy, &self.fine, p)
    }

    /// Per tile the mean of a function given at the sample points: summed in
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::world::World;

    #[test]
    fn a_walk_ends_in_its_tile_or_out_as_worked_out_by_hand() {
        // one tile, out of the house at the rate l: (mu I - Q) x = mu e_0 gives
        // stopped in the tile mu / (mu + l), out l / (mu + l)
        let (mu, l) = (0.425, 0.3);
        let q = [-l, 0.0, l, 0.0];
        let end = walk_end_of(&q, 1, 0, mu);
        assert!(
            (end[0] - mu / (mu + l)).abs() < 1e-15 && (end[1] - l / (mu + l)).abs() < 1e-15,
            "{end:?}"
        );
    }

    #[test]
    fn two_tiles_share_their_border() {
        // a room of 0.8 x 0.4 m: two squares of 0.4 m, 16 cells each, a border of 0.4 m
        let config = crate::testing::config(
            &[],
            r#"[{"id": "r", "name": "R", "kind": "room", "shape": "rect", "points": [[0, 0], [0.8, 0.4]]}]"#,
            "[]",
            "[]",
        );
        let world = World::build(&config, Params::default()).expect("a world");
        let t = &world.tiles;
        assert_eq!((t.n, t.regions, t.area.clone()), (2, 0, vec![16.0 * CELL * CELL; 2]));
        for (c, want) in t.centers.iter().zip([[0.2, 0.2], [0.6, 0.2]]) {
            assert!(
                (c[0] - want[0]).abs() < 1e-12 && (c[1] - want[1]).abs() < 1e-12,
                "{c:?}"
            );
        }
        assert_eq!(t.points.len(), 8);
        // D L / (A d) both ways, what leaves on the diagonal (3: tiles and outside)
        let d = diffusion(&world.params);
        let rate = d * 0.4 / (0.16 * 0.4);
        let q = &t.generator;
        assert!((q[3] - rate).abs() < 1e-12 && (q[1] - rate).abs() < 1e-12 && (q[0] + rate).abs() < 1e-12);
        // one tick keeps everybody in the room, the same both ways
        assert!((t.walk_t[0] + t.walk_t[1] - 1.0).abs() < 1e-12 && (t.walk_t[1] - t.walk_t[2]).abs() < 1e-15);
    }
}
