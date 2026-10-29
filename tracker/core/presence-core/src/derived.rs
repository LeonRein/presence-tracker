//! What the web UI shows of the model of a saved configuration (docs/rust-core.md 1: the model is the core's,
//! the editor the web UI's): the walls as the radar sees them (real walls without their door openings), each
//! sensor's sight area, the plan (observed rooms with the share the sensors see, regions), the thresholds
//! that follow from the parameters; and, of the running world, the coverage per tile, and where a person's
//! cloud is cut at the walls. Lengths, areas, labels, where a door sits and the rooms between the walls are
//! the editor's (static/js): nothing of the editor is here.

use std::collections::BTreeMap;

use serde::Serialize;

use crate::config::{Config, SIGHT_OFFSET, SensorConfig};
use crate::geometry::{Point, visible};
use crate::params::{OutputParams, Params};
use crate::plan::{OBSERVED_SHARE, Plan, RoomIdx, ZoneRole};
use crate::world::World;
use crate::world::tiles::SIZE;

/// A sensor sees a tile where its sight of a person there is at least this (MODEL.md 4.1: the sight falls to
/// half at the edge of the field of view)
pub const SEES_TILE: f64 = 0.5;

/// mm: what the display gets
fn mm(v: f64) -> f64 {
    (v * 1000.0).round() / 1000.0
}

fn mm2(p: Point) -> Point {
    [mm(p[0]), mm(p[1])]
}

#[derive(Debug, Serialize)]
pub struct RegionShown {
    pub name: String,
    pub rooms: Vec<String>,
    /// a way out of the house leads from it
    pub open: bool,
}

