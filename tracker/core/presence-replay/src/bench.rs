//! bench: one window of the test bench (Prüfstand; tools/pruefstand.py writes the job and reads the result).
//!
//!   presence-replay bench JOB.json
//!
//! The job (JSON): the configuration (a file as the app keeps it, its output thresholds already as live), what
//! was learned (a file {"ld_background", "dest_map"} as evalp's learned_*.json, or null), the recordings
//! (hourly .jsonl or .jsonl.gz, in order; a cut-off gzip ends its file), the start of the replay, the window
//! [m0, m1] that is scored, what must be lit / must be dark / must not hold two, the intervals not scored
//! (skip), the app's tick, the factor from this machine's CPU to Inge's, the backlog at which a run fails and
//! a CPU limit. With "regel" set, no model runs: a room is occupied while an LD2450 target is in it (the
//! simple rule, erbe-17-11/werkzeuge/regel_vs_modell.py).
//!
//! The model is driven as the app drives it (as `trace`): each frame checked, its time from the board's
//! uptime, a step and the zones' states every `tick` s of frame time; the light as Node-RED's
//! Präsenzlicht: on while the room is occupied, off 120 s after. Scored once per second of frame time in
//! [m0, m1] as evalp.py scores. What the frames and steps cost is the instructions the thread retires
//! (perf) over `ips_leer`, the CPU seconds of an idle core (not CPU time, which grows when the machine is
//! busy, nor the wall's); without the counter the thread's CPU time. Times `factor` that is the time Inge
//! needs; the queue in front of the model is simulated with it from m0 on: its backlog grows by that and
//! shrinks with the frame time going on. A run whose backlog exceeds `max_backlog` s, or whose process CPU
//! exceeds `cpu_limit` s, stops: failed.
//!
//! Instructions are blind to how many cycles an instruction takes (cache misses, a changed data layout). So
//! the same queue also runs on the thread's CPU time (`rueckstand_cpu_max_s`, an upper bound: a busy machine
//! makes it larger), and on the thread's CPU time over `last_max` (`rueckstand_lastbereinigt_max_s`, a lower
//! bound: the most a busy machine stretches the same work, measured 18.11.); that one exceeding `max_backlog`
//! fails the run as well. `ips_ist` = instructions / thread CPU tells the caller whether the instructions
//! can be trusted (tools/pruefstand.py compares it with 0.64 in the same window). The queue on instructions
//! also runs with the ends of the factor's valid range (`faktor_spanne`): the factor is fitted, not known.
//!
//! Every `progress` s of frame time a line "# ..." goes out at once; at the end one line of JSON.

use std::collections::{BTreeMap, HashMap};
use std::fs::File;
use std::io::{BufRead, BufReader, Read, Write};

use presence_core::frames::{Frame, SensorClock};
use presence_core::tracker::Tracker;
use serde_json::{Map, Value, json};

/// s of light after the room is no longer occupied (Node-RED Präsenzlicht)
const HOLD: f64 = 120.0;

fn cpu_clock(id: libc::clockid_t) -> f64 {
    let mut ts = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    // SAFETY: clock_gettime writes the timespec it is given, nothing else
    unsafe { libc::clock_gettime(id, &raw mut ts) };
    ts.tv_sec as f64 + ts.tv_nsec as f64 * 1e-9
}

/// CPU time of this thread (the model's work).
fn thread_cpu() -> f64 {
    cpu_clock(libc::CLOCK_THREAD_CPUTIME_ID)
}

/// CPU time of the process (with reading and scoring).
fn process_cpu() -> f64 {
    cpu_clock(libc::CLOCK_PROCESS_CPUTIME_ID)
}

fn num(v: &Value, k: &str) -> Result<f64, String> {
    v[k].as_f64().ok_or_else(|| format!("job: {k} missing"))
}

fn intervals(v: &Value) -> Vec<(String, f64, f64)> {
    v.as_array()
        .map(|a| {
            a.iter()
                .filter_map(|x| Some((x[0].as_str()?.to_string(), x[1].as_f64()?, x[2].as_f64()?)))
                .collect()
        })
        .unwrap_or_default()
}

/// The sensors' and rooms' geometry as evalp.py reads it from the configuration (world, inside): for the
/// simple rule and the sitters.
struct Raw {
    sensors: HashMap<String, RawSensor>,
    target_height: f64,
    /// the observed rooms: id, polygon
    rooms: Vec<(String, Vec<[f64; 2]>)>,
}

struct RawSensor {
    x: f64,
    y: f64,
    heading: f64,
    height: f64,
    scale: f64,
    mirror: bool,
}

impl Raw {
    fn of(config: &Value, observed: &[String]) -> Self {
        let mut sensors = HashMap::new();
        for s in config["sensors"].as_array().into_iter().flatten() {
            let on = |k: &str| s[k].as_bool().unwrap_or(false);
            if !on("placed") || !on("enabled") {
                continue;
            }
            let f = |k: &str, d: f64| s[k].as_f64().unwrap_or(d);
            if let Some(id) = s["id"].as_str() {
                sensors.insert(
                    id.to_string(),
                    RawSensor {
                        x: f("x", 0.0),
                        y: f("y", 0.0),
                        heading: f("heading", 90.0),
                        height: f("height", 1.5),
                        scale: f("scale", 1.0),
                        mirror: on("mirror"),
                    },
                );
            }
        }
        // in the order of `observed` (the indices are the caller's)
        let zones = config["zones"].as_array().cloned().unwrap_or_default();
        let rooms = observed
            .iter()
            .map(|id| {
                let pts = zones
                    .iter()
                    .find(|z| z["id"].as_str() == Some(id.as_str()))
                    .and_then(|z| z["points"].as_array())
                    .map(|a| a.iter().filter_map(|p| Some([p[0].as_f64()?, p[1].as_f64()?])).collect())
                    .unwrap_or_default();
                (id.clone(), pts)
            })
            .collect();
        Self {
            sensors,
            target_height: config["params"]["target_height"].as_f64().unwrap_or(1.0),
            rooms,
        }
    }

