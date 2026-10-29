//! The room's own LD2450 measures somebody in it (MODEL.md 6, "Belegt"): an output rule
//! beside the filter. A room is occupied if P(somebody there) is above its threshold or the room's own
//! LD2450 had a measured target inside the room within the last seen_hold s. Measured: what the filter
//! itself takes as a measurement (not held by the sensor, not behind a wall or outside all rooms, not
//! at the mount); inside: the first observed room whose outline holds the point is this room. Other
//! sensors don't count: from 6-7 m a person at a room's edge is measured in the next room.
//!
//! Silent: a room whose own sensors in use all sent no frame for more than a limit (SILENT_AFTER = 30 s)
//! has no data (Home Assistant gets it as unavailable, not as empty). 30 s, not LOST (6 s): the
//! WLAN's gaps of 6-20 s came 88 times in 64 h; a board that is gone stays silent for minutes. A sensor
//! that sent nothing since the model started counts from the start.
//!
//! Only output: nothing here changes the filter. Rooms and sensors by their ids, so that what was seen
//! goes on over a new plan.
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use crate::geometry::Point;
use crate::plan::{Plan, RoomIdx};

/// s without any frame until a room's own board counts as gone and its room as silent (the shell's and the
/// replay's; a world is built with it, World::build).
pub const SILENT_AFTER: f64 = 30.0;

#[derive(Debug, Default)]
pub struct RoomSeen {
    /// per sensor id (also one the plan lacks) the time of its last frame
    heard: Vec<(String, f64)>,
    /// per room id the last time its own LD2450 measured somebody in it
    last: Vec<(String, f64)>,
}

fn get(times: &[(String, f64)], id: &str) -> Option<f64> {
    times.iter().find(|(k, _)| k == id).map(|&(_, t)| t)
}

fn set(times: &mut Vec<(String, f64)>, id: &str, t: f64) {
    match times.iter_mut().find(|(k, _)| k == id) {
        Some((_, at)) => *at = t,
        None => times.push((id.to_owned(), t)),
    }
}

impl RoomSeen {
    /// A new plan: only rooms that are a sensor's own keep when they were seen.
    pub fn follow(&mut self, plan: &Plan) {
        let owned = |room: &str| {
            plan.sensors
                .iter()
                .filter_map(|s| s.own_room)
                .any(|k| plan.room(k).id == room)
        };
        self.last.retain(|(room, _)| owned(room));
    }

    /// A frame of the sensor with this id at t, whatever is in it.
    pub fn heard_from(&mut self, id: &str, t: f64) {
        if t > get(&self.heard, id).unwrap_or(f64::NEG_INFINITY) {
            set(&mut self.heard, id, t);
        }
    }

    /// A frame of sensor si (of the plan) at t with these measured targets.
    pub fn frame(&mut self, plan: &Plan, si: usize, t: f64, measured: &[Point]) {
        let sensor = &plan.sensors[si];
        let Some(room) = sensor.own_room.filter(|_| sensor.in_use) else {
            return;
        };
        if measured.iter().any(|&p| plan.room_at(p) == Some(room)) {
            set(&mut self.last, &plan.room(room).id, t);
        }
    }

    /// Per room of the plan whether its own LD2450 measured somebody in it within the last `hold` s
    /// (0: never).
    pub fn seen(&self, plan: &Plan, now: f64, hold: f64) -> Vec<bool> {
        plan.observed()
            .map(|room| hold > 0.0 && get(&self.last, &room.id).is_some_and(|t| now - t <= hold))
            .collect()
    }

    /// Whether the sensor sent a frame within the last `limit` s; one that sent nothing counts from
    /// the model's start (None: not started, online). The web UI's "online" too.
    pub fn online(&self, id: &str, now: f64, start: Option<f64>, limit: f64) -> bool {
        start.is_none_or(|start| now - get(&self.heard, id).unwrap_or(start) <= limit)
    }

    /// Per room of the plan whether its own sensors in use all sent nothing for more than `limit` s
    /// (none: not silent).
    pub fn silent(&self, plan: &Plan, now: f64, start: Option<f64>, limit: f64) -> Vec<bool> {
        (0..plan.rooms.len())
            .map(|k| {
                let mut own = plan
                    .sensors
                    .iter()
                    .filter(|s| s.in_use && s.own_room == Some(RoomIdx(k)))
                    .peekable();
                own.peek().is_some() && own.all(|s| !self.online(&s.id, now, start, limit))
            })
            .collect()
    }

    /// Per room id the last time its own LD2450 measured somebody in it.
    pub fn last(&self) -> &[(String, f64)] {
        &self.last
    }

