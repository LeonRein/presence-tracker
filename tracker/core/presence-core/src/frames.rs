//! From an LD2450 frame to detections in the house frame (MODEL.md 4.1, preprocessing): the payload
//! checked, the frame's time from the sensor's uptime, the targets turned into positions on the floor
//! and marked where they can't be a person (behind a wall, outside all rooms, at the mount). Which of
//! them are measurements at all (coasted, frozen) decides ld2450::tracks.

use std::fmt;

use serde_json::{Map, Value};

use crate::config::{SensorConfig, TrackerParams};
use crate::geometry::Point;
use crate::vmath::py_hypot;
use crate::world::World;

/// The LD2450 reports at most three targets (its slots); a frame with more is no frame of it.
pub const MAX_TARGETS: usize = 3;
/// m: targets this close to a sensor come from its mount
pub const MOUNT_RADIUS: f64 = 0.3;
/// s: a frame time this far from the anchor is a new one (a reboot, a broker that stalled)
const CLOCK_JUMP: f64 = 2.0;
/// s per s: how fast the anchor creeps up (clock drift, a broker that got slower)
const CLOCK_CREEP: f64 = 0.001;

/// One target as the LD2450 reports it: its slot, x and y in mm in its own frame (x right, y
/// forward), its radial speed in mm/s (negative: approaching).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Target {
    pub slot: i64,
    pub x: f64,
    pub y: f64,
    pub speed: f64,
}

impl Target {
    /// Where it is in the sensor's frame, in m.
    pub fn local(&self) -> Point {
        [self.x / 1000.0, self.y / 1000.0]
    }
}

/// The energies of the LD2410C's 9 gates (0.75 m each), moving and still (engineering mode).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LdGates {
    pub moving: [f64; 9],
    pub still: [f64; 9],
}

/// What a frame says of its LD2410C.
#[derive(Debug, Default, Clone, Copy, PartialEq)]
pub enum Ld2410 {
    /// nothing: the frame has no LD2410C part
    #[default]
    Absent,
    /// a part without the 9 + 9 gate energies (the firmware's "no value")
    NoGates,
    Gates(LdGates),
}

/// A frame as MQTT brings it (presence/<id>/frame), checked.
#[derive(Debug, Clone, PartialEq)]
pub struct Frame {
    pub targets: Vec<Target>,
    pub ld2410: Ld2410,
}

/// Why a payload is no frame (frames.parse_frame): a wrong payload under the prefix (another device,
/// a test, a firmware error) is dropped like a frame lost on the way.
#[derive(Debug, Clone, PartialEq)]
pub enum FrameError {
    NoJson(String),
    NotAnObject,
    Uptime,
    Targets,
    TooManyTargets(usize),
    /// the target at this index: no object, x or y no number, speed no number, slot no integer
    Target(usize),
    DuplicateSlot(i64),
    Ld2410,
    LdGates(&'static str),
    LdDistance(&'static str),
    /// a sensor the world does not have
    NoSensor(usize),
    /// the frame's time is no number
    Time,
}

impl fmt::Display for FrameError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NoJson(e) => write!(f, "no JSON ({e})"),
            Self::NotAnObject => write!(f, "no object"),
            Self::Uptime => write!(f, "uptime_ms is no number >= 0"),
            Self::Targets => write!(f, "targets is no list"),
            Self::TooManyTargets(n) => write!(f, "{n} targets, the LD2450 has {MAX_TARGETS} slots"),
            Self::Target(k) => write!(f, "target {k}: no object with numbers x, y (speed) and an integer slot"),
            Self::DuplicateSlot(s) => write!(f, "two targets in slot {s}"),
            Self::Ld2410 => write!(f, "ld2410 is no object"),
            Self::LdGates(k) => write!(f, "ld2410 {k} is no list of numbers"),
            Self::LdDistance(k) => write!(f, "ld2410 {k} is no number"),
            Self::NoSensor(si) => write!(f, "no sensor {si}"),
            Self::Time => write!(f, "the frame's time is no number"),
        }
    }
}

impl std::error::Error for FrameError {}

/// A finite number (JSON has no others; true and false are none).
fn number(v: &Value) -> Option<f64> {
    v.as_f64().filter(|x| x.is_finite())
}

impl Frame {
    /// The payload as MQTT brings it.
    pub fn parse(payload: &[u8]) -> Result<Self, FrameError> {
        let value: Value = serde_json::from_slice(payload).map_err(|e| FrameError::NoJson(e.to_string()))?;
        Self::from_json(&value)
    }

