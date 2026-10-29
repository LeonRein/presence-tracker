//! presence-replay: a recording through the core without Python (docs/rust-core.md 6), driven as the app
//! drives it (rust-port/trace.py, perf-core/prof.py): each frame checked as the app checks it (an invalid one
//! is dropped), its time from the board's uptime (SensorClock), then every 0.1 s of frame time a step and
//! the zones' states.
//!
//!   presence-replay trace CONFIG RECORDING SECONDS OUT [EVERY]
//!   presence-replay prof CONFIG RECORDING SECONDS
//!
//! CONFIG is the JSON of model.Config.to_dict() (the app's tracker.json); the model the core's defaults.
//! trace writes every EVERY s (1) of frame time the line trace.py writes through the Python shell, byte for
//! byte: the hypotheses' weights, the count and place distributions, the zones' states. prof tells the time
//! of the frames and of the steps with the outputs, as prof.py does with the shell (without Python: the
//! difference is what the shell costs). Its clock is the wall's (std has no CPU clock): run it on a core
//! of its own (taskset).

mod pyjson;

use std::collections::HashMap;
use std::process::ExitCode;
use std::time::{Duration, Instant};
use std::{env, fs, io::Write};

use presence_core::config::Config;
use presence_core::frames::{Frame, SensorClock};
use presence_core::outputs::ZoneState;
use presence_core::params::Params;
use presence_core::tracker::Tracker;
use presence_core::world::World;
use serde_json::Value;

use pyjson::{Dict, Fields, PyJson};

const USAGE: &str = "usage: presence-replay trace CONFIG RECORDING SECONDS OUT [EVERY]\n       presence-replay prof CONFIG RECORDING SECONDS";

/// s of frame time between the steps (the app's tick)
const TICK: f64 = 0.1;

fn main() -> ExitCode {
    let args: Vec<String> = env::args().skip(1).collect();
    let args: Vec<&str> = args.iter().map(String::as_str).collect();
    let done = match args[..] {
        ["trace", config, recording, seconds, out] => trace(config, recording, seconds, out, "1"),
        ["trace", config, recording, seconds, out, every] => trace(config, recording, seconds, out, every),
        ["prof", config, recording, seconds] => prof(config, recording, seconds),
        _ => Err(USAGE.to_string()),
    };
    match done {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("presence-replay: {e}");
            ExitCode::FAILURE
        }
    }
}

fn seconds(s: &str) -> Result<f64, String> {
    s.parse::<f64>()
        .ok()
        .filter(|x| *x > 0.0)
        .ok_or_else(|| format!("not a number of seconds > 0: {s}"))
}

/// A frame of a recording: when it was received, the board, its payload.
struct Message {
    t: f64,
    board: String,
    payload: Value,
}

/// The frames of a recording (JSON lines {"t", "topic": "presence/<board>/frame", "payload"}; other topics
/// are skipped).
fn messages(path: &str) -> Result<Vec<Message>, String> {
    let text = fs::read_to_string(path).map_err(|e| format!("{path}: {e}"))?;
    let mut out = Vec::new();
    for (k, line) in text.lines().enumerate() {
        let mut m: Value = serde_json::from_str(line).map_err(|e| format!("{path}:{}: {e}", k + 1))?;
        let Some(topic) = m["topic"].as_str().filter(|t| t.ends_with("/frame")) else {
            continue;
        };
        let (Some(t), Some(board)) = (m["t"].as_f64(), topic.split('/').nth(1)) else {
            return Err(format!("{path}:{}: a frame without t or board", k + 1));
        };
        let board = board.to_string();
        out.push(Message {
            t,
            board,
            payload: m["payload"].take(),
        });
    }
    Ok(out)
}

/// The tracker of a configuration with the core's model.
fn tracker(config: &str) -> Result<Tracker, String> {
    let text = fs::read_to_string(config).map_err(|e| format!("{config}: {e}"))?;
    let config = Config::from_json(&text).map_err(|e| format!("{config}: {e}"))?;
    let mut tracker = Tracker::new(World::build(&config, Params::default())?);
    tracker.reset_people(None)?; // nothing known about the people (the shell's Tracker() starts so)
    Ok(tracker)
}

/// How a replay went: frames given to the tracker, those dropped as invalid, the frame time it spanned, the
/// time of the frames (checking and the tracker's frame) and of the steps with the zones' states.
#[derive(Default)]
struct Replayed {
    frames: usize,
    dropped: usize,
    span: f64,
    in_frames: Duration,
    in_steps: Duration,
}

/// Drives the tracker as the app does over the first `limit` s of frame time; after each step and the
/// zones' states `stepped` gets the tracker, the frame time, that of the first frame and the states.
fn replay(
    tracker: &mut Tracker,
    messages: &[Message],
    limit: f64,
    mut stepped: impl FnMut(&mut Tracker, f64, f64, &[ZoneState]) -> Result<(), String>,
) -> Result<Replayed, String> {
    let mut clocks: HashMap<&str, SensorClock> = HashMap::new();
    let mut done = Replayed::default();
    let (mut t0, mut next) = (None, 0.0);
    for m in messages {
        let start = Instant::now();
        let Ok(frame) = Frame::from_json(&m.payload) else {
            done.dropped += 1; // as the app drops it (logged there)
            continue;
        };
        done.in_frames += start.elapsed();
        let t = clocks.entry(&m.board).or_default().at(m.t, frame.uptime_ms);
        let t0 = *t0.get_or_insert(t);
        done.span = t - t0;
        if t - t0 > limit {
            break;
        }
        let start = Instant::now();
        tracker
            .frame(&m.board, t, &frame)
            .map_err(|e| format!("the frame at {t}: {e:?}"))?;
        done.in_frames += start.elapsed();
        done.frames += 1;
        if t >= next && tracker.start().is_some() {
            let start = Instant::now();
            tracker.step(t).map_err(|e| format!("the step at {t}: {e}"))?;
            let states = tracker.zone_states(tracker.now());
            done.in_steps += start.elapsed();
            next = t + TICK;
            stepped(tracker, t, t0, &states)?;
        }
    }
    Ok(done)
}

