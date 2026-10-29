//! The world the filter runs in (MODEL.md 2, 5.3): the plan's raster and walls, the tiles, the
//! sensors' sight, the model's numbers. Built once, unchanged until the configuration changes; every
//! step borrows it.
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

mod build;
pub mod ld;
pub mod raster;
pub mod sight;
pub mod targets;
pub mod tiles;

pub use ld::{LdPlace, LdSight, LdTiles};
pub use raster::{CELL, Grid, Raster, RoomGrid};
pub use sight::Sight;
pub use targets::EntryTargets;
pub use tiles::{AGE_EDGES, DoorTile, POINT, Tiles};

use crate::config::{Config, SensorConfig};
use crate::filter::densities::DensityShape;
use crate::formats::{Fingerprint, round_numpy};
use crate::frames::Reading;
use crate::geometry::{Point, Walls};
use crate::ld2410::likelihood::Likelihood;
use crate::ld2450::rates::RateModel;
use crate::learn::destination::DestGeometry;
use crate::params::{OutputParams, Params, Shapes};
use crate::plan::{Plan, RoomIdx};
use crate::sensors::SensorSet;
use crate::vmath::pairwise_sum;

#[derive(Debug)]
pub struct World {
    pub params: Params,
    pub outputs: OutputParams,
    pub shapes: Shapes,
    pub rates: RateModel,
    pub raster: Raster,
    pub walls: Walls,
    /// m: per cell a lower bound of the distance from anywhere in it to the nearest wall
    pub wall_dist: Grid,
    pub tiles: Tiles,
    /// per sensor (in the order of the configuration)
    pub sights: Vec<Sight>,
    /// per sensor its LD2410C
    pub ld: Vec<LdSight>,
    /// per sensor how it hangs: what turns its targets into floor positions
    pub mounts: Vec<crate::config::Mount>,
    /// what reading the frames needs of the tracker's parameters
    pub reading: Reading,
    /// per cell in view its observed room
    pub rooms: RoomGrid,
    /// the LD2410C's likelihood (its tables are built once)
    pub ld_lik: Likelihood,
    pub plan: Plan,
    /// the zones of "wird betreten"
    pub targets: EntryTargets,
    /// the cells and outcomes of the destination map ("Ziel")
    pub destinations: DestGeometry,
    /// the key of the configuration's geometry (Config::geometry_key): what was learned holds only under it
    pub geometry: String,
}

#[cfg(test)]
/// What the Python tracker of 0.38.1 built (docs/rust-core.md 6): a world given as arrays, for the core's
/// tests (their fixtures of the Python build and the small test worlds).
#[derive(Debug)]
#[cfg_attr(test, derive(serde::Deserialize))]
pub struct WorldExport {
    pub params: Params,
    pub shapes: Shapes,
    pub raster: Raster,
    pub walls: Vec<[f64; 4]>,
    pub wall_dist: Vec<f64>,
    pub tiles: Tiles,
    pub sensors: Vec<SensorExport>,
    /// per cell its room, the last index for none
    pub rooms: Vec<f64>,
}

#[cfg(test)]
/// A sensor as the Python tracker built it: where it is, where its sight lines start, its sight per
/// cell and how well it can test a person per tile.
#[derive(Debug)]
#[cfg_attr(test, derive(serde::Deserialize))]
pub struct SensorExport {
    pub at: Point,
    pub origin: Point,
    pub grid: Vec<f64>,
    pub observed: Vec<f64>,
    pub ld: LdExport,
}

#[cfg(test)]
/// A sensor's LD2410C as the Python tracker built it (LdSight with its line of sight per cell).
#[derive(Debug)]
#[cfg_attr(test, derive(serde::Deserialize))]
pub struct LdExport {
    pub axis: Point,
    pub dh2: f64,
    pub los: Vec<f64>,
    pub view: Option<Vec<f64>>,
    pub tiles: LdTiles,
}

