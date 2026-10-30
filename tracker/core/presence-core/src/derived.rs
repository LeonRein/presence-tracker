//! The model's quantities the web UI shows (docs/rust-core.md 1: the model is the core's, how it looks the web
//! UI's): of a saved configuration the plan (observed rooms with the share the sensors see, regions) and the
//! thresholds that follow from the parameters; of the running world, per tile how many sensors see it and
//! each sensor's sight there (the numbers the filter computes with); of what the running model learned, each part
//! as the filter (or "Ziel") reads it. No geometry for drawing: the web UI
//! draws the walls, doors, tiles and clouds itself (static/js/view.js), from the numbers as they are (the
//! shell rounds what it sends).

use std::collections::BTreeMap;

use serde::Serialize;

use crate::config::Config;
use crate::geometry::Point;
use crate::ld2410::{CELLS, MOVING};
use crate::learn::Learned;
use crate::learn::destination::DestShown;
use crate::params::{OUTPUT_FIELDS, OutputParams, Params};
use crate::plan::{DOOR_ON_WALL, OBSERVED_SHARE, Opening, Plan, RoomIdx, ZoneRole, door_openings};
use crate::world::World;
use crate::world::tiles::SIZE;

/// A sensor sees a tile where its sight of a person there is at least this (MODEL.md 4.1: the sight falls to
/// half at the edge of the field of view)
pub const SEES_TILE: f64 = 0.5;

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
    /// the zones of the home (its rooms and areas, in the configuration's order): what Home Assistant gets
    pub home: Vec<String>,
    /// per door of the configuration (in its order) its opening in the real wall it is on (None: on none)
    pub doors: Vec<Option<Opening>>,
    /// m: a door belongs to the real wall its centre lies on up to this (the editor keeps it there)
    pub door_on_wall: f64,
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
    pub plan: PlanShown,
    pub thresholds: Thresholds,
    /// m: the length of an LD2410C gate (its energies come per gate)
    pub ld2410_gate: f64,
    /// the parameters only the outputs read: a change of them alone does not start the model over
    pub output_params: [&'static str; OUTPUT_FIELDS.len()],
}

/// The derived view of a configuration.
pub fn derive(config: &Config) -> Derived {
    let plan = Plan::new(config);
    let outputs = OutputParams::of(&config.params, Params::default().silent_after);
    Derived {
        plan: plan_shown(&plan, door_openings(config)),
        thresholds: Thresholds {
            occupied: outputs.occupied_above(),
            entering: outputs.entering_above(),
        },
        ld2410_gate: crate::ld2410::GATE,
        output_params: OUTPUT_FIELDS,
    }
}

fn plan_shown(plan: &Plan, doors: Vec<Option<Opening>>) -> PlanShown {
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
            .filter_map(|z| plan.shares[z].map(|s| (id(z), s)))
            .collect(),
        observed_share: OBSERVED_SHARE,
        in_use: plan.sensors.iter().filter(|s| s.in_use).map(|s| s.id.clone()).collect(),
        home: plan.home().map(|z| z.id.clone()).collect(),
        doors,
        door_on_wall: DOOR_ON_WALL,
    }
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
/// test a person there; per sensor in use its sight of a person in each of these tiles (Sight::tiles, what
/// the filter weighs with); per room of the home its area, whether it is observed, and the area of its tiles
/// that no sensor or only one sees.
#[derive(Debug, Serialize)]
pub struct Coverage {
    pub size: f64,
    pub sees: f64,
    pub tiles: Vec<(Point, String, usize, f64)>,
    pub sight: BTreeMap<String, Vec<f64>>,
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
    let mut sight: BTreeMap<String, Vec<f64>> = in_use
        .iter()
        .map(|si| (plan.sensors[si].id.clone(), Vec::new()))
        .collect();
    for (t, (&room, &best)) in tiles.room.iter().zip(&best).enumerate() {
        let Some(RoomIdx(r)) = room else { continue };
        for si in in_use.iter() {
            if let Some(v) = sight.get_mut(&plan.sensors[si].id) {
                v.push(world.sights[si].tiles[t]);
            }
        }
        let seen = in_use
            .iter()
            .filter(|&si| world.sights[si].tiles[t] >= SEES_TILE)
            .count();
        match seen {
            0 => blind[r] += tiles.area[t],
            1 => single[r] += tiles.area[t],
            _ => {}
        }
        shown.push((tiles.squares[t], plan.room(RoomIdx(r)).id.clone(), seen, best));
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
            let of_area = |a: f64| if area > 0.0 { a / area } else { 0.0 };
            Some(RoomCoverage {
                id: z.id.clone(),
                name: z.name.clone(),
                area,
                observed: observed.is_some(),
                share,
                blind,
                single,
                blind_share: of_area(blind),
                single_share: of_area(single),
            })
        })
        .collect();
    Coverage {
        size: SIZE,
        sees: SEES_TILE,
        tiles: shown,
        sight,
        rooms,
    }
}

