//! The world the filter runs in (MODEL.md 2, 5.3): the plan's raster and walls, the tiles, the
//! sensors' sight, the model's numbers. Built once, unchanged until the configuration changes; every
//! step borrows it.

mod build;
pub mod ld;
pub mod raster;
pub mod sight;
pub mod targets;
pub mod tiles;

pub use ld::{LdPlace, LdSight, LdTiles};
pub use raster::{CELL, Grid, Raster};
pub use sight::Sight;
pub use targets::EntryTargets;
pub use tiles::{DoorTile, POINT, Tiles};

use crate::config::Config;
use crate::filter::densities::DensityShape;
use crate::geometry::{Point, Walls};
use crate::ld2410::likelihood::Likelihood;
use crate::ld2450::rates::RateModel;
use crate::params::{OutputParams, Params, Shapes};
use crate::plan::Plan;
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
    /// per cell its room (an index into the views of the LD2410C; the last for none)
    pub rooms: Grid,
    /// the LD2410C's likelihood (its tables are built once)
    pub ld_lik: Likelihood,
    pub plan: Plan,
    /// the zones of "wird betreten"
    pub targets: EntryTargets,
}

/// What the Python tracker built (docs/rust-core.md 6): the world handed over while the build is
/// still Python, and later the aid that checks the build in Rust against it.
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

impl WorldExport {
    /// Whether the export fits together: the model's numbers, the arrays' lengths, the indices in
    /// range, each cell's room one of the rooms of a sensor's view (Err names what does not).
    pub fn check(&self) -> Result<(), String> {
        self.params.validate()?;
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
    /// The world of a configuration (world.World, tiling.Tiling, the sensors' sight; MODEL.md 2, 4, 5.3):
    /// built here from the plan, in this order, nothing changed afterwards. `silent_after`: s without a
    /// frame until a room's own sensor counts as gone (roomseen.SILENT_AFTER).
    pub fn build(config: &Config, params: Params, silent_after: f64) -> Result<Self, String> {
        params.validate()?;
        let outputs = OutputParams::of(&config.params, silent_after);
        outputs.validate()?;
        let plan = Plan::new(config);
        let raster = build::raster_of(config);
        let labels = build::labels_of(&plan, &raster);
        let rooms = Grid::new(raster, build::rooms_of(&plan, &raster, &labels));
        let walls = Walls::new(plan.walls.iter().map(|&[a, b]| [a[0], a[1], b[0], b[1]]).collect());
        let wall_dist = Grid::new(raster, build::wall_dist(&raster, &plan.walls));
        let tiles = Tiles::build(&plan, raster, &labels, rooms.values(), &walls, &params, &config.params);
        let shapes = Shapes::new(&params);
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
        let targets = EntryTargets::new(&plan, raster, &rooms, &tiles);
        Ok(Self {
            ld_lik: Likelihood::new(&params),
            params,
            outputs,
            shapes,
            rates,
            raster,
            walls,
            wall_dist,
            tiles,
            sights,
            ld,
            rooms,
            plan,
            targets,
        })
    }

    /// The world of a checked export (the aid that checks the build against Python's, docs/rust-core.md
    /// 6): the plan from the configuration, the rest as Python built it.
    pub fn from_export(e: WorldExport, config: &Config, silent_after: f64) -> Result<Self, String> {
        e.check()?;
        let outputs = OutputParams::of(&config.params, silent_after);
        outputs.validate()?;
        let plan = Plan::new(config);
        if plan.sensors.len() != e.sensors.len() || e.tiles.room.iter().flatten().any(|&k| k >= plan.rooms.len()) {
            return Err("the export does not fit the configuration".into());
        }
        let rates = RateModel::new(&e.params);
        let raster = e.raster;
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
                    view: sensor.ld.view,
                    tiles: sensor.ld.tiles,
                };
                (s, ld)
            })
            .unzip();
        let rooms = Grid::new(raster, e.rooms);
        let targets = EntryTargets::new(&plan, raster, &rooms, &e.tiles);
        Ok(Self {
            ld_lik: Likelihood::new(&e.params),
            params: e.params,
            outputs,
            shapes: e.shapes,
            rates,
            raster,
            walls: Walls::new(e.walls),
            wall_dist: Grid::new(raster, e.wall_dist),
            tiles: e.tiles,
            sights,
            ld,
            rooms,
            plan,
            targets,
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

    /// No wall within r of the point (world.World.clear).
    pub fn clear(&self, p: Point, r: f64) -> bool {
        self.wall_dist.at(p) > r
    }

    /// The share of N(mean, diag var) at each sample point of the tiles (summing to 1), the points
    /// behind a wall seen from the mean given nothing - those with a density above 1e-3 of the peak
    /// (nobody walks through a wall, the Gaussian itself does not know them); None if the Gaussian
    /// has nothing in view (tiling.Tiling.gauss_points).
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

    /// The share of N(mean, diag var) in each tile (tiling.Tiling.gauss_mass): by the sample points,
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