fn trace(config: &str, recording: &str, limit: &str, out: &str, every: &str) -> Result<(), String> {
    let (limit, every) = (seconds(limit)?, seconds(every)?);
    let mut tracker = tracker(config)?;
    let layout = Layout::of(tracker.world());
    let messages = messages(recording)?;
    let mut file = fs::File::create(out).map_err(|e| format!("{out}: {e}"))?;
    let mut due = 0.0;
    let mut line = String::new();
    let done = replay(&mut tracker, &messages, limit, |tracker, t, t0, states| {
        if t < due {
            return Ok(());
        }
        due = t + every;
        line.clear();
        layout.trace_line(tracker, t - t0, states, &mut line);
        line.push('\n');
        file.write_all(line.as_bytes()).map_err(|e| format!("{out}: {e}"))
    })?;
    if done.dropped > 0 {
        eprintln!("{} invalid frames dropped", done.dropped);
    }
    Ok(())
}

fn prof(config: &str, recording: &str, limit: &str) -> Result<(), String> {
    let limit = seconds(limit)?;
    let mut tracker = tracker(config)?;
    let messages = messages(recording)?;
    let done = replay(&mut tracker, &messages, limit, |_, _, _, _| Ok(()))?;
    let (frames, steps) = (done.in_frames.as_secs_f64(), done.in_steps.as_secs_f64());
    println!(
        "frames {} span {:.0}s  cpu frames {frames:.1}s step {steps:.1}s total {:.1}s = {:.2}% of a core",
        done.frames,
        done.span,
        frames + steps,
        100.0 * (frames + steps) / done.span
    );
    Ok(())
}

/// The names of the trace's distributions: the observed rooms' ids and the places' names (count_distribution,
/// place_distribution of the shell).
struct Layout {
    rooms: Vec<String>,
    places: Vec<String>,
}

impl Layout {
    fn of(world: &World) -> Self {
        let plan = &world.plan;
        Self {
            rooms: plan.observed().map(|z| z.id.clone()).collect(),
            places: plan.place_names().into_iter().map(String::from).collect(),
        }
    }

    /// The line of trace.py: {"t", "hyps", "w", "counts", "places", "zones"}.
    fn trace_line(&self, tracker: &mut Tracker, t: f64, states: &[ZoneState], out: &mut String) {
        let mut w = tracker.hyp_weights();
        w.sort_by(|a, b| b.total_cmp(a));
        let hyps = w.len();
        let zones = Dict(states.iter().map(|st| (st.id.as_str(), ZoneOut(st))).collect());
        Fields::new(out)
            .field("t", &t)
            .field("hyps", &hyps)
            .field("w", &w)
            .field("counts", &named(&self.rooms, "_observed", tracker.count_distribution()))
            .field("places", &named(&self.places, "_house", tracker.place_distribution()))
            .field("zones", &zones)
            .end();
    }
}

/// The rows of a distribution by name, the last one (of all together) as `total` (count_distribution(),
/// place_distribution() of the shell).
fn named<'a>(names: &'a [String], total: &'a str, rows: &'a [Vec<f64>]) -> Dict<'a, &'a [f64]> {
    let last = rows.last().map_or(&[][..], Vec::as_slice);
    Dict(
        names
            .iter()
            .map(String::as_str)
            .zip(rows.iter().map(Vec::as_slice))
            .chain([(total, last)])
            .collect(),
    )
}

impl PyJson for &[f64] {
    fn write(&self, out: &mut String) {
        (**self).write(out);
    }
}

/// A zone's state as trace.py writes it (dataclasses.asdict of the shell's zones.ZoneState: every field in
/// order).
struct ZoneOut<'a>(&'a ZoneState);

impl PyJson for ZoneOut<'_> {
    fn write(&self, out: &mut String) {
        let s = self.0;
        let from = s.target_from.as_deref();
        let source = s.decided.source().map(|s| s.name());
        Fields::new(out)
            .field("count", &s.count)
            .field("moving", &s.moving)
            .field("still", &s.still)
            .field("approaching", &s.approaching)
            .field("p_enter", &s.p_enter)
            .field("eta", &s.eta)
            .field("distance", &s.distance)
            .field("person", &s.person)
            .field("c_enter", &s.c_enter)
            .field("target", &s.target)
            .field("p_target", &s.p_target)
            .field("c_target", &s.c_target)
            .field("target_weight", &s.target_weight)
            .field("target_walks", &s.target_walks)
            .field("target_from", &from)
            .field("target_distance", &s.target_distance)
            .field("target_eta", &s.target_eta)
            .field("target_person", &s.target_person)
            .field("probability", &s.probability)
            .field("decided", &s.decided.occupied())
            .field("source", &source)
            .field("available", &s.available)
            .field("occupied", &s.occupied)
            .field("c_occupied", &s.c_occupied)
            .field("target_source", &s.target_source)
            .end();
    }
}
