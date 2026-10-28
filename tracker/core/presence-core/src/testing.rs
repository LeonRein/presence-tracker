//! What the tests share: a small deterministic random generator and a small world.

/// splitmix64: deterministic, no dependency.
pub struct Rng(u64);

impl Rng {
    pub fn new(seed: u64) -> Self {
        Self(seed)
    }

    pub fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    /// Uniform in [0, 1).
    pub fn uniform(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 / (1u64 << 53) as f64
    }

    pub fn range(&mut self, lo: f64, hi: f64) -> f64 {
        lo + (hi - lo) * self.uniform()
    }

    /// Uniform in 0..n.
    pub fn below(&mut self, n: usize) -> usize {
        (self.uniform() * n as f64) as usize
    }

    /// Standard normal (Box-Muller).
    pub fn normal(&mut self) -> f64 {
        let (u, v) = (1.0 - self.uniform(), self.uniform());
        (-2.0 * u.ln()).sqrt() * (2.0 * std::f64::consts::PI * v).cos()
    }

    /// Gamma(shape, scale 1) for shape >= 1 (Marsaglia & Tsang 2000).
    pub fn gamma(&mut self, shape: f64) -> f64 {
        let d = shape - 1.0 / 3.0;
        let c = 1.0 / (9.0 * d).sqrt();
        loop {
            let x = self.normal();
            let v = (1.0 + c * x).powi(3);
            if v > 0.0 && self.uniform().ln() < 0.5 * x * x + d - d * v + d * v.ln() {
                return d * v;
            }
        }
    }
}

use crate::config::Config;
use crate::geometry::Point;
use crate::ld2410::{FLOOR, profile::expected};
use crate::params::{Params, Shapes};
use crate::params::{STILL, WALK};
use crate::plan::RoomIdx;
use crate::world::{
    CELL, Grid, LdExport, LdPlace, LdSight, LdTiles, Raster, RoomGrid, SensorExport, Tiles, World, WorldExport,
};

/// What a person at each tile's centre puts into an LD2410C at `at` looking along `axis` (1.5 m
/// above a person's height: dh2 0.64), the tiles it sees.
fn ld_tiles(raster: Raster, at: Point, axis: Point, centers: &[Point]) -> LdTiles {
    let p = Params::default();
    let everywhere = |v: f64| Grid::new(raster, vec![v; raster.cells()]);
    let ld = LdSight {
        at,
        axis,
        dh2: 0.64,
        los: everywhere(1.0),
        view: None,
        tiles: LdTiles::default(),
    };
    let rooms = RoomGrid::new(raster, vec![Some(RoomIdx(0)); raster.cells()]);
    let mut out = LdTiles::default();
    for (c, &q) in centers.iter().enumerate() {
        let LdPlace { angle, slant, sight } = ld.place(q, &rooms);
        let (still, walk) = (
            expected(&p, STILL, angle, slant, sight),
            expected(&p, WALK, angle, slant, sight),
        );
        if still.iter().chain(&walk).any(|&x| x > FLOOR) {
            out.idx.push(c);
            out.still.push(still);
            out.walk.push(walk);
        }
    }
    out
}

/// Small shapes: two kinds of stay, two levels of detectability, two of the amplitude.
pub fn shapes() -> Shapes {
    let go = vec![1.0 / 600.0, 1.0 / 20.0];
    let go_w = vec![0.5, 0.5];
    let ongoing: Vec<f64> = go_w.iter().zip(&go).map(|(w, g)| w / g).collect();
    let sum: f64 = ongoing.iter().sum();
    Shapes {
        go_w_ongoing: ongoing.iter().map(|x| x / sum).collect(),
        go,
        go_w,
        kappa: vec![0.5, 1.5],
        kappa_w: vec![0.5, 0.5],
        amp: vec![0.5, 1.5],
        ka_w: vec![0.25; 4],
        amp_given_kappa: vec![0.5; 4],
    }
}

/// m: side of the tiles of the test world
pub const TILE: f64 = 0.5;

fn segment_distance(p: Point, [x0, y0, x1, y1]: [f64; 4]) -> f64 {
    let (dx, dy) = (x1 - x0, y1 - y0);
    let t = (((p[0] - x0) * dx + (p[1] - y0) * dy) / (dx * dx + dy * dy)).clamp(0.0, 1.0);
    (p[0] - x0 - t * dx).hypot(p[1] - y0 - t * dy)
}

