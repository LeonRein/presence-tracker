//! The simple rule (erbe-17-11/werkzeuge/regel_vs_modell.py): a room is occupied while an LD2450 target is in it
//! (seen within the last second); with Node-RED's 120 s after that, the light is on while a target was there in
//! the last 2 min. Time is the time the frame was received.
//!
//! Parameters (`--set k=v`): `bewegt=true` only moving targets count; `eigener=true` only the room's own sensor
//! (presence-<room>).

use serde_json::{Map, Value};

use super::{Applied, Context, Input, Model, RoomValues, Values};
use crate::bench::Raw;

/// s: a room is occupied this long after its last target
const SEEN: f64 = 1.0;

pub struct Regel {
    raw: Raw,
    rooms: Vec<String>,
    moving_only: bool,
    own_only: bool,
    seen: Vec<f64>,
    /// the targets in each room in the last frame of each sensor (for E[count])
    count: Vec<Vec<(String, usize)>>,
    occupied: Vec<bool>,
}

impl Regel {
    pub fn new(ctx: &Context, set: &Map<String, Value>) -> Result<(Self, Vec<Applied>), String> {
        let (mut moving_only, mut own_only, mut applied) = (false, false, Vec::new());
        for (k, v) in set {
            let on = v
                .as_bool()
                .ok_or_else(|| format!("regel: {k} ist true oder false, nicht {v}"))?;
            match k.as_str() {
                "bewegt" => moving_only = on,
                "eigener" => own_only = on,
                _ => {
                    return Err(format!(
                        "unbekannter Parameter {k:?} der Regel (es gibt bewegt, eigener)"
                    ));
                }
            }
            applied.push(Applied {
                key: k.clone(),
                place: "regel",
                old: Value::Bool(false),
                new: v.clone(),
            });
        }
        let n = ctx.rooms.len();
        Ok((
            Self {
                raw: Raw::of(ctx.config, ctx.rooms),
                rooms: ctx.rooms.to_vec(),
                moving_only,
                own_only,
                seen: vec![f64::NEG_INFINITY; n],
                count: vec![Vec::new(); n],
                occupied: vec![false; n],
            },
            applied,
        ))
    }
}

impl Model for Regel {
    fn frame(&mut self, input: &Input) -> Result<f64, String> {
        let t = input.received;
        let mut here = vec![0usize; self.rooms.len()];
        for (p, _, speed) in self.raw.detections(input.board, input.payload) {
            if self.moving_only && speed == 0.0 {
                continue;
            }
            if let Some(k) = self.raw.room_at(p)
                && (!self.own_only || input.board == format!("presence-{}", self.rooms[k]))
            {
                self.seen[k] = t;
                here[k] += 1;
            }
        }
        for (k, n) in here.into_iter().enumerate() {
            let c = &mut self.count[k];
            match c.iter_mut().find(|(b, _)| b == input.board) {
                Some(e) => e.1 = n,
                None => c.push((input.board.to_string(), n)),
            }
        }
        Ok(t)
    }

    fn tick(&mut self, t: f64) -> Result<Option<Vec<bool>>, String> {
        self.occupied = self.seen.iter().map(|&s| t - s <= SEEN).collect();
        Ok(Some(self.occupied.clone()))
    }

    /// P(somebody) 1 or 0; E[count] the most targets one sensor sees in the room now (at least 1 while it is
    /// occupied); P(>= 2) unknown.
    fn values(&mut self) -> Values {
        let rooms = (0..self.rooms.len())
            .map(|k| {
                let on = self.occupied[k];
                let most = self.count[k].iter().map(|(_, n)| *n).max().unwrap_or(0);
                RoomValues {
                    p_any: f64::from(u8::from(on)),
                    e_count: if on { most.max(1) as f64 } else { 0.0 },
                    p_two: None,
                }
            })
            .collect();
        Values { rooms, e_house: None }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_room_is_occupied_while_a_target_is_in_it() {
        let config = json!({
            "sensors": [{"id": "presence-wohn", "x": 0.0, "y": 0.0, "heading": 90.0, "height": 1.5,
                         "placed": true, "enabled": true}],
            "zones": [{"id": "wohn", "points": [[-3, 0], [3, 0], [3, 5], [-3, 5]]},
                      {"id": "bad", "points": [[-3, 5], [3, 5], [3, 9], [-3, 9]]}],
            "params": {"target_height": 1.0}
        });
        let rooms = vec!["wohn".to_string(), "bad".to_string()];
        let ctx = Context {
            config: &config,
            config_path: "",
            learned: None,
            rooms: &rooms,
            scored: (0.0, 1e12),
            tz: 0.0,
        };
        let (mut m, _) = Regel::new(&ctx, &Map::new()).expect("the rule");
        let seen = json!({"uptime_ms": 1, "targets": [{"slot": 1, "x": 0, "y": 2000, "speed": 0}]});
        let empty = json!({"uptime_ms": 2, "targets": []});
        for (t, payload) in [(100.0, &seen), (100.5, &empty)] {
            let frame = presence_core::frames::Frame::from_json(payload).expect("a frame");
            let input = Input {
                received: t,
                board: "presence-wohn",
                frame: &frame,
                payload,
            };
            assert!((m.frame(&input).expect("frame") - t).abs() < 1e-12, "the rule's time is the receive time");
        }
        assert_eq!(m.tick(100.9).expect("tick"), Some(vec![true, false]));
        assert!((m.values().rooms[0].e_count - 0.0).abs() > 0.5);
        assert_eq!(m.tick(101.2).expect("tick"), Some(vec![false, false]));
        let mut set = Map::new();
        set.insert("gibt_es_nicht".into(), json!(true));
        assert!(Regel::new(&ctx, &set).is_err());
    }
}
