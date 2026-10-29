//! What is proposed: per placed sensor with walking measurements a new heading and scale, each with its
//! uncertainty, only what is determined; and the progress of the data before a solve.

use std::collections::BTreeMap;

use serde::Serialize;

use super::fit::Fit;
use super::problem::{Kind, Problem, walking_times};
use super::space::Space;
use super::{
    DETERMINED_SCALE, EVERY, JACK_GROUPS, MIN_POINTS, MIN_STRETCHES, MODEL_HEADING, MODEL_SCALE, PRIOR_DEG, Row,
    SECOND_MODE, STRETCH, angle_diff, determined_heading, median, py_floordiv, py_max, py_mod,
};
use crate::config::Config;
use crate::plan::Plan;
use crate::vmath::{pairwise_sum, py_hypot, py_round};

/// m: a walking point farther than this from what its sensor can see is out of sight
const OUT_OF_SIGHT: f64 = 0.3;
/// m: two sensors' points of a pair closer than this agree
const AGREE: f64 = 1.0;

/// Why a value is not proposed (the shell words it).
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Reason {
    /// walking data in fewer hours than needed
    Few { stretches: usize, min: usize },
    /// the new pose puts more walking points out of sight (shares before and after)
    Worse { after: f64, before: f64 },
    /// the floor plan alone and the pairs and handovers alone want other headings (deg)
    Conflict { plan: f64, pairs: f64 },
    /// another heading (deg) fits almost as well
    Ambiguous { alt: Option<f64> },
    /// the heading's uncertainty (deg)
    HeadingUncertain { sd: f64 },
    /// too few pairs with other sensors for an own scale
    NoRuler,
    /// the scale's uncertainty
    ScaleUncertain { sd: f64 },
    /// mirrored, the measurements fit better (log posterior, adjusted)
    Mirrored { gain: f64 },
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
pub struct Apply {
    pub heading: bool,
    pub scale: bool,
    /// the measurements fit the other x direction better: take `mirror` (with heading and scale)
    pub mirror: bool,
}

/// The result for one sensor (calibration.solve of 0.43.0, its reasons as Reason).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct SensorReport {
    pub id: String,
    /// what to take: the fitted value if it is determined, else the configured one
    pub heading: f64,
    pub scale: f64,
    pub mirror: bool,
    pub fitted_heading: f64,
    pub fitted_scale: f64,
    pub heading_sd: f64,
    pub scale_sd: f64,
    /// the heading of the floor plan alone and of the pairs and handovers alone
    pub heading_plan: f64,
    pub heading_pairs: f64,
    /// hours with walking data
    pub stretches: usize,
    /// the jackknife's standard deviations of heading and scale (None: too few hours)
    pub jackknife: Option<[f64; 2]>,
    /// degrees from the configured heading
    pub turn: f64,
    pub apply: Apply,
    /// "ok" (both determined), "warn" (one), "bad"
    pub quality: &'static str,
    pub reasons: Vec<Reason>,
    pub few: bool,
    /// walking more would help
    pub walk_more: bool,
    pub points: usize,
    pub tracks: usize,
    pub pairs: usize,
    pub handovers: usize,
    /// agreeing pairs with other sensors
    pub agree: i64,
    /// share of the walking points out of sight, drawn and fitted
    pub outside: [f64; 2],
    /// median distance of the agreeing simultaneous pairs, drawn and fitted
    pub pair_median: [Option<f64>; 2],
    #[serde(skip_serializing_if = "Option::is_none")]
    pub mirror_gain: Option<f64>,
}

