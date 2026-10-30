//! The check of a configuration (the JSON the shell stores, or what the web UI sends: only what is there is
//! checked, what is missing the shell fills with its defaults): what the model cannot run with, named by
//! where it is, what is wrong and the limits it broke (`check`). The one place that knows the limits of what
//! the model reads: the web UI reads them for its fields (config_limits), the shell only words the problems
//! for it. The background image is the web UI's alone (operating, not the model): the core never reads it.

use std::collections::BTreeSet;
use std::fmt;

use serde_json::{Map, Value};

use crate::plan::wall_of_door;

/// What a number may be: from lo to hi, each end in or out.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Limits {
    pub lo: f64,
    pub hi: f64,
    pub lo_open: bool,
    pub hi_open: bool,
}

impl Limits {
    /// lo <= x <= hi
    const fn closed(lo: f64, hi: f64) -> Self {
        Self {
            lo,
            hi,
            lo_open: false,
            hi_open: false,
        }
    }

    /// lo < x <= hi
    const fn above(lo: f64, hi: f64) -> Self {
        Self {
            lo,
            hi,
            lo_open: true,
            hi_open: false,
        }
    }

    /// lo < x < hi
    const fn between(lo: f64, hi: f64) -> Self {
        Self {
            lo,
            hi,
            lo_open: true,
            hi_open: true,
        }
    }

    pub fn admits(&self, x: f64) -> bool {
        let low = if self.lo_open { x > self.lo } else { x >= self.lo };
        let high = if self.hi_open { x < self.hi } else { x <= self.hi };
        low && high
    }
}

impl fmt::Display for Limits {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let (open, close) = (
            if self.lo_open { '(' } else { '[' },
            if self.hi_open { ')' } else { ']' },
        );
        write!(f, "{open}{}, {}{close}", self.lo, self.hi)
    }
}

/// m: the farthest a sensor may be said to see (the LD2450 sees 6 m; a sensor's sight is built over its range)
pub const MAX_RANGE: f64 = 10.0;
/// cells of 0.1 m the raster of the plan may have at most (10^6: 100 m x 100 m): a typing error such as
/// x = 1e6 is refused, not tried (the configuration is saved: the app would run into it at every start)
pub const MAX_CELLS: f64 = 1e6;
/// sensors at most (a set of sensors is a bit set, SensorSet)
pub const MAX_SENSORS: usize = 64;

/// The threshold of "Ziel", the default and each room's own (target_thresholds).
pub const TARGET_THRESHOLD: Limits = Limits::between(0.0, 1.0);

/// The tracker's parameters and their limits. A value outside is a typo, not a setting: light_cost 0 (an
/// emptied field) put every room above its threshold, every light on.
pub const PARAMS: [(&str, Limits); 14] = [
    ("target_height", Limits::closed(0.0, 2.5)),
    ("range_sigma_base", Limits::above(0.0, 2.0)),
    ("range_sigma_slope", Limits::closed(0.0, 1.0)),
    ("lateral_sigma_base", Limits::above(0.0, 2.0)),
    ("lateral_sigma_slope", Limits::closed(0.0, 1.0)),
    ("wall_margin", Limits::closed(0.0, 2.0)),
    ("dwell_median", Limits::above(0.0, 7.0 * 86400.0)), // ln(dwell_median)
    ("dwell_spread", Limits::above(0.0, 10.0)),          // divides
    ("ld2410_hold", Limits::closed(0.0, 60.0)),
    ("light_cost", Limits::above(0.0, 1000.0)), // the threshold c / (c + 1) > 0
    ("occupied_off", Limits::closed(0.0, 1.0)),
    ("lead_time", Limits::above(0.0, 5.0)),
    ("approach_cost", Limits::above(0.0, 1000.0)),
    ("target_threshold", TARGET_THRESHOLD),
];

