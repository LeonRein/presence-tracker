//! The model of today: the Bayes filter of presence-core, driven as the app drives it (as `trace`): each frame's
//! time from the board's uptime (SensorClock), then every tick a step and the zones' states.
//!
//! Parameters (`--set k=v`): a field of the model (presence_core::params::Params, the core's defaults otherwise,
//! as the app runs it) or of the configuration's "params" (TrackerParams, e.g. light_cost); anything else is an
//! error.

use std::collections::{BTreeSet, HashMap};

use presence_core::config::TrackerParams;
use presence_core::frames::SensorClock;
use presence_core::params::Params;
use presence_core::tracker::{Branched, Event, Tracker};
use serde_json::{Map, Value, json};

use super::{Applied, Context, Input, Model, RoomValues, Values, expected};

pub struct Filter {
    tracker: Tracker,
    clocks: HashMap<String, SensorClock>,
    /// per room of the context: its index among the zones' states' ids
    index: HashMap<String, usize>,
    rooms: usize,
    /// per room of the context: its row in count_distribution (the tracker's observed rooms)
    rows: Vec<Option<usize>>,
    scored: (f64, f64),
    tz: f64,
    /// branches of the hypotheses in the scored window: a track found again, a track born; their children
    branch_again: u64,
    branch_born: u64,
    branch_children: u64,
    learned: Map<String, Value>,
    thresholds: Value,
    dump_every: Option<(f64, f64, f64)>,
    next_dump: std::cell::Cell<f64>,
}

/// The names of the configuration's parameters and those of the model, with their values unless set.
fn known() -> (Map<String, Value>, Map<String, Value>) {
    let obj = |v: Value| match v {
        Value::Object(m) => m,
        _ => Map::new(),
    };
    (
        obj(serde_json::to_value(TrackerParams::default()).unwrap_or(Value::Null)),
        obj(serde_json::to_value(Params::default()).unwrap_or(Value::Null)),
    )
}

/// The parameters `set` split into the configuration's (written into a copy of `config`) and the model's;
/// what takes effect where. An unknown name is an error.
pub fn resolve(config: &Value, set: &Map<String, Value>) -> Result<(Value, Params, Vec<Applied>), String> {
    let (config_keys, model_keys) = known();
    let mut config = config.clone();
    let mut model = Map::new();
    let mut applied = Vec::new();
    for (k, v) in set {
        if let Some(default) = config_keys.get(k) {
            let params = config
                .as_object_mut()
                .ok_or("the configuration is no object")?
                .entry("params")
                .or_insert_with(|| json!({}));
            let old = params.get(k).cloned().unwrap_or_else(|| default.clone());
            params[k] = v.clone();
            applied.push(Applied {
                key: k.clone(),
                place: "konfig",
                old,
                new: v.clone(),
            });
        } else if let Some(default) = model_keys.get(k) {
            model.insert(k.clone(), v.clone());
            applied.push(Applied {
                key: k.clone(),
                place: "modell",
                old: default.clone(),
                new: v.clone(),
            });
        } else {
            return Err(format!(
                "unbekannter Parameter {k:?}: weder im Modell (presence_core::params::Params) noch in der \
                 Konfiguration (params); `presence-replay parameter` zeigt alle"
            ));
        }
    }
    let params = if model.is_empty() {
        Params::default()
    } else {
        // the model as the fields that differ from the defaults (Params::from_json)
        let p = Params::from_json(&Value::Object(model).to_string()).map_err(|e| format!("--set: {e}"))?;
        p.validate().map_err(|e| format!("--set: {e}"))?;
        p
    };
    Ok((config, params, applied))
}

/// `presence-replay parameter [k=v ...]`: the parameters there are (and their values unless set), or where the
/// given ones take effect.
pub fn list_parameters(sets: &[&str]) -> Result<(), String> {
    let (config_keys, model_keys) = known();
    if sets.is_empty() {
        println!("{}", json!({"modell": model_keys, "konfig": config_keys}));
        return Ok(());
    }
    let mut set = Map::new();
    for kv in sets {
        let (k, v) = kv.split_once('=').ok_or_else(|| format!("not k=v: {kv}"))?;
        set.insert(
            k.to_string(),
            serde_json::from_str(v).unwrap_or_else(|_| Value::String(v.to_string())),
        );
    }
    let (_, _, applied) = resolve(&json!({"params": {}}), &set)?;
    println!("{}", Applied::json(&applied));
    Ok(())
}