    /// A frame as JSON: an object; uptime_ms a number >= 0 (or missing); targets a list of at most
    /// three objects with numbers x, y (speed, an integer slot if there), each slot once; ld2410 an
    /// object (or missing) with its gates lists of numbers and its distances numbers.
    pub fn from_json(value: &Value) -> Result<Self, FrameError> {
        let frame = value.as_object().ok_or(FrameError::NotAnObject)?;
        match frame.get("uptime_ms") {
            None | Some(Value::Null) => {}
            Some(up) => {
                number(up).filter(|&u| u >= 0.0).ok_or(FrameError::Uptime)?;
            }
        }
        let targets = match frame.get("targets") {
            None => &[][..],
            Some(Value::Array(targets)) => targets.as_slice(),
            Some(_) => return Err(FrameError::Targets),
        };
        if targets.len() > MAX_TARGETS {
            return Err(FrameError::TooManyTargets(targets.len()));
        }
        let targets = targets
            .iter()
            .enumerate()
            .map(|(k, tg)| target(tg).ok_or(FrameError::Target(k)))
            .collect::<Result<Vec<_>, _>>()?;
        for (k, a) in targets.iter().enumerate() {
            if targets[..k].iter().any(|b| b.slot == a.slot) {
                return Err(FrameError::DuplicateSlot(a.slot));
            }
        }
        let ld2410 = match frame.get("ld2410") {
            None => Ld2410::Absent,
            Some(Value::Null) => Ld2410::NoGates,
            Some(Value::Object(ld)) => ld2410(ld)?,
            Some(_) => return Err(FrameError::Ld2410),
        };
        Ok(Self { targets, ld2410 })
    }
}

fn target(value: &Value) -> Option<Target> {
    let tg = value.as_object()?;
    let speed = match tg.get("speed") {
        None => 0.0,
        Some(v) => number(v)?,
    };
    let slot = match tg.get("slot") {
        None => 0,
        Some(v) => v.as_i64()?,
    };
    Some(Target {
        slot,
        x: number(tg.get("x")?)?,
        y: number(tg.get("y")?)?,
        speed,
    })
}

fn ld2410(ld: &Map<String, Value>) -> Result<Ld2410, FrameError> {
    let gates = |key: &'static str| -> Result<Option<Vec<f64>>, FrameError> {
        match ld.get(key) {
            None | Some(Value::Null) => Ok(None),
            Some(Value::Array(g)) => g
                .iter()
                .map(number)
                .collect::<Option<_>>()
                .map(Some)
                .ok_or(FrameError::LdGates(key)),
            Some(_) => Err(FrameError::LdGates(key)),
        }
    };
    let (moving, still) = (gates("move_gates")?, gates("still_gates")?);
    for key in ["moving_distance", "still_distance"] {
        if ld.get(key).is_some_and(|d| !d.is_null() && number(d).is_none()) {
            return Err(FrameError::LdDistance(key));
        }
    }
    let nine = |g: Option<Vec<f64>>| g.and_then(|g| <[f64; 9]>::try_from(g).ok());
    Ok(match (nine(moving), nine(still)) {
        (Some(moving), Some(still)) => Ld2410::Gates(LdGates { moving, still }),
        _ => Ld2410::NoGates,
    })
}

/// Frame time from the sensor's uptime, anchored to the receive time (frames.SensorClock).
///
/// The receive time jitters with WiFi; the uptime does not. The anchor follows the fastest delivery
/// seen (smallest receive - uptime) and creeps up by 1 ms per second so that it follows clock drift
/// and a broker that got slower. It starts over on a reboot or a jump.
#[derive(Debug, Default, Clone, Copy, PartialEq)]
pub struct SensorClock {
    anchor: Option<Anchor>,
}

/// receive time - uptime as the clock holds it, and the uptime (s) of the frame that moved it last
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Anchor {
    pub offset: f64,
    pub last_up: f64,
}

impl SensorClock {
    /// A clock that goes on from a saved anchor.
    pub fn anchored(anchor: Anchor) -> Self {
        Self { anchor: Some(anchor) }
    }

    pub fn anchor(&self) -> Option<Anchor> {
        self.anchor
    }