/// A sensor's numbers and their limits.
pub const SENSOR: [(&str, Limits); 7] = [
    ("x", Limits::closed(-1000.0, 1000.0)),
    ("y", Limits::closed(-1000.0, 1000.0)),
    ("heading", Limits::closed(-720.0, 720.0)),
    ("height", Limits::closed(0.1, 4.0)),
    ("fov", Limits::above(0.0, 180.0)),
    ("range", Limits::above(0.0, MAX_RANGE)),
    ("scale", Limits::closed(0.25, 4.0)),
];

/// A door's width.
pub const DOOR: [(&str, Limits); 1] = [("width", Limits::above(0.0, 20.0))];

const SENSOR_FLAGS: [&str; 3] = ["mirror", "enabled", "placed"];
const ZONE_KINDS: [&str; 3] = ["room", "area", "entry"];
const ZONE_SHAPES: [&str; 3] = ["rect", "circle", "polygon"];
const WALL_KINDS: [&str; 2] = ["wall", "divider"];

/// Where in the configuration a problem is.
#[derive(Debug, Clone, PartialEq)]
pub enum Part {
    /// the configuration as a whole
    Config,
    /// the tracker's parameters
    Params,
    /// a room's own threshold of "Ziel" (target_thresholds): the room's id
    Threshold(String),
    /// a sensor: its name, else its id (None: it has none)
    Sensor(Option<String>),
    /// a wall: its number, from 1
    Wall(usize),
    Door,
    /// a zone: its name, else its id (None: it has none)
    Zone(Option<String>),
    /// the LD2410Cs' measured view: the pair "own<-other" (None: the table)
    LdView(Option<String>),
}

/// What is wrong.
#[derive(Debug, Clone, PartialEq)]
pub enum Problem {
    NotAnObject,
    NotAList,
    /// a number missing: null or "" (an emptied field in the web UI)
    Empty,
    /// a value that is no finite number, as it was given
    NotANumber(Value),
    OutOfRange {
        value: f64,
        limits: Limits,
    },
    /// no finite number of at least 0, as it was given (a negative number, a text, null)
    NotAtLeast0(Value),
    NotABool,
    NoId,
    /// a sensor's id with /, + or # (its MQTT topic is presence/<id>/frame)
    IdChars,
    /// the id of one before
    IdTwice(String),
    /// points that are no finite coordinates
    NotPoints,
    /// a wall that is not two different points
    NotASegment,
    UnknownKind(Value),
    UnknownShape(Value),
    /// a rectangle without two corners
    RectCorners,
    /// a polygon with fewer than three corners
    PolygonCorners,
    /// a circle without a centre
    NoCentre,
    /// a door without a finite position
    NoPosition,
    /// a door whose centre is on no real wall (plan::DOOR_ON_WALL; the editor puts doors exactly on one)
    NotOnWall,
    TooManySensors {
        most: usize,
    },
    /// the plan spans more cells of 0.1 m than the raster may have
    TooLarge {
        most: f64,
    },
    /// not readable as a configuration (no JSON, or a field missing that the shell always writes)
    Unreadable(String),
}

/// A problem of a configuration: where, in which field (if one), what.
#[derive(Debug, Clone, PartialEq)]
pub struct ConfigError {
    pub part: Part,
    pub field: Option<&'static str>,
    pub problem: Problem,
}

impl ConfigError {
    fn at(part: Part, field: Option<&'static str>, problem: Problem) -> Self {
        Self { part, field, problem }
    }

    /// Not readable as a configuration.
    #[allow(clippy::needless_pass_by_value)] // map_err hands the error over by value
    pub fn unreadable(why: impl ToString) -> Self {
        Self::at(Part::Config, None, Problem::Unreadable(why.to_string()))
    }
}