    /// Per room of the plan whether it has an own sensor in use (it may go silent; Home Assistant gives
    /// it its own availability).
    pub fn watched(plan: &Plan) -> Vec<bool> {
        (0..plan.rooms.len())
            .map(|k| plan.sensors.iter().any(|s| s.in_use && s.own_room == Some(RoomIdx(k))))
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Two rooms without a wall between them (test_roomseen.open_plan): wohn 6 x 5 m, ess 4 x 5 m
    /// right of it; sensor 0 wohn's, sensor 1 ess's, sensor 2 nobody's.
    fn open_plan() -> Plan {
        let config = crate::testing::config(
            &[
                ("w", "wohn", [0.05, 0.05], 45.0),
                ("e", "ess", [9.95, 2.5], 180.0),
                ("x", "", [5.0, 4.9], 270.0),
            ],
            r#"[{"id": "wohn", "name": "Wohn", "kind": "room", "shape": "rect", "points": [[0, 0], [6, 5]]},
                {"id": "ess", "name": "Ess", "kind": "room", "shape": "rect", "points": [[6, 0], [10, 5]]}]"#,
            "[]",
            "[]",
        );
        let mut plan = Plan::new(&config);
        plan.sensors[2].own_room = None; // on the border of both: nobody's
        plan
    }

    #[test]
    fn only_the_rooms_own_sensor_measuring_somebody_in_it_counts() {
        let plan = open_plan();
        let mut seen = RoomSeen::default();
        // ess's sensor measures somebody in wohn (no wall between): no room is seen
        seen.frame(&plan, 1, 1.0, &[[4.0, 2.0]]);
        // nobody's sensor: nothing either
        seen.frame(&plan, 2, 1.0, &[[2.0, 2.0]]);
        assert_eq!(seen.seen(&plan, 1.0, 10.0), [false, false]);
        // wohn's own, in wohn: seen for hold s after its frame, and never with hold 0
        seen.frame(&plan, 0, 2.0, &[[7.0, 2.0], [2.0, 2.0]]);
        assert_eq!(seen.seen(&plan, 12.0, 10.0), [true, false]);
        assert_eq!(seen.seen(&plan, 12.1, 10.0), [false, false]);
        assert_eq!(seen.seen(&plan, 3.0, 0.0), [false, false]);
        // a sensor not in use counts not
        let mut off = open_plan();
        off.sensors[0].in_use = false;
        let mut seen = RoomSeen::default();
        seen.frame(&off, 0, 2.0, &[[2.0, 2.0]]);
        assert_eq!(seen.seen(&off, 2.0, 10.0), [false, false]);
    }

    #[test]
    fn a_room_is_silent_when_all_its_own_sensors_in_use_are() {
        let mut plan = open_plan();
        let mut seen = RoomSeen::default();
        // not started: nothing silent; started: one that sent nothing counts from the start
        assert_eq!(seen.silent(&plan, 1e9, None, 30.0), [false, false]);
        assert_eq!(seen.silent(&plan, 29.0, Some(0.0), 30.0), [false, false]);
        assert_eq!(seen.silent(&plan, 31.0, Some(0.0), 30.0), [true, true]);
        seen.heard_from("w", 20.0);
        seen.heard_from("w", 10.0); // an older frame changes nothing
        assert_eq!(seen.silent(&plan, 50.0, Some(0.0), 30.0), [false, true]);
        assert!(seen.online("w", 50.0, Some(0.0), 30.0) && !seen.online("e", 50.0, Some(0.0), 30.0));
        assert!(seen.online("e", 50.0, None, 30.0), "not started: online");
        // two own sensors: silent only when both are
        plan.sensors[1].own_room = Some(RoomIdx(0));
        assert_eq!(seen.silent(&plan, 50.0, Some(0.0), 30.0), [false, false]);
        assert_eq!(seen.silent(&plan, 51.0, Some(0.0), 30.0), [true, false]);
        // a room without an own sensor in use is never silent
        plan.sensors[0].in_use = false;
        plan.sensors[1].in_use = false;
        assert_eq!(seen.silent(&plan, 1e9, Some(0.0), 30.0), [false, false]);
        assert_eq!(RoomSeen::watched(&plan), [false, false]);
    }

    #[test]
    fn a_new_plan_keeps_what_its_rooms_own_sensors_saw() {
        let plan = open_plan();
        let mut seen = RoomSeen::default();
        seen.frame(&plan, 0, 4.9, &[[2.0, 2.0]]);
        seen.frame(&plan, 1, 5.0, &[[8.0, 2.0]]);
        // ess has no own sensor any more: what was seen there goes; wohn's stays
        let mut other = open_plan();
        other.sensors[1].own_room = None;
        seen.follow(&other);
        assert_eq!(seen.last(), [("wohn".to_string(), 4.9)]);
    }
}
