//! The energy each sensor's LD2410C sees without anybody (MODEL.md 4.3; ld2410.Background), per cell,
//! learned online by EM of the superposition: the background's share of an energy is b / mu
//! (Richardson 1972, Lucy 1974; Shepp & Vardi 1982), with mu from the people as the filter saw them
//! before these frames (the energies' own judgement is not learned back, Park et al. 2020), weighed
//! with P(no echo source on). Forgotten with ld_forget s towards an anchor: the sensor's own long-run
//! level, learned the same way over days from frames after ld_anchor_quiet s with nothing in sight of
//! the housing's LD2450 (a stationary AR(1) level, Harvey 1989). With it the rate at which echo sources
//! begin. A part holds while its sensor hangs where it hung (cells are distances from the sensor).

use serde_json::{Map, Value, json};

use super::likelihood::{Stats, log_gammaq};
use super::{CAP, CELLS};
use crate::filter::LdLearn;
use crate::formats::{self, number, object, round_python};
use crate::learn::LearnedError;
use crate::params::Params;
use crate::plan::Plan;
use crate::plan::Pose;

type Cells = [f64; CELLS];

/// A level learned from frames: s x the background's share of the energy, and s watched.
#[derive(Debug, Clone, Copy, PartialEq)]
struct Level {
    num: Cells,
    den: f64,
}

impl Level {
    /// The level as b: (prior x prior_time + num) / (prior_time + den).
    fn mean(&self, prior: &Cells, prior_time: f64) -> Cells {
        std::array::from_fn(|c| (prior[c] * prior_time + self.num[c]) / (prior_time + self.den))
    }

    /// Forgotten with k, then `w` x these frames added.
    fn learn(level: Option<Level>, k: f64, w: f64, gained: &Cells, time: f64) -> Level {
        let old = level.unwrap_or(Level {
            num: [0.0; CELLS],
            den: 0.0,
        });
        Level {
            num: std::array::from_fn(|c| old.num[c] * k + w * gained[c]),
            den: old.den * k + w * time,
        }
    }
}

/// The expected number of echo sources begun, in this many s with nobody in view (both forgotten like
/// the level).
#[derive(Debug, Clone, Copy, PartialEq)]
struct EchoCount {
    began: f64,
    quiet: f64,
}

#[derive(Debug, Clone, PartialEq)]
struct Part {
    id: String,
    /// where it hangs (only place, heading and height are kept: they decide what its LD2410C sees)
    pose: Pose,
    level: Option<Level>,
    anchor: Option<Level>,
    echoes: Option<EchoCount>,
    /// s of its frames still not learned from (Background.pause)
    held: f64,
}

impl Part {
    fn new(id: &str, pose: Pose) -> Self {
        Self {
            id: id.to_string(),
            pose,
            level: None,
            anchor: None,
            echoes: None,
            held: 0.0,
        }
    }
}

/// The background before anything is learned (ld2410.prior).
fn prior(p: &Params) -> Cells {
    let [g0, g1, rest, still] = p.ld_background;
    std::array::from_fn(|c| match c {
        0 => g0,
        1 => g1,
        2..9 => rest,
        _ => still,
    })
}

/// What a weighing of a sensor's LD2410C hands the background (Tracker._ld_weigh): its learning
/// (LdLearn), the background and the echo rate it was weighed with, the cells' Gamma shapes, and how
/// long the housing's LD2450 has had nothing in sight.
pub struct Weighed<'a> {
    pub learn: &'a LdLearn,
    pub b: &'a Cells,
    pub rate: f64,
    pub alpha: &'a Cells,
    pub quiet: f64,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Background {
    /// also the parts of sensors no longer in the plan (as the Python app kept them)
    parts: Vec<Part>,
    /// per sensor of the plan its part (after follow)
    of: Vec<usize>,
}

impl Background {
    /// Keep what was learned for the sensors that hang where they hung (recalibrated or not); the ones
    /// mounted elsewhere and new ones start over (Background.use).
    pub fn follow(&mut self, plan: &Plan) {
        self.of = plan
            .sensors
            .iter()
            .map(|s| match self.parts.iter().position(|p| p.id == s.id) {
                Some(k) => {
                    let part = &mut self.parts[k];
                    if s.pose.remounted(&part.pose) {
                        *part = Part {
                            held: part.held,
                            ..Part::new(&s.id, s.pose)
                        };
                    }
                    part.pose = s.pose;
                    k
                }
                None => {
                    self.parts.push(Part::new(&s.id, s.pose));
                    self.parts.len() - 1
                }
            })
            .collect();
    }