impl fmt::Display for Part {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let some = |x: &Option<String>| x.clone().unwrap_or_else(|| "?".into());
        match self {
            Self::Config => write!(f, "the configuration"),
            Self::Params => write!(f, "params"),
            Self::Threshold(room) => write!(f, "target_thresholds {room}"),
            Self::Sensor(name) => write!(f, "sensor {}", some(name)),
            Self::Wall(k) => write!(f, "wall {k}"),
            Self::Door => write!(f, "a door"),
            Self::Zone(name) => write!(f, "zone {}", some(name)),
            Self::LdView(pair) => write!(f, "ld_view {}", pair.as_deref().unwrap_or("")),
        }
    }
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "the configuration does not fit: {}", self.part)?;
        if let Some(field) = self.field {
            write!(f, ", {field}")?;
        }
        match &self.problem {
            Problem::NotAnObject => write!(f, ": not an object"),
            Problem::NotAList => write!(f, ": not a list"),
            Problem::Empty => write!(f, ": empty"),
            Problem::NotANumber(v) => write!(f, ": no number ({v})"),
            Problem::OutOfRange { value, limits } => write!(f, ": {value} outside {limits}"),
            Problem::NotAtLeast0(v) => write!(f, ": no number >= 0 ({v})"),
            Problem::NotABool => write!(f, ": not true or false"),
            Problem::NoId => write!(f, ": no id"),
            Problem::IdChars => write!(f, ": its id has /, + or #"),
            Problem::IdTwice(id) => write!(f, ": the id {id} twice"),
            Problem::NotPoints => write!(f, ": points that are no finite coordinates"),
            Problem::NotASegment => write!(f, ": not two different points"),
            Problem::UnknownKind(v) => write!(f, ": an unknown kind {v}"),
            Problem::UnknownShape(v) => write!(f, ": an unknown shape {v}"),
            Problem::RectCorners => write!(f, ": a rectangle without two corners"),
            Problem::PolygonCorners => write!(f, ": a polygon with fewer than three corners"),
            Problem::NoCentre => write!(f, ": a circle without a centre"),
            Problem::NoPosition => write!(f, ": no finite position"),
            Problem::NotOnWall => write!(f, ": on no wall"),
            Problem::TooManySensors { most } => write!(f, ": more than {most} sensors"),
            Problem::TooLarge { most } => write!(f, ": the plan's extent, more than {most} cells of 0.1 m"),
            Problem::Unreadable(why) => write!(f, ": not readable ({why})"),
        }
    }
}

impl std::error::Error for ConfigError {}

/// A finite number (not a bool: JSON's true is none).
fn finite(v: &Value) -> Option<f64> {
    v.as_f64().filter(|x| x.is_finite())
}

/// A number within its limits; null and "" are empty.
fn number(v: &Value, limits: Limits) -> Result<(), Problem> {
    match v {
        Value::Null => Err(Problem::Empty),
        Value::String(s) if s.is_empty() => Err(Problem::Empty),
        _ => match finite(v) {
            None => Err(Problem::NotANumber(v.clone())),
            Some(x) if !limits.admits(x) => Err(Problem::OutOfRange { value: x, limits }),
            Some(_) => Ok(()),
        },
    }
}

/// A finite number of at least 0.
fn at_least_0(v: &Value) -> Result<(), Problem> {
    match finite(v) {
        Some(x) if x >= 0.0 => Ok(()),
        _ => Err(Problem::NotAtLeast0(v.clone())),
    }
}

/// A list of finite (x, y).
fn points(v: &Value) -> Option<Vec<[f64; 2]>> {
    v.as_array()?
        .iter()
        .map(|p| match p.as_array().map(Vec::as_slice) {
            Some([x, y]) => Some([finite(x)?, finite(y)?]),
            _ => None,
        })
        .collect()
}

/// The numbers of an object that are there, each within its limits.
fn numbers(o: &Map<String, Value>, limits: &[(&'static str, Limits)], part: &Part) -> Result<(), ConfigError> {
    for &(key, lim) in limits {
        if let Some(v) = o.get(key) {
            number(v, lim).map_err(|p| ConfigError::at(part.clone(), Some(key), p))?;
        }
    }
    Ok(())
}

/// The value under a key as an object; missing or null: empty.
fn object<'a>(
    o: &'a Map<String, Value>,
    key: &'static str,
    part: Part,
) -> Result<Option<&'a Map<String, Value>>, ConfigError> {
    match o.get(key) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::Object(m)) => Ok(Some(m)),
        Some(_) => Err(ConfigError::at(part, Some(key), Problem::NotAnObject)),
    }
}

