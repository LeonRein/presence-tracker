//! The filter's bookkeeping (test_hypotheses, MODEL.md 5.1, 5.6) and its events, with the
//! invariants checked after each of them.

use super::branch::{Category, Found, Frame};
use super::densities::{DensityData, DensityId, Kind};
use super::hyp::{Ghost, GroupId, Hyp, Owner};
use super::mixture::{NewTrack, SegId, ghost_source};
use super::step::Observed;
use super::tracks::TrackInfo;
use super::*;
use crate::params::LostKind;
use crate::sensors::{SensorSet, SensorState};
use crate::testing;
use crate::world::World;

fn shape(world: &World) -> DensityShape {
    DensityShape {
        tiles: world.tiles.n,
        kinds: world.shapes.kinds(),
        levels: world.shapes.levels(),
        regions: world.tiles.regions,
        ages: world.tiles.ages,
        amps: world.shapes.amps(),
    }
}

/// Somebody standing anywhere in the room alike (the unknown ones: with `people` expected).
fn anywhere(world: &World, kind: Kind, people: f64) -> DensityData {
    let s = shape(world);
    let mut still = vec![0.0; s.still_len()];
    let per = people / s.still_len() as f64;
    still.fill(per);
    DensityData {
        walk: vec![0.0; s.tiles],
        still,
        region: vec![0.0; s.region_len()],
        out: 0.0,
        clock: 0.0,
        r: 1.0,
        kind,
        amp: None,
    }
}

/// A filter with one hypothesis: the unknown ones (one expected) and these known people.
fn filter(world: &World, known: usize) -> (Filter, Vec<DensityId>) {
    let mut f = Filter::new(shape(world), world.sights.len());
    let densities = &mut f.objects_mut().densities;
    let unknown = densities.insert(anywhere(world, Kind::Intensity, 1.0));
    let hidden: Vec<DensityId> = (0..known)
        .map(|_| densities.insert(anywhere(world, Kind::Person, 1.0)))
        .collect();
    f.set_hyps(vec![Hyp::new(0.0, hidden.clone(), unknown)]);
    f.check().expect("a filter as made");
    (f, hidden)
}

#[test]
fn objects_nobody_holds_are_released() {
    let world = testing::flat();
    let (mut f, hidden) = filter(&world, 2);
    assert_eq!(f.objects().densities.len(), 3);
    let mut hy = f.hyps()[0].clone(); // the hypothesis without its second person
    hy.hidden.pop();
    f.set_hyps(vec![hy]);
    f.check().expect("released");
    assert_eq!(f.objects().densities.len(), 2);
    assert!(!f.objects().densities.contains(hidden[1]));
}

#[test]
fn alternatives_alike_are_merged_before_the_cut() {
    // test_hypotheses: one strong child without the person, thirty weak ones (5e-8 each) with
    // them: alone each is below the cut (1e-7), together they are 1.5e-6 - the person stays with r
    // = their share
    let world = testing::flat();
    let (mut f, hidden) = filter(&world, 1);
    let parent = f.hyps()[0].clone(); // the children's parent
    let n = 30;
    let mut children = vec![Hyp {
        hidden: Vec::new(),
        ..parent.child(0.0)
    }];
    children.extend((0..n).map(|_| parent.child(5e-8f64.ln())));
    f.take(children, &world.params).expect("finite");
    f.check().expect("taken");
    assert_eq!(f.hyps().len(), 1);
    let u = f.hyps()[0].hidden[0];
    assert_ne!(u, hidden[0]);
    let r = f.objects().densities.get(u).r;
    let want = n as f64 * 5e-8 / (1.0 + n as f64 * 5e-8);
    assert!((r - want).abs() < 1e-12, "{r}");
}

