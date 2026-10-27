//! The observed area as square tiles (tiling.Tiling, MODEL.md 5.3): where a person without a track
//! can be, and how walkers move between the tiles, into the regions without a sensor and out.

use crate::config::TrackerParams;
use crate::geometry::{Point, Walls};
use crate::linalg::{expm, solve};
use crate::params::Params;
use crate::plan::Plan;
use crate::world::raster::{CELL, Raster};

/// A tile next to a door into a region or out of the house: as its small Gaussian (centre, spread
/// per axis, area) with its rates of walking through each door (tiling.Tiling.near_doors).
#[derive(Debug)]
#[cfg_attr(test, derive(serde::Deserialize))]
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
#[cfg_attr(test, derive(serde::Deserialize))]
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
    #[cfg_attr(test, serde(default))]
    pub walk_end: Vec<f64>,
    /// per tile its observed room (tiling.Tiling.room; None: in none)
    #[cfg_attr(test, serde(default))]
    pub room: Vec<Option<usize>>,
    /// R x A: per region the age distribution of a stay seen at a random time (Tiling.region_start)
    #[cfg_attr(test, serde(default))]
    pub region_start: Vec<f64>,
    /// the generator of walking (n + R + 1)^2, [to][from], for walk_end
    #[cfg_attr(test, serde(default))]
    pub generator: Vec<f64>,
}

/// m: side of a tile (tiling.SIZE)
pub const SIZE: f64 = 0.4;
/// s: walking moves the tiles' masses in steps of this length (tiling.TICK)
pub const TICK: f64 = 0.1;

/// The age bins of a stay in a region (hidden.AGE_EDGES): 0, 2 s ... 36 h, each up to twice its start.
fn age_edges() -> Vec<f64> {
    std::iter::once(0.0).chain((1..18).map(|k| 2f64.powi(k))).collect()
}

/// Share of the visits to a region without a sensor that last longer than dt: log-normal with the
/// configuration's median and spread (unobserved.Dwell.survival, MODEL.md 3.3).
fn survival(p: &TrackerParams, dt: f64) -> f64 {
    if dt <= 0.0 {
        return 1.0;
    }
    0.5 * libm::erfc((dt.ln() - p.dwell_median.ln()) / (p.dwell_spread * 2f64.sqrt()))
}