#[derive(Debug, Serialize)]
pub struct PlanShown {
    /// the observed rooms, in the configuration's order
    pub observed: Vec<String>,
    /// the regions without a sensor by id
    pub regions: BTreeMap<String, RegionShown>,
    /// per room of the home the share the sensors in use see, and the share from which it is observed
    pub shares: BTreeMap<String, f64>,
    pub observed_share: f64,
    /// the sensors in use (enabled and placed)
    pub in_use: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct Thresholds {
    /// P(somebody there) above which a room is occupied (light_cost)
    pub occupied: f64,
    /// p_enter above which "wird betreten" holds (approach_cost)
    pub entering: f64,
}

/// The model's view of a configuration (`derive`).
#[derive(Debug, Serialize)]
pub struct Derived {
    /// what blocks people and the radar: real walls without their door openings
    pub walls: Vec<[Point; 2]>,
    /// per placed sensor what it sees: its field of view out to its range, cut at the walls (the rule of
    /// SensorConfig::sees: sight lines from its sight origin)
    pub sensors: BTreeMap<String, Vec<Point>>,
    pub plan: PlanShown,
    pub thresholds: Thresholds,
}

/// The derived view of a configuration.
pub fn derive(config: &Config) -> Derived {
    let plan = Plan::new(config);
    let sensors = config
        .sensors
        .iter()
        .filter(|s| s.placed)
        .map(|s| (s.id.clone(), sight_area(s, &plan.walls).into_iter().map(mm2).collect()))
        .collect();
    let outputs = OutputParams::of(&config.params, Params::default().silent_after);
    Derived {
        walls: plan.walls.iter().map(|w| w.map(mm2)).collect(),
        sensors,
        plan: plan_shown(&plan),
        thresholds: Thresholds {
            occupied: outputs.occupied_above(),
            entering: outputs.entering_above(),
        },
    }
}

fn plan_shown(plan: &Plan) -> PlanShown {
    let id = |z: usize| plan.zones[z].id.clone();
    PlanShown {
        observed: plan.observed().map(|z| z.id.clone()).collect(),
        regions: plan
            .regions
            .iter()
            .map(|r| {
                let shown = RegionShown {
                    name: r.name.clone(),
                    rooms: r.rooms.iter().map(|z| id(z.0)).collect(),
                    open: r.open(),
                };
                (r.id.clone(), shown)
            })
            .collect(),
        shares: (0..plan.zones.len())
            .filter_map(|z| plan.shares[z].map(|s| (id(z), mm(s))))
            .collect(),
        observed_share: OBSERVED_SHARE,
        in_use: plan.sensors.iter().filter(|s| s.in_use).map(|s| s.id.clone()).collect(),
    }
}

/// What a sensor sees on the floor: its field of view (apex at the sensor, out to range x scale: SensorConfig::
/// in_fov) as far as the sight lines from its sight origin reach past no wall. Empty if the range ends before
/// the sight origin.
pub fn sight_area(s: &SensorConfig, walls: &[[Point; 2]]) -> Vec<Point> {
    let reach = s.range * s.scale;
    if reach <= SIGHT_OFFSET {
        return Vec::new();
    }
    let (o, at, axis) = (s.sight_origin(), s.at(), s.axis());
    let half = (s.fov / 2.0).to_radians();
    let (c, sn) = (half.cos(), half.sin());
    // the edges of the field of view (the axis turned by +-half) and their normals into it
    let left = [axis[0] * c - axis[1] * sn, axis[0] * sn + axis[1] * c];
    let right = [axis[0] * c + axis[1] * sn, axis[1] * c - axis[0] * sn];
    let normals = [[left[1], -left[0]], [-right[1], right[0]]];
    let w = [o[0] - at[0], o[1] - at[1]];
    // along a ray from o: where it leaves the field of view (o is inside it, which is convex up to 180 deg)
    let limit = |d: Point| {
        let b = w[0] * d[0] + w[1] * d[1];
        let cc = w[0] * w[0] + w[1] * w[1] - reach * reach;
        let mut t = -b + (b * b - cc).max(0.0).sqrt();
        for n in normals {
            let nd = n[0] * d[0] + n[1] * d[1];
            if nd < 0.0 {
                t = t.min((n[0] * w[0] + n[1] * w[1]) / -nd);
            }
        }
        t.max(0.0)
    };
    let towards = |p: Point| (p[1] - o[1]).atan2(p[0] - o[0]);
    let ends = [left, right].map(|e| towards([at[0] + reach * e[0], at[1] + reach * e[1]]));
    visible(o, walls, reach + SIGHT_OFFSET, limit, &[towards(at), ends[0], ends[1]])
}

/// Where a Gaussian of a person (mean, 3 standard deviations `r`) may be drawn: what is in sight from its
/// mean (a person is not seen through a wall) within a square of half side r around it; None when no wall
/// is that near (nothing to cut).
pub fn cloud_clip(mean: Point, r: f64, walls: &[[Point; 2]]) -> Option<Vec<Point>> {
    let reach = r * std::f64::consts::SQRT_2;
    if !walls
        .iter()
        .any(|&[a, b]| crate::geometry::distance_to_segment(mean, a, b) < reach)
    {
        return None;
    }
    let square = |d: Point| r / d[0].abs().max(d[1].abs());
    let q = std::f64::consts::FRAC_PI_4;
    let corners = [q, 3.0 * q, 5.0 * q, 7.0 * q];
    Some(
        visible(mean, walls, reach, square, &corners)
            .into_iter()
            .map(mm2)
            .collect(),
    )
}

/// A room of the home as the coverage shows it.
#[derive(Debug, Serialize)]
pub struct RoomCoverage {
    pub id: String,
    pub name: String,
    /// m^2 of its outline
    pub area: f64,
    /// the sensors see enough of it (OBSERVED_SHARE) for the model to track people in it
    pub observed: bool,
    pub share: f64,
    /// m^2 of its tiles no sensor sees, that only one sees (not observed: none), and their shares of its area
    pub blind: f64,
    pub single: f64,
    pub blind_share: f64,
    pub single_share: f64,
}

/// The coverage of a built world (MODEL.md 4.1, 5.3, 5.5): per tile (its square's lower left corner, its
/// room, which cuts the square) how many sensors in use see it (SEES_TILE) and how well the best of them can
/// test a person there; per room of the home its area, whether it is observed, and the area of its tiles
/// that no sensor or only one sees.
#[derive(Debug, Serialize)]
pub struct Coverage {
    pub size: f64,
    pub tiles: Vec<(Point, String, usize, f64)>,
    pub rooms: Vec<RoomCoverage>,
}

pub fn coverage(world: &World) -> Coverage {
    let (plan, tiles) = (&world.plan, &world.tiles);
    let mut in_use = crate::sensors::SensorSet::default();
    for (si, s) in plan.sensors.iter().enumerate() {
        if s.in_use {
            in_use.insert(si);
        }
    }
    let best = world.observed(in_use);
    let mut blind = vec![0.0; plan.rooms.len()];
    let mut single = vec![0.0; plan.rooms.len()];
    let mut shown = Vec::new();
    for (t, (&room, &best)) in tiles.room.iter().zip(&best).enumerate() {
        let Some(RoomIdx(r)) = room else { continue };
        let seen = in_use
            .iter()
            .filter(|&si| world.sights[si].tiles[t] >= SEES_TILE)
            .count();
        match seen {
            0 => blind[r] += tiles.area[t],
            1 => single[r] += tiles.area[t],
            _ => {}
        }
        shown.push((mm2(tiles.squares[t]), plan.room(RoomIdx(r)).id.clone(), seen, mm(best)));
    }
    let rooms = plan
        .zones
        .iter()
        .zip(&plan.shares)
        .filter_map(|(z, share)| {
            let share = (*share)?;
            let observed = match z.role {
                ZoneRole::Observed(RoomIdx(r)) => Some(r),
                _ => None,
            };
            let area = z.shape.area();
            let (blind, single) = observed.map_or((0.0, 0.0), |r| (blind[r], single[r]));
            let of_area = |a: f64| if area > 0.0 { mm(a / area) } else { 0.0 };
            Some(RoomCoverage {
                id: z.id.clone(),
                name: z.name.clone(),
                area: mm(area),
                observed: observed.is_some(),
                share: mm(share),
                blind: mm(blind),
                single: mm(single),
                blind_share: of_area(blind),
                single_share: of_area(single),
            })
        })
        .collect();
    Coverage {
        size: SIZE,
        tiles: shown,
        rooms,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::geometry::in_polygon;
    use crate::outputs::{Person, Shown};
    use crate::testing::{config, flat_config, wall};
    use crate::tracker::Tracker;

    /// A room x -0.5..4, y 0..4 with a wall piece x = 3, y 1.5..2.5 in it; a door of 0.8 m in the bottom wall
    /// at x = 1; sensor "s" at (0, 2) looking along +x (fov 120, range 6).
    fn room() -> Config {
        let walls = [
            wall([-0.5, 0.0], [4.0, 0.0]),
            wall([4.0, 0.0], [4.0, 4.0]),
            wall([4.0, 4.0], [-0.5, 4.0]),
            wall([-0.5, 4.0], [-0.5, 0.0]),
            wall([3.0, 1.5], [3.0, 2.5]),
        ];
        config(
            &[("s", "s", [0.0, 2.0], 0.0)],
            r#"[{"id": "r", "name": "R", "kind": "room", "shape": "polygon", "points": [[-0.5, 0], [4, 0], [4, 4], [-0.5, 4]]},
                {"id": "a", "name": "A", "kind": "area", "shape": "rect", "points": [[0, 0], [2, 3]]},
                {"id": "c", "name": "C", "kind": "area", "shape": "circle", "points": [], "center": [3, 3], "radius": 0.5}]"#,
            &format!("[{}]", walls.join(", ")),
            r#"[{"id": "d", "x": 1.0, "y": 0.0, "width": 0.8}]"#,
        )
    }

    #[test]
    fn a_door_cuts_its_opening_out_of_its_wall() {
        let d = derive(&room());
        // centre 1 m along the wall from (-0.5, 0): 1.5 m, half width 0.4: 1.1 .. 1.9 m, x 0.6 .. 1.4
        assert!(d.walls.contains(&[[-0.5, 0.0], [0.6, 0.0]]) && d.walls.contains(&[[1.4, 0.0], [4.0, 0.0]]));
        assert_eq!(d.walls.len(), 6);
    }

    #[test]
    fn shares_and_thresholds() {
        let d = derive(&room());
        // the sensor sees most of the room: observed; light_cost 2: occupied above 2/3, approach_cost 0.05: 1/21
        assert_eq!(d.plan.observed, ["r"]);
        assert!(d.plan.shares["r"] >= OBSERVED_SHARE && d.plan.in_use == ["s"]);
        assert!((d.thresholds.occupied - 2.0 / 3.0).abs() < 1e-12);
        assert!((d.thresholds.entering - 0.05 / 1.05).abs() < 1e-12);
    }

    #[test]
    fn a_sensor_sees_its_field_of_view_up_to_the_walls() {
        let d = derive(&room());
        let sight = &d.sensors["s"];
        let inside = |p: Point| in_polygon(p, sight);
        // 45 deg off its axis: in view; 71.6 deg (0.5, 3.5): not; behind it and past the outer wall: not
        assert!(inside([1.0, 3.0]) && inside([3.9, 2.0 - 1.5]) && inside([0.3, 2.0]));
        assert!(!inside([0.5, 3.5]) && !inside([-0.3, 2.0]) && !inside([4.3, 2.0]));
        // behind the wall piece x = 3 (y 1.5..2.5) from the sight origin (0.2, 2): (3.5, 2) not; (3.5, 3): the line
        // passes x = 3 at y = 2 + 1 * 2.8 / 3.3 = 2.85, above the piece: in view
        assert!(!inside([3.5, 2.0]) && inside([3.5, 3.0]));
        // the field of view's upper edge meets the wall y = 4 at x = 2 / tan 60 = 1.155: nothing above y = 4
        assert!(sight.iter().all(|p| p[1] <= 4.0 + 1e-9 && p[0] <= 4.0 + 1e-9));
        assert!(inside([1.25, 3.9]) && !inside([1.0, 3.9]));
    }

    #[test]
    fn a_sensor_in_a_corner_sees_its_room() {
        // flat: sensor "wohn" at (0.05, 0.05) in the corner of the 6 x 5 m room, looking at 45 deg; the walls
        // meet behind it (all their points in a line: the polygon must keep the corners, 29.10.)
        let sight = &derive(&flat_config()).sensors["wohn"];
        let inside = |p: Point| in_polygon(p, sight);
        // (4, 3): 5 m, 8 deg off its axis; (0.5, 4.5): 4.5 m, 39 deg; (5.5, 4.5): 7 m, beyond its range 6
        assert!(inside([4.0, 3.0]) && inside([0.5, 4.5]) && inside([3.0, 0.3]));
        assert!(!inside([5.5, 4.5]) && !inside([-0.5, 1.0]));
    }

    #[test]
    fn a_cloud_is_cut_only_at_a_wall_near_it() {
        assert_eq!(cloud_clip([0.0, 0.0], 1.0, &[[[3.0, -2.0], [3.0, 2.0]]]), None);
        let clip = cloud_clip([0.0, 0.0], 1.0, &[[[0.5, -2.0], [0.5, 2.0]]]).expect("a wall 0.5 m away");
        // the square of half side 1 around the mean, cut at x = 0.5
        assert!(
            clip.iter()
                .all(|p| p[0] <= 0.5 + 1e-9 && p[0] >= -1.0 - 1e-9 && p[1].abs() <= 1.0 + 1e-9)
        );
        assert!(in_polygon([-0.95, -0.95], &clip) && in_polygon([0.45, 0.9], &clip) && !in_polygon([0.7, 0.0], &clip));
    }

    #[test]
    fn the_coverage_counts_the_sensors_per_tile() {
        // the flat room (6 x 5 m) seen from two corners; without the second sensor no tile has two
        let both = coverage(&World::build(&flat_config(), Params::default()).expect("the flat world"));
        assert_eq!(both.rooms.len(), 1);
        let r = &both.rooms[0];
        assert!(r.observed && r.share == 1.0 && r.area == 30.0);
        assert!(both.tiles.iter().any(|t| t.2 == 2) && both.tiles.iter().all(|t| t.1 == "wohn" && t.2 <= 2));
        assert!(r.blind + r.single <= r.area && (r.blind_share - r.blind / 30.0).abs() < 1e-3);
        let mut one = flat_config();
        one.sensors[1].enabled = false;
        let one = coverage(&World::build(&one, Params::default()).expect("the flat world"));
        assert!(one.tiles.iter().all(|t| t.2 <= 1) && one.rooms[0].single > r.single);
    }

    #[test]
    fn a_trail_breaks_where_a_person_jumped() {
        let mut tracker = Tracker::new(World::build(&flat_config(), Params::default()).expect("the flat world"));
        let at = |x: f64, y: f64| Person {
            id: 1,
            places: vec![1.0, 0.0],
            lost: false,
            r: 1.0,
            shown: Some(Shown {
                at: [x, y],
                velocity: [0.2, 0.0],
                sigma: 0.1,
                walk: 0.6,
            }),
        };
        assert!(at(1.0, 1.0).moving());
        assert!(
            !Person {
                lost: true,
                ..at(1.0, 1.0)
            }
            .moving()
        );
        // the first time nothing to break; 0.5 m: no; 1.5 m: yes; 0.5 m through the wall x = 0: yes
        let mut breaks = |x, y| tracker.trail_breaks(&[at(x, y)])[0];
        assert!(!breaks(1.0, 1.0) && !breaks(1.5, 1.0) && breaks(3.0, 1.0));
        assert!(!breaks(2.2, 1.0) && !breaks(1.3, 1.0) && !breaks(0.4, 1.0) && breaks(-0.3, 1.0));
    }
}