/// model.TrackerParams' defaults as the shell writes them.
pub const PARAMS: &str = r#"{"target_height": 1.0, "range_sigma_base": 0.15, "range_sigma_slope": 0.02,
    "lateral_sigma_base": 0.1, "lateral_sigma_slope": 0.05, "wall_margin": 0.4, "dwell_median": 120.0,
    "dwell_spread": 1.5, "ld2410_hold": 1.5, "light_cost": 2.0, "seen_hold": 10.0, "lead_time": 2.0,
    "approach_cost": 0.05, "target_threshold": 0.8, "target_thresholds": {}}"#;

/// A configuration as the shell writes it (every field): these sensors (id, name, where, heading;
/// placed), the zones, walls and doors as JSON.
pub fn config(sensors: &[(&str, &str, Point, f64)], zones: &str, walls: &str, doors: &str) -> Config {
    let sensors = sensors
        .iter()
        .map(|(id, name, [x, y], heading)| {
            format!(
                r#"{{"id": "{id}", "name": "{name}", "x": {x}, "y": {y}, "heading": {heading}, "height": 1.5,
                "mirror": false, "scale": 1.0, "fov": 120.0, "range": 6.0, "enabled": true, "placed": true}}"#
            )
        })
        .collect::<Vec<_>>()
        .join(", ");
    let json = format!(
        r#"{{"sensors": [{sensors}], "zones": {zones}, "walls": {walls}, "doors": {doors}, "params": {PARAMS}}}"#
    );
    Config::from_json(&json).expect("a test configuration")
}

/// A wall (JSON) from a to b.
pub fn wall(a: Point, b: Point) -> String {
    format!(r#"{{"points": [{a:?}, {b:?}], "kind": "wall"}}"#)
}

/// A configuration of these sensors (ids s0, s1, ...), placed, and nothing else (for worlds of
/// fixtures: the outputs count nobody in a room).
pub fn config_of_sensors(sensors: usize) -> Config {
    let ids: Vec<String> = (0..sensors).map(|si| format!("s{si}")).collect();
    let specs: Vec<(&str, &str, Point, f64)> = ids.iter().map(|id| (id.as_str(), "", [0.0, 0.0], 0.0)).collect();
    config(&specs, "[]", "[]", "[]")
}

/// The configuration of the flat test world: the room "wohn" (6 x 5 m) with walls around, an area
/// "sofa" in its upper left corner (0..2 x 3..5), sensor 0 ("wohn") and sensor 1 ("b") in the bottom
/// corners.
pub fn flat_config() -> Config {
    let walls = [
        wall([0.0, 0.0], [6.0, 0.0]),
        wall([6.0, 0.0], [6.0, 5.0]),
        wall([6.0, 5.0], [0.0, 5.0]),
        wall([0.0, 5.0], [0.0, 0.0]),
    ];
    config(
        &[("wohn", "wohn", [0.05, 0.05], 45.0), ("b", "b", [5.95, 0.05], 135.0)],
        r#"[{"id": "wohn", "name": "Wohn", "kind": "room", "shape": "rect", "points": [[0, 0], [6, 5]]},
            {"id": "sofa", "name": "Sofa", "kind": "area", "shape": "rect", "points": [[0, 3], [2, 5]]}]"#,
        &format!("[{}]", walls.join(", ")),
        "[]",
    )
}

/// The room of test_filter.flat_config without its doors: 6 x 5 m with walls around, square tiles
/// of TILE m nobody walks between, two sensors in the bottom corners that see the whole room. The
/// room is "wohn", sensor 0 ("wohn") its own; an area "sofa" in its upper left corner (0..2 x 3..5).
pub fn flat() -> World {
    World::from_export(flat_export(), &flat_config(), 30.0).expect("the test world fits")
}