#[cfg(test)]
impl WorldExport {
    /// Whether the export fits together: the model's numbers, the arrays' lengths, the indices in
    /// range, each cell's room one of the rooms of a sensor's view (Err names what does not).
    pub fn check(&self) -> Result<(), String> {
        self.params.validate().map_err(|e| e.to_string())?;
        let cells = self.raster.nx * self.raster.ny;
        let (t, n) = (&self.tiles, self.tiles.n);
        let below = |idx: &[usize], top: usize| idx.iter().all(|&i| i < top);
        let room = |x: f64| x >= 0.0 && x.fract() == 0.0;
        let mut bad: Vec<String> = [
            ("the raster", cells > 0),
            (
                "wall_dist, rooms",
                self.wall_dist.len() == cells && self.rooms.len() == cells,
            ),
            ("the rooms", self.rooms.iter().all(|&x| room(x))),
            (
                "the tiles",
                [t.centers.len(), t.var.len(), t.area.len()].iter().all(|&k| k == n),
            ),
            ("fine", t.fine.len() == t.fine_xy.len() && below(&t.fine, n)),
            (
                "walk_t, into_t",
                t.walk_t.len() == n * n && t.into_t.len() == n * (t.regions + 1),
            ),
            (
                "the regions",
                t.region_doors.len() == t.regions && t.exits.len() == t.regions,
            ),
            (
                "region_rates, widths",
                t.region_rates.len() == t.regions * t.ages && t.widths.len() == t.ages,
            ),
            (
                "region_doors, entries",
                t.region_doors.iter().all(|d| below(d, n)) && below(&t.entries, n),
            ),
            (
                "door_tiles",
                t.door_tiles.iter().all(|d| d.rates.len() == t.regions + 1),
            ),
            (
                "room, region_start",
                t.room.len() == n && t.region_start.len() == t.regions * t.ages,
            ),
            (
                "points",
                t.point_tile.len() == t.points.len() && below(&t.point_tile, n) && t.point_count.len() == n,
            ),
        ]
        .into_iter()
        .filter(|&(_, ok)| !ok)
        .map(|(what, _)| what.to_string())
        .collect();
        for (si, s) in self.sensors.iter().enumerate() {
            let ld = &s.ld;
            let tiles = &ld.tiles;
            let ok = s.grid.len() == cells
                && s.observed.len() == n
                && ld.los.len() == cells
                && below(&tiles.idx, n)
                && tiles.still.len() == tiles.idx.len()
                && tiles.walk.len() == tiles.idx.len()
                && ld
                    .view
                    .as_ref()
                    .is_none_or(|v| self.rooms.iter().all(|&x| (x as usize) < v.len()));
            if !ok {
                bad.push(format!("sensor {si}"));
            }
        }
        if bad.is_empty() {
            Ok(())
        } else {
            Err(format!("the world's export does not fit together: {}", bad.join(", ")))
        }
    }
}

impl World {
    /// The world of a configuration (the sensors' sight; MODEL.md 2, 4, 5.3):
    /// built here from the plan, in this order, nothing changed afterwards.
    pub fn build(config: &Config, params: Params) -> Result<Self, String> {
        params.validate().map_err(|e| e.to_string())?;
        let outputs = OutputParams::of(&config.params, params.silent_after);
        outputs.validate()?;
        let plan = Plan::new(config);
        let raster = build::raster_of(&plan);
        let labels = build::labels_of(&plan, &raster);
        let rooms = build::rooms_of(&plan, &raster, &labels);
        let walls = Walls::new(plan.walls.iter().map(|&[a, b]| [a[0], a[1], b[0], b[1]]).collect());
        let wall_dist = Grid::new(raster, build::wall_dist(&raster, &plan.walls));
        let tiles = Tiles::build(&plan, raster, &labels, &rooms, &walls, &params, &config.params)?;
        let rates = RateModel::new(&params);
        let built = build::Built {
            config,
            params: &params,
            plan: &plan,
            raster,
            labels: &labels,
            rooms: &rooms,
            walls: &walls,
            tiles: &tiles,
        };
        let (mut sights, ld): (Vec<Sight>, Vec<LdSight>) =
            config.sensors.iter().map(|s| build::sensor(&built, s)).unzip();
        for s in &mut sights {
            s.set_rates(&rates, &tiles);
        }
        Ok(Self {
            geometry: config.geometry_key(),
            destinations: DestGeometry::new(&plan, &raster, &labels, &rooms, &walls),
            targets: EntryTargets::new(&plan, raster, &rooms, &tiles),
            ld_lik: Likelihood::new(&params),
            shapes: Shapes::new(&params),
            params,
            outputs,
            rates,
            raster,
            walls,
            wall_dist,
            tiles,
            sights,
            ld,
            mounts: config.sensors.iter().map(SensorConfig::mount).collect(),
            reading: Reading::of(&config.params),
            rooms,
            plan,
        })
    }

