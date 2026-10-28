//! Hypotheses and the objects they hold (MODEL.md 5): Gaussian mixtures of the people with a track,
//! densities over the tiles of the people without one, and what moves, weighs, branches and merges
//! them. The filter owns the objects; hypotheses hold indices, the holders say who holds what
//! (docs/rust-core.md 2).

pub mod branch;
pub mod densities;
pub(crate) mod evidence;
pub(crate) mod holders;
pub mod hyp;
mod ld_weigh;
pub(crate) mod merge;
#[cfg(test)]
#[cfg(test)]
pub mod mirror;
pub mod mixture;
pub(crate) mod objects;
pub mod pool;
pub(crate) mod saved;
pub(crate) mod small_map;
mod start;
pub(crate) mod step;
pub(crate) mod tracks;

#[cfg(test)]
mod tests;

use densities::{DensityData, DensityId, DensityShape};
use holders::Holders;
use hyp::{GroupId, Hyp, Owner};
use mixture::{MixtureId, SegId};
use objects::Objects;
use tracks::Tracks;

pub use ld_weigh::LdLearn;
pub use start::Start;

/// What the filter cannot go on from: the hypotheses' weights are not finite (every output after
/// it would be NaN, and NaN shows as "nobody there"; the app starts the model over).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct NotFinite(pub f64);

impl std::fmt::Display for NotFinite {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "the hypotheses' weights are not finite ({})", self.0)
    }
}

impl std::error::Error for NotFinite {}

#[derive(Debug)]
pub struct Filter {
    hyps: Vec<Hyp>,
    objects: Objects,
    /// always those of hyps: built anew wherever the hypotheses change (set_hyps)
    holders: Holders,
    tracks: Tracks,
    next_group: GroupIds,
    /// log evidence of everything seen so far, and the part of it that is mass dropped by the cut
    loglik: f64,
    loglik_cut: f64,
    /// time of the last move of everybody
    flushed: f64,
    walkers: WalkerCache,
    /// counts the moves of everybody and the events that concern all hypotheses (moved, an event of
    /// a frame, merged, cut, a gap, a new world, a start): what is derived from all of them, the
    /// outputs, is current while it stays (filter.Tracker._version). Only counts up: what was derived
    /// at another epoch never passes for current.
    epoch: u64,
}

/// The ids of the people with tracks: each new one the next (they count on in branches that are
/// later cut).
#[derive(Debug)]
struct GroupIds(u64);

impl GroupIds {
    fn next(&mut self) -> GroupId {
        self.0 += 1;
        GroupId(self.0 - 1)
    }
}

/// Per sensor the expected number of walkers in its view per hypothesis, as of the last move of
/// everybody or event (Tracker._walkers): counted when first needed, void when the hypotheses
/// change, everybody moves, or an event concerns everybody.
#[derive(Debug)]
struct WalkerCache {
    per_sensor: Vec<Option<Vec<f64>>>,
}

impl WalkerCache {
    fn new(sensors: usize) -> Self {
        Self {
            per_sensor: vec![None; sensors],
        }
    }

    fn clear(&mut self) {
        self.per_sensor.fill(None);
    }

    /// The walkers per hypothesis of a sensor, counted if they are not known.
    fn get(&mut self, si: usize, count: impl FnOnce() -> Vec<f64>) -> &[f64] {
        self.per_sensor[si].get_or_insert_with(count)
    }
}

/// A hypothesis as tests and tools read it (Tracker::hypotheses): its weight, which live tracks are one
/// person's and which are ghosts, how probably each known person exists (1 with a track), and the mass
/// of the unknown ones outside.
#[derive(Debug)]
pub struct HypView {
    pub logw: f64,
    pub people: Vec<Vec<SegId>>,
    pub ghosts: Vec<SegId>,
    pub known: Vec<f64>,
    pub unknown_out: f64,
}

/// What is known about the people, to start from after a restart (MODEL.md 5.3; Tracker.
/// people_state): per hypothesis its weight (normalized), its known people and the unknown ones as
/// indices into `densities`.
#[derive(Debug)]
pub struct PeopleState {
    pub hyps: Vec<(f64, Vec<usize>, usize)>,
    pub densities: Vec<DensityData>,
}

impl Filter {
    /// A filter without hypotheses (load them with `set_hyps`).
    pub fn new(shape: DensityShape, sensors: usize) -> Self {
        Self {
            hyps: Vec::new(),
            objects: Objects::new(shape),
            holders: Holders::default(),
            tracks: Tracks::new(sensors),
            next_group: GroupIds(1),
            loglik: 0.0,
            loglik_cut: 0.0,
            flushed: 0.0,
            walkers: WalkerCache::new(sensors),
            epoch: 0,
        }
    }