    /// evalp.world: a detection (mm, the sensor's frame) on the floor plan
    fn world(&self, s: &RawSensor, x: f64, y: f64) -> [f64; 2] {
        let (mut lx, ly) = (x / 1000.0, y / 1000.0);
        if s.mirror {
            lx = -lx;
        }
        let sl = lx.hypot(ly);
        let g = (sl * sl - (s.height - self.target_height).powi(2)).max(0.01).sqrt();
        let k = if sl > 0.0 { s.scale * g / sl } else { s.scale };
        let (gx, gy) = (lx * k, ly * k);
        let (sn, c) = s.heading.to_radians().sin_cos();
        [s.x + gy * c + gx * sn, s.y + gy * sn - gx * c]
    }

    fn room_at(&self, p: [f64; 2]) -> Option<usize> {
        self.rooms.iter().position(|(_, pts)| inside(pts, p))
    }

    /// The detections of a frame (at least 0.3 m from the sensor) on the floor plan, with their raw
    /// coordinates and speed.
    fn detections(&self, board: &str, payload: &Value) -> Vec<([f64; 2], (i64, i64), f64)> {
        let Some(s) = self.sensors.get(board) else {
            return Vec::new();
        };
        payload["targets"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(|d| {
                let (x, y) = (d["x"].as_f64()?, d["y"].as_f64()?);
                if x.hypot(y) < 300.0 {
                    return None;
                }
                Some((self.world(s, x, y), (x as i64, y as i64), d["speed"].as_f64().unwrap_or(0.0)))
            })
            .collect()
    }
}

/// evalp.inside: ray casting
fn inside(pts: &[[f64; 2]], p: [f64; 2]) -> bool {
    let mut r = false;
    let n = pts.len();
    for i in 0..n {
        let ([x1, y1], [x2, y2]) = (pts[i], pts[(i + 1) % n]);
        if (y1 > p[1]) != (y2 > p[1]) && p[0] < x1 + (p[1] - y1) * (x2 - x1) / (y2 - y1) {
            r = !r;
        }
    }
    r
}

/// evalp.Sitters: rooms with a resting, not frozen LD2450 track resting for at least 60 s (a sitting
/// person, or a ghost there: only for comparisons).
#[derive(Default)]
struct Sitters {
    tracks: HashMap<String, Vec<SitTrack>>,
}

struct SitTrack {
    t0: f64,
    /// (t, x, y, raw)
    pts: Vec<(f64, f64, f64, (i64, i64))>,
}

impl Sitters {
    fn frame(&mut self, board: &str, t: f64, dets: &[([f64; 2], (i64, i64), f64)]) {
        let old = self.tracks.remove(board).unwrap_or_default();
        let mut used = vec![false; dets.len()];
        let mut new = Vec::new();
        for mut tr in old {
            let &(tl, px, py, _) = tr.pts.last().expect("a track has points");
            let best = dets
                .iter()
                .enumerate()
                .filter(|(i, _)| !used[*i])
                .map(|(i, (p, _, _))| ((p[0] - px).hypot(p[1] - py), i))
                .min_by(|a, b| a.0.total_cmp(&b.0));
            if let Some((d, i)) = best
                && d <= 0.6
                && t - tl < 1.5
            {
                used[i] = true;
                let (p, r, _) = dets[i];
                tr.pts.push((t, p[0], p[1], r));
                tr.pts.retain(|q| q.0 >= t - 90.0);
                new.push(tr);
            }
        }
        for (i, (p, r, _)) in dets.iter().enumerate() {
            if !used[i] {
                new.push(SitTrack {
                    t0: t,
                    pts: vec![(t, p[0], p[1], *r)],
                });
            }
        }
        self.tracks.insert(board.to_string(), new);
    }

    fn rooms_now(&self, t: f64, raw: &Raw) -> Vec<usize> {
        let mut out = Vec::new();
        for tr in self.tracks.values().flatten() {
            let last_t = tr.pts.last().map_or(f64::NEG_INFINITY, |q| q.0);
            if t - tr.t0 < 60.0 || t - last_t > 1.0 {
                continue;
            }
            let last: Vec<_> = tr.pts.iter().filter(|q| q.0 >= t - 30.0).collect();
            let mut raws: Vec<_> = last.iter().map(|q| q.3).collect();
            raws.sort_unstable();
            raws.dedup();
            if raws.len() < 3 {
                continue; // frozen: the firmware holds a lost track
            }
            let n = last.len() as f64;
            let (mx, my) = (
                last.iter().map(|q| q.1).sum::<f64>() / n,
                last.iter().map(|q| q.2).sum::<f64>() / n,
            );
            if last.iter().any(|q| (q.1 - mx).hypot(q.2 - my) > 0.3) {
                continue;
            }
            if let Some(r) = raw.room_at([mx, my])
                && !out.contains(&r)
            {
                out.push(r);
            }
        }
        out
    }
}

/// Lines of the recordings: (received, board, payload) of the frames in [from, to], in order. A cut-off
/// gzip (a recording still being written) ends its file; that is told.
struct Lines {
    files: Vec<String>,
    k: usize,
    cur: Option<Box<dyn BufRead>>,
    from: f64,
    to: f64,
    buf: String,
    cut: Vec<String>,
    done: bool,
}

impl Lines {
    fn open(path: &str) -> Result<Box<dyn BufRead>, String> {
        let f = File::open(path).map_err(|e| format!("{path}: {e}"))?;
        let r: Box<dyn Read> = if path.ends_with(".gz") {
            Box::new(flate2::read::MultiGzDecoder::new(f))
        } else {
            Box::new(f)
        };
        Ok(Box::new(BufReader::with_capacity(1 << 20, r)))
    }