impl Filter {
    pub fn new(ctx: &Context, set: &Map<String, Value>) -> Result<(Self, Vec<Applied>), String> {
        let (config, params, applied) = resolve(ctx.config, set)?;
        let text = if applied.iter().any(|a| a.place == "konfig") {
            config.to_string()
        } else {
            std::fs::read_to_string(ctx.config_path).map_err(|e| format!("{}: {e}", ctx.config_path))?
        };
        let mut tracker = crate::tracker_of(&text, ctx.config_path, params)?;
        let mut learned = Map::new();
        if let Some(v) = ctx.learned {
            if !v["ld_background"].is_null() {
                let dropped = tracker
                    .use_background(Some(&v["ld_background"]))
                    .map_err(|e| format!("ld_background: {e:?}"))?;
                learned.insert("ld_teile_verworfen".into(), json!(dropped.len()));
                learned.insert("ld".into(), json!(true));
            }
            if !v["dest_map"].is_null() {
                let used = tracker
                    .use_dest_map(Some(&v["dest_map"]))
                    .map_err(|e| format!("dest_map: {e:?}"))?;
                learned.insert("zielkarte".into(), json!(used));
            }
        }
        let observed: Vec<String> = tracker.world().plan.observed().map(|z| z.id.clone()).collect();
        let rows = ctx.rooms.iter().map(|r| observed.iter().position(|o| o == r)).collect();
        let index = ctx.rooms.iter().enumerate().map(|(k, r)| (r.clone(), k)).collect();
        let outputs = &tracker.world().outputs;
        let thresholds = json!({"an": outputs.occupied_above(), "aus": outputs.occupied_below(),
                                "light_cost": outputs.light_cost, "occupied_off": outputs.occupied_off});
        let dump_every = std::env::var("PT_DUMP").ok().and_then(|v| {
            let x: Vec<f64> = v.split(',').filter_map(|s| s.parse().ok()).collect();
            (x.len() == 3).then(|| (x[0], x[1], x[2]))
        });
        let observed_set: BTreeSet<&String> = observed.iter().collect();
        let rooms_set: BTreeSet<&String> = ctx.rooms.iter().collect();
        if observed_set != rooms_set {
            learned.insert(
                "raeume_abweichend".into(),
                json!({"beobachtet": observed, "bewertet": ctx.rooms}),
            );
        }
        Ok((
            Self {
                tracker,
                clocks: HashMap::new(),
                index,
                rooms: ctx.rooms.len(),
                rows,
                scored: ctx.scored,
                tz: ctx.tz,
                branch_again: 0,
                branch_born: 0,
                branch_children: 0,
                learned,
                thresholds,
                dump_every,
                next_dump: std::cell::Cell::new(f64::NEG_INFINITY),
            },
            applied,
        ))
    }
}

impl Model for Filter {
    fn frame(&mut self, input: &Input) -> Result<f64, String> {
        let t = self.clocks.entry(input.board.to_string()).or_default().at(
            input.received,
            input.frame.uptime_ms,
            self.tracker.now(),
        );
        let events = self
            .tracker
            .frame(input.board, t, input.frame)
            .map_err(|e| format!("the frame at {t}: {e:?}"))?;
        let (m0, m1) = self.scored;
        if (m0..=m1).contains(&t) {
            for e in &events {
                if let Event::Branch { how, children, .. } = e {
                    match how {
                        Branched::FoundAgain => self.branch_again += 1,
                        Branched::Born => self.branch_born += 1,
                    }
                    self.branch_children += children.len() as u64;
                }
            }
        }
        Ok(t)
    }

    fn tick(&mut self, t: f64) -> Result<Option<Vec<bool>>, String> {
        if self.tracker.start().is_none() {
            return Ok(None);
        }
        self.tracker.step(t).map_err(|e| format!("the step at {t}: {e}"))?;
        let states = self.tracker.zone_states(self.tracker.now());
        let mut occ = vec![false; self.rooms];
        for st in &states {
            if let Some(&k) = self.index.get(&st.id) {
                occ[k] = st.occupied;
            }
        }
        Ok(Some(occ))
    }