    fn part(&self, si: usize) -> &Part {
        &self.parts[self.of[si]]
    }

    /// The background of sensor si's cells (Background.b).
    pub fn b(&self, si: usize, p: &Params) -> Cells {
        let part = self.part(si);
        match &part.level {
            None => prior(p),
            Some(level) => {
                let anchor = self.anchor(si, p);
                std::array::from_fn(|c| (anchor[c] * p.ld_prior_time + level.num[c]) / (p.ld_prior_time + level.den))
            }
        }
    }

    /// The level the background goes back to while nothing is learned: the sensor's own, learned over
    /// days, from the measured prior (Background.anchor).
    pub fn anchor(&self, si: usize, p: &Params) -> Cells {
        let prior = prior(p);
        self.part(si).anchor.map_or(prior, |a| a.mean(&prior, p.ld_prior_time))
    }

    /// Echo sources begun per s at sensor si: the prior with what was counted (Background.rate).
    pub fn rate(&self, si: usize, p: &Params) -> f64 {
        let e = self.part(si).echoes.unwrap_or(EchoCount { began: 0.0, quiet: 0.0 });
        (p.ld_echo_rate * p.ld_echo_prior_time + e.began) / (p.ld_echo_prior_time + e.quiet)
    }

    /// The level learned for sensor si: s x the background's share of the energies, s watched.
    #[cfg(test)]
    pub(crate) fn level(&self, si: usize) -> Option<(Cells, f64)> {
        self.part(si).level.map(|l| (l.num, l.den))
    }

    /// Learn nothing from the next `seconds` s of each sensor's frames (Background.pause: after
    /// "Tracks zurücksetzen" whom the filter does not see yet would be learned as background).
    pub fn hold(&mut self, seconds: f64) {
        for part in &mut self.parts {
            part.held = seconds;
        }
    }

    /// One step of online EM from a weighing of sensor si (Background.learn, then learn_echoes): the
    /// background's share b / mu of the energies, of a capped one of what it was, E[e | e >= CAP]
    /// under Gamma(alpha, mu) (Dempster, Laird & Rubin 1977), weighed with P(no echo source on) and the
    /// time nobody was in sight; then the echo sources begun where nobody was in view, judged with the
    /// prior's rate in place of the learned one (Park et al. 2020). Held: nothing, the echoes neither.
    pub fn learn(&mut self, si: usize, w: &Weighed, p: &Params) {
        let part = &mut self.parts[self.of[si]];
        let st = &w.learn.stats;
        if part.held > 0.0 {
            part.held -= st.time;
            return;
        }
        let k = (-st.time / p.ld_forget).exp();
        if let Some(e) = &mut part.echoes {
            e.began *= k;
            e.quiet *= k;
        }
        let gained = gained(w.learn, w.b, w.alpha);
        let w_bg = if st.time > 0.0 {
            w.learn.echo_off * (1.0 - st.t_target / st.time)
        } else {
            0.0
        };
        part.level = Some(Level::learn(part.level, k, w_bg, &gained, st.time));
        // the anchor: the same over days, only from frames after ld_anchor_quiet s in which the LD2450
        // of the housing saw nothing (as the empty level was measured, MODEL.md 4.3)
        let ka = (-st.time / p.ld_anchor_forget).exp();
        let w_a = if w.quiet >= p.ld_anchor_quiet { w_bg } else { 0.0 };
        part.anchor = Some(Level::learn(part.anchor, ka, w_a, &gained, st.time));
        let mut began = w.learn.began;
        if 0.0 < began && began < 1.0 {
            let c = p.ld_echo_rate / w.rate;
            began = began * c / (began * c + 1.0 - began);
        }
        let e = part.echoes.get_or_insert(EchoCount { began: 0.0, quiet: 0.0 });
        e.began += w.learn.nobody * began;
        e.quiet += w.learn.nobody * st.time;
    }