    /// The frame time of a frame received at `recv` with this uptime (ms; None: the receive time).
    pub fn at(&mut self, recv: f64, uptime_ms: Option<f64>) -> f64 {
        let Some(up) = uptime_ms.map(|ms| ms / 1000.0) else {
            return recv;
        };
        let candidate = recv - up;
        let offset = match self.anchor {
            Some(a) if up >= a.last_up && (candidate - a.offset).abs() <= CLOCK_JUMP => {
                candidate.min(a.offset + CLOCK_CREEP * (up - a.last_up))
            }
            _ => candidate,
        };
        self.anchor = Some(Anchor { offset, last_up: up });
        up + offset
    }
}

/// How a sensor hangs (model.SensorConfig): what turns its targets into floor positions.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Mount {
    pub at: Point,
    /// (cos, sin) of its heading
    pub axis: Point,
    pub height: f64,
    /// its x axis points to the left
    pub mirror: bool,
    /// true distance on the floor / measured one (from the calibration)
    pub scale: f64,
}

/// A target on the floor: where, and its distance to the sensor on the floor and along the beam.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct OnFloor {
    pub pos: Point,
    pub ground: f64,
    pub slant: f64,
}

impl Mount {
    pub fn of(s: &SensorConfig) -> Self {
        Self {
            at: s.at(),
            axis: s.axis(),
            height: s.height,
            mirror: s.mirror,
            scale: s.scale,
        }
    }

    /// A target (m, sensor frame) in the house frame (SensorConfig.to_world). The radar measures the
    /// slant distance from its antenna; the height difference to the reflecting body is taken out so
    /// that the position lies on the floor. Then the sensor's scale corrects the distance.
    pub fn to_world(&self, [lx, ly]: Point, target_height: f64) -> OnFloor {
        let lx = if self.mirror { -lx } else { lx };
        let slant = py_hypot(lx, ly);
        let dh = self.height - target_height;
        let ground = (slant * slant - dh * dh).max(0.01).sqrt();
        let k = if slant > 0.0 {
            self.scale * ground / slant
        } else {
            self.scale
        };
        let (gx, gy) = (lx * k, ly * k);
        // sensor frame: y forward along the heading, x to the right
        let [c, s] = self.axis;
        OnFloor {
            pos: [self.at[0] + gy * c + gx * s, self.at[1] + gy * s - gx * c],
            ground: self.scale * ground,
            slant: self.scale * slant,
        }
    }
}

/// What reading the frames needs of the tracker's parameters.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Reading {
    /// m: height of the reflecting body (chest), for the slant correction
    pub target_height: f64,
    /// m: how far behind a wall or outside a room a target may be and still be measured
    pub wall_margin: f64,
}

impl Reading {
    pub fn of(p: &TrackerParams) -> Self {
        Self {
            target_height: p.target_height,
            wall_margin: p.wall_margin,
        }
    }
}

/// A target of a frame in the house frame.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Detection {
    pub target: Target,
    pub pos: Point,
    /// radial speed on the floor, m/s, negative = approaching
    pub speed: f64,
    /// behind a wall, outside all rooms or at the mount: no person (a reflection, the mount itself)
    pub hidden: bool,
}

