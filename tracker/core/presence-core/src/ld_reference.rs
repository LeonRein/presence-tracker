//! The LD2410C's weighing against the Python filter of 16d148f, the last one weighing in Python
//! (fixtures/ld_reference.py): a small fixed case - three hypotheses, a person with a track whose
//! mixture has a part gone through a door, a known person whose amplitude was measured, another one
//! anywhere, the unknown ones - two rounds of frames of two sensors. Everything the weighing changes
//! or learns agrees to 1e-12.

use std::io::Read;

use serde_json::Value;

use crate::filter::Filter;
use crate::filter::densities::{Amp, DensityData, Kind};
use crate::filter::hyp::GroupId;
use crate::filter::mirror::{MirrorHyp, MirrorMixture, MirrorState};
use crate::filter::mixture::{Kalman, Mixture, SegId, Slot};
use crate::filter::tracks::TrackInfo;
use crate::ld2410::CELLS;
use crate::ld2410::sensor::LdCollect;
use crate::tracker::Tracker;
use crate::world::{World, WorldExport};

const TOL: f64 = 1e-12;

fn fixture() -> Value {
    let gz = include_bytes!("../fixtures/ld_reference.json.gz");
    let mut json = String::new();
    flate2::read::GzDecoder::new(&gz[..])
        .read_to_string(&mut json)
        .expect("gzip");
    serde_json::from_str(&json).expect("json")
}

fn f(v: &Value) -> f64 {
    v.as_f64().expect("a number")
}

fn fs(v: &Value) -> Vec<f64> {
    v.as_array().expect("an array").iter().map(f).collect()
}

fn us(v: &Value) -> Vec<usize> {
    v.as_array()
        .expect("an array")
        .iter()
        .map(|x| x.as_u64().expect("an index") as usize)
        .collect()
}

fn cells(v: &Value) -> [f64; CELLS] {
    fs(v).try_into().expect("16 cells")
}

fn gates(v: &Value) -> [f64; 9] {
    fs(v).try_into().expect("9 gates")
}

fn density(d: &Value) -> DensityData {
    DensityData {
        walk: fs(&d["walk"]),
        still: fs(&d["still"]),
        region: fs(&d["region"]),
        out: f(&d["out"]),
        clock: f(&d["clock"]),
        r: f(&d["r"]),
        kind: if d["intensity"].as_bool().expect("bool") {
            Kind::Intensity
        } else {
            Kind::Person
        },
        amp: (!d["amp"].is_null()).then(|| Amp {
            levels: fs(&d["amp"]),
            keep: (!d["keep"].is_null()).then(|| fs(&d["keep"])),
        }),
    }
}

fn mixture(m: &Value) -> MirrorMixture {
    let (mean, cov) = (fs(&m["mean"]), fs(&m["cov"]));
    let d = mean.len() / 4;
    let mode = |k: usize| Kalman {
        mean: mean[k * 2 * d..][..2 * d].to_vec(),
        cov: cov[k * 2 * d * d..][..2 * d * d].to_vec(),
    };
    let logw = fs(&m["logw"]);
    MirrorMixture {
        id: None,
        mixture: Mixture {
            logw: [logw[0], logw[1]],
            modes: [mode(0), mode(1)],
            stay: fs(&m["stay"]),
            ka: fs(&m["ka"]),
            slots: m["slots"]
                .as_array()
                .expect("slots")
                .iter()
                .map(|s| {
                    (
                        SegId(s[0].as_u64().expect("seg")),
                        Slot {
                            index: s[1].as_u64().expect("index") as usize,
                            var: f(&s[2]),
                        },
                    )
                })
                .collect(),
            phantom: m["phantom"].as_bool().expect("bool"),
            a: f(&m["a"]),
            away: None,
            t: m["t"].as_f64(),
        },
        away: m["away"].as_u64().map(|i| i as usize),
    }
}

