//! Time goes on (MODEL.md 3, 5): everybody is moved and weighed by the tracks not started meanwhile,
//! in parts at most max_step long (filter.Tracker._flush_parts, _sync_part); known people who almost
//! surely do not exist join the unknown ones (_recycle); a gap in the data is moved over (_predict_gap).

use std::borrow::Cow;

use crate::filter::Filter;
use crate::filter::densities::{Densities, DensityId, DensityMut, Kind, TileFactors, TileFactorsBuf};
use crate::filter::holders::Obj;
use crate::filter::mixture::{Mixture, MixtureId};
use crate::filter::objects::Objects;
use crate::filter::tracks::Tracks;
use crate::ld2450::rates::TrackSpot;
use crate::motion::imm::{self, ImmStep};
use crate::motion::walk::{leap, move_densities};
use crate::params::{STILL, WALK};
use crate::sensors::{SensorSet, SensorState, live};
use crate::vmath::pairwise_sum;
use crate::world::World;

/// How well the best of a set of live sensors sees each tile, 0..1 (tiling.Tiling.observed, MODEL.md
/// 5.5), per set of sensors that occurs.
#[derive(Debug, Default)]
pub struct Observed {
    sets: Vec<(SensorSet, Vec<f64>)>,
}

impl Observed {
    pub fn insert(&mut self, sensors: SensorSet, per_tile: Vec<f64>) {
        match self.sets.iter_mut().find(|(s, _)| *s == sensors) {
            Some((_, v)) => *v = per_tile,
            None => self.sets.push((sensors, per_tile)),
        }
    }

    /// Panics if the set was not given.
    pub fn get(&self, sensors: SensorSet) -> &[f64] {
        &self
            .sets
            .iter()
            .find(|(s, _)| *s == sensors)
            .expect("observed tiles of a set of live sensors")
            .1
    }
}

/// The rates (1/s) per tile at which the live sensors start a track: on walkers (n), on still people
/// at detectability 1 (n).
#[derive(Debug)]
struct TileRateSums {
    walk: Vec<f64>,
    still: Vec<f64>,
}

impl TileRateSums {
    /// The factors per tile for no track started in dt: exp(-rate dt), the still people's rate times
    /// their detectability kappa_k (per level and tile).
    fn no_track(&self, kappa: &[f64], dt: f64) -> TileFactorsBuf {
        TileFactorsBuf {
            walk: self.walk.iter().map(|rw| (-rw * dt).exp()).collect(),
            still: kappa
                .iter()
                .flat_map(|&k| self.still.iter().map(move |rs| (-(k * rs) * dt).exp()))
                .collect(),
        }
    }
}

/// The sensors' rates per tile given their live tracks (filter.Tracker._tile_rates), each computed
/// once per move of everybody: the tracks stay as they are meanwhile.
pub struct TileRates<'a> {
    world: &'a World,
    spots: &'a [Vec<TrackSpot>],
    rates: Vec<Option<Cow<'a, [Vec<f64>; 2]>>>,
}

impl<'a> TileRates<'a> {
    /// spots: per sensor where its live tracks are.
    pub fn new(world: &'a World, spots: &'a [Vec<TrackSpot>]) -> Self {
        Self {
            world,
            spots,
            rates: vec![None; spots.len()],
        }
    }

    /// The rates (1/s) per tile of a sensor [STILL, WALK] (filter.Tracker._tile_rates).
    pub fn get(&mut self, si: usize) -> &[Vec<f64>; 2] {
        let (world, spots) = (self.world, self.spots);
        self.rates[si].get_or_insert_with(|| {
            let sight = &world.sights[si];
            let spots = &spots[si];
            if spots.is_empty() {
                return Cow::Borrowed(sight.base_tiles());
            }
            let at = world
                .rates
                .masked_rates(sight, &world.tiles.points, sight.base_points(), spots);
            Cow::Owned(at.each_ref().map(|r| world.tiles.average(r)))
        })
    }