#[test]
fn the_cut_drops_at_most_its_mass_and_the_evidence_counts_it() {
    // test_hypotheses: from the weakest up while the dropped ones weigh at most 1e-7: 1e-9 and one
    // 6e-8; the evidence of this step is all the mass, of which the dropped part is reported
    let world = testing::flat();
    let (mut f, _) = filter(&world, 0);
    let parent = f.hyps()[0].clone(); // the children's parent
    let w: [f64; 5] = [1.0, 6e-8, 6e-8, 6e-8, 1e-9];
    let children = w
        .iter()
        .enumerate()
        .map(|(k, &wk)| {
            let mut ch = parent.child(wk.ln());
            ch.tracks.insert(SegId(k as u64), Owner::Ghost); // each says something else: nothing to merge
            ch
        })
        .collect();
    f.set_hyps(children);
    let (before, _) = f.loglik();
    f.prune(&world.params).expect("finite");
    let top = f.hyps()[0].logw;
    let mut kept: Vec<f64> = f
        .hyps()
        .iter()
        .map(|h| ((h.logw - top).exp() * 1e12).round() / 1e12)
        .collect();
    kept.sort_by(f64::total_cmp);
    assert_eq!(kept, [6e-8, 6e-8, 1.0]);
    let sum: f64 = f.hyps().iter().map(|h| h.logw.exp()).sum();
    assert!((sum - 1.0).abs() < 1e-12);
    let total: f64 = w.iter().sum();
    let (after, cut) = f.loglik();
    assert!((after - before - total.ln()).abs() < 1e-12);
    assert!((cut - (-(-6.1e-8 / total).ln_1p())).abs() < 1e-15);
}

#[test]
fn a_nan_weight_is_no_weight() {
    let world = testing::flat();
    let (mut f, _) = filter(&world, 0);
    let mut hy = f.hyps()[0].clone(); // the same hypothesis with a NaN weight
    hy.logw = f64::NAN;
    f.set_hyps(vec![hy]);
    assert!(f.normalize(0.0).is_err());
    assert!(f.cut(&world.params).is_err());
}

#[test]
fn the_people_without_a_track_are_paired_by_their_distance() {
    let world = testing::flat();
    let (mut f, hidden) = filter(&world, 3);
    let objects = f.objects_mut();
    for (k, &u) in hidden.iter().enumerate() {
        let d = objects.densities.get_mut(u);
        d.still.fill(0.0);
        d.still[k] = 1.0; // each in its own spot
    }
    let other = [hidden[2], hidden[0], hidden[1]];
    assert_eq!(objects.pair(&hidden, &other), hidden);
    // all alike: the first permutation
    let same = [hidden[0]; 3];
    assert_eq!(objects.pair(&same, &other), other);
}

/// The world's two sensors, both watching since t.
fn watching(t: f64) -> Vec<SensorState> {
    vec![
        SensorState {
            active: true,
            last_frame: t,
        };
        2
    ]
}

fn observed(world: &World) -> Observed {
    let mut o = Observed::default();
    o.insert([0, 1].into_iter().collect::<SensorSet>(), vec![1.0; world.tiles.n]);
    o
}

fn frame(t: f64, skip: &[SegId]) -> Frame<'_> {
    Frame {
        si: 0,
        t,
        last: 0.089,
        censored: false,
        skip,
        prior_rate: 1.8e-5,
    }
}

