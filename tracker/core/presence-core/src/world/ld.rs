//! What a sensor's LD2410C sees (MODEL.md 4.3, _ld_tiles): where a
//! point is off its axis and how far, how much of a person there is in its line of sight and in the
//! measured view of their room; and what a person in each tile it sees puts into its cells.
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use super::raster::{Grid, RoomGrid};
use crate::geometry::Point;
use crate::ld2410::CELLS;
use crate::plan::RoomIdx;

/// The tiles an LD2410C sees and what a person there puts in, standing and walking (averaged over
/// each tile).
#[derive(Debug, Default)]
#[cfg_attr(test, derive(serde::Deserialize))]
pub struct LdTiles {
    pub idx: Vec<usize>,
    pub still: Vec<[f64; CELLS]>,
    pub walk: Vec<[f64; CELLS]>,
}

#[derive(Debug)]
pub struct LdSight {
    pub at: Point,
    /// its axis: cos and sin of its heading
    pub axis: Point,
    /// m^2: (its height - a person's target height)^2
    pub dh2: f64,
    /// how much of a person at a cell is in its line of sight (0..1)
    pub los: Grid,
    /// per observed room the share of the geometric sight it gets from a person there, if measured
    /// (in no room: all of it)
    pub view: Option<Vec<f64>>,
    pub tiles: LdTiles,
}

/// Where a point is for an LD2410C: degrees off its axis, slant distance, in sight (0..1).
#[derive(Debug, Clone, Copy)]
pub struct LdPlace {
    pub angle: f64,
    pub slant: f64,
    pub sight: f64,
}

impl LdSight {
    /// Where the point p is for this LD2410C; `rooms` gives each cell's room (as in `view`).
    pub fn place(&self, p: Point, rooms: &RoomGrid) -> LdPlace {
        let (dx, dy) = (p[0] - self.at[0], p[1] - self.at[1]);
        let ahead = dx * self.axis[0] + dy * self.axis[1];
        let side = dx * self.axis[1] - dy * self.axis[0];
        let angle = side.atan2(ahead.max(1e-9)).abs().to_degrees();
        let slant = (dx * dx + dy * dy + self.dh2).sqrt();
        let mut sight = if ahead > 0.0 { self.los.at(p) } else { 0.0 };
        if let Some(view) = &self.view {
            sight *= rooms.at(p).map_or(1.0, |RoomIdx(k)| view[k]);
        }
        LdPlace { angle, slant, sight }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::params::Params;
    use crate::testing::{config, wall};
    use crate::world::World;

    /// Two rooms 4 x 4 m side by side, a door between at (4, 2), one sensor in each looking at it
    /// (test_ld2410._two_rooms); the LD2410C's measured view of another room as `view` says.
    fn two_rooms(view: &[(&str, f64)]) -> World {
        let walls = [
            wall([0.0, 0.0], [8.0, 0.0]),
            wall([8.0, 0.0], [8.0, 4.0]),
            wall([8.0, 4.0], [0.0, 4.0]),
            wall([0.0, 4.0], [0.0, 0.0]),
            wall([4.0, 0.0], [4.0, 4.0]),
        ];
        let mut c = config(
            &[("a", "a", [0.05, 2.0], 0.0), ("b", "b", [7.95, 2.0], 180.0)],
            r#"[{"id": "links", "name": "links", "kind": "room", "shape": "rect", "points": [[0, 0], [4, 4]]},
                {"id": "rechts", "name": "rechts", "kind": "room", "shape": "rect", "points": [[4, 0], [8, 4]]}]"#,
            &format!("[{}]", walls.join(", ")),
            r#"[{"id": "tuer", "x": 4.0, "y": 2.0, "width": 0.9}]"#,
        );
        c.ld_view = view.iter().map(|&(k, v)| (k.to_string(), v)).collect();
        World::build(&c, Params::default()).expect("a world")
    }

    #[test]
    fn the_ld2410c_sees_another_room_as_far_as_measured() {
        // test_ld2410 (MODEL.md 4.3 "Blick des LD2410C"): the measured view of the other room scales
        // the sight there; where it gives the LD2410C nothing above FLOOR the place is out of its view
        // (weighed by 1), its own room keeps the geometric sight
        let pts = [[2.0, 2.0], [5.0, 2.0]]; // in front of a: its own room, and through the door
        let sight = |w: &World, si: usize, rooms: &RoomGrid| pts.map(|q| w.ld[si].place(q, rooms).sight);
        let base = two_rooms(&[]);
        let s0 = sight(&base, 0, &base.rooms);
        assert!(s0[0] > 0.5 && s0[1] > 0.5, "{s0:?}");
        let half = two_rooms(&[("links<-rechts", 0.5)]);
        let s = sight(&half, 0, &half.rooms);
        assert!(
            (s[0] - s0[0]).abs() < 1e-12 && (s[1] - 0.5 * s0[1]).abs() < 1e-12,
            "{s:?}"
        );
        // without the rooms (in no room: all of it) the geometric sight is the same
        let nowhere = RoomGrid::new(half.raster, vec![None; half.raster.cells()]);
        assert_eq!(sight(&half, 0, &nowhere), s0);
        let blind = two_rooms(&[("links<-rechts", 0.0)]);
        assert_eq!(sight(&blind, 0, &blind.rooms), [s0[0], 0.0]);
        // the other sensor is untouched; no tile of the other room is in a's view any more
        assert_eq!(sight(&blind, 1, &blind.rooms), sight(&base, 1, &base.rooms));
        let rechts = (0..blind.plan.rooms.len())
            .map(RoomIdx)
            .find(|&r| blind.plan.room(r).id == "rechts")
            .expect("the room");
        let other = |w: &World| w.ld[0].tiles.idx.iter().any(|&c| w.tiles.room[c] == Some(rechts));
        assert!(other(&base) && !other(&blind));
    }
}