/// The value under a key as a list; missing or null: empty.
fn list<'a>(o: &'a Map<String, Value>, key: &'static str) -> Result<&'a [Value], ConfigError> {
    match o.get(key) {
        None | Some(Value::Null) => Ok(&[]),
        Some(Value::Array(a)) => Ok(a),
        Some(_) => Err(ConfigError::at(Part::Config, Some(key), Problem::NotAList)),
    }
}

/// A non-empty string.
fn text(v: Option<&Value>) -> Option<&str> {
    v.and_then(Value::as_str).filter(|s| !s.is_empty())
}

/// Checks a configuration (its JSON as a value) for what the model reads: the first problem, in the order
/// params, sensors, walls, doors, zones, ld_view, the number of sensors, the plan's extent.
pub fn check(config: &Value) -> Result<(), ConfigError> {
    let Some(d) = config.as_object() else {
        return Err(ConfigError::at(Part::Config, None, Problem::NotAnObject));
    };
    if let Some(params) = object(d, "params", Part::Params)? {
        numbers(params, &PARAMS, &Part::Params)?;
        if let Some(rooms) = object(params, "target_thresholds", Part::Params)? {
            for (room, v) in rooms {
                number(v, TARGET_THRESHOLD)
                    .map_err(|p| ConfigError::at(Part::Threshold(room.clone()), Some("target_threshold"), p))?;
            }
        }
    }
    let sensors = list(d, "sensors")?;
    check_sensors(sensors)?;
    let mut real = Vec::new();
    for (k, w) in list(d, "walls")?.iter().enumerate() {
        let part = || Part::Wall(k + 1);
        let pts = w.get("points").and_then(points);
        let Some(pts) = pts else {
            return Err(ConfigError::at(part(), Some("points"), Problem::NotPoints));
        };
        if pts.len() != 2 || pts[0] == pts[1] {
            return Err(ConfigError::at(part(), Some("points"), Problem::NotASegment));
        }
        let kind = w.get("kind").cloned().unwrap_or_else(|| "wall".into());
        if !kind.as_str().is_some_and(|k| WALL_KINDS.contains(&k)) {
            return Err(ConfigError::at(part(), Some("kind"), Problem::UnknownKind(kind)));
        }
        if kind == "wall" {
            real.push([pts[0], pts[1]]);
        }
    }
    for door in list(d, "doors")? {
        let Some(o) = door
            .as_object()
            .filter(|o| o.get("x").and_then(finite).is_some() && o.get("y").and_then(finite).is_some())
        else {
            return Err(ConfigError::at(Part::Door, None, Problem::NoPosition));
        };
        numbers(o, &DOOR, &Part::Door)?;
        let centre = [o["x"].as_f64().unwrap_or_default(), o["y"].as_f64().unwrap_or_default()];
        if wall_of_door(centre, &real).is_none() {
            return Err(ConfigError::at(Part::Door, None, Problem::NotOnWall));
        }
    }
    let zones = list(d, "zones")?;
    check_zones(zones)?;
    if let Some(view) = object(d, "ld_view", Part::LdView(None))? {
        for (pair, v) in view {
            at_least_0(v).map_err(|p| ConfigError::at(Part::LdView(Some(pair.clone())), None, p))?;
        }
    }
    if sensors.len() > MAX_SENSORS {
        return Err(ConfigError::at(
            Part::Config,
            Some("sensors"),
            Problem::TooManySensors { most: MAX_SENSORS },
        ));
    }
    if cells(zones) > MAX_CELLS {
        return Err(ConfigError::at(
            Part::Config,
            Some("zones"),
            Problem::TooLarge { most: MAX_CELLS },
        ));
    }
    Ok(())
}