/// An LD2410C's energies per gate (ld2410::GATE m each, from the sensor): moving 0..8, still 0..8 (gates 0 and 1
/// have no still energy: None).
#[derive(Debug, Serialize)]
pub struct Gates {
    pub moving: Vec<f64>,
    pub still: Vec<Option<f64>>,
}

impl Gates {
    fn of(cells: &[f64; CELLS]) -> Self {
        let (moving, still) = cells.split_at(MOVING);
        Self {
            moving: moving.to_vec(),
            still: [None, None]
                .into_iter()
                .chain(still.iter().copied().map(Some))
                .collect(),
        }
    }
}

/// What a sensor's LD2410C learned (MODEL.md 4.3): the energy without anybody the filter weighs with now and the
/// level it goes back to while nothing is learned (the sensor's own over days), the hours each learned from
/// (faded) and how long each remembers; the echo sources begun per hour, learned and the prior's.
#[derive(Debug, Serialize)]
pub struct BackgroundShown {
    pub now: Gates,
    pub anchor: Gates,
    pub hours: f64,
    pub forget_hours: f64,
    pub anchor_hours: f64,
    pub anchor_forget_hours: f64,
    pub echo_rate: f64,
    pub echo_prior: f64,
}

#[derive(Debug, Serialize)]
pub struct SensorLearned {
    pub background: BackgroundShown,
    /// per observed room (id) the share of its geometric sight the LD2410C gets from a person there: measured
    /// (the configuration's ld_view), not learned; None: nothing measured for this sensor (all of it)
    pub ld_view: Option<BTreeMap<String, f64>>,
}

/// What the running model learned, for the web UI: per sensor in use its LD2410C's background and its
/// measured view; the destination map ("Ziel": an output only).
#[derive(Debug, Serialize)]
pub struct LearnedShown {
    pub sensors: BTreeMap<String, SensorLearned>,
    pub destinations: DestShown,
}

