//! The models the test bench (bench.rs) can score: frames in, per tick whether each room is occupied out, and
//! per scored second P(somebody there) and E[count] per room. docs/pruefstand.md 1 describes the interface; a
//! new model is a module here and a line in `build`.

use presence_core::frames::Frame;
use serde_json::{Map, Value};

pub mod filter;
pub mod regel;

/// What a model is built from.
pub struct Context<'a> {
    /// the configuration as the app keeps it (tracker.json: sensors, zones, walls, doors, params)
    pub config: &'a Value,
    /// the same as a file
    pub config_path: &'a str,
    /// what was learned, {"ld_background", "dest_map"} (as evalp's learned_*.json), or None
    pub learned: Option<&'a Value>,
    /// the rooms scored, in this order
    pub rooms: &'a [String],
    /// the scored window [m0, m1] (epoch, s)
    pub scored: (f64, f64),
    /// the local time's offset from UTC (s), for text only
    pub tz: f64,
}

/// A frame as the app receives it.
pub struct Input<'a> {
    /// when it was received (epoch, s), as recorded
    pub received: f64,
    /// the board, e.g. "presence-wohnzimmer"
    pub board: &'a str,
    /// the frame, checked as the app checks it (invalid ones never reach a model)
    pub frame: &'a Frame,
    /// the same as recorded (JSON)
    pub payload: &'a Value,
}

/// What a model tells about one room.
#[derive(Clone, Copy, Debug, Default)]
pub struct RoomValues {
    /// P(somebody there)
    pub p_any: f64,
    /// E[number of people there]
    pub e_count: f64,
    /// P(at least two there), where the model knows it
    pub p_two: Option<f64>,
}

/// What a model tells about the rooms (in the order of Context::rooms) and the house.
#[derive(Clone, Debug, Default)]
pub struct Values {
    pub rooms: Vec<RoomValues>,
    /// E[people in the house]; None: the sum over the rooms
    pub e_house: Option<f64>,
}

/// A model the bench drives as the app drives the filter. What `frame` and `tick` cost is the model's CPU (the
/// queue on Inge); the rest is not counted.
pub trait Model {
    /// A frame; returns its time on the model's clock (epoch, s): the ticks and the scoring follow it.
    fn frame(&mut self, input: &Input) -> Result<f64, String>;

    /// A tick at t (every `tick` s of frame time): go on, then per room whether it is occupied (in the order
    /// of Context::rooms). None while the model has not started.
    fn tick(&mut self, t: f64) -> Result<Option<Vec<bool>>, String>;

    /// P(somebody there), E[count] (and P(>= 2)) per room as of the last tick; asked once per scored second.
    fn values(&mut self) -> Values;

    /// Diagnostic numbers now (name, value); the bench keeps the most of each in the lead, the window and its
    /// first and last 10 min.
    fn load(&self) -> Vec<(&'static str, f64)> {
        Vec::new()
    }

    /// More numbers for the result, at the end.
    fn summary(&self) -> Map<String, Value> {
        Map::new()
    }

    /// A line of diagnosis at t (PT_DUMP).
    fn dump(&self, _t: f64) -> Option<String> {
        None
    }
}

/// A parameter set (`--set k=v`) that takes effect: where (the model's "modell", the configuration's
/// "konfig", ...), the value before and after.
#[derive(Clone, Debug)]
pub struct Applied {
    pub key: String,
    pub place: &'static str,
    pub old: Value,
    pub new: Value,
}

impl Applied {
    pub fn json(list: &[Applied]) -> Value {
        Value::Array(
            list.iter()
                .map(|a| serde_json::json!({"name": a.key, "wo": a.place, "vorher": a.old, "jetzt": a.new}))
                .collect(),
        )
    }
}

fn boxed<M: Model + 'static>((m, applied): (M, Vec<Applied>)) -> (Box<dyn Model>, Vec<Applied>) {
    (Box::new(m), applied)
}

/// The names of the models there are.
pub const MODELS: &[&str] = &["filter", "regel"];

/// The model `name`, with the parameters `set` (each one it does not know is an error, never passed over).
pub fn build(name: &str, ctx: &Context, set: &Map<String, Value>) -> Result<(Box<dyn Model>, Vec<Applied>), String> {
    Ok(match name {
        "filter" => boxed(filter::Filter::new(ctx, set)?),
        "regel" => boxed(regel::Regel::new(ctx, set)?),
        _ => return Err(format!("kein Modell {name:?} (es gibt: {})", MODELS.join(", "))),
    })
}

/// E[count] of a count distribution (P(0), P(1), ...).
pub fn expected(row: &[f64]) -> f64 {
    row.iter().enumerate().map(|(k, q)| k as f64 * q).sum::<f64>()
}
