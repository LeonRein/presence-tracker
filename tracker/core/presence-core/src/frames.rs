//! From an LD2450 frame to detections in the house frame (MODEL.md 4.1, preprocessing): the payload
//! checked, the frame's time from the sensor's uptime, the targets turned into positions on the floor
//! and marked where they can't be a person (behind a wall, outside all rooms, at the mount). Which of
//! them are measurements at all (coasted, frozen) decides ld2450::tracks.

use std::fmt;

use serde_json::{Map, Value};

use crate::config::TrackerParams;
use crate::geometry::Point;
use crate::vmath::py_hypot;
use crate::world::World;

/// The LD2450 reports at most three targets (its slots 1..=3); a frame with more is no frame of it.
pub const MAX_TARGETS: usize = 3;
/// mm, mm/s: the LD2450 reports a target as signed 16-bit numbers (the firmware rounds them to integers)
pub const LD2450_LIMIT: i64 = 32767;
/// the LD2410C's gate energies are percentages
pub const MAX_ENERGY: i64 = 100;
/// mm: the LD2410C reports its distances in cm as 16-bit numbers; the firmware sends mm
pub const MAX_LD_DISTANCE: i64 = 655_350;
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
    pub slot: u8,
    pub x: f64,
    pub y: f64,
    pub speed: f64,
}

impl Target {
    /// Where it is in the sensor's frame, in m.
    pub fn local(&self) -> Point {
        [self.x / 1000.0, self.y / 1000.0]
    }

    /// Its radial speed as measured (along the beam), m/s.
    pub fn radial_speed(&self) -> f64 {
        self.speed / 1000.0
    }

    /// In front of the sensor: a target it can have measured (the others are its "no value").
    pub fn in_front(&self) -> bool {
        self.local()[1] > 0.0
    }
}

/// The energies of the LD2410C's 9 gates (0.75 m each, 0..=100), moving and still (engineering mode).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LdGates {
    pub moving: [f64; 9],
    pub still: [f64; 9],
}

/// What a frame says of its LD2410C: its own presence flags and distances (mm; the web UI), and the
/// gates' energies (the filter, MODEL.md 4.3) if it sends them.
#[derive(Debug, Default, Clone, Copy, PartialEq)]
pub struct Ld2410 {
    pub moving: bool,
    pub still: bool,
    pub moving_distance: f64,
    pub still_distance: f64,
    pub gates: Option<LdGates>,
}

/// A frame as MQTT brings it (presence/<id>/frame), checked against what the firmware sends
/// (esphome/packages/radar-combo.yaml): the uptime as millis(), at most three targets in their slots,
/// the LD2410C part (None: the frame has none).
#[derive(Debug, Clone, PartialEq)]
pub struct Frame {
    pub uptime_ms: Option<u32>,
    pub targets: Vec<Target>,
    pub ld2410: Option<Ld2410>,
}

/// What is wrong with a target.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TargetField {
    NotAnObject,
    /// no integer 1..=3
    Slot,
    /// x, y, speed: no integer within the LD2450's limits
    X,
    Y,
    Speed,
}

/// What is wrong with a list of gate energies.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GateFault {
    NoList,
    /// not 9 gates
    Length(usize),
    /// an energy that is no integer 0..=100
    Energy,
    /// one of the two lists without the other
    Unpaired,
}