impl Tiles {
    /// The tiles of the observed area (tiling.Tiling): squares of SIZE cut at the rooms' borders
    /// (`rooms`: per cell its observed room, the last index for none; `labels`: per cell its place),
    /// their sample points, and walking between them as the diffusion limit of the walking model
    /// (MODEL.md 3.2, finite volumes), into the regions across doors and out of the house; one tick of
    /// it by the matrix exponential.
    pub fn build(
        plan: &Plan,
        raster: Raster,
        labels: &[Option<usize>],
        rooms: &[f64],
        walls: &Walls,
        params: &Params,
        dwell: &TrackerParams,
    ) -> Self {
        let (nx, ny) = (raster.nx, raster.ny);
        let none = plan.rooms.len();
        // the cells in view, row-major, and their squares and rooms
        let cells: Vec<(usize, usize)> = (0..nx)
            .flat_map(|i| (0..ny).map(move |j| (i, j)))
            .filter(|&(i, j)| labels[i * ny + j] == Some(0))
            .collect();
        let at: Vec<Point> = cells.iter().map(|&(i, j)| raster.centre(i, j)).collect();
        let b = ((SIZE / CELL).round() as usize).max(1);
        let (i0, j0) = (
            cells.iter().map(|c| c.0).min().unwrap_or(0),
            cells.iter().map(|c| c.1).min().unwrap_or(0),
        );
        // (square, room; -1 none, as tracker.room_of)
        let key = |&(i, j): &(usize, usize)| {
            let room = rooms[i * ny + j] as usize;
            ((i - i0) / b, (j - j0) / b, if room == none { -1 } else { room as i64 })
        };
        // the tiles in the order of their keys (np.unique)
        let mut keys: Vec<(usize, usize, i64)> = cells.iter().map(key).collect();
        keys.sort_unstable();
        keys.dedup();
        let tile: Vec<usize> = cells
            .iter()
            .map(|c| keys.binary_search(&key(c)).expect("a key"))
            .collect();
        let n = keys.len();
        let mut count = vec![0.0; n];
        let mut sum = vec![[0.0; 2]; n];
        for (&t, p) in tile.iter().zip(&at) {
            count[t] += 1.0;
            sum[t] = [sum[t][0] + p[0], sum[t][1] + p[1]];
        }
        let centers: Vec<Point> = sum.iter().zip(&count).map(|(s, c)| [s[0] / c, s[1] / c]).collect();
        let mut spread = vec![[0.0; 2]; n];
        for (&t, p) in tile.iter().zip(&at) {
            let d = [p[0] - centers[t][0], p[1] - centers[t][1]];
            spread[t] = [spread[t][0] + d[0] * d[0], spread[t][1] + d[1] * d[1]];
        }
        let var: Vec<Point> = spread
            .iter()
            .zip(&count)
            .map(|(s, c)| [s[0] / c + CELL * CELL / 12.0, s[1] / c + CELL * CELL / 12.0])
            .collect();
        let area: Vec<f64> = count.iter().map(|c| c * CELL * CELL).collect();
        // sample points: the cells on a POINT raster, and each tile's most central one if it has none
        let p = ((POINT / CELL).round() as usize).max(1);
        let on: Vec<usize> = (0..cells.len())
            .filter(|&k| cells[k].0 % p == p / 2 && cells[k].1 % p == p / 2)
            .collect();
        let mut pick = on.clone(); // the points on the raster, then the extra ones
        let covered: std::collections::BTreeSet<usize> = on.iter().map(|&k| tile[k]).collect();
        for t in (0..n).filter(|t| !covered.contains(t)) {
            let dist = |k: usize| {
                let (dx, dy) = (at[k][0] - centers[t][0], at[k][1] - centers[t][1]);
                (dx * dx + dy * dy) + if tile[k] != t { 1e9 } else { 0.0 }
            };
            let best = (0..cells.len()).fold(0, |best, k| if dist(k) < dist(best) { k } else { best });
            pick.push(best);
        }
        let points: Vec<Point> = pick.iter().map(|&k| at[k]).collect();
        let point_tile: Vec<usize> = pick.iter().map(|&k| tile[k]).collect();
        let mut point_count = vec![0.0; n];
        for &t in &point_tile {
            point_count[t] += 1.0;
        }
        // neighbouring cells not across a wall: the borders between tiles
        let mut index = vec![usize::MAX; nx * ny];
        for (k, &(i, j)) in cells.iter().enumerate() {
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
            &pairs.iter().map(|&(a, _)| at[a]).collect::<Vec<_>>(),
            &pairs.iter().map(|&(_, c)| at[c]).collect::<Vec<_>>(),
        );
        let regions = plan.regions.len();
        let big = n + regions + 1;
        let d = (params.speed * params.speed + params.speed_spread * params.speed_spread) / (2.0 * params.turn_rate);
        let mut q = vec![0.0; big * big]; // [to][from]: tiles, regions, outside
        let mut border = vec![0.0; n * n];
        for (&(a, c), cross) in pairs.iter().zip(crossed) {
            let (ta, tc) = (tile[a], tile[c]);
            if !cross && ta != tc {
                border[ta * n + tc] += CELL;
            }
        }
        for i in 0..n {
            for j in 0..n {
                let l = border[i * n + j] + border[j * n + i];
                if l != 0.0 {
                    let dist = (centers[i][0] - centers[j][0]).hypot(centers[i][1] - centers[j][1]);
                    q[j * big + i] = d * l / (area[i] * dist.max(CELL));
                }
            }
        }
        // doors into the regions and out: a cell in view next to one of a region or an outside room
        let mut doors: Vec<((usize, usize), Vec<Point>)> = Vec::new();
        for (di, dj) in [(1i64, 0i64), (-1, 0), (0, 1), (0, -1)] {
            let next: Vec<(usize, usize)> = (0..cells.len())
                .filter_map(|k| {
                    let (ti, tj) = (cells[k].0 as i64 + di, cells[k].1 as i64 + dj);
                    let inside = ti >= 0 && ti < nx as i64 && tj >= 0 && tj < ny as i64;
                    let place = inside.then(|| labels[ti as usize * ny + tj as usize]).flatten();
                    place.filter(|&l| l > 0).map(|l| (k, l))
                })
                .collect();
            let other: Vec<Point> = next
                .iter()
                .map(|&(k, _)| raster.centre((cells[k].0 as i64 + di) as usize, (cells[k].1 as i64 + dj) as usize))
                .collect();
            let crossed = walls.crosses(&next.iter().map(|&(k, _)| at[k]).collect::<Vec<_>>(), &other);
            for (&(k, place), cross) in next.iter().zip(crossed) {
                if cross {
                    continue;
                }
                let mid = [at[k][0] + 0.5 * CELL * di as f64, at[k][1] + 0.5 * CELL * dj as f64];
                let key = (tile[k], place - 1);
                match doors.iter_mut().find(|(d, _)| *d == key) {
                    Some((_, mids)) => mids.push(mid),
                    None => doors.push((key, vec![mid])),
                }
            }
        }
        let mut door_rate = vec![vec![0.0; regions + 1]; n];
        for ((t, r), mids) in &doors {
            let m = mids.len() as f64;
            let mean = mids.iter().fold([0.0, 0.0], |s, p| [s[0] + p[0], s[1] + p[1]]);
            let mean = [mean[0] / m, mean[1] / m];
            let dd =
                2.0 * crate::filter::mixture::max2((mean[0] - centers[*t][0]).hypot(mean[1] - centers[*t][1]), CELL);
            door_rate[*t][*r] = d * m * CELL / (area[*t] * dd);
            q[(n + r) * big + t] += door_rate[*t][*r];
        }
        for t in 0..n {
            let out = (0..big).fold(0.0, |s, i| s + q[i * big + t]);
            q[t * big + t] = -out;
        }
        let door_tiles = (0..n)
            .filter(|&t| crate::vmath::pairwise_sum(&door_rate[t]) > 0.0)
            .map(|t| DoorTile {
                center: centers[t],
                var: var[t],
                area: area[t],
                rates: door_rate[t].clone(), // a tile's rates, also kept by the matrix
            })
            .collect();
        // one tick of walking
        let scaled: Vec<f64> = q.iter().map(|x| x * TICK).collect();
        let step: Vec<f64> = expm(&scaled, big)
            .iter()
            .map(|&x| if x < 1e-12 { 0.0 } else { x })
            .collect();
        let walk_t = (0..n * n).map(|k| step[(k % n) * big + k / n]).collect();
        let into_t = (0..n * (regions + 1))
            .map(|k| step[(n + k % (regions + 1)) * big + k / (regions + 1)])
            .collect();
        let mut tiles = Self {
            n,
            centers,
            var,
            area,
            fine: tile,
            fine_xy: at,
            regions,
            ages: 18,
            tick: TICK,
            walk_t,
            into_t,
            door_tiles,
            region_doors: vec![Vec::new(); regions],
            exits: plan.regions.iter().map(|r| r.exits).collect(),
            entries: Vec::new(),
            region_rates: Vec::new(),
            widths: Vec::new(),
            points,
            point_tile,
            point_count,
            walk_end: Vec::new(),
            room: keys.iter().map(|k| usize::try_from(k.2).ok()).collect(),
            region_start: Vec::new(),
            generator: q,
        };
        // where somebody coming out of a region appears; the ways in from outside straight into view
        for portal in &plan.portals {
            if let Some(c) = tiles.cell_near(portal.watch) {
                if portal.place == plan.outside() {
                    tiles.entries.push(c);
                } else {
                    tiles.region_doors[portal.place - 1].push(c);
                }
            }
        }
        let edges = age_edges();
        tiles.widths = edges
            .iter()
            .zip(edges.iter().skip(1).chain([&(edges[17] * 2.0)]))
            .map(|(a, b)| b - a)
            .collect();
        let rates: Vec<f64> = edges
            .iter()
            .zip(&tiles.widths)
            .map(|(&a, &w)| (survival(dwell, a).max(1e-12) / survival(dwell, a + w).max(1e-12)).ln() / w)
            .collect();
        let start: Vec<f64> = edges
            .iter()
            .zip(&tiles.widths)
            .map(|(&a, &w)| survival(dwell, a + 0.5 * w) * w)
            .collect();
        let total = crate::vmath::pairwise_sum(&start);
        for _ in 0..regions {
            tiles.region_rates.extend_from_slice(&rates);
            tiles.region_start.extend(start.iter().map(|s| s / total));
        }
        tiles
    }

    /// Where a walk that begins in a tile ends - stopped in a tile, gone into a region or out - whatever
    /// its length (Tiling.walk_end): mu (mu I - Q)^-1, its first n columns, shares below 1e-12 as 0.
    pub fn compute_walk_end(&self, params: &Params) -> Vec<f64> {
        let (n, big) = (self.n, self.n + self.regions + 1);
        if n == 0 {
            return Vec::new();
        }
        let mu = params.stop_rate();
        let a: Vec<f64> = (0..big * big)
            .map(|k| if k / big == k % big { mu } else { 0.0 } - self.generator[k])
            .collect();
        let b: Vec<f64> = (0..big * n).map(|k| if k / n == k % n { mu } else { 0.0 }).collect();
        solve(&a, &b, big, n)
            .into_iter()
            .map(|x| if x < 1e-12 { 0.0 } else { x })
            .collect()
    }

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