    /// The summed rates per tile of the live sensors (filter.Tracker._watching); None if no sensor
    /// watches or there are no tiles.
    fn summed(&mut self, live: SensorSet) -> Option<TileRateSums> {
        let n = self.world.tiles.n;
        if live.is_empty() || n == 0 {
            return None;
        }
        let mut sums = TileRateSums {
            walk: vec![0.0; n],
            still: vec![0.0; n],
        };
        for si in live.iter() {
            let r = self.get(si);
            for (sum, x) in sums
                .walk
                .iter_mut()
                .zip(&r[WALK])
                .chain(sums.still.iter_mut().zip(&r[STILL]))
            {
                *sum += x;
            }
        }
        Some(sums)
    }
}

/// The number of parts of a move over `span` s, none longer than max_step.
fn parts_of(span: f64, max_step: f64) -> usize {
    (span / max_step - 1e-9).ceil() as usize
}

/// What a move reads besides the objects: the world, the sensors' state (which of them watch),
/// their live tracks, and how well the sets of live sensors see each tile.
#[derive(Clone, Copy)]
pub struct Watchers<'a> {
    pub world: &'a World,
    pub sensors: &'a [SensorState],
    pub tracks: &'a Tracks,
    pub observed: &'a Observed,
}

/// A Gaussian mixture moved with everybody: its entry in the holders, and the sensors with a
/// measuring track of the person (none of theirs starts one on them).
#[derive(Debug, Clone, Copy)]
struct Mover {
    entry: usize,
    id: MixtureId,
    measured: SensorSet,
}

/// The log factors of one round of a move, per entry of the holders (None: not weighed, no sensor
/// live).
type RoundFactors = Vec<(usize, Option<f64>)>;

/// The sensors with a measuring track of the person (none of theirs starts one on them).
fn measured_by(mix: &Mixture, tracks: &Tracks) -> SensorSet {
    mix.slots
        .keys()
        .filter(|&&s| tracks.measuring(s))
        .map(|&s| tracks.get(s).expect("a live track").si)
        .collect()
}

/// Who watches a mixture in a part of its move: the sensors' tracks, the sensors that could start one
/// on the person, and the factors per tile for its part gone through a door (None: not weighed).
struct Watch<'a> {
    spots: &'a [Vec<TrackSpot>],
    users: SensorSet,
    away: Option<TileFactors<'a>>,
}

/// One part dt of a mixture's own move (filter.Tracker._sync_part; its part gone through a door
/// already moved): the prediction, and if sensors watch, the weighing by no track started on the
/// person in dt by those without a measuring track of them (`users`) - its part gone through a door
/// with the factors per tile `away` if given, else not weighed. Returns the log factor if weighed.
fn sync_part(
    world: &World,
    mix: &mut Mixture,
    densities: &mut Densities,
    dt: f64,
    keep_away: bool,
    watch: Option<Watch>,
) -> Option<f64> {
    let walls = world.tiles.n > 0 && !mix.phantom;
    imm::predict(
        world,
        mix,
        densities,
        &ImmStep::new(&world.params, &world.shapes, dt),
        walls,
        keep_away,
    );
    let Watch { spots, users, away } = watch?;
    let rates = world
        .rates
        .mixture_rates(&world.sights, spots, users, &mix.sigma(world, walls));
    let d_away = match (mix.away, away) {
        (Some(id), Some(f)) => densities.get_mut(id).weigh_person(f),
        _ => 0.0,
    };
    Some(mix.weigh_no_track(&rates, &world.shapes.kappa, dt, d_away))
}