    /// t of a line written as {"t":<t>, ...} without parsing all of it.
    fn quick_t(line: &str) -> Option<f64> {
        let rest = line.strip_prefix("{\"t\":")?;
        let end = rest.find(',')?;
        rest[..end].trim().parse().ok()
    }

    fn next(&mut self) -> Result<Option<(f64, String, Value)>, String> {
        loop {
            if self.done {
                return Ok(None);
            }
            if self.cur.is_none() {
                if self.k >= self.files.len() {
                    return Ok(None);
                }
                self.cur = Some(Self::open(&self.files[self.k])?);
                self.k += 1;
            }
            self.buf.clear();
            let read = self.cur.as_mut().expect("open").read_line(&mut self.buf);
            match read {
                Ok(0) => {
                    self.cur = None;
                    continue;
                }
                Ok(_) => {}
                Err(_) => {
                    self.cut.push(self.files[self.k - 1].clone());
                    self.cur = None;
                    continue;
                }
            }
            let line = self.buf.trim_end();
            if !line.contains("/frame\"") {
                continue;
            }
            if let Some(t) = Self::quick_t(line) {
                if t < self.from {
                    continue;
                }
                if t > self.to {
                    self.done = true;
                    return Ok(None);
                }
            }
            let Ok(mut m) = serde_json::from_str::<Value>(line) else {
                // the last line of a cut-off file
                continue;
            };
            let Some(topic) = m["topic"].as_str().filter(|t| t.ends_with("/frame")) else {
                continue;
            };
            let (Some(t), Some(board)) = (m["t"].as_f64(), topic.split('/').nth(1)) else {
                continue;
            };
            if t < self.from {
                continue;
            }
            if t > self.to {
                self.done = true;
                return Ok(None);
            }
            let board = board.to_string();
            return Ok(Some((t, board, m["payload"].take())));
        }
    }
}

/// The rule's variant (regel_vs_modell.py VARIANTS): only moving targets, only the room's own sensor.
struct Rule {
    moving_only: bool,
    own_only: bool,
}

#[derive(Default)]
struct Acc {
    s: f64,
    licht: f64,
    licht_zuviel: f64,
    lit_room: BTreeMap<String, f64>,
    muss_s: f64,
    muss_dunkel: f64,
    muss_dunkel_wann: Vec<f64>,
    muss_luecke_max: f64,
    verbot_s: f64,
    verbot_licht: f64,
    zwei_s: f64,
    zwei_p: f64,
    schlaf_dunkel: f64,
    schlaf_unbelegt: f64,
    schlaf_e: f64,
    zuviel: f64,
    mehrraum: f64,
    sitzer: f64,
    dunkel_sitzer: f64,
    max_e_room: BTreeMap<String, f64>,
}

#[derive(Default, Clone, Copy)]
struct Load {
    hyps: usize,
    hidden: usize,
    hidden_r: f64,
    densities: usize,
}

impl Load {
    fn widen(&mut self, l: &Load) {
        self.hyps = self.hyps.max(l.hyps);
        self.hidden = self.hidden.max(l.hidden);
        self.hidden_r = self.hidden_r.max(l.hidden_r);
        self.densities = self.densities.max(l.densities);
    }
}

/// The queue in front of the model on Inge: it grows by what a frame costs there (`factor` times the cost here)
/// and shrinks with the frame time going on.
#[derive(Default)]
struct Queue {
    factor: f64,
    backlog: f64,
    max: f64,
    at: f64,
}

impl Queue {
    fn new(factor: f64) -> Self {
        Self { factor, ..Self::default() }
    }

    fn go_on(&mut self, dt: f64) {
        self.backlog = (self.backlog - dt.max(0.0)).max(0.0);
    }

    fn add(&mut self, cost: f64, t: f64) {
        self.backlog += cost * self.factor;
        if self.backlog > self.max {
            (self.max, self.at) = (self.backlog, t);
        }
    }
}

fn load_of(tracker: &Tracker) -> Load {
    let f = tracker.filter();
    let dens = f.objects().densities();
    let mut l = Load {
        hyps: f.hyps().len(),
        hidden: 0,
        hidden_r: 0.0,
        densities: dens.len(),
    };
    for h in f.hyps() {
        l.hidden = l.hidden.max(h.hidden.len());
        let r: f64 = h.hidden.iter().map(|&u| dens.get(u).r).sum();
        l.hidden_r = l.hidden_r.max(r);
    }
    l
}

/// The instructions this thread retired in user space (perf_event_open, PERF_COUNT_HW_INSTRUCTIONS): a measure of
/// the model's work that does not depend on how busy the machine is (its CPU time does: with both SMT threads of a
/// core busy the same work takes up to 1,7 times the CPU time, 18.11.). None where the kernel does not allow it.
struct Instructions {
    fd: libc::c_int,
}

/// perf_event_attr up to config1 (PERF_ATTR_SIZE_VER0, 64 bytes)
#[repr(C)]
#[derive(Default)]
struct PerfAttr {
    kind: u32,
    size: u32,
    config: u64,
    sample_period: u64,
    sample_type: u64,
    read_format: u64,
    flags: u64,
    wakeup_events: u32,
    bp_type: u32,
    config1: u64,
}

impl Instructions {
    fn open() -> Option<Self> {
        let attr = PerfAttr {
            kind: 0,                     // PERF_TYPE_HARDWARE
            size: 64,                    // PERF_ATTR_SIZE_VER0
            config: 1,                   // PERF_COUNT_HW_INSTRUCTIONS
            flags: (1 << 5) | (1 << 6), // exclude_kernel, exclude_hv
            ..PerfAttr::default()
        };
        // SAFETY: the attribute lives for the call; pid 0, cpu -1: this thread on any CPU
        let fd = unsafe {
            libc::syscall(libc::SYS_perf_event_open, &raw const attr, 0, -1, -1, 0)
        };
        (fd >= 0).then(|| Self { fd: fd as libc::c_int })
    }