/// What `learned` learned in `world`, as LearnedShown (computed when asked; nothing changes).
pub fn learned(world: &World, learned: &Learned) -> LearnedShown {
    let (plan, p) = (&world.plan, &world.params);
    let hours = |s: f64| s / 3600.0;
    let sensors = (0..plan.sensors.len())
        .filter(|&si| plan.sensors[si].in_use)
        .map(|si| {
            let bg = &learned.background;
            let [level, anchor] = bg.learned_for(si);
            let background = BackgroundShown {
                now: Gates::of(&bg.b(si, p)),
                anchor: Gates::of(&bg.anchor(si, p)),
                hours: hours(level),
                forget_hours: hours(p.ld_forget),
                anchor_hours: hours(anchor),
                anchor_forget_hours: hours(p.ld_anchor_forget),
                echo_rate: bg.rate(si, p) * 3600.0,
                echo_prior: p.ld_echo_rate * 3600.0,
            };
            let ld_view = world.ld[si].view.as_ref().map(|v| {
                (0..plan.rooms.len())
                    .map(|k| (plan.room(RoomIdx(k)).id.clone(), v[k]))
                    .collect()
            });
            (plan.sensors[si].id.clone(), SensorLearned { background, ld_view })
        })
        .collect();
    LearnedShown {
        sensors,
        destinations: learned.destinations.map().shown(&world.destinations),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing::{config, flat_config, wall};

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
    fn shares_thresholds_and_the_numbers_the_editor_reads() {
        let d = derive(&room());
        // the sensor sees most of the room: observed; light_cost 2: occupied above 2/3, approach_cost 0.05: 1/21
        assert_eq!(d.plan.observed, ["r"]);
        assert!(d.plan.shares["r"] >= OBSERVED_SHARE && d.plan.in_use == ["s"]);
        assert!((d.thresholds.occupied - 2.0 / 3.0).abs() < 1e-12);
        assert!((d.thresholds.entering - 0.05 / 1.05).abs() < 1e-12);
        assert_eq!(
            (d.plan.door_on_wall, d.ld2410_gate),
            (DOOR_ON_WALL, crate::ld2410::GATE)
        );
        // the zones of the home; the door's opening in the bottom wall (wall 0), 0.8 m about x = 1 (1.5 m from
        // its first end at x = -0.5)
        assert_eq!(d.plan.home, ["r", "a", "c"]);
        let o = d.plan.doors[0].expect("on a wall");
        assert_eq!(o.wall, 0);
        assert!((o.t0 - 1.1).abs() < 1e-12 && (o.t1 - 1.9).abs() < 1e-12);
        assert!(d.output_params.contains(&"light_cost") && !d.output_params.contains(&"wall_margin"));
    }

    #[test]
    fn a_door_opening_ends_at_its_wall_ends_and_names_the_wall_of_the_configuration() {
        // a divider first (no wall), then a 1 m wall with a door of 0.8 m at 0.2 m from its first end
        let c = config(
            &[("s", "s", [0.0, 2.0], 0.0)],
            "[]",
            r#"[{"points": [[0, 5], [4, 5]], "kind": "divider"}, {"points": [[0, 0], [1, 0]], "kind": "wall"}]"#,
            r#"[{"id": "d", "x": 0.2, "y": 0.0, "width": 0.8}]"#,
        );
        let o = derive(&c).plan.doors[0].expect("on a wall");
        assert_eq!(o.wall, 1);
        assert!(o.t0 == 0.0 && (o.t1 - 0.6).abs() < 1e-12);
    }

    #[test]
    fn what_was_learned_is_shown_per_sensor_in_use_as_the_filter_reads_it() {
        let w = World::build(&flat_config(), Params::default()).expect("the flat world");
        let l = Learned::new(&w);
        let shown = learned(&w, &l);
        assert_eq!(shown.sensors.keys().collect::<Vec<_>>(), ["b", "wohn"]);
        let s = &shown.sensors["wohn"];
        // the LD2410C: nothing learned yet, the prior per gate, no still energy in gates 0 and 1
        let bg = &s.background;
        let prior = crate::ld2410::background::prior(&w.params);
        assert_eq!(bg.now.moving, prior[..MOVING]);
        assert_eq!(bg.now.still[..3], [None, None, Some(prior[MOVING])]);
        assert_eq!(
            (bg.hours, bg.anchor_hours, bg.now.moving.len(), bg.anchor.still.len()),
            (0.0, 0.0, 9, 9)
        );
        assert!((bg.echo_rate - bg.echo_prior).abs() < 1e-12);
        // nothing measured of the view, no walks
        assert!(s.ld_view.is_none() && shown.destinations.cells.is_empty() && shown.destinations.walks == 0.0);
    }

    #[test]
    fn the_coverage_counts_the_sensors_per_tile_and_has_their_sight() {
        // the flat room (6 x 5 m) seen from two corners; without the second sensor no tile has two
        let both = coverage(&World::build(&flat_config(), Params::default()).expect("the flat world"));
        assert_eq!(both.rooms.len(), 1);
        let r = &both.rooms[0];
        assert!(r.observed && r.share == 1.0 && (r.area - 30.0).abs() < 1e-9);
        assert!(both.tiles.iter().any(|t| t.2 == 2) && both.tiles.iter().all(|t| t.1 == "wohn" && t.2 <= 2));
        assert!(r.blind + r.single <= r.area && (r.blind_share - r.blind / r.area).abs() < 1e-12);
        // per sensor in use its sight per tile shown: as many seeing it at least SEES_TILE as the count says
        assert_eq!(both.sight.len(), 2);
        assert!(
            both.sight
                .values()
                .all(|v| v.len() == both.tiles.len() && v.iter().all(|&x| (0.0..=1.0).contains(&x)))
        );
        for (k, t) in both.tiles.iter().enumerate() {
            assert_eq!(both.sight.values().filter(|v| v[k] >= both.sees).count(), t.2);
        }
        let mut one = flat_config();
        one.sensors[1].enabled = false;
        let one = coverage(&World::build(&one, Params::default()).expect("the flat world"));
        assert!(one.tiles.iter().all(|t| t.2 <= 1) && one.rooms[0].single > r.single && one.sight.len() == 1);
    }
}