/// The record of a known person without a track lasts only as far as a measurement could test it
/// (filter.Tracker._expire, MODEL.md 5.5): the share of them no live sensor can see expires at
/// 1 / unseen_life - in a tile by 1 - how well the live sensors see it (`fade` per tile: exp(-dt /
/// unseen_life x that)), in the regions and out of the house whole - so r falls by r x that share.
fn expire(world: &World, d: &mut DensityMut, dt: f64, fade: &[f64]) {
    if d.kind != Kind::Person || d.r.is_nan() || *d.r <= 0.0 {
        return;
    }
    let q = -(-dt / world.params.unseen_life).exp_m1();
    let mut lost = *d.out * q;
    *d.out -= lost;
    let reg = pairwise_sum(d.region);
    if reg > 0.0 {
        for v in d.region.iter_mut() {
            *v *= 1.0 - q;
        }
        lost += reg * q;
    }
    if world.tiles.n > 0 {
        lost += d.take_in_view(fade);
    }
    if lost > 0.0 {
        *d.r *= 1.0 - lost;
        d.normalize();
    }
}

/// exp(-dt / unseen_life x (1 - how well the sensors see each tile)): what of a record lasts dt.
fn fade(world: &World, observed: &[f64], dt: f64) -> Vec<f64> {
    observed
        .iter()
        .map(|o| (-dt / world.params.unseen_life * (1.0 - o)).exp())
        .collect()
}

/// Moves everybody from t0 (the last move of everybody) to t (filter.Tracker._flush_parts): the
/// densities in parts of the move, each mixture in parts from its own time, round by round - the
/// parts gone through a door of the mixtures move with the densities (one move_densities per
/// round). The densities are weighed by no track started (the people nobody knows of by its
/// Poisson void probability), a known person's record expires where nobody could test it; a
/// mixture's part gone through a door is weighed with the factors of the move's last part, scaled
/// to its own part. Returns the log factors per round, by the holders' entries.
fn flush_parts(
    w: Watchers,
    objects: &mut Objects,
    in_view: &[(usize, DensityId)],
    moving: &[Mover],
    (t0, t): (f64, f64),
) -> Vec<RoundFactors> {
    let world = w.world;
    let max_step = world.params.max_step;
    let parts = parts_of(t - t0, max_step);
    let dt = (t - t0) / parts as f64;
    let spots = w.tracks.spots();
    let mut rates = TileRates::new(world, spots);
    let kappa = &world.shapes.kappa;
    let watch: Vec<(SensorSet, Option<TileRateSums>)> = (0..parts)
        .map(|k| {
            let at = live(w.sensors, t0 + (t - t0) * (k + 1) as f64 / parts as f64);
            (at, rates.summed(at))
        })
        .collect();
    // a mixture's part gone through a door is weighed by the rates of the move's last part over its
    // own part (the Python filter took the last part's factors to the power of the share: the same
    // up to rounding), per length of part
    let last = watch.last().and_then(|(_, r)| r.as_ref());
    let mut away_factors: Vec<(f64, TileFactorsBuf)> = Vec::new();
    // per mixture: its own time, number of parts and their length
    let own: Vec<(f64, usize, f64)> = moving
        .iter()
        .map(|m| {
            let g0 = objects.mixtures.get(m.id).t.unwrap_or(t0);
            let n = if t > g0 { parts_of(t - g0, max_step) } else { 0 };
            (g0, n, if n > 0 { (t - g0) / n as f64 } else { 0.0 })
        })
        .collect();
    if let Some(r) = last {
        for &(_, n, g_dt) in &own {
            if n > 0 && !away_factors.iter().any(|(len, _)| *len == g_dt) {
                away_factors.push((g_dt, r.no_track(kappa, g_dt)));
            }
        }
    }
    let rounds = own.iter().map(|&(_, n, _)| n).max().unwrap_or(0).max(parts);
    let mut out = Vec::with_capacity(rounds);
    for j in 0..rounds {
        let mut movers: Vec<(DensityId, f64)> = if j < parts {
            in_view.iter().map(|&(_, id)| (id, dt)).collect()
        } else {
            Vec::new()
        };
        for (m, &(_, n, g_dt)) in moving.iter().zip(&own) {
            if let Some(away) = objects.mixtures.get(m.id).away.filter(|_| j < n) {
                movers.push((away, g_dt));
            }
        }
        move_densities(world, &mut objects.densities, &movers);
        let mut round = RoundFactors::new();
        if j < parts {
            let (at, sums) = &watch[j];
            let factors = sums.as_ref().map(|r| r.no_track(kappa, dt));
            let fade = fade(world, w.observed.get(*at), dt);
            for &(entry, id) in in_view {
                let mut d = objects.densities.get_mut(id);
                expire(world, &mut d, dt, &fade);
                round.push((entry, Some(factors.as_ref().map_or(0.0, |f| d.weigh(f.view())))));
            }
        }
        for (m, &(g0, n, g_dt)) in moving.iter().zip(&own) {
            if j >= n {
                continue;
            }
            let mix = objects.mixtures.get_mut(m.id);
            let at = if mix.phantom {
                SensorSet::default()
            } else {
                live(w.sensors, g0 + g_dt * (j + 1) as f64)
            };
            let away = match last {
                Some(_) if !at.is_empty() => away_factors.iter().find(|(len, _)| *len == g_dt).map(|(_, f)| f.view()),
                _ => None,
            };
            let watch = (!at.is_empty()).then_some(Watch {
                spots,
                users: at.without(m.measured),
                away,
            });
            round.push((
                m.entry,
                sync_part(world, mix, &mut objects.densities, g_dt, true, watch),
            ));
        }
        out.push(round);
    }
    for (m, &(_, n, _)) in moving.iter().zip(&own) {
        if n > 0 {
            objects.mixtures.get_mut(m.id).t = Some(t);
        }
    }
    out
}