    fn values(&mut self) -> Values {
        let dist = self.tracker.count_distribution();
        let rooms = self
            .rows
            .iter()
            .map(|row| match row.and_then(|k| dist.get(k)) {
                Some(row) => {
                    let p0 = row.first().copied().unwrap_or(1.0);
                    RoomValues {
                        p_any: 1.0 - p0,
                        e_count: expected(row),
                        p_two: Some(1.0 - p0 - row.get(1).copied().unwrap_or(0.0)),
                    }
                }
                None => RoomValues::default(),
            })
            .collect();
        Values {
            rooms,
            e_house: dist.last().map(|r| expected(r)),
        }
    }

    fn load(&self) -> Vec<(&'static str, f64)> {
        let f = self.tracker.filter();
        let dens = f.objects().densities();
        let (mut hidden, mut hidden_r) = (0usize, 0.0_f64);
        for h in f.hyps() {
            hidden = hidden.max(h.hidden.len());
            let r: f64 = h.hidden.iter().map(|&u| dens.get(u).r).sum();
            hidden_r = hidden_r.max(r);
        }
        vec![
            ("hyps", f.hyps().len() as f64),
            ("ohne_spur", hidden as f64),
            ("ohne_spur_r", hidden_r),
            ("dichten", dens.len() as f64),
        ]
    }

    fn summary(&self) -> Map<String, Value> {
        let mut o = Map::new();
        o.insert("schwellen".into(), self.thresholds.clone());
        o.insert("gelernt".into(), Value::Object(self.learned.clone()));
        o.insert("verzweigt_wiedergefunden".into(), json!(self.branch_again));
        o.insert("verzweigt_neu".into(), json!(self.branch_born));
        o.insert("verzweigt_kinder".into(), json!(self.branch_children));
        o
    }