/// The targets of sensor si in front of it as detections (frames.detections with the mount).
pub fn detections(world: &World, si: usize, targets: &[Target]) -> Vec<Detection> {
    let (mount, reading) = (&world.mounts[si], world.reading);
    let origin = world.sights[si].origin;
    targets
        .iter()
        .filter(|tg| tg.local()[1] > 0.0)
        .map(|&target| {
            let on = mount.to_world(target.local(), reading.target_height);
            let (dx, dy) = (on.pos[0] - mount.at[0], on.pos[1] - mount.at[1]);
            let reach = on.ground.max(1e-3);
            let u = [dx / reach, dy / reach];
            let ratio = if on.ground > 0.0 { on.slant / on.ground } else { 1.0 };
            let hidden = world.plan.hidden(origin, on.pos, u, reading.wall_margin) || py_hypot(dx, dy) < MOUNT_RADIUS;
            Detection {
                target,
                pos: on.pos,
                speed: target.speed / 1000.0 * ratio,
                hidden,
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing;

    fn parse(s: &str) -> Result<Frame, FrameError> {
        Frame::parse(s.as_bytes())
    }

    #[test]
    fn a_payload_that_is_no_frame_says_why() {
        let cases = [
            ("{", "no JSON"),
            ("[1]", "no object"),
            (r#"{"uptime_ms": "x"}"#, "uptime_ms"),
            (r#"{"uptime_ms": -1}"#, "uptime_ms"),
            (r#"{"uptime_ms": true}"#, "uptime_ms"),
            (r#"{"targets": null}"#, "targets is no list"),
            (
                r#"{"targets": [{"x": 1, "y": 2}, {"x": 1, "y": 2, "slot": 1}, {"x": 1, "y": 2, "slot": 2}, {"x": 1, "y": 2, "slot": 3}]}"#,
                "4 targets",
            ),
            (r#"{"targets": [[1]]}"#, "target 0"),
            (r#"{"targets": [{"x": 100, "slot": 1}]}"#, "target 0"),
            (
                r#"{"targets": [{"x": 1, "y": 2, "slot": 1}, {"x": null, "y": 2, "slot": 2}]}"#,
                "target 1",
            ),
            (r#"{"targets": [{"x": 1, "y": 2, "slot": 1.0}]}"#, "target 0"),
            (r#"{"targets": [{"x": 1, "y": 2, "slot": true}]}"#, "target 0"),
            (r#"{"targets": [{"x": 1, "y": 2, "speed": null}]}"#, "target 0"),
            (
                r#"{"targets": [{"x": 1, "y": 2, "slot": 1}, {"x": 3, "y": 4, "slot": 1}]}"#,
                "two targets in slot 1",
            ),
            (r#"{"ld2410": []}"#, "ld2410 is no object"),
            (
                r#"{"ld2410": {"move_gates": ["a"], "still_gates": []}}"#,
                "ld2410 move_gates",
            ),
            (r#"{"ld2410": {"still_gates": 3}}"#, "ld2410 still_gates"),
            (r#"{"ld2410": {"moving_distance": "far"}}"#, "ld2410 moving_distance"),
        ];
        for (payload, why) in cases {
            let e = parse(payload).expect_err(payload);
            assert!(e.to_string().starts_with(why), "{payload}: {e}");
        }
    }

    #[test]
    fn a_frame_keeps_its_targets_and_says_what_its_ld2410c_brought() {
        let f = parse(
            r#"{"uptime_ms": 5, "targets": [{"x": -120, "y": 2500, "speed": -30, "slot": 2}, {"x": 1, "y": 2}]}"#,
        )
        .unwrap();
        let t0 = Target {
            slot: 2,
            x: -120.0,
            y: 2500.0,
            speed: -30.0,
        };
        assert_eq!(
            f.targets,
            vec![
                t0,
                Target {
                    slot: 0,
                    x: 1.0,
                    y: 2.0,
                    speed: 0.0
                }
            ]
        );
        assert_eq!(f.targets[0].local(), [-0.12, 2.5]);
        assert_eq!(f.ld2410, Ld2410::Absent);
        assert_eq!(parse(r#"{"ld2410": null}"#).unwrap().ld2410, Ld2410::NoGates);
        assert_eq!(
            parse(r#"{"ld2410": {"moving": true}}"#).unwrap().ld2410,
            Ld2410::NoGates
        );
        let eight = r#"{"ld2410": {"move_gates": [1,2,3,4,5,6,7,8], "still_gates": [1,2,3,4,5,6,7,8,9]}}"#;
        assert_eq!(parse(eight).unwrap().ld2410, Ld2410::NoGates, "8 gates: no value");
        let nine = r#"{"ld2410": {"move_gates": [1,2,3,4,5,6,7,8,9], "still_gates": [0,0,0,0,0,0,0,0,9.5],
            "moving_distance": 120, "still_distance": null}}"#;
        let Ld2410::Gates(g) = parse(nine).unwrap().ld2410 else {
            panic!("gates")
        };
        assert_eq!((g.moving[8], g.still[8]), (9.0, 9.5));
        assert!(parse("{}").unwrap().targets.is_empty());
    }

    #[test]
    fn the_clock_follows_the_fastest_delivery_and_starts_over_after_a_reboot_or_a_jump() {
        let mut clock = SensorClock::default();
        assert_eq!(clock.at(7.0, None), 7.0, "without an uptime: the receive time");
        // frames every 0.1 s by the uptime, received with 0..50 ms of jitter: the times keep the
        // uptime's spacing, anchored at the fastest delivery
        let jitter = [0.03, 0.0, 0.05, 0.01, 0.02];
        let times: Vec<f64> = jitter
            .iter()
            .enumerate()
            .map(|(k, j)| clock.at(100.0 + 0.1 * k as f64 + j, Some(1000.0 + 100.0 * k as f64)))
            .collect();
        for (k, w) in times.windows(2).enumerate() {
            let want = if k == 0 { 0.07 } else { 0.1 };
            assert!((w[1] - w[0] - want).abs() < 1e-3 + 1e-9, "{k}: {w:?}");
        }
        let a = clock.anchor().unwrap();
        assert!((a.offset - 99.0).abs() < 1e-3 + 1e-9);
        // a slower broker: the anchor creeps up by 1 ms per s, no faster
        let up = a.last_up + 10.0;
        let t = clock.at(a.offset + up + 0.5, Some(up * 1000.0));
        assert!((t - (a.offset + 0.01 + up)).abs() < 1e-9);
        // a reboot (the uptime goes back) or a jump of more than 2 s: anchored anew
        assert_eq!(clock.at(500.0, Some(1000.0)), 500.0);
        assert_eq!(clock.at(503.6, Some(1500.0)), 503.6);
        let resumed = SensorClock::anchored(Anchor {
            offset: 499.0,
            last_up: 1.0,
        });
        assert_eq!(resumed.anchor().unwrap().offset, 499.0);
    }

    fn mount(mirror: bool) -> Mount {
        Mount {
            at: [1.0, 2.0],
            axis: [0.0, 1.0], // looks along +y
            height: 1.5,
            mirror,
            scale: 1.1,
        }
    }

    #[test]
    fn a_target_lands_on_the_floor_at_its_ground_range() {
        // looking along +y, x to the right is +x of the house; slant 2.5 m from 1.5 m to 1.0 m height
        let on = mount(false).to_world([1.5, 2.0], 1.0);
        let ground = (2.5f64 * 2.5 - 0.25).sqrt();
        let k = 1.1 * ground / 2.5;
        assert!((on.pos[0] - (1.0 + 1.5 * k)).abs() < 1e-12 && (on.pos[1] - (2.0 + 2.0 * k)).abs() < 1e-12);
        assert!((on.ground - 1.1 * ground).abs() < 1e-12 && (on.slant - 1.1 * 2.5).abs() < 1e-12);
        let mirrored = mount(true).to_world([1.5, 2.0], 1.0);
        assert!((mirrored.pos[0] - (1.0 - 1.5 * k)).abs() < 1e-12);
        // at the antenna: the floor below it, at least 0.1 m away
        assert!((mount(false).to_world([0.0, 0.0], 1.5).ground - 0.11).abs() < 1e-12);
    }

    #[test]
    fn to_world_and_to_local_are_inverse_on_the_floor() {
        for mirror in [false, true] {
            let config = testing::config(&[("a", "a", [1.0, 2.0], 37.0)], "[]", "[]", "[]");
            let mut s = config.sensors.into_iter().next().unwrap();
            (s.mirror, s.scale, s.height) = (mirror, 1.1, 1.0); // the body at the antenna's height
            for local in [[0.3, 2.0], [-1.2, 4.5], [2.0, 0.5]] {
                let on = Mount::of(&s).to_world(local, 1.0);
                let back = s.to_local(on.pos);
                assert!(
                    (back[0] - local[0]).abs() < 1e-12 && (back[1] - local[1]).abs() < 1e-12,
                    "{mirror} {local:?}"
                );
            }
        }
    }

    fn target(x: f64, y: f64, speed: f64) -> Target {
        Target { slot: 1, x, y, speed }
    }

    #[test]
    fn targets_behind_a_wall_outside_or_at_the_mount_are_hidden() {
        // the flat: room 6 x 5 m, sensor 0 at (0.05, 0.05) looking at 45 degrees, at 1.5 m
        let world = testing::flat();
        let dets = detections(
            &world,
            0,
            &[
                target(0.0, 3000.0, 400.0),
                target(0.0, 9000.0, 0.0),
                target(0.0, -500.0, 0.0),
            ],
        );
        assert_eq!(dets.len(), 2, "behind the sensor: dropped");
        assert!(!dets[0].hidden && dets[1].hidden, "9 m away is beyond the room's walls");
        // radial speed on the floor: slant / ground of the measured one
        let slant = 3.0;
        let ground = (slant * slant - 0.25f64).sqrt();
        assert!((dets[0].speed - 0.4 * slant / ground).abs() < 1e-12);
        let at_mount = detections(&world, 0, &[target(0.0, 500.0, 0.0)]);
        assert!(
            at_mount[0].hidden,
            "0.5 m slant from 0.5 m above: right below it, the mount"
        );
    }
}