impl Filter {
    /// Moves everybody to time t (filter.Tracker._flush without the LD2410C): the people without a
    /// track all from the last move of everybody, each Gaussian mixture from its own time, in
    /// parts no longer than max_step; the log factors go to the hypotheses holding each object,
    /// round by round.
    pub fn flush(&mut self, world: &World, sensors: &[SensorState], observed: &Observed, t: f64) {
        if t <= self.flushed {
            return;
        }
        let t0 = self.flushed;
        let mut in_view = Vec::new();
        let mut moving = Vec::new();
        for (entry, obj) in self.holders.entries(true) {
            match obj {
                Obj::Density(id) => in_view.push((entry, id)),
                Obj::Mixture(id) => {
                    let mix = self.objects.mixtures.get(id);
                    if t > mix.t.unwrap_or(t0) {
                        let measured = measured_by(mix, &self.tracks);
                        moving.push(Mover { entry, id, measured });
                    }
                }
            }
        }
        let watchers = Watchers {
            world,
            sensors,
            tracks: &self.tracks,
            observed,
        };
        for round in flush_parts(watchers, &mut self.objects, &in_view, &moving, (t0, t)) {
            for (entry, d) in round {
                if let Some(d) = d {
                    self.holders.book(&mut self.hyps, entry, d);
                }
            }
        }
        self.flushed = t;
        self.bump();
    }

    /// Moves one Gaussian mixture from its own time to t without its part gone through a door (a
    /// measurement of the person follows and drops it), weighed by no track started on the person
    /// meanwhile (filter.Tracker._sync with drop_away). Returns the log factor of each part weighed.
    pub(super) fn sync(&mut self, world: &World, sensors: &[SensorState], id: MixtureId, t: f64) -> Vec<f64> {
        let objects = &mut self.objects;
        let mix = objects.mixtures.get_mut(id);
        let t0 = mix.t.unwrap_or(self.flushed);
        if t <= t0 {
            return Vec::new();
        }
        let parts = parts_of(t - t0, world.params.max_step);
        let dt = (t - t0) / parts as f64;
        let measured = measured_by(mix, &self.tracks);
        let mut factors = Vec::new();
        for k in 0..parts {
            let at = if mix.phantom {
                SensorSet::default()
            } else {
                live(sensors, t0 + dt * (k + 1) as f64)
            };
            let watch = (!at.is_empty()).then_some(Watch {
                spots: self.tracks.spots(),
                users: at.without(measured),
                away: None,
            });
            factors.extend(sync_part(world, mix, &mut objects.densities, dt, false, watch));
        }
        mix.t = Some(t);
        factors
    }

