//! Whether the sensors are heard (MODEL.md 6, "Sensor des Raums stumm"): a room whose own sensors in use
//! all sent no frame for more than a limit (SILENT_AFTER = 30 s) has no data (Home Assistant gets it as
//! unavailable, not as empty). 30 s, not LOST (6 s): the WLAN's gaps of 6-20 s came 88 times in 64 h; a
//! board that is gone stays silent for minutes. A sensor that sent nothing since the model started counts
//! from the start.
//!
//! Only output: nothing here changes the filter. Sensors by their ids, so that what was heard goes on over
//! a new plan.

use crate::plan::{Plan, RoomIdx};

/// s without any frame until a room's own board counts as gone and its room as silent (the shell's and the
/// replay's; a world is built with it, World::build).
pub const SILENT_AFTER: f64 = 30.0;

/// Per sensor id (also one the plan lacks) the time of its last frame.
#[derive(Debug, Default)]
pub struct Heard(Vec<(String, f64)>);

impl Heard {
    fn last(&self, id: &str) -> Option<f64> {
        self.0.iter().find(|(k, _)| k == id).map(|&(_, t)| t)
    }

    /// A frame of the sensor with this id at t, whatever is in it.
    pub fn heard_from(&mut self, id: &str, t: f64) {
        match self.0.iter_mut().find(|(k, _)| k == id) {
            Some((_, at)) => *at = at.max(t),
            None => self.0.push((id.to_owned(), t)),
        }
    }

    /// Whether the sensor sent a frame within the last `limit` s; one that sent nothing counts from
    /// the model's start (None: not started, online). The web UI's "online" too.
    pub fn online(&self, id: &str, now: f64, start: Option<f64>, limit: f64) -> bool {
        start.is_none_or(|start| now - self.last(id).unwrap_or(start) <= limit)
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

    /// Two rooms without a wall between them (test_silent.open_plan): wohn 6 x 5 m, ess 4 x 5 m
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
    fn a_room_is_silent_when_all_its_own_sensors_in_use_are() {
        let mut plan = open_plan();
        let mut heard = Heard::default();
        // not started: nothing silent; started: one that sent nothing counts from the start
        assert_eq!(heard.silent(&plan, 1e9, None, 30.0), [false, false]);
        assert_eq!(heard.silent(&plan, 29.0, Some(0.0), 30.0), [false, false]);
        assert_eq!(heard.silent(&plan, 31.0, Some(0.0), 30.0), [true, true]);
        heard.heard_from("w", 20.0);
        heard.heard_from("w", 10.0); // an older frame changes nothing
        assert_eq!(heard.silent(&plan, 50.0, Some(0.0), 30.0), [false, true]);
        assert!(heard.online("w", 50.0, Some(0.0), 30.0) && !heard.online("e", 50.0, Some(0.0), 30.0));
        assert!(heard.online("e", 50.0, None, 30.0), "not started: online");
        // two own sensors: silent only when both are
        plan.sensors[1].own_room = Some(RoomIdx(0));
        assert_eq!(heard.silent(&plan, 50.0, Some(0.0), 30.0), [false, false]);
        assert_eq!(heard.silent(&plan, 51.0, Some(0.0), 30.0), [true, false]);
        // a room without an own sensor in use is never silent
        plan.sensors[0].in_use = false;
        plan.sensors[1].in_use = false;
        assert_eq!(heard.silent(&plan, 1e9, Some(0.0), 30.0), [false, false]);
        assert_eq!(Heard::watched(&plan), [false, false]);
    }
}