    pub fn hyps(&self) -> &[Hyp] {
        &self.hyps
    }

    pub fn objects(&self) -> &Objects {
        &self.objects
    }

    /// Who holds each object (always those of the hypotheses).
    pub fn holders(&self) -> &Holders {
        &self.holders
    }

    /// The objects, for making new ones that a following set_hyps hands to hypotheses (tests).
    #[cfg(test)]
    pub fn objects_mut(&mut self) -> &mut Objects {
        &mut self.objects
    }

    pub fn tracks(&self) -> &Tracks {
        &self.tracks
    }

    /// The live tracks, for setting them (tests).
    #[cfg(test)]
    pub fn tracks_mut(&mut self) -> &mut Tracks {
        &mut self.tracks
    }

    /// An event concerns everybody: what is cached per hypothesis is void (the Python filter's
    /// version went on).
    pub fn void_cache(&mut self) {
        self.walkers.clear();
        self.epoch += 1;
    }

    /// What the outputs are cached by (the field `epoch`).
    pub fn epoch(&self) -> u64 {
        self.epoch
    }

    pub fn flushed(&self) -> f64 {
        self.flushed
    }

    pub fn set_flushed(&mut self, t: f64) {
        self.flushed = t;
    }

    /// (log evidence, the part of it dropped by the cut)
    pub fn loglik(&self) -> (f64, f64) {
        (self.loglik, self.loglik_cut)
    }

    pub fn set_loglik(&mut self, (loglik, cut): (f64, f64)) {
        (self.loglik, self.loglik_cut) = (loglik, cut);
    }

    /// Replaces the hypotheses: the holders are built anew and what nobody holds then is released.
    pub(crate) fn set_hyps(&mut self, hyps: Vec<Hyp>) {
        self.hyps = hyps;
        self.rebuild();
    }

    /// The hypotheses changed in place: the holders anew, what nobody holds released.
    fn rebuild(&mut self) {
        let objects = &self.objects;
        self.holders = Holders::build(&self.hyps, objects.mixtures.capacity(), objects.densities.capacity());
        self.objects.release_unheld(&self.holders);
        self.walkers.clear();
    }

    /// No live tracks (Tracker._clear): from time t; what was derived before is void.
    pub fn clear(&mut self, t: f64) {
        self.tracks = Tracks::new(self.tracks.sensors());
        self.flushed = t;
        self.void_cache();
    }

    /// Every hypothesis' weight NaN (a test of what the shell does then).
    #[cfg(any(test, feature = "testing"))]
    pub fn spoil_weights_for_test(&mut self) {
        for hy in &mut self.hyps {
            hy.logw = f64::NAN;
        }
    }

    /// The hypotheses' weights, normalized by the largest (Tracker.hyp_weights).
    pub fn hyp_weights(&self) -> Vec<f64> {
        let lw: Vec<f64> = self.hyps.iter().map(|h| h.logw).collect();
        let top = crate::vmath::nan_max(&lw);
        let w: Vec<f64> = lw.iter().map(|l| (l - top).exp()).collect();
        let s = crate::vmath::pairwise_sum(&w);
        w.iter().map(|x| x / s).collect()
    }