fn check_sensors(sensors: &[Value]) -> Result<(), ConfigError> {
    let mut ids = BTreeSet::new();
    for s in sensors {
        let (Some(o), Some(id)) = (s.as_object(), text(s.get("id"))) else {
            return Err(ConfigError::at(Part::Sensor(None), Some("id"), Problem::NoId));
        };
        let part = Part::Sensor(Some(text(o.get("name")).unwrap_or(id).to_string()));
        if id.contains(['/', '+', '#']) {
            return Err(ConfigError::at(part, Some("id"), Problem::IdChars));
        }
        if !ids.insert(id) {
            return Err(ConfigError::at(part, Some("id"), Problem::IdTwice(id.to_string())));
        }
        numbers(o, &SENSOR, &part)?;
        for key in SENSOR_FLAGS {
            if o.get(key).is_some_and(|v| !v.is_boolean()) {
                return Err(ConfigError::at(part, Some(key), Problem::NotABool));
            }
        }
    }
    Ok(())
}

fn check_zones(zones: &[Value]) -> Result<(), ConfigError> {
    let mut ids = BTreeSet::new();
    for z in zones {
        let Some(o) = z.as_object() else {
            return Err(ConfigError::at(Part::Zone(None), None, Problem::NotAnObject));
        };
        let Some(id) = text(o.get("id")) else {
            return Err(ConfigError::at(Part::Zone(None), Some("id"), Problem::NoId));
        };
        let part = || Part::Zone(Some(text(o.get("name")).unwrap_or(id).to_string()));
        let fail = |field: &'static str, problem: Problem| Err(ConfigError::at(part(), Some(field), problem));
        if !ids.insert(id) {
            return fail("id", Problem::IdTwice(id.to_string()));
        }
        let given = |key: &str, default: &'static str| o.get(key).cloned().unwrap_or_else(|| default.into());
        let (kind, shape) = (given("kind", "area"), given("shape", "rect"));
        if !kind.as_str().is_some_and(|k| ZONE_KINDS.contains(&k)) {
            return fail("kind", Problem::UnknownKind(kind));
        }
        if !shape.as_str().is_some_and(|s| ZONE_SHAPES.contains(&s)) {
            return fail("shape", Problem::UnknownShape(shape));
        }
        let corners = match o.get("points") {
            None | Some(Value::Null) => 0,
            Some(v) => match points(v) {
                Some(pts) => pts.len(),
                None => return fail("points", Problem::NotPoints),
            },
        };
        let centre = o.get("center").filter(|c| !c.is_null());
        match shape.as_str() {
            Some("rect") if corners != 2 => return fail("points", Problem::RectCorners),
            Some("polygon") if corners < 3 => return fail("points", Problem::PolygonCorners),
            Some("circle") if centre.is_none() => return fail("center", Problem::NoCentre),
            _ => {}
        }
        if let Some(c) = centre
            && points(&Value::Array(vec![c.clone()])).is_none()
        {
            return fail("center", Problem::NotPoints);
        }
        if let Some(r) = o.get("radius") {
            at_least_0(r).or_else(|p| fail("radius", p))?;
        }
    }
    Ok(())
}