    #[cfg(test)]
    /// The world of a checked export (the aid that checks the build against Python's, docs/rust-core.md
    /// 6): the plan from the configuration, the rest as Python built it.
    pub fn from_export(e: WorldExport, config: &Config) -> Result<Self, String> {
        e.check()?;
        let outputs = OutputParams::of(&config.params, e.params.silent_after);
        outputs.validate()?;
        let plan = Plan::new(config);
        let observed = plan.rooms.len();
        if plan.sensors.len() != e.sensors.len() || e.tiles.room.iter().flatten().any(|r| r.0 >= observed) {
            return Err("the export does not fit the configuration".into());
        }
        let rates = RateModel::new(&e.params);
        let raster = e.raster;
        // the export's rooms per cell: the number of rooms for none
        let rooms = RoomGrid::new(
            raster,
            e.rooms
                .iter()
                .map(|&k| (k < observed as f64).then_some(RoomIdx(k as usize)))
                .collect(),
        );
        let (sights, ld) = e
            .sensors
            .into_iter()
            .map(|sensor| {
                let mut s = Sight::new(
                    sensor.at,
                    sensor.origin,
                    Grid::new(raster, sensor.grid),
                    sensor.observed,
                );
                s.set_rates(&rates, &e.tiles);
                let ld = LdSight {
                    at: sensor.at,
                    axis: sensor.ld.axis,
                    dh2: sensor.ld.dh2,
                    los: Grid::new(raster, sensor.ld.los),
                    // the export's view has a last entry for no room
                    view: sensor.ld.view.map(|v| v[..observed].to_vec()),
                    tiles: sensor.ld.tiles,
                };
                (s, ld)
            })
            .unzip();
        let walls = Walls::new(e.walls);
        let labels = build::labels_of(&plan, &raster);
        Ok(Self {
            geometry: config.geometry_key(),
            destinations: DestGeometry::new(&plan, &raster, &labels, &rooms, &walls),
            targets: EntryTargets::new(&plan, raster, &rooms, &e.tiles),
            ld_lik: Likelihood::new(&e.params),
            params: e.params,
            outputs,
            shapes: e.shapes,
            rates,
            raster,
            walls,
            wall_dist: Grid::new(raster, e.wall_dist),
            tiles: e.tiles,
            sights,
            ld,
            mounts: config.sensors.iter().map(SensorConfig::mount).collect(),
            reading: Reading::of(&config.params),
            rooms,
            plan,
        })
    }

    /// The shape of a density over these tiles.
    pub fn density_shape(&self) -> DensityShape {
        DensityShape {
            tiles: self.tiles.n,
            kinds: self.shapes.kinds(),
            levels: self.shapes.levels(),
            regions: self.tiles.regions,
            ages: self.tiles.ages,
            amps: self.shapes.amps(),
        }
    }

    /// What a density over these tiles depends on: the tiles (where, how big,
    /// which room), the places behind the doors and the kinds of stay and detectability. A saved state
    /// (Tracker::saved_people) fits only tiles with the same fingerprint.
    pub fn fingerprint(&self) -> String {
        let t = &self.tiles;
        let mut f = Fingerprint::default();
        f.floats(t.centers.iter().flatten().map(|&x| round_numpy(x, 3)))
            .floats(t.area.iter().map(|&a| round_numpy(a, 4)))
            .floats(t.room.iter().map(|r| r.map_or(-1.0, |RoomIdx(k)| k as f64)))
            .floats(t.exits.iter().map(|&e| e as f64))
            .floats(self.shapes.go.iter().map(|&g| round_numpy(g, 12)))
            .floats(self.shapes.kappa.iter().map(|&k| round_numpy(k, 9)))
            .floats(AGE_EDGES)
            .text(&self.plan.place_names().join("|"));
        f.hex()
    }

    /// How well the best of these sensors can test a person in each tile, 0..1 (tiling.Tiling.
    /// observed, MODEL.md 5.5): 0 where none of them sees, everywhere if none watches.
    pub fn observed(&self, sensors: SensorSet) -> Vec<f64> {
        let mut out = vec![0.0; self.tiles.n];
        for si in sensors.iter() {
            for (o, &x) in out.iter_mut().zip(&self.sights[si].observed) {
                *o = f64::max(*o, x);
            }
        }
        out
    }