    // ------------------------------------------------------------ the format (Background.to_dict)

    pub fn to_json(&self) -> Value {
        let (mut poses, mut num, mut den, mut num_a, mut den_a, mut echoes, mut quiet, mut held) = Default::default();
        let put = |m: &mut Map<String, Value>, id: &str, v: Value| {
            m.insert(id.to_string(), v);
        };
        let cells = |c: &Cells| json!(c.iter().map(|&x| round_python(x, 4)).collect::<Vec<_>>());
        for part in &self.parts {
            let (id, q) = (part.id.as_str(), &part.pose);
            put(&mut poses, id, json!([q.x, q.y, q.heading, q.height]));
            if let Some(l) = &part.level {
                put(&mut num, id, cells(&l.num));
                put(&mut den, id, json!(round_python(l.den, 3)));
            }
            if let Some(l) = &part.anchor {
                put(&mut num_a, id, cells(&l.num));
                put(&mut den_a, id, json!(round_python(l.den, 3)));
            }
            if let Some(e) = &part.echoes {
                put(&mut echoes, id, json!(round_python(e.began, 4)));
                put(&mut quiet, id, json!(round_python(e.quiet, 3)));
            }
            if part.held > 0.0 {
                put(&mut held, id, json!(round_python(part.held, 3)));
            }
        }
        let mut out = json!({"poses": poses, "num": num, "den": den, "num_a": num_a, "den_a": den_a,
                             "echoes": echoes, "quiet": quiet});
        if !held.is_empty() {
            out["held"] = Value::Object(held);
        }
        out
    }

    /// A saved background (Background.load_dict) for the sensors of the plan (it follows them at once),
    /// and the parts not taken: a sensor's part with a value out of range or of another size is dropped
    /// alone, as the Python app did (the others keep what they learned; the caller logs them). A level
    /// needs both halves, the echoes a level. Err only if it is no background at all.
    pub fn from_json(v: &Value, plan: &Plan) -> Result<(Self, Vec<LearnedError>), LearnedError> {
        const SECTIONS: [&str; 8] = ["poses", "num", "den", "num_a", "den_a", "echoes", "quiet", "held"];
        let m = object(v, "ld_background")?;
        let mut sections = [None; 8];
        for (section, name) in sections.iter_mut().zip(SECTIONS) {
            *section = m.get(name).map(|x| object(x, name)).transpose()?;
        }
        let [poses, ..] = sections;
        let mut parts = Vec::new();
        let mut dropped = Vec::new();
        for (id, pose) in poses.into_iter().flatten() {
            let of = |k: usize| sections[k].and_then(|s| s.get(id));
            match saved_part(id, pose, of) {
                Ok(part) => parts.push(part),
                Err(e) => dropped.push(e),
            }
        }
        // what is learned for a sensor without its pose cannot be placed: dropped, said so
        let mut orphans: Vec<&String> = sections.iter().flatten().flat_map(|s| s.keys()).collect();
        orphans.sort_unstable();
        orphans.dedup();
        dropped.extend(
            orphans
                .into_iter()
                .filter(|id| !poses.is_some_and(|p| p.contains_key(*id)))
                .map(|id| LearnedError::Part {
                    field: "poses",
                    sensor: id.clone(),
                }),
        );
        let mut bg = Self { parts, of: Vec::new() };
        bg.follow(plan);
        Ok((bg, dropped))
    }
}

/// A sensor's saved part, its sections' values by index of SECTIONS in from_json.
fn saved_part<'a>(id: &str, pose: &Value, of: impl Fn(usize) -> Option<&'a Value>) -> Result<Part, LearnedError> {
    let bad = |field: &'static str| LearnedError::Part {
        field,
        sensor: id.to_string(),
    };
    let seconds = |k: usize, field| of(k).map(|x| number(x, field, 0.0).map_err(|_| bad(field))).transpose();
    let level =
        |[num, den]: [usize; 2], [num_field, den_field]: [&'static str; 2]| -> Result<Option<Level>, LearnedError> {
            let (Some(n), Some(d)) = (of(num), of(den)) else {
                return Ok(None);
            };
            let num = formats::numbers(n, num_field, CELLS, 0.0).map_err(|_| bad(num_field))?;
            Ok(Some(Level {
                num: num.try_into().expect("CELLS numbers"),
                den: number(d, den_field, 0.0).map_err(|_| bad(den_field))?,
            }))
        };
    let level_now = level([1, 2], ["num", "den"])?;
    let began = seconds(5, "echoes")?.unwrap_or(0.0);
    let quiet = seconds(6, "quiet")?.filter(|_| level_now.is_some());
    Ok(Part {
        id: id.to_string(),
        pose: ld_pose(pose).map_err(|_| bad("poses"))?,
        anchor: level([3, 4], ["num_a", "den_a"])?,
        echoes: quiet.map(|quiet| EchoCount { began, quiet }),
        level: level_now,
        held: seconds(7, "held")?.unwrap_or(0.0),
    })
}