    /// The people as densities over the tiles: those with tracks go to the tiles as when their last
    /// track ends (5.4), ghost tracks end, and the hypotheses that then say the same are merged
    /// (5.6). Changes nothing of the filter: it works on copies (reading a stored amplitude settles
    /// it; in the model's own densities the run went another way after every save, 0.28), which
    /// are released before it returns.
    pub fn people_state(&mut self, world: &crate::world::World) -> PeopleState {
        let objects = &mut self.objects;
        let mut own: Vec<(DensityId, DensityId)> = Vec::new();
        let mut copy = |objects: &mut Objects, id: DensityId| match own.iter().find(|(k, _)| *k == id) {
            Some(&(_, c)) => c,
            None => {
                let c = objects.densities.fork(id);
                own.push((id, c));
                c
            }
        };
        let mut tiled: Vec<(MixtureId, DensityId)> = Vec::new();
        let mut hyps = Vec::with_capacity(self.hyps.len());
        for hy in &self.hyps {
            let mut people: Vec<DensityId> = hy.hidden.iter().map(|&u| copy(objects, u)).collect();
            for &g in hy.people.values() {
                let d = match tiled.iter().find(|(k, _)| *k == g) {
                    Some(&(_, d)) => d,
                    None => {
                        // on a copy: its part gone through a door is settled when it is mixed in
                        let mix = objects.mixtures.get(g).fork(&mut objects.densities);
                        let d = mix.to_tiles(world, &mut objects.densities);
                        if let Some(away) = mix.away {
                            objects.densities.release(away);
                        }
                        tiled.push((g, d));
                        d
                    }
                };
                people.push(d);
            }
            let unknown = copy(objects, hy.unknown);
            hyps.push(Hyp::new(hy.logw, people, unknown));
        }
        objects.merge_alike(&mut hyps);
        let total = crate::vmath::logsumexp(&hyps.iter().map(|h| h.logw).collect::<Vec<_>>());
        let mut order: Vec<DensityId> = Vec::new();
        let mut index = |d: DensityId| match order.iter().position(|&x| x == d) {
            Some(i) => i,
            None => {
                order.push(d);
                order.len() - 1
            }
        };
        let hyps = hyps
            .iter()
            .map(|hy| {
                let people = hy.hidden.iter().map(|&u| index(u)).collect();
                (hy.logw - total, people, index(hy.unknown))
            })
            .collect();
        let densities = order
            .iter()
            .map(|&d| {
                objects.densities.get_mut(d).settle_amp();
                objects.densities.data(d)
            })
            .collect();
        // the copies go: nobody holds them
        objects.release_unheld(&self.holders);
        PeopleState { hyps, densities }
    }

    /// The hypotheses for reading (HypView).
    pub fn hypotheses(&self) -> Vec<HypView> {
        let densities = self.objects.densities();
        self.hyps
            .iter()
            .map(|hy| HypView {
                logw: hy.logw,
                people: hy.group_segs().into_values().collect(),
                ghosts: hy
                    .tracks
                    .iter()
                    .filter(|(_, o)| **o == Owner::Ghost)
                    .map(|(&s, _)| s)
                    .collect(),
                known: hy
                    .people
                    .values()
                    .map(|_| 1.0)
                    .chain(hy.hidden.iter().map(|&u| densities.get(u).r))
                    .collect(),
                unknown_out: densities.get(hy.unknown).out,
            })
            .collect()
    }

    /// The bookkeeping's invariants (Tracker.check, and those of the pools): no two hypotheses
    /// alike; each says whose every live track is; its people with tracks are its groups, each
    /// holding the offsets of its tracks; every ghost track has its source; the unknown ones an
    /// intensity, the known ones persons; the holders and the objects (Objects::check).
    pub fn check(&self) -> Result<(), String> {
        use densities::Kind;
        let mut keys = Vec::new();
        for (h, hy) in self.hyps.iter().enumerate() {
            let key = hy.key();
            if keys.contains(&key) {
                return Err(format!("hypothesis {h}: two hypotheses alike"));
            }
            keys.push(key);
            let live: Vec<_> = self.tracks.iter().map(|(&s, _)| s).collect();
            if hy.tracks.len() != live.len() || !live.iter().all(|s| hy.tracks.contains_key(s)) {
                return Err(format!("hypothesis {h}: it and the live tracks differ"));
            }
            let groups = hy.group_segs();
            if groups.len() != hy.people.len() || !groups.keys().all(|g| hy.people.contains_key(g)) {
                return Err(format!(
                    "hypothesis {h}: a person with tracks and no mixture, or the reverse"
                ));
            }
            for (gid, segs) in groups.iter() {
                let m = *hy.people.get(gid).expect("checked above");
                if !self.objects.mixtures.contains(m) {
                    return Err(format!("hypothesis {h}: a released mixture"));
                }
                let mix = self.objects.mixtures.get(m);
                if !segs.iter().all(|s| mix.slots.contains_key(s)) {
                    return Err(format!("hypothesis {h}: a track of a person without its offset"));
                }
            }
            for (s, o) in hy.tracks.iter() {
                if *o == Owner::Ghost && !hy.ghosts.contains_key(s) {
                    return Err(format!("hypothesis {h}: a ghost without its source"));
                }
            }
            let kind = |d: DensityId| {
                self.objects
                    .densities
                    .contains(d)
                    .then(|| self.objects.densities.get(d).kind)
            };
            if kind(hy.unknown) != Some(Kind::Intensity) {
                return Err(format!("hypothesis {h}: no unknown people"));
            }
            if hy.hidden.iter().any(|&u| kind(u) != Some(Kind::Person)) {
                return Err(format!("hypothesis {h}: a known person as an intensity, or released"));
            }
        }
        self.objects.check(&self.hyps, &self.holders)
    }
}