    fn read(&self) -> u64 {
        let mut v = 0_u64;
        // SAFETY: reads 8 bytes into v
        let n = unsafe { libc::read(self.fd, (&raw mut v).cast(), 8) };
        if n == 8 { v } else { 0 }
    }
}

impl Drop for Instructions {
    fn drop(&mut self) {
        // SAFETY: our own descriptor
        unsafe { libc::close(self.fd) };
    }
}

fn hms(t: f64, tz: f64) -> String {
    let s = (t + tz).rem_euclid(86400.0) as i64;
    format!("{:02}:{:02}:{:02}", s / 3600, (s / 60) % 60, s % 60)
}

#[allow(clippy::too_many_lines)]
pub fn bench(job_path: &str) -> Result<(), String> {
    let cpu_begin = process_cpu();
    let text = std::fs::read_to_string(job_path).map_err(|e| format!("{job_path}: {e}"))?;
    let job: Value = serde_json::from_str(&text).map_err(|e| format!("{job_path}: {e}"))?;
    let name = job["name"].as_str().unwrap_or("?").to_string();
    let kind = job["kind"].as_str().unwrap_or("").to_string();
    let config_path = job["config"].as_str().ok_or("job: config missing")?;
    let (start, m0, m1) = (num(&job, "start")?, num(&job, "m0")?, num(&job, "m1")?);
    let tick = job["tick"].as_f64().unwrap_or(0.1);
    let factor = job["factor"].as_f64().unwrap_or(1.0);
    // the factor is fitted on one day (17.11.); the range in which it separates the days measured
    let (f_lo, f_hi) = match job["faktor_spanne"].as_array().map(Vec::as_slice) {
        Some([a, b]) => (a.as_f64().unwrap_or(factor), b.as_f64().unwrap_or(factor)),
        _ => (factor, factor),
    };
    // the most a busy machine stretches the CPU time of the same work (18.11.: instructions per CPU second
    // 1,66-1,83e10 on the idle machine, down to 0,8e10 with all 14 slots busy)
    let load_max_stretch = job["last_max"].as_f64().unwrap_or(2.3);
    let max_backlog = job["max_backlog"].as_f64().unwrap_or(60.0);
    let cpu_limit = job["cpu_limit"].as_f64().unwrap_or(f64::INFINITY);
    let progress = job["progress"].as_f64().unwrap_or(60.0);
    // the lead before the scored window is not queued: the cold start (nothing known, everybody anywhere)
    // costs what the running app never pays (it goes on, or restarts from people.json); its CPU is told apart
    // (cpu_anlauf_s). The queue starts at `rueckstand_ab` (default m0).
    let queue_from = job["rueckstand_ab"].as_f64().unwrap_or(m0);
    // ... but a lead that cannot keep up at all is no cold start any more: its own queue (same cost and factor)
    // over `max_backlog_lead` s fails the run too (0.64 can never reach 600 s there: its whole lead costs at most
    // 153 s, times 3 = 459 s, 18.11.)
    let max_backlog_lead = job["max_backlog_lead"].as_f64().unwrap_or(600.0);
    // the model's work as instructions (perf), turned into CPU seconds of the idle machine with `ips_leer` (its
    // instructions per CPU second there, the same workload): the queue and the CPU numbers then do not depend on how
    // busy the machine is. Without the counter (or ips_leer): the thread's CPU time as measured.
    let instructions = job["ips_leer"].as_f64().and_then(|ips| Instructions::open().map(|c| (c, ips)));
    let mut instr_total = 0_u64;
    // optional: the model's CPU per second of frame time, "t cpu" per line (to calibrate the factor)
    let mut cpu_trace = match job["cpu_spur"].as_str() {
        Some(p) => Some((std::io::BufWriter::new(File::create(p).map_err(|e| format!("{p}: {e}"))?), 0.0_f64, 0.0_f64)),
        None => None,
    };
    let tz = job["tz"].as_f64().unwrap_or(3600.0);
    let must = intervals(&job["must"]);
    let verbot = intervals(&job["verbot"]);
    let zwei = intervals(&job["hoechstens_eins"]);
    let skip: Vec<(f64, f64)> = job["skip"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|x| Some((x[0].as_f64()?, x[1].as_f64()?)))
        .collect();
    let truth = if kind == "zwei" { 2 } else { 1 };
    let truth_people = if kind == "zwei" || kind == "nacht" { 2.0 } else { 1.0 };
    let rule = job["regel"].as_object().map(|r| Rule {
        moving_only: r.get("bewegt").and_then(Value::as_bool).unwrap_or(false),
        own_only: r.get("eigener").and_then(Value::as_bool).unwrap_or(false),
    });

    let config_text = std::fs::read_to_string(config_path).map_err(|e| format!("{config_path}: {e}"))?;
    let config_value: Value = serde_json::from_str(&config_text).map_err(|e| format!("{config_path}: {e}"))?;
    let mut tracker = crate::tracker(config_path)?;
    let mut learned_info = Map::new();
    if let Some(path) = job["learned"].as_str() {
        let t = std::fs::read_to_string(path).map_err(|e| format!("{path}: {e}"))?;
        let v: Value = serde_json::from_str(&t).map_err(|e| format!("{path}: {e}"))?;
        if !v["ld_background"].is_null() {
            let dropped = tracker
                .use_background(Some(&v["ld_background"]))
                .map_err(|e| format!("{path}: ld_background: {e:?}"))?;
            learned_info.insert("ld_teile_verworfen".into(), json!(dropped.len()));
            learned_info.insert("ld".into(), json!(true));
        }
        if !v["dest_map"].is_null() {
            let used = tracker
                .use_dest_map(Some(&v["dest_map"]))
                .map_err(|e| format!("{path}: dest_map: {e:?}"))?;
            learned_info.insert("zielkarte".into(), json!(used));
        }
    }
    let observed: Vec<String> = tracker.world().plan.observed().map(|z| z.id.clone()).collect();
    let rule_rooms: Vec<String> = job["regel_raeume"]
        .as_array()
        .map(|a| a.iter().filter_map(|x| x.as_str().map(String::from)).collect())
        .unwrap_or_else(|| observed.clone());
    let rooms = if rule.is_some() { rule_rooms } else { observed.clone() };
    let raw = Raw::of(&config_value, &rooms);
    let outputs = &tracker.world().outputs;
    let thresholds = json!({"an": outputs.occupied_above(), "aus": outputs.occupied_below(),
                            "light_cost": outputs.light_cost, "occupied_off": outputs.occupied_off});
    let room_k = |id: &str| rooms.iter().position(|r| r == id);
    let schlaf = room_k("schlafzimmer");

    let mut lines = Lines {
        files: job["files"]
            .as_array()
            .ok_or("job: files missing")?
            .iter()
            .filter_map(|f| f.as_str().map(String::from))
            .collect(),
        k: 0,
        cur: None,
        from: start,
        to: m1,
        buf: String::new(),
        cut: Vec::new(),
        done: false,
    };

    let mut clocks: HashMap<String, SensorClock> = HashMap::new();
    let mut sitters = Sitters::default();
    let mut acc = Acc::default();
    let mut occ = vec![false; rooms.len()];
    let mut lit_until = vec![f64::NEG_INFINITY; rooms.len()];
    let mut rule_seen = vec![f64::NEG_INFINITY; rooms.len()];
    let mut dark_run: Vec<f64> = vec![0.0; must.len()];
    let (mut next_step, mut last_sample, mut t_prev) = (f64::NEG_INFINITY, f64::NEG_INFINITY, None::<f64>);
    let (mut frames, mut dropped) = (0usize, 0usize);
    let mut queue = Queue::new(factor);
    let (mut queue_lo, mut queue_hi) = (Queue::new(f_lo), Queue::new(f_hi));
    let mut queue_cpu = Queue::new(factor);
    let mut queue_cpu_lb = Queue::new(factor / load_max_stretch);
    let mut queue_lead = Queue::new(factor);
    let (mut model_cpu, mut warmup_cpu, mut raw_cpu) = (0.0, 0.0, 0.0);
    let mut per_min: BTreeMap<i64, f64> = BTreeMap::new();
    // per minute of frame time from the queue's start: the instructions and the thread's CPU (to compare the
    // instructions per CPU second minute by minute with 0.64 on the same frames)
    let mut per_min_ic: BTreeMap<i64, (u64, f64)> = BTreeMap::new();
    // the load at most: in the scored window [m0, m1]; in the lead before it (cold start); in its first and its
    // last 10 min (growing over time)
    let (mut load_max, mut load_lead, mut load_begin, mut load_end) =
        (Load::default(), Load::default(), Load::default(), Load::default());
    let mut failed: Option<String> = None;
    let mut next_progress = f64::NEG_INFINITY;
    let (mut t_first, mut t_last) = (None::<f64>, 0.0);
    let mut prog_cpu = 0.0;
    // branches of the hypotheses in the scored window [m0, m1]: a track found again, a track born; their
    // children
    let (mut branch_again, mut branch_born, mut branch_children) = (0u64, 0u64, 0u64);
    let out = std::io::stdout();

    while let Some((recv, board, payload)) = lines.next()? {
        let c0 = thread_cpu();
        let i0 = instructions.as_ref().map_or(0, |(c, _)| c.read());
        let Ok(frame) = Frame::from_json(&payload) else {
            dropped += 1;
            continue;
        };
        let t = if rule.is_some() {
            recv
        } else {
            clocks
                .entry(board.clone())
                .or_default()
                .at(recv, frame.uptime_ms, tracker.now())
        };
        t_first.get_or_insert(t);
        t_last = t;
        let dets = raw_dets(&raw, &board, &payload);
        if let Some(rule) = &rule {
            for (p, _, speed) in &dets {
                if rule.moving_only && *speed == 0.0 {
                    continue;
                }
                if let Some(k) = raw.room_at(*p)
                    && (!rule.own_only || board == format!("presence-{}", rooms[k]))
                {
                    rule_seen[k] = t;
                    lit_until[k] = t + HOLD;
                }
            }
            for (k, o) in occ.iter_mut().enumerate() {
                *o = t - rule_seen[k] <= 1.0;
            }
        } else {
            let events = tracker
                .frame(&board, t, &frame)
                .map_err(|e| format!("the frame at {t}: {e:?}"))?;
            if (m0..=m1).contains(&t) {
                for e in &events {
                    if let presence_core::tracker::Event::Branch { how, children, .. } = e {
                        match how {
                            presence_core::tracker::Branched::FoundAgain => branch_again += 1,
                            presence_core::tracker::Branched::Born => branch_born += 1,
                        }
                        branch_children += children.len() as u64;
                    }
                }
            }
            if t >= next_step && tracker.start().is_some() {
                tracker.step(t).map_err(|e| format!("the step at {t}: {e}"))?;
                let states = tracker.zone_states(tracker.now());
                for st in &states {
                    if let Some(k) = room_k(&st.id) {
                        occ[k] = st.occupied;
                        if st.occupied {
                            lit_until[k] = t + HOLD;
                        }
                    }
                }
                next_step = t + tick;
            }
        }
        frames += 1;
        let raw_cost = thread_cpu() - c0;
        raw_cpu += raw_cost;
        let mut di = 0_u64;
        let cost = match &instructions {
            Some((c, ips)) => {
                di = c.read().saturating_sub(i0);
                instr_total += di;
                di as f64 / ips
            }
            None => raw_cost,
        };
        if t >= queue_from {
            let e = per_min_ic.entry((t / 60.0).floor() as i64).or_default();
            e.0 += di;
            e.1 += raw_cost;
        }
        model_cpu += cost;
        prog_cpu += cost;
        *per_min.entry((t / 60.0).floor() as i64).or_default() += cost;
        if let Some((w, sec, acc_s)) = &mut cpu_trace {
            if t.floor() > *sec {
                if *sec > 0.0 {
                    let _ = writeln!(w, "{sec:.0} {acc_s:.6}");
                }
                (*sec, *acc_s) = (t.floor(), 0.0);
            }
            *acc_s += cost;
        }
        if let Some(tp) = t_prev {
            for q in [&mut queue, &mut queue_lo, &mut queue_hi, &mut queue_cpu, &mut queue_cpu_lb, &mut queue_lead] {
                q.go_on(t - tp);
            }
        }
        if t < queue_from {
            warmup_cpu += cost;
            queue_lead.add(cost, t);
        } else {
            for q in [&mut queue, &mut queue_lo, &mut queue_hi] {
                q.add(cost, t);
            }
            queue_cpu.add(raw_cost, t);
            queue_cpu_lb.add(raw_cost, t);
        }
        t_prev = Some(t);
        if raw.sensors.contains_key(&board) {
            sitters.frame(&board, t, &dets);
        }
        if rule.is_none() && (frames % 16 == 0 || t >= next_progress) {
            let l = load_of(&tracker);
            if t < m0 {
                load_lead.widen(&l);
            } else {
                load_max.widen(&l);
                if t < m0 + 600.0 {
                    load_begin.widen(&l);
                }
                if t >= m1 - 600.0 {
                    load_end.widen(&l);
                }
            }
        }
        if t >= next_progress {
            let lit: Vec<&str> = (0..rooms.len())
                .filter(|&k| lit_until[k] >= t)
                .map(|k| rooms[k].as_str())
                .collect();
            let l = if rule.is_none() {
                let l = load_of(&tracker);
                format!(" hyps {} ohne_spur {} ({:.2}) dichten {}", l.hyps, l.hidden, l.hidden_r, l.densities)
            } else {
                String::new()
            };
            let span = if next_progress.is_finite() { progress } else { 0.0 };
            let mut o = out.lock();
            let _ = writeln!(
                o,
                "# {} {} cpu {:.0} ms/min rueckstand {:.1} s{} licht [{}]",
                name,
                hms(t, tz),
                if span > 0.0 { 1000.0 * prog_cpu * 60.0 / span } else { 0.0 },
                queue.backlog,
                l,
                lit.join(",")
            );
            let _ = o.flush();
            prog_cpu = 0.0;
            next_progress = if next_progress.is_finite() { next_progress + progress } else { t + progress };
        }
        if queue.backlog > max_backlog {
            failed = Some(format!(
                "Rückstand {:.0} s > {max_backlog:.0} s um {} (Faktor {factor:.2})",
                queue.backlog,
                hms(t, tz)
            ));
            break;
        }
        if instructions.is_some() && queue_cpu_lb.backlog > max_backlog {
            failed = Some(format!(
                "Rückstand nach Thread-CPU auch lastbereinigt (/{load_max_stretch:.1}) {:.0} s > {max_backlog:.0} s um {} \
                 (Instruktionen: {:.0} s; mehr Takte je Instruktion)",
                queue_cpu_lb.backlog,
                hms(t, tz),
                queue.backlog
            ));
            break;
        }
        if queue_lead.backlog > max_backlog_lead {
            failed = Some(format!(
                "Anlauf vor dem Fenster langsamer als Echtzeit: Rückstand {:.0} s > {max_backlog_lead:.0} s um {}",
                queue_lead.backlog,
                hms(t, tz)
            ));
            break;
        }
        if frames % 256 == 0 && process_cpu() - cpu_begin > cpu_limit {
            failed = Some(format!("Zeitgrenze {cpu_limit:.0} s CPU um {}", hms(t, tz)));
            break;
        }

        // scoring, once per second of frame time in [m0, m1] (evalp.py run_window)
        if t < m0 || t - last_sample < 1.0 || skip.iter().any(|&(a, b)| a <= t && t <= b) {
            continue;
        }
        last_sample = t;
        acc.s += 1.0;
        let lit: Vec<bool> = lit_until.iter().map(|&u| u >= t).collect();
        let n_lit = lit.iter().filter(|&&x| x).count();
        acc.licht += n_lit as f64;
        acc.licht_zuviel += n_lit.saturating_sub(truth) as f64;
        for (k, &on) in lit.iter().enumerate() {
            if on {
                *acc.lit_room.entry(rooms[k].clone()).or_default() += 1.0;
            }
        }
        for (i, (r, a, b)) in must.iter().enumerate() {
            if *a <= t && t <= *b {
                acc.muss_s += 1.0;
                if room_k(r).is_some_and(|k| lit[k]) {
                    dark_run[i] = 0.0;
                } else {
                    acc.muss_dunkel += 1.0;
                    dark_run[i] += 1.0;
                    acc.muss_luecke_max = acc.muss_luecke_max.max(dark_run[i]);
                    if acc.muss_dunkel_wann.len() < 50 {
                        acc.muss_dunkel_wann.push(t);
                    }
                }
            }
        }
        for (r, a, b) in &verbot {
            if *a <= t && t <= *b {
                acc.verbot_s += 1.0;
                if room_k(r).is_some_and(|k| lit[k]) {
                    acc.verbot_licht += 1.0;
                }
            }
        }
        if let Some(k) = schlaf {
            acc.schlaf_dunkel += f64::from(!lit[k]);
            acc.schlaf_unbelegt += f64::from(!occ[k]);
        }
        acc.mehrraum += f64::from(occ.iter().filter(|&&x| x).count() >= 2);
        let sit = sitters.rooms_now(t, &raw);
        acc.dunkel_sitzer += f64::from(sit.iter().any(|&k| !lit[k]));
        acc.sitzer += f64::from(sit.iter().any(|&k| !occ[k]));
        if rule.is_none() {
            let dist = tracker.count_distribution();
            let e = |row: &[f64]| row.iter().enumerate().map(|(k, q)| k as f64 * q).sum::<f64>();
            if let Some(total) = dist.last() {
                acc.zuviel += (e(total) - truth_people).max(0.0);
            }
            for (k, row) in dist.iter().take(observed.len()).enumerate() {
                let m = acc.max_e_room.entry(observed[k].clone()).or_default();
                *m = m.max(e(row));
            }
            if let Some(row) = schlaf.and_then(|k| dist.get(k)) {
                acc.schlaf_e += e(row);
            }
            for (r, a, b) in &zwei {
                if *a <= t
                    && t <= *b
                    && let Some(row) = room_k(r).and_then(|k| dist.get(k))
                {
                    acc.zwei_s += 1.0;
                    acc.zwei_p += 1.0 - row.first().copied().unwrap_or(1.0) - row.get(1).copied().unwrap_or(0.0);
                }
            }
        }
    }

    let span = t_first.map_or(0.0, |t0| t_last - t0);
    let scored_span = (t_last.min(m1) - m0).max(0.0);
    let per_min_ms: Vec<f64> = per_min.values().map(|c| c * 1000.0).collect();
    let min = |x: f64| x / 60.0;
    let status = if failed.is_some() { "durchgefallen" } else { "ok" };
    let mut r = json!({
        "fenster": name,
        "art": kind,
        "status": status,
        "grund": failed,
        "modus": if rule.is_some() { "regel" } else { "modell" },
        "schwellen": thresholds,
        "gelernt": learned_info,
        "raeume": rooms,
        "daten_min": min(span),
        "bewertet_min": min(acc.s),
        "vollstaendig": failed.is_none() && t_last >= m1 - 60.0,
        "abgeschnitten": lines.cut,
        "frames": frames,
        "verworfen": dropped,
        "licht": min(acc.licht),
        "licht_zuviel": min(acc.licht_zuviel),
        "licht_raum": acc.lit_room.iter().map(|(k, v)| (k.clone(), json!(min(*v)))).collect::<Map<_, _>>(),
        "muss_min": min(acc.muss_s),
        "muss_dunkel": min(acc.muss_dunkel),
        "muss_luecke_max_s": acc.muss_luecke_max,
        "muss_dunkel_wann": acc.muss_dunkel_wann.iter().map(|&t| hms(t, tz)).collect::<Vec<_>>(),
        "verbot_min": min(acc.verbot_s),
        "verbot_licht": min(acc.verbot_licht),
        "schlaf_dunkel": min(acc.schlaf_dunkel),
        "schlaf_unbelegt": min(acc.schlaf_unbelegt),
        "mehrraum": min(acc.mehrraum),
        "sitzer": min(acc.sitzer),
        "dunkel_sitzer": min(acc.dunkel_sitzer),
        "cpu_modell_s": model_cpu,
        "cpu_prozess_s": process_cpu() - cpu_begin,
        "cpu_anlauf_s": warmup_cpu,
        "cpu_modell_roh_s": raw_cpu,
        "instruktionen": instr_total,
        "kosten": if instructions.is_some() { "instruktionen" } else { "cpu" },
        "ips_leer": instructions.as_ref().map(|(_, ips)| *ips),
        "rueckstand_ab": hms(queue_from, tz),
        "cpu_ms_je_datenmin": if span > 0.0 { 1000.0 * model_cpu * 60.0 / span } else { 0.0 },
        "cpu_ms_je_datenmin_max": per_min_ms.iter().copied().fold(0.0, f64::max),
        "faktor": factor,
        "rueckstand_max_s": queue.max,
        "rueckstand_max_um": hms(queue.at, tz),
        "faktor_spanne": [f_lo, f_hi],
        "rueckstand_spanne_max_s": [queue_lo.max, queue_hi.max],
        "rueckstand_cpu_max_s": queue_cpu.max,
        "rueckstand_cpu_max_um": hms(queue_cpu.at, tz),
        "rueckstand_lastbereinigt_max_s": queue_cpu_lb.max,
        "anlauf_rueckstand_max_s": queue_lead.max,
        "last_max": load_max_stretch,
        "minuten": per_min_ic.iter().map(|(m, (i, c))| json!([m, i, c])).collect::<Vec<_>>(),
        "ips_ist": if raw_cpu > 0.0 && instr_total > 0 { Some(instr_total as f64 / raw_cpu) } else { None },
        "bewertet_bis": hms(t_last.min(m1), tz),
        "bewertet_spanne_min": min(scored_span),
    });
    if rule.is_none() {
        let n = acc.s.max(1.0);
        let o = r.as_object_mut().expect("an object");
        o.insert("zuviel".into(), json!(min(acc.zuviel)));
        o.insert("verzweigt_wiedergefunden".into(), json!(branch_again));
        o.insert("verzweigt_neu".into(), json!(branch_born));
        o.insert("verzweigt_kinder".into(), json!(branch_children));
        o.insert("schlaf_e".into(), json!(acc.schlaf_e / n));
        o.insert("max_e_raum".into(), json!(acc.max_e_room));
        o.insert("zwei_p".into(), json!(if acc.zwei_s > 0.0 { Some(acc.zwei_p / acc.zwei_s) } else { None }));
        // max_*: the scored window only (not the cold start before it, anlauf_*); anfang_*/ende_*: its first and
        // last 10 min
        for (pre, l) in [("max", load_max), ("anlauf_max", load_lead), ("anfang", load_begin), ("ende", load_end)] {
            o.insert(format!("{pre}_hyps"), json!(l.hyps));
            o.insert(format!("{pre}_ohne_spur"), json!(l.hidden));
            o.insert(format!("{pre}_ohne_spur_r"), json!(l.hidden_r));
            o.insert(format!("{pre}_dichten"), json!(l.densities));
        }
    }
    let mut o = out.lock();
    writeln!(o, "{r}").map_err(|e| e.to_string())?;
    o.flush().map_err(|e| e.to_string())?;
    Ok(())
}

fn raw_dets(raw: &Raw, board: &str, payload: &Value) -> Vec<([f64; 2], (i64, i64), f64)> {
    raw.detections(board, payload)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_queue_grows_by_the_cost_on_inge_and_drains_with_frame_time() {
        let mut q = Queue::new(3.0);
        q.add(10.0, 1.0); // 30 s of Inge's time
        q.go_on(20.0);
        assert!((q.backlog - 10.0).abs() < 1e-9 && (q.max - 30.0).abs() < 1e-9 && (q.at - 1.0).abs() < 1e-9);
        q.go_on(50.0);
        assert!(q.backlog.abs() < 1e-12);
    }

    #[test]
    fn inside_is_evalps_ray_casting() {
        let square = [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]];
        assert!(inside(&square, [1.0, 1.0]));
        assert!(!inside(&square, [2.5, 1.0]));
        assert!(!inside(&square, [1.0, -0.1]));
    }

