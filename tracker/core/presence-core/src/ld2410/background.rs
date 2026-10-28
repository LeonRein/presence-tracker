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
use crate::learn::{LearnedError, Pose};
use crate::params::Params;
use crate::plan::Plan;

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

    /// A saved background (Background.load_dict), by the sensors it names; follow the plan before use.
    /// A level needs both halves; the echoes a level.
    pub fn from_json(v: &Value) -> Result<Self, LearnedError> {
        let m = object(v, "ld_background")?;
        let levels = |num_key, den_key| -> Result<Vec<(String, Level)>, LearnedError> {
            let den = formats::per_sensor(m, den_key, |_, x| number(x, den_key, 0.0))?;
            let num = formats::per_sensor(m, num_key, |id, x| {
                formats::numbers(x, num_key, CELLS, 0.0).map_err(|_| LearnedError::Size {
                    field: num_key,
                    sensor: id.to_string(),
                })
            })?;
            Ok(num
                .into_iter()
                .filter_map(|(id, n)| {
                    let &(_, d) = den.iter().find(|(k, _)| *k == id)?;
                    Some((
                        id,
                        Level {
                            num: n.try_into().expect("CELLS numbers"),
                            den: d,
                        },
                    ))
                })
                .collect())
        };
        let (level, anchor) = (levels("num", "den")?, levels("num_a", "den_a")?);
        let seconds = |name| formats::per_sensor(m, name, |_, x| number(x, name, 0.0));
        let (began, quiet, held) = (seconds("echoes")?, seconds("quiet")?, seconds("held")?);
        let of = |list: &[(String, f64)], id: &str| list.iter().find(|(k, _)| k == id).map(|&(_, x)| x);
        let ld_of = |list: &[(String, Level)], id: &str| list.iter().find(|(k, _)| k == id).map(|&(_, l)| l);
        let parts = formats::per_sensor(m, "poses", |_, x| ld_pose(x))?
            .into_iter()
            .map(|(id, pose)| {
                let level = ld_of(&level, &id);
                let echoes = of(&quiet, &id).filter(|_| level.is_some()).map(|q| EchoCount {
                    began: of(&began, &id).unwrap_or(0.0),
                    quiet: q,
                });
                Part {
                    anchor: ld_of(&anchor, &id),
                    held: of(&held, &id).unwrap_or(0.0),
                    level,
                    echoes,
                    pose,
                    id,
                }
            })
            .collect();
        Ok(Self { parts, of: Vec::new() })
    }
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
        bg.hold(2.0);
        let v = bg.to_json();
        let mut back = Background::from_json(&v).expect("readable");
        back.follow(&w.plan);
        assert_eq!(back.to_json(), v);
        assert!((back.b(0, p)[3] - bg.b(0, p)[3]).abs() < 1e-3);
        let id = w.plan.sensors[0].id.clone();
        let mut bad = v.clone();
        bad["num"][&id] = json!([1.0, 2.0]);
        assert_eq!(
            Background::from_json(&bad),
            Err(LearnedError::Size {
                field: "num",
                sensor: id.clone()
            })
        );
        let mut bad = v;
        bad["den"][&id] = json!(f64::NAN);
        assert_eq!(Background::from_json(&bad), Err(LearnedError::Field("den")));
        // a part mounted elsewhere starts over, a recalibrated one keeps it
        let mut plan = testing::flat().plan;
        plan.sensors[0].pose.mirror = true;
        back.follow(&plan);
        assert!(back.part(0).level.is_some());
        plan.sensors[0].pose.heading += 45.0;
        back.follow(&plan);
        assert!(back.part(0).level.is_none() && back.part(0).held == 2.0);
    }
}