fn hyp(h: &Value) -> MirrorHyp {
    let pairs = |key: &str| -> Vec<(u64, Value)> {
        h[key]
            .as_array()
            .expect(key)
            .iter()
            .map(|p| (p[0].as_u64().expect("id"), p[1].clone()))
            .collect()
    };
    MirrorHyp {
        logw: f(&h["logw"]),
        tracks: pairs("tracks")
            .into_iter()
            .map(|(s, g)| (SegId(s), g.as_u64().map(GroupId)))
            .collect(),
        ghosts: h["ghosts"]
            .as_array()
            .expect("ghosts")
            .iter()
            .map(|g| {
                (
                    SegId(g[0].as_u64().expect("seg")),
                    fs(&g[1]),
                    g[2].as_u64().expect("source") as usize,
                )
            })
            .collect(),
        people: pairs("people")
            .into_iter()
            .map(|(g, m)| (GroupId(g), m.as_u64().expect("mixture") as usize))
            .collect(),
        hidden: us(&h["hidden"]),
        unknown: h["unknown"].as_u64().expect("unknown") as usize,
    }
}

/// The tracker in the fixture's world with its state at the start.
fn start(fx: &Value) -> Tracker {
    let mut export: WorldExport = serde_json::from_value(fx["world"].clone()).expect("the world");
    export.plan = crate::testing::plan_without_rooms(&export.tiles, export.sensors.len());
    let mut tracker = Tracker::new(World::from_export(export).expect("a world"));
    let st = &fx["start"];
    for t in st["tracks"].as_array().expect("tracks") {
        let pt = |v: &Value| -> [f64; 2] { fs(v).try_into().expect("a point") };
        let info = TrackInfo {
            si: t["si"].as_u64().expect("si") as usize,
            t: f(&t["t"]),
            zt: f(&t["zt"]),
            gt: t["gt"].as_f64(),
            var: f(&t["var"]),
            z: pt(&t["z"]),
            z0: pt(&t["z0"]),
            born: f(&t["born"]),
            lost: None,
            map_odds: f(&t["map_odds"]),
        };
        tracker
            .filter_mut()
            .tracks_mut()
            .begin(SegId(t["seg"].as_u64().expect("seg")), info);
    }
    let list = |key: &str| st[key].as_array().expect(key).clone();
    tracker
        .load(MirrorState {
            densities: list("densities").iter().map(|d| (None, density(d))).collect(),
            mixtures: list("mixtures").iter().map(mixture).collect(),
            hyps: list("hyps").iter().map(hyp).collect(),
        })
        .expect("the state fits");
    let t0 = f(&fx["t0"]);
    for si in 0..2 {
        tracker.set_ld_collect(
            si,
            LdCollect {
                t: t0,
                last: None,
                target_t: t0,
            },
        );
    }
    tracker
}

#[track_caller]
fn near(what: &str, got: &[f64], want: &[f64]) {
    assert_eq!(got.len(), want.len(), "{what}: length");
    for (i, (g, w)) in got.iter().zip(want).enumerate() {
        assert!((g - w).abs() <= TOL * (1.0 + w.abs()), "{what}[{i}]: {g} against {w}");
    }
}

fn near_density(what: &str, filter: &Filter, id: crate::filter::densities::DensityId, want: &Value) {
    let got = filter.objects().densities().data(id);
    let want = density(want);
    near(&format!("{what} walk"), &got.walk, &want.walk);
    near(&format!("{what} still"), &got.still, &want.still);
    near(&format!("{what} region"), &got.region, &want.region);
    near(
        &format!("{what} out, clock, r"),
        &[got.out, got.clock, got.r],
        &[want.out, want.clock, want.r],
    );
    assert_eq!(got.kind, want.kind, "{what}: kind");
    match (&got.amp, &want.amp) {
        (Some(g), Some(w)) => near(&format!("{what} amplitude"), &g.levels, &w.levels),
        (None, None) => {}
        _ => panic!("{what}: measured on one side only"),
    }
}