/// [x, y, heading, height] (ld2410.pose_of).
fn ld_pose(v: &Value) -> Result<Pose, LearnedError> {
    let a = formats::numbers(v, "poses", 4, f64::MIN)?;
    Ok(Pose {
        x: a[0],
        y: a[1],
        heading: a[2],
        height: a[3],
        mirror: false,
        scale: 1.0,
    })
}

/// s x the background's share of the energies of these frames: of the uncensored ones b / mu, of the
/// capped ones b Q(alpha + 1, x) / Q(alpha, x) with x = alpha CAP / mu (until 0.20 a capped energy
/// counted as 100), each mixed over the hypotheses with their weights before.
fn gained(l: &LdLearn, b: &Cells, alpha: &Cells) -> Cells {
    let st: &Stats = &l.stats;
    let mix = |f: &dyn Fn(&Cells, usize) -> f64, c: usize| {
        l.prior_w.iter().zip(&l.mus).fold(0.0, |s, (w, mu)| s + w * f(mu, c))
    };
    let censored = st.t_cens.iter().any(|&t| t != 0.0);
    std::array::from_fn(|c| {
        let share = mix(&|mu, c| b[c] / mu[c], c) * st.t_e[c];
        if !censored {
            return share;
        }
        let tail = |mu: &Cells, c: usize| {
            let x = alpha[c] * CAP / mu[c];
            (log_gammaq(alpha[c] + 1.0, x) - log_gammaq(alpha[c], x)).exp()
        };
        share + b[c] * mix(&tail, c) * st.t_cens[c]
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing;

    fn learning(stats: Stats, mus: Vec<Cells>, prior_w: Vec<f64>) -> LdLearn {
        LdLearn {
            mus,
            prior_w,
            nobody: 0.5,
            echo_off: 0.8,
            began: 0.2,
            stats,
            target_t: 0.0,
        }
    }

    fn background() -> (Background, crate::world::World) {
        let w = testing::flat();
        let mut bg = Background::default();
        bg.follow(&w.plan);
        (bg, w)
    }

    #[test]
    fn without_anything_learned_it_is_the_prior() {
        let (bg, w) = background();
        let p = &w.params;
        assert_eq!(bg.b(0, p), prior(p));
        assert_eq!(bg.b(0, p)[..3], [13.0, 9.0, 4.5]);
        assert_eq!(bg.b(0, p)[9], 5.0);
        assert_eq!(bg.anchor(0, p), prior(p));
        assert!((bg.rate(0, p) - p.ld_echo_rate).abs() < 1e-20);
    }

    #[test]
    fn a_step_learns_the_backgrounds_share_by_hand() {
        let (mut bg, w) = background();
        let p = &w.params;
        let mut st = Stats::default();
        st.add(&[10.0; CELLS], 2.0, false);
        // two hypotheses: in the first nobody (mu = b), in the second somebody doubling the energy
        let b = [10.0; CELLS];
        let l = learning(st, vec![[10.0; CELLS], [20.0; CELLS]], vec![0.75, 0.25]);
        let alpha = [2.0; CELLS];
        bg.learn(
            0,
            &Weighed {
                learn: &l,
                b: &b,
                rate: p.ld_echo_rate,
                alpha: &alpha,
                quiet: 1e9,
            },
            p,
        );
        // share 0.75 x 1 + 0.25 x 0.5 = 0.875 of 2 s x 10, weighed with P(no echo source) 0.8
        let level = bg.part(0).level.expect("learned");
        assert!((level.num[0] - 0.8 * 0.875 * 20.0).abs() < 1e-12 && (level.den - 1.6).abs() < 1e-12);
        assert_eq!(
            bg.part(0).anchor,
            Some(level),
            "quiet long enough: the anchor learns alike"
        );
        // the echoes where nobody was in view, judged with the prior's rate (the same here)
        let e = bg.part(0).echoes.expect("counted");
        assert!((e.began - 0.1).abs() < 1e-15 && (e.quiet - 1.0).abs() < 1e-15);
        let anchor = bg.anchor(0, p);
        let want = (anchor[0] * p.ld_prior_time + level.num[0]) / (p.ld_prior_time + level.den);
        assert_eq!(bg.b(0, p)[0], want);
    }

    #[test]
    fn row_3_the_echoes_begun_are_judged_with_the_prior_rate_in_place_of_the_one_read_before() {
        let (mut bg, w) = background();
        let p = &w.params;
        let mut st = Stats::default();
        st.add(&[10.0; CELLS], 2.0, false);
        let l = learning(st, vec![[10.0; CELLS]], vec![1.0]);
        let (b, alpha) = ([10.0; CELLS], [2.0; CELLS]);
        // the rate read before the weighing, twice the prior: the odds of "a source began" halved
        let rate = 2.0 * p.ld_echo_rate;
        bg.learn(
            0,
            &Weighed {
                learn: &l,
                b: &b,
                rate,
                alpha: &alpha,
                quiet: 1e9,
            },
            p,
        );
        // began 0.2: odds 0.25 -> 0.125, P = 1/9; counted where nobody (0.5) was in view
        let e = bg.part(0).echoes.expect("counted");
        assert!((e.began - 0.5 / 9.0).abs() < 1e-15, "{}", e.began);
        assert!((e.quiet - 0.5 * 2.0).abs() < 1e-15);
        let want = (p.ld_echo_rate * p.ld_echo_prior_time + 0.5 / 9.0) / (p.ld_echo_prior_time + 1.0);
        assert!((bg.rate(0, p) - want).abs() < 1e-20);
    }

    #[test]
    fn held_learns_nothing_not_even_the_echoes_and_a_capped_energy_counts_for_more() {
        let (mut bg, w) = background();
        let p = &w.params;
        let mut st = Stats::default();
        st.add(&[100.0; CELLS], 1.0, false);
        let l = learning(st, vec![[10.0; CELLS]], vec![1.0]);
        let (b, alpha) = ([10.0; CELLS], [2.0; CELLS]);
        let weighed = Weighed {
            learn: &l,
            b: &b,
            rate: p.ld_echo_rate,
            alpha: &alpha,
            quiet: 0.0,
        };
        bg.hold(1.5);
        bg.learn(0, &weighed, p);
        assert_eq!(
            (bg.part(0).level, bg.part(0).echoes, bg.part(0).held),
            (None, None, 0.5)
        );
        bg.learn(0, &weighed, p);
        assert!(bg.part(0).level.is_none() && bg.part(0).held < 0.0);
        bg.learn(0, &weighed, p);
        let level = bg.part(0).level.expect("learned");
        // E[e | e >= CAP] > CAP: the share of a capped energy is more than b / mu x CAP... here all of it
        // is the background's (mu = b), the num counts the whole capped energy's expectation
        assert!(level.num[0] > 0.8 * CAP && level.num[0] < 0.8 * 3.0 * CAP);
        assert_eq!(
            bg.part(0).anchor.map(|a| a.den),
            Some(0.0),
            "not quiet: the anchor learns nothing"
        );
    }

    #[test]
    fn the_format_comes_back_and_a_broken_one_is_named() {
        let (mut bg, w) = background();
        let p = &w.params;
        let mut st = Stats::default();
        st.add(&[7.0; CELLS], 1.0, false);
        let l = learning(st, vec![[8.0; CELLS]], vec![1.0]);
        let (b, alpha) = ([8.0; CELLS], [2.0; CELLS]);
        bg.learn(
            0,
            &Weighed {
                learn: &l,
                b: &b,
                rate: p.ld_echo_rate,
                alpha: &alpha,
                quiet: 1e9,
            },
            p,
        );
        bg.learn(
            1,
            &Weighed {
                learn: &l,
                b: &b,
                rate: p.ld_echo_rate,
                alpha: &alpha,
                quiet: 1e9,
            },
            p,
        );
        bg.hold(2.0);
        let v = bg.to_json();
        let (mut back, dropped) = Background::from_json(&v, &w.plan).expect("readable");
        assert_eq!((back.to_json(), dropped), (v.clone(), Vec::new()));
        assert!((back.b(0, p)[3] - bg.b(0, p)[3]).abs() < 1e-3);
        // a broken part is dropped alone: that sensor starts from the prior, the other keeps its own
        let id0 = w.plan.sensors[0].id.clone();
        for (field, value) in [("num", json!([1.0, 2.0])), ("den", json!(null)), ("quiet", json!(-1.0))] {
            let mut bad = v.clone();
            bad[field][&id0] = value;
            let (read, dropped) = Background::from_json(&bad, &w.plan).expect("a background");
            assert_eq!(
                dropped,
                [LearnedError::Part {
                    field,
                    sensor: id0.clone()
                }]
            );
            assert_eq!((read.b(0, p), read.b(1, p)), (prior(p), back.b(1, p)), "{field}");
        }
        assert_eq!(
            Background::from_json(&json!([]), &w.plan),
            Err(LearnedError::Field("ld_background"))
        );
        let (_, dropped) = Background::from_json(&json!({"num": {"x": [1.0]}}), &w.plan).expect("a background");
        assert_eq!(
            dropped,
            [LearnedError::Part {
                field: "poses",
                sensor: "x".into()
            }]
        );
        let mut bad = v;
        bad["num"] = json!([]);
        assert_eq!(Background::from_json(&bad, &w.plan), Err(LearnedError::Field("num")));
        // a part mounted elsewhere starts over, a recalibrated one keeps it
        let mut plan = testing::flat().plan;
        plan.sensors[0].pose.mirror = true;
        back.follow(&plan);
        assert!(back.part(0).level.is_some());
        plan.sensors[0].pose.heading += 45.0;
        back.follow(&plan);
        assert!(back.part(0).level.is_none() && back.part(0).held == 2.0);
    }

    // ------------------------------------------------------------ test_learning (ld2410.Background)

    /// The cells' Gamma shapes (ld2410.cell_values(m, *m.ld_shape)).
    fn alpha(p: &Params) -> Cells {
        super::super::cell_values(p.ld_shape[0], p.ld_shape[1])
    }

    /// Stats of `seconds` s of frames whose energies are Gamma(alpha, mean mu), independent every 0.1 s,
    /// capped as the firmware caps them (test_learning._frames).
    fn frames(mu: &Cells, seconds: f64, seed: u64, alpha: &Cells) -> Stats {
        let mut rng = testing::Rng::new(seed);
        let mut st = Stats::default();
        for _ in 0..(seconds * 10.0) as usize {
            let e = std::array::from_fn(|c| (rng.gamma(alpha[c]) * mu[c] / alpha[c]).round_ties_even().min(100.0));
            st.add(&e, 0.1, false);
        }
        st
    }

    /// These frames as Background.learn took them in test_learning: one hypothesis, P(no echo source
    /// on) `off`; no echo source began, nobody in view counted.
    fn em(stats: Stats, off: f64) -> LdLearn {
        LdLearn {
            mus: Vec::new(),
            prior_w: vec![1.0],
            nobody: 0.0,
            echo_off: off,
            began: 0.0,
            stats,
            target_t: 0.0,
        }
    }

    /// One step of sensor 0 with the background b, the hypothesis' mean mu, the LD2450 quiet for
    /// `quiet` s.
    fn step(bg: &mut Background, l: &mut LdLearn, b: Cells, mu: Cells, quiet: f64, p: &Params) {
        l.mus = vec![mu];
        let alpha = alpha(p);
        bg.learn(
            0,
            &Weighed {
                learn: l,
                b: &b,
                rate: p.ld_echo_rate,
                alpha: &alpha,
                quiet,
            },
            p,
        );
    }

    /// Whether every cell of x is within `tol` of want, relatively.
    fn near(x: &Cells, want: &Cells, tol: f64) -> bool {
        x.iter().zip(want).all(|(a, b)| (a / b - 1.0).abs() < tol)
    }

    fn times(k: f64, b: &Cells) -> Cells {
        b.map(|x| k * x)
    }

    #[test]
    fn capped_energies_count_for_what_they_were() {
        // test_learning: somebody 1.8 m in front of the sensor puts 160 into a still gate whose
        // background is 5: the firmware reports 100 in most frames. Until 0.20 those counted as 100,
        // and the background's share of them made it learn 3 instead of 5
        let (mut bg, w) = background();
        let p = &w.params;
        let b = prior(p);
        let mut mu = b;
        mu[9] += 160.0;
        let mut l = em(frames(&mu, 3600.0, 1, &alpha(p)), 1.0);
        let st = &l.stats;
        assert!(st.t_cens[9] > 0.5 * st.time);
        // the old share of a capped 100
        let old =
            (p.ld_prior_time * b[9] + b[9] / mu[9] * (st.t_e[9] + 100.0 * st.t_cens[9])) / (p.ld_prior_time + st.time);
        assert!(old < 0.7 * b[9], "{old}");
        step(&mut bg, &mut l, b, mu, f64::INFINITY, p);
        let learned = bg.b(0, p);
        assert!(near(&learned, &b, 0.1), "{learned:?}");
    }

    #[test]
    fn no_background_is_learned_while_something_is_seen_or_an_echo_source_holds_the_energy() {
        let (mut bg, w) = background();
        let p = &w.params;
        let b = prior(p);
        let mut l = em(frames(&times(3.0, &b), 600.0, 1, &alpha(p)), 0.0);
        step(&mut bg, &mut l, b, b, f64::INFINITY, p);
        assert!(near(&bg.b(0, p), &b, 1e-12));
        // the same frames with a target of the LD2450 in sight
        let mut seen = Stats::default();
        seen.add(&times(3.0, &b), 600.0, true);
        step(&mut bg, &mut em(seen, 1.0), b, b, f64::INFINITY, p);
        assert!(near(&bg.b(0, p), &b, 1e-12));
        l.echo_off = 1.0;
        step(&mut bg, &mut l, b, b, f64::INFINITY, p);
        assert!(bg.b(0, p)[0] > 1.5 * b[0]);
    }

    #[test]
    fn what_is_not_learned_goes_back_to_the_sensors_own_level() {
        // test_learning: the background wanders around its sensor's own mean (MODEL.md 4.3). The
        // bedroom's sensor gives 9 where the prior says 5; a night with somebody in sight of its LD2450
        // teaches nothing, and until 0.27 the level went back to the prior's 5 by the morning - the
        // empty room's 9 then held an unknown person there (MODEL.md 10)
        let (mut bg, w) = background();
        let p = &w.params;
        let (b, alpha) = (prior(p), alpha(p));
        for hour in 0..6 {
            // an empty day: 1.8 times the prior
            let now = bg.b(0, p);
            step(
                &mut bg,
                &mut em(frames(&times(1.8, &b), 3600.0, hour, &alpha), 1.0),
                now,
                now,
                f64::INFINITY,
                p,
            );
        }
        // the pull towards the anchor is the sensor's own level, not the prior (until 0.27 19 % of the
        // prior with 0.7 h of forgetting)
        assert!(near(&bg.b(0, p), &times(1.8, &b), 0.05));
        // somebody in sight of the LD2450 all night: nothing learned
        let mut night = Stats::default();
        night.add(&times(3.0, &b), 600.0, true);
        let mut night = em(night, 1.0);
        for _ in 0..9 * 6 {
            let now = bg.b(0, p);
            step(&mut bg, &mut night, now, now, f64::INFINITY, p);
        }
        assert!(near(&bg.b(0, p), &times(1.8, &b), 0.05));
        // a level learned for an hour (somebody the LD2450 did not see) goes back towards the sensor's
        // own, which holds that hour with its share of the day (1 of 7 h here)
        let now = bg.b(0, p);
        step(
            &mut bg,
            &mut em(frames(&times(4.0, &b), 3600.0, 9, &alpha), 1.0),
            now,
            now,
            f64::INFINITY,
            p,
        );
        assert!(bg.b(0, p).iter().zip(&b).all(|(x, y)| *x > 3.0 * y));
        for _ in 0..9 * 6 {
            let now = bg.b(0, p);
            step(&mut bg, &mut night, now, now, f64::INFINITY, p);
        }
        assert!(near(&bg.b(0, p), &times((6.0 * 1.8 + 4.0) / 7.0, &b), 0.1));
    }

    #[test]
    fn the_anchor_learns_only_after_a_long_silence_of_the_ld2450() {
        // test_learning: sleepers and sitters leave the LD2450 gaps of seconds to minutes; what the
        // background learns in them does not reach the sensor's own level (the anchor), what follows
        // 10 min of silence does
        let (mut bg, w) = background();
        let p = &w.params;
        let b = prior(p);
        let mut l = em(frames(&times(3.0, &b), 3600.0, 1, &alpha(p)), 1.0);
        step(&mut bg, &mut l, b, b, 90.0, p);
        let above = |x: Cells| x.iter().zip(&b).all(|(x, y)| *x > 2.5 * y);
        assert!(above(bg.b(0, p)), "the fast level learns it");
        assert!(near(&bg.anchor(0, p), &b, 1e-12), "the anchor not");
        let now = bg.b(0, p);
        step(&mut bg, &mut l, now, now, p.ld_anchor_quiet, p);
        assert!(above(bg.anchor(0, p)));
    }

    #[test]
    fn the_echo_rate_counts_where_nobody_is_and_without_itself() {
        let (mut bg, _) = background();
        let p = Params {
            ld_echo_rate: 1e-4,
            ld_echo_prior_time: 3600.0,
            ..Params::default()
        };
        let (b, alpha) = (prior(&p), alpha(&p));
        let mut st = Stats::default();
        st.add(&b, 3600.0, false);
        let mut l = em(st, 1.0);
        l.began = 0.5;
        let learn = |bg: &mut Background, l: &LdLearn, rate| {
            let w = Weighed {
                learn: l,
                b: &b,
                rate,
                alpha: &alpha,
                quiet: 0.0,
            };
            bg.learn(0, &w, &p);
        };
        // somebody in view: nothing
        learn(&mut bg, &l, 1e-4);
        assert!((bg.rate(0, &p) / 1e-4 - 1.0).abs() < 1e-12);
        l.nobody = 1.0;
        learn(&mut bg, &l, 1e-3);
        // judged with 10 times the prior's rate: odds 1 -> 0.1 with the prior's
        let e = bg.part(0).echoes.expect("counted");
        assert!((e.began - 1.0 / 11.0).abs() < 1e-9, "{}", e.began);
        assert!((bg.rate(0, &p) - (0.36 + 1.0 / 11.0) / 7200.0).abs() < 1e-12);
    }

    #[test]
    fn a_level_without_its_time_is_dropped_on_load() {
        // test_ld2410: a sensor's num without its den (a file broken by hand): until 0.28 kept, and
        // b() failed at every LD2410C frame of it; now that sensor's level starts from the prior, its
        // anchor and the other sensor keep what they learned
        let (mut bg, w) = background();
        let p = &w.params;
        let b = prior(p);
        for si in 0..2 {
            let mut st = Stats::default();
            st.add(&times(2.0, &b), 3600.0, false);
            let mut l = em(st, 1.0);
            l.mus = vec![b];
            let alpha = alpha(p);
            let weighed = Weighed {
                learn: &l,
                b: &b,
                rate: p.ld_echo_rate,
                alpha: &alpha,
                quiet: f64::INFINITY,
            };
            bg.learn(si, &weighed, p);
        }
        let mut v = bg.to_json();
        let id0 = &w.plan.sensors[0].id;
        v["den"].as_object_mut().expect("den").remove(id0);
        let (read, _) = Background::from_json(&v, &w.plan).expect("a background");
        let (a, other) = (read.part(0), read.part(1));
        assert!(a.level.is_none() && a.echoes.is_none() && a.anchor.is_some());
        assert!(other.level.is_some() && other.echoes.is_some());
        assert_eq!(read.b(0, p), b);
        assert!(read.b(1, p).iter().all(|x| x.is_finite()));
    }
}