/// Why a payload is no frame (frames.parse_frame): a wrong payload under the prefix (another device,
/// a test, a firmware error) is dropped like a frame lost on the way.
#[derive(Debug, Clone, PartialEq)]
pub enum FrameError {
    NoJson(String),
    NotAnObject,
    /// uptime_ms is no integer 0..=u32::MAX (millis())
    Uptime,
    Targets,
    TooManyTargets(usize),
    Target {
        index: usize,
        field: TargetField,
    },
    DuplicateSlot(u8),
    Ld2410,
    /// moving or still is no true/false
    LdFlag(&'static str),
    /// a distance is no integer 0..=MAX_LD_DISTANCE
    LdDistance(&'static str),
    LdGates {
        key: &'static str,
        fault: GateFault,
    },
}

impl fmt::Display for FrameError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NoJson(e) => write!(f, "no JSON ({e})"),
            Self::NotAnObject => write!(f, "no object"),
            Self::Uptime => write!(f, "uptime_ms is no integer 0..={}", u32::MAX),
            Self::Targets => write!(f, "targets is no list"),
            Self::TooManyTargets(n) => write!(f, "{n} targets, the LD2450 has {MAX_TARGETS} slots"),
            Self::Target { index, field } => {
                let why = match field {
                    TargetField::NotAnObject => "no object".to_string(),
                    TargetField::Slot => format!("slot is no integer 1..={MAX_TARGETS}"),
                    TargetField::X => format!("x is no integer within ±{LD2450_LIMIT}"),
                    TargetField::Y => format!("y is no integer within ±{LD2450_LIMIT}"),
                    TargetField::Speed => format!("speed is no integer within ±{LD2450_LIMIT}"),
                };
                write!(f, "target {index}: {why}")
            }
            Self::DuplicateSlot(s) => write!(f, "two targets in slot {s}"),
            Self::Ld2410 => write!(f, "ld2410 is no object"),
            Self::LdFlag(k) => write!(f, "ld2410 {k} is no true/false"),
            Self::LdDistance(k) => write!(f, "ld2410 {k} is no integer 0..={MAX_LD_DISTANCE}"),
            Self::LdGates { key, fault } => match fault {
                GateFault::NoList => write!(f, "ld2410 {key} is no list"),
                GateFault::Length(n) => write!(f, "ld2410 {key} has {n} gates, not 9"),
                GateFault::Energy => write!(f, "ld2410 {key} has an energy that is no integer 0..={MAX_ENERGY}"),
                GateFault::Unpaired => write!(f, "ld2410 {key} without the other gates"),
            },
        }
    }
}

impl std::error::Error for FrameError {}

/// A call with what no frame can be: a sensor there is not, a time that is no number.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CallError {
    NoSensor(usize),
    Time,
}

impl fmt::Display for CallError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NoSensor(si) => write!(f, "no sensor {si}"),
            Self::Time => write!(f, "the frame's time is no number"),
        }
    }
}

impl std::error::Error for CallError {}

/// An integer in lo..=hi (JSON's 3.0 is none: the firmware sends integers).
fn integer(v: &Value, lo: i64, hi: i64) -> Option<i64> {
    v.as_i64().filter(|x| (lo..=hi).contains(x))
}

impl Frame {
    /// The payload as MQTT brings it.
    pub fn parse(payload: &[u8]) -> Result<Self, FrameError> {
        let value: Value = serde_json::from_slice(payload).map_err(|e| FrameError::NoJson(e.to_string()))?;
        Self::from_json(&value)
    }

    /// A frame as JSON: an object; uptime_ms an integer 0..=u32::MAX (or missing); targets a list of at
    /// most three objects with a slot 1..=3, each once, integers x, y (and speed) within the LD2450's
    /// limits; ld2410 an object (or missing) with flags moving, still, distances in mm and both or
    /// none of its lists of 9 gate energies 0..=100. Other keys (seq, resolution, the energies of the
    /// LD2410C's own target) are not read.
    pub fn from_json(value: &Value) -> Result<Self, FrameError> {
        let frame = value.as_object().ok_or(FrameError::NotAnObject)?;
        let uptime_ms = match frame.get("uptime_ms") {
            None | Some(Value::Null) => None,
            Some(up) => Some(integer(up, 0, u32::MAX.into()).ok_or(FrameError::Uptime)? as u32),
        };
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
            .map(|(index, tg)| target(tg).map_err(|field| FrameError::Target { index, field }))
            .collect::<Result<Vec<_>, _>>()?;
        for (k, a) in targets.iter().enumerate() {
            if targets[..k].iter().any(|b| b.slot == a.slot) {
                return Err(FrameError::DuplicateSlot(a.slot));
            }
        }
        let ld2410 = match frame.get("ld2410") {
            None => None,
            // a part without anything in it
            Some(Value::Null) => Some(Ld2410::default()),
            Some(Value::Object(ld)) => Some(ld2410(ld)?),
            Some(_) => return Err(FrameError::Ld2410),
        };
        Ok(Self {
            uptime_ms,
            targets,
            ld2410,
        })
    }