    /// Known people who almost surely do not exist join the unknown ones (MODEL.md 5.5;
    /// Tracker._recycle): those the measurements refuted and those whose record expired. The
    /// unknown ones are one Poisson process for all hypotheses (B_GarciaFernandez2018 eq. 7-10):
    /// what the hypotheses give back is added to it weighted by their probability, as a new
    /// intensity. Returns whether anybody was given back (then the shell moves everybody and
    /// merges).
    pub fn recycle(&mut self, give_up: f64) -> bool {
        let densities = &self.objects.densities;
        let gone: Vec<Vec<DensityId>> = self
            .hyps
            .iter()
            .map(|hy| {
                hy.hidden
                    .iter()
                    .copied()
                    .filter(|&u| densities.get(u).r < give_up)
                    .collect()
            })
            .collect();
        if gone.iter().all(Vec::is_empty) {
            return false;
        }
        let w = self.hyp_weights();
        // per intensity of the unknown ones: the weight of the hypotheses holding it, and what each
        // of them gives back
        struct Part<'a> {
            unknown: DensityId,
            weight: f64,
            given: Vec<(f64, &'a [DensityId])>,
        }
        let mut parts: Vec<Part> = Vec::new();
        for ((&wi, hy), g) in w.iter().zip(&self.hyps).zip(&gone) {
            match parts.iter_mut().find(|part| part.unknown == hy.unknown) {
                Some(part) => {
                    part.weight += wi;
                    part.given.push((wi, g));
                }
                None => parts.push(Part {
                    unknown: hy.unknown,
                    weight: 0.0 + wi,
                    given: vec![(wi, g)],
                }),
            }
        }
        let densities = &mut self.objects.densities;
        let mut new: Option<DensityId> = None;
        for part in parts {
            let sum = densities.fork(part.unknown);
            densities.get_mut(sum).scale(part.weight);
            for (wi, g) in part.given {
                for &u in g {
                    densities.add_scaled(sum, u, wi);
                }
            }
            match new {
                None => new = Some(sum),
                Some(new) => {
                    densities.add_scaled(new, sum, 1.0);
                    densities.release(sum);
                }
            }
        }
        let new = new.expect("a hypothesis");
        for (hy, g) in self.hyps.iter_mut().zip(&gone) {
            hy.unknown = new;
            for u in g {
                let at = hy.hidden.iter().position(|x| x == u).expect("held");
                hy.hidden.remove(at);
            }
        }
        self.rebuild();
        true
    }

    /// Everybody moves on over a gap in the data (MODEL.md 4.4, 5.3; Tracker._predict_gap): its last
    /// gap_exact s as always, what lies before in leaps of `leap` s, at most max_leaps. The sensors
    /// `placed` were there, only the app was not watching: what they could have tested lasts.
    /// After a restart only densities are held.
    pub fn predict_gap(&mut self, world: &World, observed: &Observed, placed: SensorSet, gap: f64) {
        if gap <= 0.0 {
            return;
        }
        let p = &world.params;
        let lead = (gap - p.gap_exact).max(0.0);
        let leaps = ((lead / p.leap - 1e-9).ceil() as usize).min(p.max_leaps);
        let parts = parts_of(gap - lead, p.max_step);
        let seen = observed.get(placed);
        let densities = &mut self.objects.densities;
        for (_, obj) in self.holders.entries(false) {
            let Obj::Density(id) = obj else {
                continue; // nobody has a track after a restart
            };
            if leaps > 0 {
                let dt = lead / leaps as f64;
                let f = fade(world, seen, dt);
                for _ in 0..leaps {
                    leap(world, densities, id, dt);
                    expire(world, &mut densities.get_mut(id), dt, &f);
                }
            }
            let dt = (gap - lead) / parts as f64;
            let f = fade(world, seen, dt);
            for _ in 0..parts {
                move_densities(world, densities, &[(id, dt)]);
                expire(world, &mut densities.get_mut(id), dt, &f);
            }
        }
        self.bump();
    }
}