/// The export of `flat`.
pub fn flat_export() -> WorldExport {
    let raster = Raster {
        x0: -0.5,
        y0: -0.5,
        nx: 70,
        ny: 60,
    };
    let walls = vec![
        [0.0, 0.0, 6.0, 0.0],
        [6.0, 0.0, 6.0, 5.0],
        [6.0, 5.0, 0.0, 5.0],
        [0.0, 5.0, 0.0, 0.0],
    ];
    let centre = |i: usize, j: usize| [raster.x0 + (i as f64 + 0.5) * CELL, raster.y0 + (j as f64 + 0.5) * CELL];
    let inside = |p: Point| p[0] > 0.0 && p[0] < 6.0 && p[1] > 0.0 && p[1] < 5.0;
    let half_diagonal = CELL * std::f64::consts::FRAC_1_SQRT_2;
    let mut wall_dist = Vec::new();
    let mut sight = Vec::new();
    for i in 0..raster.nx {
        for j in 0..raster.ny {
            let p = centre(i, j);
            let d = walls
                .iter()
                .map(|&w| segment_distance(p, w))
                .fold(f64::INFINITY, f64::min);
            wall_dist.push((d - half_diagonal).max(0.0));
            sight.push(if inside(p) { 1.0 } else { 0.0 });
        }
    }
    let (tx, ty) = ((6.0 / TILE) as usize, (5.0 / TILE) as usize);
    let n = tx * ty;
    let tile_of = |p: Point| ((p[0] / TILE) as usize).min(tx - 1) * ty + ((p[1] / TILE) as usize).min(ty - 1);
    let centers: Vec<Point> = (0..n)
        .map(|c| [((c / ty) as f64 + 0.5) * TILE, ((c % ty) as f64 + 0.5) * TILE])
        .collect();
    let tile_centers = centers.clone(); // for the LD2410C's tiles
    let fine_xy: Vec<Point> = (0..raster.nx)
        .flat_map(|i| (0..raster.ny).map(move |j| (i, j)))
        .map(|(i, j)| centre(i, j))
        .filter(|&p| inside(p))
        .collect();
    let points: Vec<Point> = (0..30)
        .flat_map(|i| (0..25).map(move |j| [0.1 + 0.2 * i as f64, 0.1 + 0.2 * j as f64]))
        .collect();
    let point_tile: Vec<usize> = points.iter().map(|&p| tile_of(p)).collect();
    let mut point_count = vec![0.0; n];
    for &c in &point_tile {
        point_count[c] += 1.0;
    }
    let mut walk_t = vec![0.0; n * n];
    for c in 0..n {
        walk_t[c * n + c] = 1.0;
    }
    let ages = 18;
    let tiles = Tiles {
        n,
        centers,
        var: vec![[TILE * TILE / 12.0; 2]; n],
        area: vec![TILE * TILE; n],
        fine: fine_xy.iter().map(|&p| tile_of(p)).collect(),
        fine_xy,
        regions: 0,
        ages,
        tick: 0.1,
        walk_t,
        into_t: vec![0.0; n],
        door_tiles: Vec::new(),
        region_doors: Vec::new(),
        exits: Vec::new(),
        entries: Vec::new(),
        region_rates: Vec::new(),
        widths: (0..ages).map(|a| 2f64.powi(a as i32 + 1)).collect(),
        points,
        point_tile,
        point_count,
        room: vec![Some(RoomIdx(0)); n],
        squares: tile_centers
            .iter()
            .map(|c| [c[0] - TILE / 2.0, c[1] - TILE / 2.0])
            .collect(),
        region_start: Vec::new(),
        generator: Vec::new(),
        walk_end: Default::default(),
    };
    WorldExport {
        params: Params::default(),
        shapes: shapes(),
        raster,
        walls,
        wall_dist,
        tiles,
        sensors: [
            ([0.05, 0.05], [0.19, 0.19], 45f64),
            ([5.95, 0.05], [5.81, 0.19], 135f64),
        ]
        .map(|(at, origin, heading)| {
            let axis = [heading.to_radians().cos(), heading.to_radians().sin()];
            SensorExport {
                at,
                origin,
                grid: sight.clone(), // the same sight for both sensors
                observed: vec![1.0; n],
                ld: LdExport {
                    axis,
                    dh2: 0.8f64.powi(2),
                    los: sight.clone(), // as the LD2450's
                    view: None,
                    tiles: ld_tiles(raster, at, axis, &tile_centers),
                },
            }
        })
        .into(),
        rooms: vec![0.0; raster.cells()],
    }
}

/// A number written as Python's float.hex().
pub fn hexf(s: &str) -> f64 {
    let (sign, s) = s.strip_prefix('-').map_or((1.0, s), |s| (-1.0, s));
    let (mant, exp) = s.trim_start_matches("0x").split_once('p').expect("p");
    let (int, frac) = mant.split_once('.').expect(".");
    let digits = u64::from_str_radix(&format!("{int}{frac}"), 16).expect("hex");
    let e: i32 = exp.parse().expect("exponent");
    // exact also for subnormals: the digits times 2^(e - 4 len) in two steps
    let shift = e - 4 * frac.len() as i32;
    sign * digits as f64 * 2f64.powi(shift / 2) * 2f64.powi(shift - shift / 2)
}