/// The result of a solve.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(untagged)]
pub enum Report {
    /// no sensor has enough walking points
    NoWalking { error: &'static str },
    Solved {
        /// in the order of the configuration
        sensors: Vec<SensorReport>,
        /// placed sensors without enough walking points
        unsolved: Vec<String>,
        min_hours: usize,
        /// share of the walking points in sight with the fitted poses
        inside: Option<f64>,
        /// exp(mu) and tau of the LD2450 scales
        scale_prior: [f64; 2],
    },
}

/// Per sensor its walking points more than OUT_OF_SIGHT from what it can see; None without a free space.
fn out_of_sight(prob: &Problem, phi: &[f64], e: usize) -> Option<Vec<bool>> {
    let pt = &prob.points[e];
    let field = prob.space.fields[pt.si].as_ref()?;
    if pt.z.is_empty() {
        return None;
    }
    let w = prob.world(phi, pt.si, &pt.z, None);
    Some(w.iter().map(|&w| field.lookup(w).1 > OUT_OF_SIGHT).collect())
}

/// Per sensor (index into config.sensors) the share of its walking points out of sight.
fn outside(prob: &Problem, phi: &[f64]) -> BTreeMap<usize, f64> {
    (0..prob.points.len())
        .filter_map(|e| {
            let out = out_of_sight(prob, phi, e)?;
            Some((
                prob.points[e].si,
                out.iter().filter(|&&o| o).count() as f64 / out.len() as f64,
            ))
        })
        .collect()
}

/// Does the new pose put more of the sensor's walking points out of sight? Paired, point by point
/// (McNemar): of the points whose verdict changes, b go out of sight and c come into it; more out by more
/// than one standard deviation of b - c (sqrt(b + c)) counts. The points of one track are not
/// independent, so this errs on the side of keeping the old pose.
fn more_out_of_sight(prob: &Problem, phi0: &[f64], phi: &[f64], e: usize) -> bool {
    let (Some(a), Some(b)) = (out_of_sight(prob, phi0, e), out_of_sight(prob, phi, e)) else {
        return false;
    };
    let went = a.iter().zip(&b).filter(|&(&x, &y)| !x && y).count();
    let came = a.iter().zip(&b).filter(|&(&x, &y)| x && !y).count();
    went as f64 - came as f64 > ((went + came) as f64).sqrt()
}

/// Per pair of sensors (simultaneous pairs only): the median distance of those within AGREE m.
fn pair_agreement(prob: &Problem, phi: &[f64]) -> Vec<(usize, usize, Option<f64>)> {
    prob.pairs
        .iter()
        .filter(|q| q.kind == Kind::Pair)
        .map(|q| {
            let (wa, wb) = (prob.world(phi, q.a, &q.za, None), prob.world(phi, q.b, &q.zb, None));
            let good: Vec<f64> = wa
                .iter()
                .zip(&wb)
                .map(|(&a, &b)| (a - b).abs())
                .filter(|&r| r < AGREE)
                .collect();
            (q.a, q.b, (!good.is_empty()).then(|| median(&good)))
        })
        .collect()
}

/// The hour of a time.
fn hour(t: f64) -> i64 {
    py_floordiv(t, STRETCH) as i64
}

/// The hours with walking data, merged into at most JACK_GROUPS groups of consecutive hours with about
/// equal numbers of walking points: (first hour, last hour).
fn stretch_groups(prob: &Problem) -> Vec<(i64, i64)> {
    let mut hours: BTreeMap<i64, usize> = BTreeMap::new();
    for t in prob.points.iter().flat_map(|pt| &pt.t) {
        *hours.entry(hour(*t)).or_default() += 1;
    }
    let keys: Vec<i64> = hours.keys().copied().collect();
    let total: usize = hours.values().sum();
    let (mut groups, mut acc) = (Vec::new(), 0);
    let Some(&first) = keys.first() else {
        return groups;
    };
    let mut first = first;
    for (i, h) in keys.iter().enumerate() {
        acc += hours[h];
        if acc as f64 >= (total * (groups.len() + 1)) as f64 / JACK_GROUPS as f64 || i == keys.len() - 1 {
            groups.push((first, *h));
            if let Some(&next) = keys.get(i + 1) {
                first = next;
            }
        }
    }
    groups
}

/// Delete-one-group jackknife standard deviations of heading (deg) and scale per fitted sensor.
fn jackknife(prob: &Problem, phi: &[f64], groups: &[(i64, i64)]) -> BTreeMap<usize, (f64, f64)> {
    let reps: Vec<Vec<f64>> = groups
        .iter()
        .map(|&(lo, hi)| {
            let weights = move |t: f64| f64::from(u8::from(hour(t) < lo || hour(t) > hi));
            Fit::new(prob, true, true, Some(&weights)).run(Some(phi), false)
        })
        .collect();
    let k = reps.len() as f64;
    let spread = |v: &[f64]| {
        let mean = pairwise_sum(v) / v.len() as f64;
        let sq: Vec<f64> = v.iter().map(|x| (x - mean).powi(2)).collect();
        ((k - 1.0) / k * pairwise_sum(&sq)).sqrt()
    };
    prob.fit
        .iter()
        .map(|&si| {
            let i = prob.slot(si).unwrap_or_default();
            let pi = std::f64::consts::PI;
            let dh: Vec<f64> = reps.iter().map(|r| py_mod(r[i] - phi[i] + pi, 2.0 * pi) - pi).collect();
            let dk: Vec<f64> = reps.iter().map(|r| r[i + 1].exp()).collect();
            (si, (spread(&dh).to_degrees(), spread(&dk)))
        })
        .collect()
}

/// The rows per sensor id that the calibration takes: of sensors of the configuration, in time order.
fn sensors_of(
    config: &Config,
    data: &BTreeMap<String, Vec<Row>>,
    keep: impl Fn(usize) -> bool,
) -> Vec<(usize, Vec<Row>)> {
    data.iter()
        .filter_map(|(id, rows)| {
            let si = config.sensors.iter().position(|s| &s.id == id)?;
            if !keep(si) || rows.is_empty() {
                return None;
            }
            let mut rows = rows.clone(); // sorted here, the shell's stay as gathered
            rows.sort_by(|a, b| a.t.total_cmp(&b.t));
            Some((si, rows))
        })
        .collect()
}

/// New heading and scale for every placed sensor with walking measurements, each with its uncertainty;
/// only what is determined is proposed. Positions and mirrors stay as configured (check_mirror: also fit
/// each sensor mirrored and report which the measurements prefer).
///
/// Per sensor, a value is proposed when its uncertainty (robust covariance and the model error) is below
/// DETERMINED_*, the heading has no second mode, and the floor plan alone and the pairs and handovers alone
/// give the same heading: if they don't, something in the drawing is off (the sensor's position, the walls
/// around it), and no heading or scale fits both. And only if the sensor has walking data from at least
/// MIN_STRETCHES hours (its uncertainty then includes the jackknife over them), and the new pose doesn't put
/// more of its walking points out of sight.
pub fn solve(config: &Config, data: &BTreeMap<String, Vec<Row>>, check_mirror: bool) -> Report {
    let placed: Vec<usize> = (0..config.sensors.len())
        .filter(|&si| config.sensors[si].placed && config.sensors[si].enabled)
        .collect();
    let data = sensors_of(config, data, |si| placed.contains(&si));
    let fit: Vec<usize> = placed
        .iter()
        .copied()
        .filter(|&si| {
            data.iter()
                .find(|(s, _)| *s == si)
                .is_some_and(|(_, rows)| walking_times(config, si, rows).len() >= MIN_POINTS)
        })
        .collect();
    if fit.is_empty() {
        return Report::NoWalking { error: "no_walking" };
    }
    let plan = Plan::new(config);
    let with: Vec<usize> = data.iter().map(|(si, _)| *si).collect();
    let space = Space::new(config, &plan, &with);
    let prob = Problem::new(config, &space, &data, fit.clone(), &[]);
    let mut joint = Fit::new(&prob, true, true, None);
    let phi = joint.run(None, true);
    // the two kinds of evidence alone: the floor plan at the scales of the joint fit (it doesn't judge
    // scales), the pairs and handovers alone
    let hold = fit.iter().map(|&si| (si, prob.pose(&phi, si, None).0.abs())).collect();
    let mut alone_plan = Fit::new(&prob, false, true, None).holding(hold);
    alone_plan.run(Some(&phi), true);
    let mut alone_pairs = Fit::new(&prob, true, false, None);
    alone_pairs.run(Some(&phi), true);
    let phi0 = prob.start();
    let (before, after) = (outside(&prob, &phi0), outside(&prob, &phi));
    let (agree0, agree1) = (pair_agreement(&prob, &phi0), pair_agreement(&prob, &phi));
    let groups = stretch_groups(&prob);
    let jack = if groups.len() >= MIN_STRETCHES {
        jackknife(&prob, &phi, &groups)
    } else {
        BTreeMap::new()
    };
    let mut sensors: Vec<SensorReport> = fit
        .iter()
        .map(|&si| {
            let s = &config.sensors[si];
            let e = prob.points.iter().position(|pt| pt.si == si).unwrap_or_default();
            let pt = &prob.points[e];
            let (heading, scale) = prob.describe(&phi, si);
            let sd = joint.sd(si);
            let (jh, jk) = jack.get(&si).copied().unwrap_or((0.0, 0.0));
            let sd_h = py_hypot(py_max(sd.heading, jh), MODEL_HEADING);
            let sd_k = py_hypot(py_max(sd.scale, jk), MODEL_SCALE);
            let mut per_hour: BTreeMap<i64, usize> = BTreeMap::new();
            for &t in &pt.t {
                *per_hour.entry(hour(t)).or_default() += 1;
            }
            let stretches = per_hour.values().filter(|&&n| n >= MIN_POINTS).count();
            let few = stretches < MIN_STRETCHES;
            let worse = more_out_of_sight(&prob, &phi0, &phi, e);
            let (alt, gap) = joint.second_mode(si);
            let (h_plan, h_pair) = (
                prob.describe(&alone_plan.phi, si).0,
                prob.describe(&alone_pairs.phi, si).0,
            );
            let sd_plan = py_hypot(alone_plan.sd(si).heading, MODEL_HEADING);
            let sd_pair = py_hypot(alone_pairs.sd(si).heading, MODEL_HEADING);
            let conflict = sd_plan < PRIOR_DEG / 2.0
                && sd_pair < PRIOR_DEG / 2.0
                && angle_diff(h_plan, h_pair).abs() > 2.0 * py_hypot(sd_plan, sd_pair);
            let good_heading = sd_h <= determined_heading() && gap >= SECOND_MODE && !conflict && !few && !worse;
            let ruler = joint.rulers.contains(&si);
            let good_scale = sd_k <= DETERMINED_SCALE && ruler && !conflict && !few && !worse;
            // per sensor only what is its own; what holds for all (why 3 hours, walk more) the web UI says once
            let mut reasons = Vec::new();
            let mut walk_more = false;
            if few {
                reasons.push(Reason::Few {
                    stretches,
                    min: MIN_STRETCHES,
                });
            } else if worse {
                reasons.push(Reason::Worse {
                    after: after.get(&si).copied().unwrap_or_default(),
                    before: before.get(&si).copied().unwrap_or_default(),
                });
            } else if conflict {
                reasons.push(Reason::Conflict {
                    plan: h_plan,
                    pairs: h_pair,
                });
            } else {
                if !good_heading {
                    reasons.push(if gap < SECOND_MODE {
                        Reason::Ambiguous { alt }
                    } else {
                        Reason::HeadingUncertain { sd: sd_h }
                    });
                }
                if !ruler {
                    reasons.push(Reason::NoRuler);
                } else if !good_scale {
                    reasons.push(Reason::ScaleUncertain { sd: sd_k });
                }
                walk_more = !reasons.is_empty();
            }
            let medians = |agree: &[(usize, usize, Option<f64>)]| {
                let mine: Vec<f64> = agree
                    .iter()
                    .filter(|&&(a, b, _)| a == si || b == si)
                    .filter_map(|&(_, _, m)| m)
                    .collect();
                (!mine.is_empty()).then(|| py_round(median(&mine), 3))
            };
            let count = |kind: Kind| -> usize {
                prob.pairs
                    .iter()
                    .filter(|q| q.kind == kind && (q.a == si || q.b == si))
                    .map(|q| q.za.len())
                    .sum()
            };
            SensorReport {
                id: s.id.clone(),
                heading: if good_heading { heading } else { s.heading },
                scale: if good_scale { scale } else { s.scale },
                mirror: s.mirror,
                fitted_heading: heading,
                fitted_scale: scale,
                heading_sd: py_round(sd_h, 2),
                scale_sd: py_round(sd_k, 4),
                heading_plan: h_plan,
                heading_pairs: h_pair,
                stretches,
                jackknife: (!jack.is_empty()).then(|| [py_round(jh, 2), py_round(jk, 4)]),
                turn: py_round(angle_diff(heading, s.heading), 1),
                apply: Apply {
                    heading: good_heading,
                    scale: good_scale,
                    mirror: false,
                },
                quality: match (good_heading, good_scale) {
                    (true, true) => "ok",
                    (true, false) | (false, true) => "warn",
                    (false, false) => "bad",
                },
                reasons,
                few,
                walk_more,
                points: pt.z.len(),
                tracks: pt.ntracks,
                pairs: count(Kind::Pair),
                handovers: count(Kind::Handover),
                agree: joint.agree[si].round_ties_even() as i64,
                outside: [
                    py_round(before.get(&si).copied().unwrap_or_default(), 3),
                    py_round(after.get(&si).copied().unwrap_or_default(), 3),
                ],
                pair_median: [medians(&agree0), medians(&agree1)],
                mirror_gain: None,
            }
        })
        .collect();
    if check_mirror {
        for (r, &si) in sensors.iter_mut().zip(&fit) {
            let flipped = !config.sensors[si].mirror;
            let other = Problem::new(config, &space, &data, fit.clone(), &[(si, flipped)]);
            let mut mirrored = Fit::new(&other, true, true, None);
            mirrored.run(None, true);
            let gain = (mirrored.ll - joint.ll) / joint.inflation(si);
            r.mirror_gain = Some(py_round(gain, 1));
            if gain > SECOND_MODE {
                let (heading, scale) = other.describe(&mirrored.phi, si);
                r.mirror = flipped;
                r.heading = heading;
                r.scale = scale;
                r.turn = py_round(angle_diff(heading, config.sensors[si].heading), 1);
                r.apply = Apply {
                    heading: true,
                    scale: true,
                    mirror: true,
                };
                r.reasons.insert(0, Reason::Mirrored { gain });
            }
        }
    }
    let inside: Vec<f64> = fit.iter().filter_map(|si| after.get(si)).map(|a| 1.0 - a).collect();
    let (mu, tau) = joint.scale_prior();
    Report::Solved {
        sensors,
        unsolved: placed
            .iter()
            .filter(|si| !fit.contains(si))
            .map(|&si| config.sensors[si].id.clone())
            .collect(),
        min_hours: MIN_STRETCHES,
        inside: (!inside.is_empty()).then(|| py_round(pairwise_sum(&inside) / inside.len() as f64, 3)),
        scale_prior: [py_round(mu.exp(), 3), py_round(tau, 3)],
    }
}

/// The progress of the data before a solve: per sensor of the configuration its walking measurements
/// ("frames"), its walking points as solve counts them ("points") and its hours with walking data
/// ("hours", a proposal needs "min_hours"), and per pair of sensors the seconds in which each had exactly
/// one moving target ("pairs", "a|b").
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Status {
    pub frames: BTreeMap<String, usize>,
    pub points: BTreeMap<String, usize>,
    pub hours: BTreeMap<String, usize>,
    pub min_hours: usize,
    pub pairs: BTreeMap<String, usize>,
    /// per pair how far it is for the display: "ok" from PAIRS_GOOD seconds, "warn" from PAIRS_SOME, else ""
    pub pair_grades: BTreeMap<String, &'static str>,
    /// the sensors with walking data from min_hours hours: a proposal is made for them
    pub ready: Vec<String>,
}

/// s of pairs that the display shows as plenty, as a start (a minute: about two rulers, MIN_RULER; 15 s)
const PAIRS_GOOD: usize = 60;
const PAIRS_SOME: usize = 15;

pub fn status(config: &Config, data: &BTreeMap<String, Vec<Row>>) -> Status {
    let mut out = Status {
        frames: BTreeMap::new(),
        points: BTreeMap::new(),
        hours: BTreeMap::new(),
        min_hours: MIN_STRETCHES,
        pairs: BTreeMap::new(),
        pair_grades: BTreeMap::new(),
        ready: Vec::new(),
    };
    let mut seconds: Vec<(&str, Vec<f64>)> = Vec::new();
    for (id, rows) in data.iter().filter(|(_, rows)| !rows.is_empty()) {
        let Some(si) = config.sensors.iter().position(|s| &s.id == id) else {
            continue;
        };
        out.frames.insert(id.clone(), rows.len());
        let times = walking_times(config, si, rows);
        out.points.insert(id.clone(), times.len());
        let mut per_hour: BTreeMap<i64, usize> = BTreeMap::new();
        for &t in &times {
            *per_hour.entry(hour(t)).or_default() += 1;
        }
        let hours = per_hour.values().filter(|&&n| n >= MIN_POINTS).count();
        out.hours.insert(id.clone(), hours);
        if hours >= MIN_STRETCHES {
            out.ready.push(id.clone());
        }
        let mut s: Vec<f64> = rows
            .iter()
            .filter(|r| r.single)
            .map(|r| (r.t / EVERY).floor())
            .collect();
        s.sort_by(f64::total_cmp);
        s.dedup();
        seconds.push((id, s));
    }
    for (k, (a, sa)) in seconds.iter().enumerate() {
        for (b, sb) in &seconds[k + 1..] {
            let n = sa
                .iter()
                .filter(|x| sb.binary_search_by(|y| y.total_cmp(x)).is_ok())
                .count();
            if n > 0 {
                let grade = if n >= PAIRS_GOOD {
                    "ok"
                } else if n >= PAIRS_SOME {
                    "warn"
                } else {
                    ""
                };
                out.pairs.insert(format!("{a}|{b}"), n);
                out.pair_grades.insert(format!("{a}|{b}"), grade);
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing::{config_json, wall};

    /// Two rooms 5 x 4 m side by side, a door in the wall between them; sensor "b" in the far corner of the
    /// right one, at chest height (no slant: its raw targets are its floor points).
    fn two_rooms() -> Config {
        let room = |id: &str, x0: f64, x1: f64| {
            format!(
                r#"{{"id": "{id}", "name": "{id}", "kind": "room", "shape": "rect", "points": [[{x0}, 0], [{x1}, 4]],
                "anchor": [{}, 2], "entry": false}}"#,
                (x0 + x1) / 2.0
            )
        };
        let walls = [
            wall([0.0, 0.0], [10.0, 0.0]),
            wall([10.0, 0.0], [10.0, 4.0]),
            wall([10.0, 4.0], [0.0, 4.0]),
            wall([0.0, 4.0], [0.0, 0.0]),
            wall([5.0, 0.0], [5.0, 4.0]),
        ];
        let json = config_json(
            &[("b", "", [9.95, 0.05], 145.0)],
            &format!("[{}, {}]", room("links", 0.0, 5.0), room("rechts", 5.0, 10.0)),
            &format!("[{}]", walls.join(", ")),
            r#"[{"id": "tuer", "x": 5.0, "y": 2.5, "width": 1.0}]"#,
        )
        .replace(r#""height": 1.5"#, r#""height": 1.0"#);
        Config::from_json(&json).expect("a configuration")
    }

    #[test]
    fn no_pose_that_puts_more_walking_out_of_sight() {
        let config = two_rooms();
        // a walk around the right room, a point every 0.5 s, as the sensor measures it
        let path = [[6.0, 1.0], [9.0, 1.0], [9.0, 3.0], [6.0, 3.0], [6.0, 1.0]];
        let mount = config.sensors[0].mount();
        let mut rows = Vec::new();
        for leg in path.windows(2) {
            for k in 0..20 {
                let f = f64::from(k) / 20.0;
                let p = [
                    leg[0][0] + f * (leg[1][0] - leg[0][0]),
                    leg[0][1] + f * (leg[1][1] - leg[0][1]),
                ];
                rows.push(Row {
                    t: rows.len() as f64 * 0.5,
                    seg: 1.0,
                    local: mount.to_local(p),
                    v: 0.5,
                    single: true,
                });
            }
        }
        let data = vec![(0, rows)];
        let plan = Plan::new(&config);
        let space = Space::new(&config, &plan, &[0]);
        let prob = Problem::new(&config, &space, &data, vec![0], &[]);
        let right = prob.start();
        assert_eq!(outside(&prob, &right)[&0], 0.0, "all in sight as drawn");
        let mut turned = right.clone();
        turned[0] += 25.0_f64.to_radians(); // its walk into the neighbouring room and the wall
        assert!(more_out_of_sight(&prob, &right, &turned, 0));
        assert!(!more_out_of_sight(&prob, &turned, &right, 0));
        assert!(!more_out_of_sight(&prob, &right, &right, 0));
    }
}