    /// The targets in front of the sensor, in the frame's order.
    pub fn in_front(&self) -> impl Iterator<Item = &Target> {
        self.targets.iter().filter(|tg| tg.in_front())
    }
}

fn target(value: &Value) -> Result<Target, TargetField> {
    let tg = value.as_object().ok_or(TargetField::NotAnObject)?;
    let field = |key: &str, which: TargetField| {
        tg.get(key)
            .and_then(|v| integer(v, -LD2450_LIMIT, LD2450_LIMIT))
            .map(|x| x as f64)
            .ok_or(which)
    };
    let slot = tg
        .get("slot")
        .and_then(|v| integer(v, 1, MAX_TARGETS as i64))
        .ok_or(TargetField::Slot)? as u8;
    Ok(Target {
        slot,
        x: field("x", TargetField::X)?,
        y: field("y", TargetField::Y)?,
        // the firmware always sends it; missing it is a still target
        speed: match tg.get("speed") {
            None => 0.0,
            Some(_) => field("speed", TargetField::Speed)?,
        },
    })
}

fn ld2410(ld: &Map<String, Value>) -> Result<Ld2410, FrameError> {
    let flag = |key: &'static str| match ld.get(key) {
        None | Some(Value::Null) => Ok(false),
        Some(Value::Bool(b)) => Ok(*b),
        Some(_) => Err(FrameError::LdFlag(key)),
    };
    let distance = |key: &'static str| match ld.get(key) {
        None | Some(Value::Null) => Ok(0.0),
        Some(v) => integer(v, 0, MAX_LD_DISTANCE)
            .map(|x| x as f64)
            .ok_or(FrameError::LdDistance(key)),
    };
    let gates = |key: &'static str| -> Result<Option<[f64; 9]>, FrameError> {
        let fault = |fault| FrameError::LdGates { key, fault };
        let list = match ld.get(key) {
            None | Some(Value::Null) => return Ok(None),
            Some(Value::Array(list)) => list,
            Some(_) => return Err(fault(GateFault::NoList)),
        };
        let energies: [&Value; 9] = list
            .iter()
            .collect::<Vec<_>>()
            .try_into()
            .map_err(|_| fault(GateFault::Length(list.len())))?;
        let mut out = [0.0; 9];
        for (o, e) in out.iter_mut().zip(energies) {
            *o = integer(e, 0, MAX_ENERGY).ok_or(fault(GateFault::Energy))? as f64;
        }
        Ok(Some(out))
    };
    let gates = match (gates("move_gates")?, gates("still_gates")?) {
        (Some(moving), Some(still)) => Some(LdGates { moving, still }),
        (None, None) => None,
        (Some(_), None) => {
            return Err(FrameError::LdGates {
                key: "move_gates",
                fault: GateFault::Unpaired,
            });
        }
        (None, Some(_)) => {
            return Err(FrameError::LdGates {
                key: "still_gates",
                fault: GateFault::Unpaired,
            });
        }
    };
    Ok(Ld2410 {
        moving: flag("moving")?,
        still: flag("still")?,
        moving_distance: distance("moving_distance")?,
        still_distance: distance("still_distance")?,
        gates,
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

    /// The frame time of a frame received at `recv` with this uptime (Frame::uptime_ms, millis(); None:
    /// the receive time).
    pub fn at(&mut self, recv: f64, uptime_ms: Option<u32>) -> f64 {
        let Some(up) = uptime_ms.map(|ms| f64::from(ms) / 1000.0) else {
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

/// The targets of a frame of sensor si in front of it as detections (frames.detections with the mount).
pub fn detections(world: &World, si: usize, frame: &Frame) -> Vec<Detection> {
    let (mount, reading) = (&world.mounts[si], world.reading);
    let origin = world.sights[si].origin;
    frame
        .in_front()
        .map(|&target| {
            let on = mount.to_world(target.local(), reading.target_height);
            // on.ground >= 0.1 x scale > 0 (Mount::to_world, the configuration's scale is positive)
            let (dx, dy) = (on.pos[0] - mount.at[0], on.pos[1] - mount.at[1]);
            let u = [dx / on.ground, dy / on.ground];
            let hidden = world.plan.hidden(origin, on.pos, u, reading.wall_margin) || py_hypot(dx, dy) < MOUNT_RADIUS;
            Detection {
                target,
                pos: on.pos,
                speed: target.radial_speed() * (on.slant / on.ground),
                hidden,
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Mount;
    use crate::testing;

    fn parse(s: &str) -> Result<Frame, FrameError> {
        Frame::parse(s.as_bytes())
    }

    fn target_error(index: usize, field: TargetField) -> FrameError {
        FrameError::Target { index, field }
    }

    fn gates_error(key: &'static str, fault: GateFault) -> FrameError {
        FrameError::LdGates { key, fault }
    }

    const NINE: &str = "[1, 2, 3, 4, 5, 6, 7, 8, 9]";

    #[test]
    fn a_payload_that_is_no_frame_says_why() {
        let three = r#"{"x": 1, "y": 2, "slot": 1}, {"x": 1, "y": 2, "slot": 2}, {"x": 1, "y": 2, "slot": 3}"#;
        let cases = [
            ("{".to_string(), FrameError::NoJson(String::new())),
            ("[1]".into(), FrameError::NotAnObject),
            (r#"{"uptime_ms": "x"}"#.into(), FrameError::Uptime),
            (r#"{"uptime_ms": -1}"#.into(), FrameError::Uptime),
            (r#"{"uptime_ms": true}"#.into(), FrameError::Uptime),
            (r#"{"uptime_ms": 1000.5}"#.into(), FrameError::Uptime),
            (r#"{"uptime_ms": 4294967296}"#.into(), FrameError::Uptime),
            // until 0.37 this was the frame time 0: a negative gap, then lost data for the sensor
            (r#"{"uptime_ms": 1e300}"#.into(), FrameError::Uptime),
            (r#"{"targets": null}"#.into(), FrameError::Targets),
            (
                format!(r#"{{"targets": [{three}, {{"x": 1, "y": 2, "slot": 1}}]}}"#),
                FrameError::TooManyTargets(4),
            ),
            (
                r#"{"targets": [[1]]}"#.into(),
                target_error(0, TargetField::NotAnObject),
            ),
            (
                r#"{"targets": [{"x": 1, "y": 2}]}"#.into(),
                target_error(0, TargetField::Slot),
            ),
            (
                r#"{"targets": [{"x": 1, "y": 2, "slot": 0}]}"#.into(),
                target_error(0, TargetField::Slot),
            ),
            (
                r#"{"targets": [{"x": 1, "y": 2, "slot": 4}]}"#.into(),
                target_error(0, TargetField::Slot),
            ),
            (
                r#"{"targets": [{"x": 1, "y": 2, "slot": 1.0}]}"#.into(),
                target_error(0, TargetField::Slot),
            ),
            (
                r#"{"targets": [{"x": 1, "y": 2, "slot": true}]}"#.into(),
                target_error(0, TargetField::Slot),
            ),
            (
                r#"{"targets": [{"x": 100, "slot": 1}]}"#.into(),
                target_error(0, TargetField::Y),
            ),
            (
                r#"{"targets": [{"x": 1, "y": 2, "slot": 1}, {"x": null, "y": 2, "slot": 2}]}"#.into(),
                target_error(1, TargetField::X),
            ),
            (
                r#"{"targets": [{"x": 1e300, "y": 2, "slot": 1}]}"#.into(),
                target_error(0, TargetField::X),
            ),
            (
                r#"{"targets": [{"x": 1, "y": 32768, "slot": 1}]}"#.into(),
                target_error(0, TargetField::Y),
            ),
            (
                r#"{"targets": [{"x": 1, "y": 2.5, "slot": 1}]}"#.into(),
                target_error(0, TargetField::Y),
            ),
            (
                r#"{"targets": [{"x": 1, "y": 2, "slot": 1, "speed": null}]}"#.into(),
                target_error(0, TargetField::Speed),
            ),
            (
                r#"{"targets": [{"x": 1, "y": 2, "slot": 1, "speed": -40000}]}"#.into(),
                target_error(0, TargetField::Speed),
            ),
            (
                r#"{"targets": [{"x": 1, "y": 2, "slot": 1}, {"x": 3, "y": 4, "slot": 1}]}"#.into(),
                FrameError::DuplicateSlot(1),
            ),
            (r#"{"ld2410": []}"#.into(), FrameError::Ld2410),
            (r#"{"ld2410": {"moving": 1}}"#.into(), FrameError::LdFlag("moving")),
            (
                r#"{"ld2410": {"moving_distance": "far"}}"#.into(),
                FrameError::LdDistance("moving_distance"),
            ),
            (
                r#"{"ld2410": {"still_distance": -10}}"#.into(),
                FrameError::LdDistance("still_distance"),
            ),
            (
                format!(r#"{{"ld2410": {{"move_gates": ["a"], "still_gates": {NINE}}}}}"#),
                gates_error("move_gates", GateFault::Length(1)),
            ),
            (
                r#"{"ld2410": {"still_gates": 3}}"#.into(),
                gates_error("still_gates", GateFault::NoList),
            ),
            (
                format!(r#"{{"ld2410": {{"move_gates": [1, 2, 3, 4, 5, 6, 7, 8], "still_gates": {NINE}}}}}"#),
                gates_error("move_gates", GateFault::Length(8)),
            ),
            (
                format!(r#"{{"ld2410": {{"move_gates": {NINE}, "still_gates": [0, 0, 0, 0, 0, 0, 0, 0, 101]}}}}"#),
                gates_error("still_gates", GateFault::Energy),
            ),
            (
                format!(r#"{{"ld2410": {{"move_gates": {NINE}, "still_gates": [0, 0, 0, 0, 0, 0, 0, 0, 1e300]}}}}"#),
                gates_error("still_gates", GateFault::Energy),
            ),
            (
                format!(r#"{{"ld2410": {{"move_gates": {NINE}}}}}"#),
                gates_error("move_gates", GateFault::Unpaired),
            ),
        ];
        for (payload, want) in cases {
            let got = parse(&payload).expect_err(&payload);
            match (&got, &want) {
                (FrameError::NoJson(_), FrameError::NoJson(_)) => {}
                _ => assert_eq!(got, want, "{payload}"),
            }
            assert!(!got.to_string().is_empty());
        }
    }

    #[test]
    fn a_frame_keeps_its_uptime_targets_and_what_its_ld2410c_brought() {
        let f = parse(
            r#"{"seq": 7, "uptime_ms": 4294967295, "targets": [{"x": -120, "y": 2500, "speed": -30, "slot": 2,
                "resolution": 320}, {"x": 1, "y": -2, "slot": 3}]}"#,
        )
        .unwrap();
        assert_eq!(f.uptime_ms, Some(u32::MAX));
        let t0 = Target {
            slot: 2,
            x: -120.0,
            y: 2500.0,
            speed: -30.0,
        };
        let t1 = Target {
            slot: 3,
            x: 1.0,
            y: -2.0,
            speed: 0.0,
        };
        assert_eq!(f.targets, vec![t0, t1]);
        assert_eq!(f.targets[0].local(), [-0.12, 2.5]);
        assert_eq!(f.targets[0].radial_speed(), -0.03);
        assert_eq!(
            f.in_front().collect::<Vec<_>>(),
            vec![&t0],
            "behind the sensor: its no value"
        );
        assert_eq!(f.ld2410, None);
        assert_eq!(parse("{}").unwrap().uptime_ms, None);
        assert_eq!(parse(r#"{"ld2410": null}"#).unwrap().ld2410, Some(Ld2410::default()));
        let ld = parse(r#"{"ld2410": {"moving": true, "moving_distance": 1200, "moving_energy": 55}}"#)
            .unwrap()
            .ld2410
            .unwrap();
        assert!(ld.moving && !ld.still && ld.moving_distance == 1200.0 && ld.gates.is_none());
        let nine = format!(r#"{{"ld2410": {{"move_gates": {NINE}, "still_gates": [0, 0, 0, 0, 0, 0, 0, 0, 100]}}}}"#);
        let g = parse(&nine).unwrap().ld2410.unwrap().gates.unwrap();
        assert_eq!((g.moving[8], g.still[8]), (9.0, 100.0));
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
            .map(|(k, j)| clock.at(100.0 + 0.1 * k as f64 + j, Some(1000 + 100 * k as u32)))
            .collect();
        for (k, w) in times.windows(2).enumerate() {
            let want = if k == 0 { 0.07 } else { 0.1 };
            assert!((w[1] - w[0] - want).abs() < 1e-3 + 1e-9, "{k}: {w:?}");
        }
        let a = clock.anchor().unwrap();
        assert!((a.offset - 99.0).abs() < 1e-3 + 1e-9);
        // a slower broker: the anchor creeps up by 1 ms per s, no faster
        assert_eq!(a.last_up, 1.4);
        let t = clock.at(a.offset + 11.4 + 0.5, Some(11_400));
        assert!((t - (a.offset + 0.01 + 11.4)).abs() < 1e-9);
        // a reboot (the uptime goes back; also millis() running over after 49.7 days) or a jump of
        // more than 2 s: anchored anew
        assert_eq!(clock.at(500.0, Some(1000)), 500.0);
        assert_eq!(clock.at(503.6, Some(1500)), 503.6);
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
                let on = s.mount().to_world(local, 1.0);
                let back = s.to_local(on.pos);
                assert!(
                    (back[0] - local[0]).abs() < 1e-12 && (back[1] - local[1]).abs() < 1e-12,
                    "{mirror} {local:?}"
                );
            }
        }
    }

    /// A frame of these targets (x, y, speed), in slots 1, 2, 3.
    fn frame(targets: &[(f64, f64, f64)]) -> Frame {
        Frame {
            uptime_ms: None,
            targets: targets
                .iter()
                .zip(1..)
                .map(|(&(x, y, speed), slot)| Target { slot, x, y, speed })
                .collect(),
            ld2410: None,
        }
    }

    #[test]
    fn targets_behind_a_wall_outside_or_at_the_mount_are_hidden() {
        // the flat: room 6 x 5 m, sensor 0 at (0.05, 0.05) looking at 45 degrees, at 1.5 m
        let world = testing::flat();
        let dets = detections(
            &world,
            0,
            &frame(&[(0.0, 3000.0, 400.0), (0.0, 9000.0, 0.0), (0.0, -500.0, 0.0)]),
        );
        assert_eq!(dets.len(), 2, "behind the sensor: dropped");
        assert!(!dets[0].hidden && dets[1].hidden, "9 m away is beyond the room's walls");
        // radial speed on the floor: slant / ground of the measured one
        let slant = 3.0;
        let ground = (slant * slant - 0.25f64).sqrt();
        assert!((dets[0].speed - 0.4 * slant / ground).abs() < 1e-12);
        let at_mount = detections(&world, 0, &frame(&[(0.0, 500.0, 0.0)]));
        assert!(
            at_mount[0].hidden,
            "0.5 m slant from 0.5 m above: right below it, the mount"
        );
    }
}