/// The cells of 0.1 m of the raster around the rooms (World's raster: their bounds and a cell beyond).
fn cells(zones: &[Value]) -> f64 {
    let mut b = [f64::INFINITY, f64::INFINITY, f64::NEG_INFINITY, f64::NEG_INFINITY];
    let mut any = false;
    for z in zones
        .iter()
        .filter(|z| z.get("kind").and_then(Value::as_str) == Some("room"))
    {
        let corners: Vec<[f64; 2]> = match (
            z.get("shape").and_then(Value::as_str),
            z.get("center").and_then(|c| points(&Value::Array(vec![c.clone()]))),
        ) {
            (Some("circle"), Some(c)) => {
                let r = z.get("radius").and_then(finite).unwrap_or(0.0);
                vec![[c[0][0] - r, c[0][1] - r], [c[0][0] + r, c[0][1] + r]]
            }
            _ => z.get("points").and_then(points).unwrap_or_default(),
        };
        for [x, y] in corners {
            b = [b[0].min(x), b[1].min(y), b[2].max(x), b[3].max(y)];
            any = true;
        }
    }
    if !any {
        return 0.0;
    }
    ((b[2] - b[0]) / 0.1 + 3.0) * ((b[3] - b[1]) / 0.1 + 3.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[allow(clippy::needless_pass_by_value)] // the cases' JSON is built in the call
    fn problem(config: Value) -> (Part, Option<&'static str>, Problem) {
        let e = check(&config).expect_err("refused");
        (e.part, e.field, e.problem)
    }

    fn sensor(id: &str) -> Value {
        json!({"id": id, "name": "", "x": 0.5, "y": 0.5, "heading": 0.0})
    }

    #[test]
    fn what_is_missing_is_not_checked_and_a_whole_configuration_passes() {
        check(&json!({})).expect("nothing given, nothing wrong");
        let whole = crate::testing::config_json(&[("s", "", [0.5, 0.5], 0.0)], "[]", "[]", "[]");
        check(&serde_json::from_str(&whole).expect("JSON")).expect("fits");
    }

    #[test]
    fn a_number_is_refused_with_its_limits() {
        let light = |v: Value| problem(json!({"params": {"light_cost": v}}));
        let lim = Limits::above(0.0, 1000.0);
        assert_eq!(
            light(json!(0)),
            (
                Part::Params,
                Some("light_cost"),
                Problem::OutOfRange {
                    value: 0.0,
                    limits: lim
                }
            )
        );
        assert_eq!(light(json!(null)).2, Problem::Empty);
        assert_eq!(light(json!("")).2, Problem::Empty);
        assert_eq!(light(json!("2")).2, Problem::NotANumber(json!("2")));
        assert_eq!(light(json!("nan")).2, Problem::NotANumber(json!("nan")));
        assert_eq!(light(json!(true)).2, Problem::NotANumber(json!(true)));
        // both ends of an open interval are out, of a closed one in
        assert!(matches!(
            problem(json!({"params": {"target_threshold": 1.0}})).2,
            Problem::OutOfRange { .. }
        ));
        check(&json!({"params": {"target_height": 0.0, "occupied_off": 1}})).expect("closed ends");
        let room = problem(json!({"params": {"target_thresholds": {"wohn": 0}}}));
        assert_eq!(
            (room.0, room.1),
            (Part::Threshold("wohn".into()), Some("target_threshold"))
        );
        let range = problem(json!({"sensors": [{"id": "s", "name": "Bad", "range": 50}]}));
        assert_eq!((range.0, range.1), (Part::Sensor(Some("Bad".into())), Some("range")));
        // the background image is not the model's: never read, never checked here
        check(&json!({"background": {"layers": [{"scale": 11}, "no layer"]}})).expect("the model runs with it");
        assert_eq!(
            problem(json!({"doors": [{"x": 1, "y": 1, "width": 0}]})).1,
            Some("width")
        );
    }

    #[test]
    fn ids_walls_and_zones_are_refused_by_name() {
        let refused = |config: Value| problem(config).2;
        assert_eq!(refused(json!({"sensors": [{"name": "x"}]})), Problem::NoId);
        assert_eq!(refused(json!({"sensors": [sensor("a/b")]})), Problem::IdChars);
        assert_eq!(
            refused(json!({"sensors": [sensor("s"), sensor("s")]})),
            Problem::IdTwice("s".into())
        );
        assert_eq!(
            refused(json!({"sensors": [{"id": "s", "mirror": 1}]})),
            Problem::NotABool
        );
        let wall = |w: Value| refused(json!({ "walls": [w] }));
        assert_eq!(wall(json!({"points": [[1, 1]]})), Problem::NotASegment);
        assert_eq!(wall(json!({"points": [[1, 1], [1, 1]]})), Problem::NotASegment);
        assert_eq!(wall(json!({"points": [[1, "a"], [2, 2]]})), Problem::NotPoints);
        assert_eq!(
            wall(json!({"points": [[1, 1], [2, 2]], "kind": "glass"})),
            Problem::UnknownKind(json!("glass"))
        );
        assert_eq!(refused(json!({"doors": [{"x": 1}]})), Problem::NoPosition);
        // a door lies on a real wall (mm rounding apart); not beside it, not on a room boundary
        let wall = |kind: &str| json!({"points": [[0, 0], [4, 0]], "kind": kind});
        let door = |y: f64| json!([{"id": "d", "x": 1.0, "y": y, "width": 0.9}]);
        check(&json!({"walls": [wall("wall")], "doors": door(0.0007)})).expect("on the wall");
        assert_eq!(
            refused(json!({"walls": [wall("wall")], "doors": door(0.05)})),
            Problem::NotOnWall
        );
        assert_eq!(
            refused(json!({"walls": [wall("divider")], "doors": door(0.0)})),
            Problem::NotOnWall
        );
        assert_eq!(refused(json!({"doors": door(0.0)})), Problem::NotOnWall);
        let zone = |z: Value| problem(json!({ "zones": [z] }));
        let area = |extra: Value| {
            let mut z = json!({"id": "z", "name": "Z", "kind": "area", "shape": "rect", "points": [[1, 1], [2, 2]]});
            z.as_object_mut()
                .expect("an object")
                .extend(extra.as_object().expect("an object").clone());
            zone(z)
        };
        assert_eq!(area(json!({"kind": "foo"})).2, Problem::UnknownKind(json!("foo")));
        assert_eq!(area(json!({"shape": "blob"})).2, Problem::UnknownShape(json!("blob")));
        assert_eq!(
            area(json!({"shape": "circle", "points": [], "radius": 1.0})).2,
            Problem::NoCentre
        );
        assert_eq!(area(json!({"shape": "polygon"})).2, Problem::PolygonCorners);
        assert_eq!(area(json!({"points": [[1, 1]]})).2, Problem::RectCorners);
        assert_eq!(area(json!({"radius": -1})).2, Problem::NotAtLeast0(json!(-1)));
        assert_eq!(area(json!({"id": null})), (Part::Zone(None), Some("id"), Problem::NoId));
        assert_eq!(area(json!({"kind": "foo"})).0, Part::Zone(Some("Z".into())));
        let twice = json!({"id": "z", "name": "Z", "points": [[1, 1], [2, 2]]});
        assert_eq!(refused(json!({"zones": [twice, twice]})), Problem::IdTwice("z".into()));
        let view = problem(json!({"ld_view": {"a<-b": -0.5}}));
        assert_eq!(
            view,
            (
                Part::LdView(Some("a<-b".into())),
                None,
                Problem::NotAtLeast0(json!(-0.5))
            )
        );
    }

    #[test]
    fn every_parameter_the_core_reads_has_its_limits() {
        // every field of TrackerParams is required (no serde defaults): with exactly the rows of PARAMS (and
        // the rooms' own thresholds, checked with TARGET_THRESHOLD) the parameters read, without any one not
        let mut params: Map<String, Value> = PARAMS.iter().map(|&(k, l)| (k.to_string(), json!(l.hi))).collect();
        params.insert("target_thresholds".into(), json!({}));
        params.insert("target_threshold".into(), json!(0.5));
        serde_json::from_value::<super::super::TrackerParams>(Value::Object(params.clone())).expect("read");
        for (key, _) in PARAMS {
            let mut fewer = params.clone();
            fewer.remove(key);
            assert!(
                serde_json::from_value::<super::super::TrackerParams>(Value::Object(fewer)).is_err(),
                "{key} is no field of TrackerParams"
            );
        }
    }

    #[test]
    fn what_the_model_cannot_hold_is_refused() {
        let many: Vec<Value> = (0..=MAX_SENSORS).map(|k| sensor(&format!("s{k}"))).collect();
        assert_eq!(
            problem(json!({ "sensors": many })).2,
            Problem::TooManySensors { most: MAX_SENSORS }
        );
        let room = |far: f64| {
            json!({"zones": [{"id": "r", "name": "R", "kind": "room", "shape": "polygon",
                                               "points": [[0, 0], [far, 0], [far, far]]}]})
        };
        assert_eq!(problem(room(200.0)).2, Problem::TooLarge { most: MAX_CELLS });
        check(&room(99.0)).expect("100 m x 100 m fit");
    }
}