/// The hypotheses and their objects against the reference's state.
fn near_state(round: usize, filter: &Filter, want: &Value) {
    let (dens, mixs) = (
        want["densities"].as_array().expect("densities"),
        want["mixtures"].as_array().expect("mixtures"),
    );
    let hyps = want["hyps"].as_array().expect("hyps");
    assert_eq!(filter.hyps().len(), hyps.len(), "round {round}: hypotheses");
    for (h, (hy, w)) in filter.hyps().iter().zip(hyps).enumerate() {
        let w = hyp(w);
        let at = format!("round {round}, hypothesis {h}");
        near(&format!("{at} logw"), &[hy.logw], &[w.logw]);
        near_density(&format!("{at} unknown"), filter, hy.unknown, &dens[w.unknown]);
        assert_eq!(hy.hidden.len(), w.hidden.len(), "{at}: known people");
        for (k, (&d, &i)) in hy.hidden.iter().zip(&w.hidden).enumerate() {
            near_density(&format!("{at} known {k}"), filter, d, &dens[i]);
        }
        for (g, i) in w.people {
            let m = *hy.people.get(&g).expect("the person");
            let got = filter.objects().mixtures().get(m);
            let want = mixture(&mixs[i]);
            let at = format!("{at} person {}", g.0);
            near(&format!("{at} logw"), &got.logw, &want.mixture.logw);
            near(&format!("{at} ka"), &got.ka, &want.mixture.ka);
            near(&format!("{at} a"), &[got.a], &[want.mixture.a]);
            for k in 0..2 {
                near(
                    &format!("{at} mean {k}"),
                    &got.modes[k].mean,
                    &want.mixture.modes[k].mean,
                );
                near(&format!("{at} cov {k}"), &got.modes[k].cov, &want.mixture.modes[k].cov);
            }
            match (got.away, want.away) {
                (Some(d), Some(j)) => near_density(&format!("{at} through the door"), filter, d, &dens[j]),
                (None, None) => {}
                _ => panic!("{at}: a part through the door on one side only"),
            }
        }
    }
}

#[test]
fn the_ld2410c_weighs_as_the_python_filter() {
    let fx = fixture();
    let mut tracker = start(&fx);
    let frames = fx["frames"].as_array().expect("frames");
    let mut next = 0;
    for (r, round) in fx["rounds"].as_array().expect("rounds").iter().enumerate() {
        let end = f(&round["t"]);
        while next < frames.len() && f(&frames[next]["t"]) <= end {
            let fr = &frames[next];
            let (moving, still) = (gates(&fr["moving"]), gates(&fr["still"]));
            let si = fr["si"].as_u64().expect("si") as usize;
            tracker.ld_frame(
                si,
                f(&fr["t"]),
                Some((&moving, &still)),
                fr["target"].as_bool().expect("bool"),
            );
            next += 1;
        }
        let weighed = round["weighed"].as_array().expect("weighed");
        let due: Vec<usize> = weighed.iter().map(|w| w["si"].as_u64().expect("si") as usize).collect();
        assert_eq!(tracker.ld_due(), due, "round {r}: the sensors due");
        for w in weighed {
            let si = w["si"].as_u64().expect("si") as usize;
            let at = format!("round {r}, sensor {si}");
            let learned = tracker
                .ld_weigh(si, &cells(&w["b"]), f(&w["rate"]))
                .expect("finite")
                .expect("frames");
            let want = &w["learned"];
            let mus: Vec<f64> = learned.mus.iter().flatten().copied().collect();
            let want_mus: Vec<f64> = want["mus"].as_array().expect("mus").iter().flat_map(fs).collect();
            near(&format!("{at} mus"), &mus, &want_mus);
            near(&format!("{at} prior_w"), &learned.prior_w, &fs(&want["prior_w"]));
            near(
                &format!("{at} echo off, began, nobody, time"),
                &[learned.echo_off, learned.began, learned.nobody, learned.stats.time],
                &[
                    f(&want["echo_off"]),
                    f(&want["began"]),
                    f(&want["nobody"]),
                    f(&want["time"]),
                ],
            );
            let ld = tracker.ld_sensor(si);
            near(&format!("{at} memory"), &ld.memory.expect("weighed"), &fs(&w["memory"]));
            near(&format!("{at} echo sources"), ld.echoes.probs(), &fs(&w["echo"]));
        }
        near_state(r, tracker.filter(), &round["state"]);
    }
    assert_eq!(next, frames.len());
}