    #[test]
    fn a_detection_lands_where_evalp_puts_it() {
        // evalp.world: a sensor at (1, 2) looking along +y (heading 90), 1.5 m high, a target at 1 m height
        let raw = Raw {
            sensors: HashMap::new(),
            target_height: 1.0,
            rooms: Vec::new(),
        };
        let s = RawSensor {
            x: 1.0,
            y: 2.0,
            heading: 90.0,
            height: 1.5,
            scale: 1.0,
            mirror: false,
        };
        let [x, y] = raw.world(&s, 0.0, 3000.0);
        let g = (3.0_f64 * 3.0 - 0.25).sqrt();
        assert!((x - 1.0).abs() < 1e-9 && (y - (2.0 + g)).abs() < 1e-9, "{x} {y}");
    }

    #[test]
    fn a_frozen_track_is_no_sitter() {
        let raw = Raw {
            sensors: HashMap::new(),
            target_height: 1.0,
            rooms: vec![("wohn".into(), vec![[0.0, 0.0], [5.0, 0.0], [5.0, 5.0], [0.0, 5.0]])],
        };
        let (mut moving, mut frozen) = (Sitters::default(), Sitters::default());
        for k in 0..800 {
            let t = f64::from(k) * 0.1;
            let wobble = i64::from(k % 5);
            moving.frame("s", t, &[([2.0, 2.0], (1000 + wobble, 2000), 0.0)]);
            frozen.frame("s", t, &[([2.0, 2.0], (1000, 2000), 0.0)]);
        }
        assert_eq!(moving.rooms_now(79.9, &raw), vec![0]);
        assert!(frozen.rooms_now(79.9, &raw).is_empty());
    }

    #[test]
    fn the_time_of_a_line_is_read_without_parsing_it() {
        assert_eq!(Lines::quick_t(r#"{"t":1794927600.02,"topic":"x"}"#), Some(1_794_927_600.02));
        assert_eq!(Lines::quick_t(r#"{"topic":"x","t":1}"#), None);
    }
}