#[test]
fn a_track_through_its_events_keeps_the_books() {
    // a track begins on somebody standing in the room, is measured, lost, held, found again and
    // ends; after each event the holders and the objects fit the hypotheses
    let world = testing::flat();
    let (mut f, _) = filter(&world, 1);
    let (seen, mut t) = (observed(&world), 1.0);
    f.set_flushed(t);
    let seg = SegId(7);
    let z = [2.0, 2.0];
    let found = Found {
        track: NewTrack { seg, z, var: 0.04 },
        map_rate: 1.8e-5,
    };
    let cats = f.branch_all(&world, &frame(t, &[seg]), &[found]).expect("finite");
    f.check().expect("after a branch");
    let cats: Vec<Category> = cats[0].iter().map(|&(_, c)| c).collect();
    assert_eq!(cats, [Category::Ghost, Category::Known, Category::New]);
    assert!(f.hyps().len() >= 2);
    for step in 0..3 {
        t += 0.3;
        f.flush(&world, &watching(t), &seen, t);
        f.measured(&world, &watching(t), seg, [2.0 + 0.05 * step as f64, 2.0], t);
        f.normalize(0.0).expect("finite");
        f.check().expect("after a measurement");
    }
    t += 0.1;
    f.lost(seg, LostKind::Coast, [2.1, 2.0], t);
    t += 0.5;
    f.flush(&world, &watching(t), &seen, t);
    f.held(&world, 0, seg, [2.1, 2.0], t);
    f.normalize(0.0).expect("finite");
    f.check().expect("after holding");
    t += 0.2;
    f.flush(&world, &watching(t), &seen, t);
    let again = Found {
        track: NewTrack {
            seg,
            z: [2.15, 2.0],
            var: 0.0,
        },
        map_rate: 0.0,
    };
    let cats = f.branch_all(&world, &frame(t, &[]), &[again]).expect("finite");
    f.check().expect("found again");
    assert!(cats[0].iter().any(|&(_, c)| c == Category::Owner));
    assert!(f.tracks().get(seg).is_some_and(|i| i.lost.is_none() && i.var == 0.04));
    t += 0.5;
    f.flush(&world, &watching(t), &seen, t);
    let ended = f.end(&world, seg, t, false);
    f.merge();
    f.normalize(0.0).expect("finite");
    f.check().expect("after the end");
    assert!(f.tracks().is_empty());
    assert!(0.0 <= ended.p_ghost && ended.p_ghost < 0.5 && ended.z0 == z);
    assert!(f.hyps().iter().all(|h| h.people.is_empty()));
    // somebody known again, without a track
    assert!(f.hyps().iter().any(|h| !h.hidden.is_empty()));
    assert!(f.recycle(world.params.give_up) || f.check().is_ok());
    f.check().expect("after giving back");
}

#[test]
fn a_track_whose_ghost_alternative_is_all_but_gone_ends() {
    // test_hypotheses: no live track loses an alternative, so "ghost" can weigh 1e-310 next to
    // "person" when the track ends; taking the map's factor out of those odds stays finite
    let world = testing::flat();
    let (mut f, hidden) = filter(&world, 1);
    let seg = SegId(3);
    let track = NewTrack {
        seg,
        z: [3.0, 2.5],
        var: 0.04,
    };
    let info = TrackInfo {
        si: 0,
        t: 1.0,
        zt: 1.0,
        gt: None,
        var: 0.04,
        z: track.z,
        z0: track.z,
        born: 1.0,
        lost: None,
        map_odds: 5.0,
    };
    f.tracks_mut().begin(seg, info);
    let objects = f.objects_mut();
    let source = objects
        .mixtures
        .insert(ghost_source(track, &world.params, &world.shapes));
    let mut person = ghost_source(track, &world.params, &world.shapes);
    person.phantom = false;
    let person = objects.mixtures.insert(person);
    let parent = f.hyps()[0].clone(); // the two alternatives' parent
    let mut ghost = parent.child(0.0);
    ghost.tracks.insert(seg, Owner::Ghost);
    ghost.ghosts.insert(
        seg,
        Ghost {
            life: vec![0.0, 0.0],
            source,
        },
    );
    let mut owner = parent.child(712.0);
    owner.tracks.insert(seg, Owner::Person(GroupId(1)));
    owner.people.insert(GroupId(1), person);
    owner.hidden.retain(|&u| u != hidden[0]);
    f.set_hyps(vec![ghost, owner]);
    f.normalize(0.0).expect("finite");
    f.check().expect("two alternatives");
    let p = f.hyp_weights()[0];
    assert!(0.0 < p && p < 1e-300);
    let ended = f.end(&world, seg, 1.5, false);
    f.merge(); // the two say the same now
    f.check().expect("ended");
    assert!(f.tracks().is_empty() && f.hyps().iter().all(|h| h.tracks.is_empty()));
    assert!(0.0 <= ended.p_ghost && ended.p_ghost < 1e-300);
}
