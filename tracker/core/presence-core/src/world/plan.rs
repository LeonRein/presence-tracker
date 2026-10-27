//! What the outputs need of the plan (MODEL.md 2, 6): the observed rooms with their outlines, which
//! sensor is whose room, the zones "wird betreten" is computed for, the regions' stays at a random
//! time. Until the plan is built here (phase 6) it comes from the Python build (PlanExport).

use crate::geometry::{Point, Shape};
use crate::world::raster::{Grid, Raster};
use crate::world::tiles::Tiles;

/// An observed room (model.Config.observed_rooms): the rooms of the outputs, in this order.
#[derive(Debug)]
pub struct Room {
    pub id: String,
    pub shape: Shape,
}

/// A sensor as the outputs know it: its room (roomseen.RoomSeen.own_room: named like the room, else
/// mounted in exactly one) and whether it is in use (enabled and placed).
#[derive(Debug)]
pub struct SensorPlan {
    pub id: String,
    pub own_room: Option<usize>,
    pub in_use: bool,
}

/// What the Python build gives of the plan.
#[derive(Debug, Default)]
pub struct PlanExport {
    pub rooms: Vec<Room>,
    pub sensors: Vec<SensorPlan>,
    /// the areas (model.Config.home_zones("area")): their outlines
    pub areas: Vec<Shape>,
    /// per tile its room, None outside the observed rooms (tiling.Tiling.room)
    pub tile_room: Vec<Option<usize>>,
    /// R x A: per region the age distribution of a stay seen at a random time (tiling.Tiling.region_start)
    pub region_start: Vec<f64>,
}

#[derive(Debug)]
pub struct Plan {
    pub rooms: Vec<Room>,
    pub sensors: Vec<SensorPlan>,
    pub tile_room: Vec<Option<usize>>,
    pub region_start: Vec<f64>,
    pub targets: EntryTargets,
}

/// The zones "wird betreten" is computed for (Tracker._entry_targets): the observed rooms, then the
/// areas - per zone its cells, and which sample points of the tiles lie in it.
#[derive(Debug, Default)]
pub struct EntryTargets {
    /// per zone, per cell of the raster whether it is in the zone
    cells: Vec<Vec<bool>>,
    /// (sample point, zone) for every point in a zone, in the order of the points
    points: Vec<(usize, usize)>,
}

impl Plan {
    /// The plan of an export on these tiles: the rooms' cells are those of `rooms` (per cell its
    /// room, the last index for none), the areas' those whose centres they hold (world.World.zone_mask).
    pub fn new(e: PlanExport, raster: Raster, rooms: &Grid, tiles: &Tiles) -> Self {
        let mut cells: Vec<Vec<bool>> = (0..e.rooms.len())
            .map(|k| rooms.values().iter().map(|&r| r == k as f64).collect())
            .collect();
        let centres: Vec<Point> = (0..raster.nx)
            .flat_map(|i| (0..raster.ny).map(move |j| raster.centre(i, j)))
            .collect();
        cells.extend(
            e.areas
                .iter()
                .map(|shape| centres.iter().map(|&c| shape.contains(c)).collect()),
        );
        let mut points = Vec::new();
        for (i, (&p, &tile)) in tiles.points.iter().zip(&tiles.point_tile).enumerate() {
            if let Some(k) = e.tile_room[tile] {
                points.push((i, k));
            }
            let cell = raster.cell_of(p);
            for (z, area) in cells.iter().enumerate().skip(e.rooms.len()) {
                if area[cell] {
                    points.push((i, z));
                }
            }
        }
        Self {
            rooms: e.rooms,
            sensors: e.sensors,
            tile_room: e.tile_room,
            region_start: e.region_start,
            targets: EntryTargets { cells, points },
        }
    }

    /// Whether the export fits the tiles (Err names what does not).
    pub fn check(e: &PlanExport, tiles: &Tiles, sensors: usize) -> Result<(), String> {
        let rooms = e.rooms.len();
        let mut bad = Vec::new();
        if e.tile_room.len() != tiles.n || e.tile_room.iter().flatten().any(|&k| k >= rooms) {
            bad.push("tile_room");
        }
        if e.region_start.len() != tiles.regions * tiles.ages {
            bad.push("region_start");
        }
        if e.sensors.len() != sensors || e.sensors.iter().any(|s| s.own_room.is_some_and(|k| k >= rooms)) {
            bad.push("the sensors' rooms");
        }
        if bad.is_empty() {
            Ok(())
        } else {
            Err(format!("the plan's export does not fit the tiles: {}", bad.join(", ")))
        }
    }

    /// The observed room a point is in: the first whose outline holds it (roomseen.RoomSeen.frame).
    pub fn room_at(&self, p: Point) -> Option<usize> {
        self.rooms.iter().position(|room| room.shape.contains(p))
    }
}

impl EntryTargets {
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