    /// PT_DUMP="von,bis,alle" (epoch, s): the strongest hypotheses, their people and those without a track.
    fn dump(&self, t: f64) -> Option<String> {
        let (a, b, every) = self.dump_every?;
        if t < a || t > b || t < self.next_dump.get() {
            return None;
        }
        self.next_dump.set(t + every);
        let tracker = &self.tracker;
        let f = tracker.filter();
        let world = tracker.world();
        let objs = f.objects();
        let dens = objs.densities();
        let shape = dens.shape();
        let xs: Vec<f64> = world.tiles.centers.iter().map(|c| c[0]).collect();
        let ys: Vec<f64> = world.tiles.centers.iter().map(|c| c[1]).collect();
        let ones = vec![1.0; xs.len()];
        let w = f.hyp_weights();
        let mut order: Vec<usize> = (0..w.len()).collect();
        order.sort_by(|&a, &b| w[b].total_cmp(&w[a]));
        let mut line = format!("#D {}", crate::bench::hms(t, self.tz));
        for &h in order.iter().take(3) {
            let hy = &f.hyps()[h];
            line += &format!(" | w{:.2}", w[h]);
            for (_, &m) in hy.people.iter() {
                let mix = objs.mixtures().get(m);
                let p = mix.modes[0].pos();
                let segs: Vec<String> = mix.slots.keys().map(|s| format!("{}", s.0)).collect();
                line += &format!(" P[{}]({:.2},{:.2})", segs.join(","), p[0], p[1]);
            }
            for &u in &hy.hidden {
                let d = dens.get(u);
                let tot = d.integrate(&ones, &shape).max(1e-12);
                let walk: f64 = d.walk.iter().sum::<f64>() * d.r;
                line += &format!(
                    " H{:.3}({:.2},{:.2} g{:.2})",
                    d.r,
                    d.integrate(&xs, &shape) / tot,
                    d.integrate(&ys, &shape) / tot,
                    walk / tot
                );
            }
        }
        Some(line)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_model_parameter_goes_to_the_model_and_a_configuration_parameter_into_the_configuration() {
        let config = json!({"params": {"light_cost": 0.2}});
        let mut set = Map::new();
        set.insert("light_cost".into(), json!(2.0));
        set.insert("go_time".into(), json!(77.0));
        let (c, p, applied) = resolve(&config, &set).expect("known parameters");
        assert_eq!(c["params"]["light_cost"], json!(2.0));
        assert!((p.go_time - 77.0).abs() < 1e-12);
        assert!(
            (Params::default().go_time - 77.0).abs() > 1e-6,
            "the test needs a value that differs"
        );
        let places: Vec<(&str, &str)> = applied.iter().map(|a| (a.key.as_str(), a.place)).collect();
        assert_eq!(places, vec![("go_time", "modell"), ("light_cost", "konfig")]);
        assert_eq!(applied[1].old, json!(0.2));
    }

    /// A room 6 x 5 m with a sensor in a corner: somebody stands in it for a minute, then no target for 10 min.
    /// P(somebody there) in the room each 10 s, with the model's parameters `set`.
    fn p_in_room(set: &Map<String, Value>) -> Vec<f64> {
        let config = json!({
            "sensors": [{"id": "presence-wohn", "name": "wohn", "x": 0.05, "y": 0.05, "heading": 45.0,
                         "height": 1.5, "fov": 120.0, "range": 6.0, "scale": 1.0, "mirror": false,
                         "placed": true, "enabled": true}],
            "zones": [{"id": "wohn", "name": "Wohn", "kind": "room", "shape": "rect", "points": [[0, 0], [6, 5]]}],
            "walls": [{"points": [[0, 0], [6, 0]], "kind": "wall"}, {"points": [[6, 0], [6, 5]], "kind": "wall"},
                      {"points": [[6, 5], [0, 5]], "kind": "wall"}, {"points": [[0, 5], [0, 0]], "kind": "wall"}],
            "doors": [],
            "params": {}
        });
        let path = std::env::temp_dir().join(format!("pruefstand-test-{}.json", std::process::id()));
        std::fs::write(&path, config.to_string()).expect("a temporary file");
        let rooms = vec!["wohn".to_string()];
        let ctx = Context {
            config: &config,
            config_path: path.to_str().expect("a path"),
            learned: None,
            rooms: &rooms,
            scored: (0.0, f64::INFINITY),
            tz: 0.0,
        };
        let (mut m, _) = Filter::new(&ctx, set).expect("the model");
        let mut out = Vec::new();
        let (t0, mut next) = (1_000_000.0, 0.0);
        for k in 0..(660 * 11) {
            let dt = f64::from(k) * 0.09;
            let targets = if dt < 60.0 {
                json!([{"slot": 1, "x": 30 * (k % 3), "y": 2500, "speed": 0, "resolution": 360}])
            } else {
                json!([])
            };
            let payload = json!({"uptime_ms": 5_000 + (dt * 1000.0) as u64, "targets": targets});
            let frame = presence_core::frames::Frame::from_json(&payload).expect("a frame");
            let t = m
                .frame(&Input {
                    received: t0 + dt,
                    board: "presence-wohn",
                    frame: &frame,
                    payload: &payload,
                })
                .expect("frame");
            if t >= next && m.tick(t).expect("tick").is_some() {
                next = t + 0.1;
            }
            if k % 110 == 0 {
                out.push(m.values().rooms[0].p_any);
            }
        }
        let _ = std::fs::remove_file(&path);
        out
    }

    #[test]
    fn a_changed_model_parameter_changes_the_result() {
        // kappa_shape: how much the detectability of a still person spreads (3 unless set); spread more (1), a
        // person no longer seen may well be one standing still unseen: P(somebody there) falls slower
        let default = p_in_room(&Map::new());
        let mut set = Map::new();
        set.insert("kappa_shape".into(), json!(1.0));
        let changed = p_in_room(&set);
        assert!(default[3] > 0.5, "somebody is there while seen: {default:?}");
        let most = default.iter().zip(&changed).map(|(a, b)| b - a).fold(0.0, f64::max);
        assert!(
            most > 0.05,
            "kappa_shape 1 instead of 3 changes nothing: {default:?} {changed:?}"
        );
        assert_eq!(p_in_room(&Map::new()), default, "the same parameters, the same result");
    }

    #[test]
    fn an_unknown_parameter_is_an_error_not_passed_over() {
        let mut set = Map::new();
        set.insert("gibt_es_nicht".into(), json!(1));
        assert!(resolve(&json!({"params": {}}), &set).is_err());
    }

    #[test]
    fn an_invalid_model_value_is_an_error() {
        let mut set = Map::new();
        set.insert("go_time".into(), json!(-1.0));
        assert!(resolve(&json!({"params": {}}), &set).is_err());
    }
}