    /// No wall within r of the point.
    pub fn clear(&self, p: Point, r: f64) -> bool {
        self.wall_dist.at(p) > r
    }

    /// The share of N(mean, diag var) at each sample point of the tiles (summing to 1), the points
    /// behind a wall seen from the mean given nothing - those with a density above 1e-3 of the peak
    /// (nobody walks through a wall, the Gaussian itself does not know them); None if the Gaussian
    /// has nothing in view.
    pub fn gauss_points(&self, mean: Point, var: Point) -> Option<Vec<f64>> {
        let tiles = &self.tiles;
        let cell = POINT * POINT / 12.0;
        let (vx, vy) = (var[0] + cell, var[1] + cell);
        let mut d: Vec<f64> = tiles
            .points
            .iter()
            .map(|&[x, y]| {
                let (dx, dy) = (x - mean[0], y - mean[1]);
                (-0.5 * (dx * dx / vx + dy * dy / vy)).exp()
            })
            .collect();
        let reach = 3.72 * (var[0].max(var[1]) + cell).sqrt();
        if !self.clear(mean, reach) {
            let near: Vec<usize> = (0..d.len()).filter(|&i| d[i] > 1e-3).collect();
            if !near.is_empty() {
                let to: Vec<Point> = near.iter().map(|&i| tiles.points[i]).collect();
                let behind = self.walls.crosses(&vec![mean; near.len()], &to);
                if behind.iter().any(|&b| b) && !behind.iter().all(|&b| b) {
                    for (&i, _) in near.iter().zip(&behind).filter(|(_, b)| **b) {
                        d[i] = 0.0;
                    }
                }
            }
        }
        let s = pairwise_sum(&d);
        (s > 0.0).then(|| d.iter().map(|x| x / s).collect())
    }

    /// The share of N(mean, diag var) in each tile: by the sample points,
    /// or all of it in the nearest tile if it has nothing in view.
    pub fn gauss_mass(&self, mean: Point, var: Point) -> Vec<f64> {
        let tiles = &self.tiles;
        let mut out = vec![0.0; tiles.n];
        match self.gauss_points(mean, var) {
            Some(d) => {
                for (&tile, &v) in tiles.point_tile.iter().zip(&d) {
                    out[tile] += v;
                }
            }
            None => {
                if let Some(c) = tiles.cell_near(mean) {
                    out[c] = 1.0;
                }
            }
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use crate::testing;

    #[test]
    fn a_plan_with_more_tiles_than_the_core_holds_is_refused_before_it_is_built() {
        // a hall of 30 x 30 m in view: ~5600 tiles, a walking matrix of 250 MB and its exponential
        let ids: Vec<String> = (0..12).map(|k| format!("s{k}")).collect();
        let sensors: Vec<(&str, &str, crate::geometry::Point, f64)> = ids
            .iter()
            .enumerate()
            .map(|(k, id)| {
                (
                    id.as_str(),
                    "",
                    [5.0 + 10.0 * (k % 3) as f64, 0.05 + 7.5 * (k / 3) as f64],
                    90.0,
                )
            })
            .collect();
        let hall = r#"[{"id": "h", "name": "H", "kind": "room", "shape": "rect", "points": [[0, 0], [30, 30]]}]"#;
        let json = testing::config_json(&sensors, hall, "[]", "[]").replace(r#""range": 6.0"#, r#""range": 10.0"#);
        let config = crate::config::Config::from_json(&json).expect("a configuration");
        assert_eq!(
            crate::plan::Plan::new(&config).observed().count(),
            1,
            "the hall is in view"
        );
        let err = super::World::build(&config, crate::params::Params::default()).expect_err("too large");
        assert!(err.contains("more than 4000"), "{err}");
    }

    #[test]
    fn an_export_that_does_not_fit_together_is_refused() {
        assert!(testing::flat_export().check().is_ok());
        let mut e = testing::flat_export();
        e.rooms[3] = -1.0;
        e.sensors[1].ld.tiles.idx[0] = e.tiles.n;
        let err = e.check().expect_err("does not fit");
        assert!(err.contains("the rooms") && err.contains("sensor 1"), "{err}");
        let mut e = testing::flat_export();
        e.params.ld_memory = 0.0;
        assert!(e.check().expect_err("no model").contains("ld_memory"));
    }
}
