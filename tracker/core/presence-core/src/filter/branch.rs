//! A new track, or a lost one found again, branches every hypothesis (MODEL.md 5.1;
//! filter.Tracker._branch). A new track is a ghost's, or a person's who has tracks of other
//! sensors, or a person's without any (from their tiles), or somebody's nobody knew of. A track
//! found again is its owner's again, or the sensor found it on somebody else near the spot, or on
//! a reflection there - the LD2450 lets a held track glide to the next target. Every alternative is
//! a hypothesis with its exact weight; the children are then merged and cut (merge.rs).

use crate::filter::densities::{TileFactors, TileFactorsBuf};
use crate::filter::evidence::{count_walkers, expect, mixture_rates, near_mixture, near_tiles, no_find};
use crate::filter::holders::Obj;
use crate::filter::hyp::{Ghost, GroupId, Hyp, Owner};
use crate::filter::mixture::{Mixture, MixtureId, NewTrack, SegId, from_tiles, ghost_source};
use crate::filter::objects::{Objects, ReleaseCache};
use crate::filter::step::TileRates;
use crate::filter::tracks::{Lost, TrackInfo, Tracks};
use crate::filter::{Filter, NotFinite};
use crate::params::{FRAME, STILL, WALK};
use crate::vmath::logsumexp;
use crate::world::World;

/// What a child of a branch says the track is (for the shell's listeners).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Category {
    /// a new track: a ghost's
    Ghost,
    /// found again: still the same ghost's
    RefindGhost,
    /// found again: its owner's
    Owner,
    /// found again: on a reflection near the spot (a ghost from now on)
    Clutter,
    /// a person's who has tracks (of other sensors, or held ones)
    Tracked,
    /// a person's known without a track
    Known,
    /// somebody's nobody knew of
    New,
}

impl Category {
    pub fn name(self) -> &'static str {
        match self {
            Category::Ghost => "ghost",
            Category::RefindGhost => "refind_ghost",
            Category::Owner => "owner",
            Category::Clutter => "clutter",
            Category::Tracked => "tracked",
            Category::Known => "known",
            Category::New => "new",
        }
    }
}

/// A track of this frame to branch on, with the ghost map's rate at its start (a new one only; a
/// track found again keeps its own variance).
#[derive(Debug, Clone, Copy)]
pub struct Found {
    pub track: NewTrack,
    pub map_rate: f64,
}

/// What a frame's branches share: the sensor, the time, how long the sensor watched (last), whether
/// it saw nothing before (censored), the tracks begun in this frame (skip), the ghost map's prior.
#[derive(Debug, Clone, Copy)]
pub struct Frame<'a> {
    pub si: usize,
    pub t: f64,
    pub last: f64,
    pub censored: bool,
    pub skip: &'a [SegId],
    pub prior_rate: f64,
}

/// How the track's start or its finding again weighs a person.
enum Chance<'a> {
    /// a new track: the rates of its start (or, censored, of its being there) per tile, and the
    /// expected walkers in view per hypothesis
    New { start: TileFactors<'a>, walkers: &'a [f64] },
    /// found again: the chance of finding it near where it was held per tile, the log of not
    /// finding it before now, and the ghost's life since it was last measured per kind
    Again {
        lost: Lost,
        near: Vec<f64>,
        lr_nf: f64,
        ghost_life: Vec<f64>,
    },
}

/// The objects made while branching on one track, once per object they are made from
/// (Tracker._branch's caches).
#[derive(Default)]
struct Made {
    source: Option<MixtureId>,
    attached: Vec<(Obj, (Option<MixtureId>, f64))>,
    found_sources: Vec<(MixtureId, (MixtureId, f64))>,
    released: ReleaseCache,
}

/// Branching on one track: what it reads, and the objects it makes.
struct Branching<'a> {
    world: &'a World,
    frame: &'a Frame<'a>,
    track: NewTrack,
    chance: Chance<'a>,
    tracks: &'a Tracks,
    objects: &'a mut Objects,
    next_group: &'a mut u64,
    made: Made,
}

/// The children of all hypotheses, what each says, and for a new track the ghost children's
/// weights with the map and with the prior at its start.
#[derive(Default)]
struct Children {
    hyps: Vec<Hyp>,
    cats: Vec<Category>,
    ghost_map: Vec<f64>,
    ghost_prior: Vec<f64>,
}

impl Children {
    fn push(&mut self, hy: Hyp, cat: Category) {
        self.hyps.push(hy);
        self.cats.push(cat);
    }
}

impl Filter {
    /// Branches on the tracks found in a frame, one after the other (Tracker._evidence step 4).
    /// Returns per track the children's weights and what each says (for the listeners).
    pub fn branch_all(
        &mut self,
        world: &World,
        frame: &Frame,
        found: &[Found],
    ) -> Result<Vec<Vec<(f64, Category)>>, NotFinite> {
        let p = &world.params;
        let kappa = &world.shapes.kappa;
        let (lw, ls) = (p.track_life[WALK], p.track_life[STILL]);
        // the rates of a start per tile, as the sensor's tracks are at the frame (once per frame)
        let spots = self.tracks.spots_without(frame.skip);
        let mut rates = TileRates::new(world, &spots);
        let [rs, rw] = rates.get(frame.si);
        let start = if frame.censored {
            TileFactorsBuf {
                walk: rw.iter().map(|r| r * lw).collect(),
                still_per_level: kappa.iter().flat_map(|&k| rs.iter().map(move |r| k * r * ls)).collect(),
            }
        } else {
            TileFactorsBuf {
                walk: rw.iter().map(|r| (r * frame.last).exp_m1()).collect(),
                still_per_level: kappa
                    .iter()
                    .flat_map(|&k| rs.iter().map(move |r| (k * r * frame.last).exp_m1()))
                    .collect(),
            }
        };
        found
            .iter()
            .map(|f| self.branch(world, frame, f, start.view()))
            .collect()
    }

    fn branch(
        &mut self,
        world: &World,
        frame: &Frame,
        found: &Found,
        start: TileFactors,
    ) -> Result<Vec<(f64, Category)>, NotFinite> {
        let seg = found.track.seg;
        let refind = self.tracks.get(seg).copied();
        let Filter {
            hyps,
            objects,
            holders,
            tracks,
            walkers,
            next_group,
            ..
        } = self;
        let (track, chance) = match refind {
            Some(info) => (
                NewTrack {
                    var: info.var,
                    ..found.track
                },
                found_again(world, frame, &info),
            ),
            None => {
                let walkers = walkers.get(frame.si, || count_walkers(hyps, objects, holders, world, frame.si));
                (found.track, Chance::New { start, walkers })
            }
        };
        let mut b = Branching {
            world,
            frame,
            track,
            chance,
            tracks,
            objects,
            next_group,
            made: Made::default(),
        };
        let mut children = Children::default();
        for (h, hy) in hyps.iter().enumerate() {
            b.children_of(h, hy, found.map_rate, refind.is_some(), &mut children);
        }
        if children.hyps.is_empty() {
            return Ok(Vec::new());
        }
        let cats: Vec<(f64, Category)> = children.hyps.iter().map(|c| c.logw).zip(children.cats).collect();
        let t = frame.t;
        match refind {
            Some(_) => self.tracks.update(seg, |i| {
                i.lost = None;
                (i.t, i.zt, i.gt, i.z) = (t, t, Some(t), track.z);
            }),
            None => {
                // the factor by which the map at z moved the odds of "ghost" (for what the map
                // learns when the track ends)
                let map_odds = if children.ghost_map.is_empty() {
                    0.0
                } else {
                    logsumexp(&children.ghost_map) - logsumexp(&children.ghost_prior)
                };
                let info = TrackInfo {
                    si: frame.si,
                    t,
                    zt: t,
                    gt: None,
                    var: track.var,
                    z: track.z,
                    z0: track.z,
                    born: t,
                    lost: None,
                    map_odds,
                };
                self.tracks.begin(seg, info);
            }
        }
        self.take(children.hyps, &world.params)?;
        Ok(cats)
    }
}

/// What finding a held track again weighs (Tracker._branch, refind).
fn found_again<'a>(world: &World, frame: &Frame, info: &TrackInfo) -> Chance<'a> {
    let p = &world.params;
    let lost = info.lost.expect("a track found again was lost");
    let (u0, u1) = (lost.u, frame.t - lost.t);
    let lr_nf = (no_find(p, lost.kind, u1) / no_find(p, lost.kind, u0).max(1e-300))
        .max(1e-300)
        .ln();
    let near = if world.tiles.n > 0 {
        near_tiles(world, frame.si, lost.z)
    } else {
        Vec::new()
    };
    let ghost_life = p
        .ghost_types
        .iter()
        .map(|&[_, life]| -(frame.t - info.ghost_t()) / life)
        .collect();
    Chance::Again {
        lost,
        near,
        lr_nf,
        ghost_life,
    }
}

impl Branching<'_> {
    /// The children of one hypothesis (h its index).
    fn children_of(&mut self, h: usize, hy: &Hyp, map_rate: f64, refind: bool, out: &mut Children) {
        let (world, p) = (self.world, &self.world.params);
        let seg = self.track.seg;
        let ghost_rates: Vec<f64> = p.ghost_types.iter().map(|&[rate, _]| rate).collect();
        let rate_sum = ghost_rates.iter().fold(0.0, |s, r| s + r);
        let cur = if refind { hy.owner_of(seg) } else { None };
        let base = match &self.chance {
            Chance::Again {
                lost,
                near,
                lr_nf,
                ghost_life,
            } => {
                let (lost, lr_nf) = (*lost, *lr_nf);
                // found in this frame on one of the finders: everybody near the spot as far as the
                // sensor sees them there, the ghost (if it is one), a reflection
                let mut e = p.find_clutter + if cur == Some(Owner::Ghost) { 1.0 } else { 0.0 };
                let (n, shape) = (world.tiles.n, self.objects.densities.shape());
                for &m in hy.people.values() {
                    let mix = self.objects.mixtures.get(m);
                    let (w, k) = (mix.weights(), near_mixture(world, self.frame.si, mix, lost.z));
                    e += (1.0 - mix.a) * (w[0] * k[0] + w[1] * k[1]);
                    if let Some(away) = mix.away.filter(|_| n > 0) {
                        e += mix.a * self.objects.densities.get(away).integrate(near, &shape);
                    }
                }
                if n > 0 {
                    for &u in hy.hidden.iter().chain([&hy.unknown]) {
                        e += self.objects.densities.get(u).integrate(near, &shape);
                    }
                }
                let pf = (-(e * lr_nf).exp_m1()).max(1e-300).ln() - e.ln();
                match cur {
                    Some(Owner::Ghost) => {
                        let ghost = hy.ghosts.get(&seg).expect("a ghost track's life");
                        let stay: Vec<f64> = ghost.life.iter().zip(ghost_life).map(|(a, g)| a + g).collect();
                        let (source, l) = self.found_source(ghost.source);
                        let mut ch = hy.child(hy.logw + pf + logsumexp(&stay) - logsumexp(&ghost.life) + l);
                        ch.ghosts.insert(seg, Ghost { life: stay, source });
                        out.push(ch, Category::RefindGhost);
                    }
                    Some(Owner::Person(gid)) => {
                        let owner = *hy.people.get(&gid).expect("a person with tracks");
                        let (new, l) = self.attach(Obj::Mixture(owner));
                        if let Some(new) = new.filter(|_| l > f64::NEG_INFINITY) {
                            let mut ch = hy.child(hy.logw + pf + l);
                            ch.people.insert(gid, new);
                            out.push(ch, Category::Owner);
                        }
                        let clutter = (p.find_clutter / (2.0 * std::f64::consts::PI * p.find_radius.powf(2.0))).ln();
                        let mut ch = self.released(hy.child(hy.logw + pf + clutter), cur);
                        ch.tracks.insert(seg, Owner::Ghost);
                        let life = ghost_rates.iter().map(|r| (r / rate_sum).ln()).collect();
                        let source = self.source();
                        ch.ghosts.insert(seg, Ghost { life, source });
                        out.push(ch, Category::Clutter);
                    }
                    None => panic!("every hypothesis says whose a live track is"),
                }
                pf
            }
            Chance::New { walkers, .. } => {
                let echo = p.ghost_echo * walkers[h];
                let mut lam: Vec<f64> = ghost_rates.iter().map(|r| r * (map_rate / rate_sum)).collect();
                let mut prior: Vec<f64> = ghost_rates
                    .iter()
                    .map(|r| r * (self.frame.prior_rate / rate_sum))
                    .collect();
                lam[0] += echo;
                prior[0] += echo;
                if self.frame.censored {
                    // ghosts there now: born at any time before
                    for (k, &[_, life]) in p.ghost_types.iter().enumerate() {
                        lam[k] = lam[k] * life / FRAME;
                        prior[k] = prior[k] * life / FRAME;
                    }
                }
                let total = lam.iter().fold(0.0, |s, x| s + x);
                let prior_total = prior.iter().fold(0.0, |s, x| s + x);
                let mut ch = hy.child(hy.logw + (total * self.frame.last).ln());
                out.ghost_map.push(ch.logw);
                out.ghost_prior.push(ch.logw + (prior_total / total).ln());
                ch.tracks.insert(seg, Owner::Ghost);
                let life = lam.iter().map(|l| (l / total).ln()).collect();
                let source = self.source();
                ch.ghosts.insert(seg, Ghost { life, source });
                out.push(ch, Category::Ghost);
                0.0
            }
        };
        // somebody with tracks (of other sensors, or held ones)
        for (&gid, &m) in hy.people.iter() {
            if cur == Some(Owner::Person(gid)) || self.measured_by_sensor(hy, gid) {
                continue;
            }
            let (new, l) = self.attach(Obj::Mixture(m));
            let Some(new) = new.filter(|_| l > f64::NEG_INFINITY) else {
                continue;
            };
            let mut ch = self.released(hy.child(hy.logw + base + l), cur);
            ch.people.insert(gid, new);
            ch.tracks.insert(seg, Owner::Person(gid));
            out.push(ch, Category::Tracked);
        }
        // somebody without a track: from their tiles, each density once with how often it is held
        let mut counted: Vec<(crate::filter::densities::DensityId, usize)> = Vec::new();
        for &u in &hy.hidden {
            match counted.iter_mut().find(|(x, _)| *x == u) {
                Some((_, m)) => *m += 1,
                None => counted.push((u, 1)),
            }
        }
        for (u, mult) in counted {
            let (new, l) = self.attach(Obj::Density(u));
            let Some(new) = new.filter(|_| l > f64::NEG_INFINITY) else {
                continue;
            };
            let mut ch = self.released(hy.child(hy.logw + base + l + (mult as f64).ln()), cur);
            let at = ch.hidden.iter().position(|&x| x == u).expect("held");
            ch.hidden.remove(at);
            let gid = Filter::new_group(self.next_group);
            ch.people.insert(gid, new);
            ch.tracks.insert(seg, Owner::Person(gid));
            out.push(ch, Category::Known);
        }
        // somebody nobody knew of (the unknown ones stay as they are: a birth is one point of the
        // Poisson process, B_GarciaFernandez2018 eq. 18-24)
        let (new, l) = self.attach(Obj::Density(hy.unknown));
        if let Some(new) = new.filter(|_| l > f64::NEG_INFINITY) {
            let mut ch = self.released(hy.child(hy.logw + base + l), cur);
            let gid = Filter::new_group(self.next_group);
            ch.people.insert(gid, new);
            ch.tracks.insert(seg, Owner::Person(gid));
            out.push(ch, Category::New);
        }
    }

    /// Does the person of group gid have a measuring track of this sensor besides this one?
    fn measured_by_sensor(&self, hy: &Hyp, gid: GroupId) -> bool {
        hy.tracks.iter().any(|(&s, &o)| {
            o == Owner::Person(gid)
                && s != self.track.seg
                && self.tracks.measuring(s)
                && self.tracks.get(s).is_some_and(|i| i.si == self.frame.si)
        })
    }

    /// The track leaves its old owner in this child.
    fn released(&mut self, mut ch: Hyp, cur: Option<Owner>) -> Hyp {
        let seg = self.track.seg;
        match cur {
            Some(Owner::Ghost) => {
                ch.ghosts.remove(&seg);
            }
            Some(Owner::Person(gid)) => self
                .objects
                .release(self.world, &mut ch, gid, seg, &mut self.made.released),
            None => {}
        }
        ch
    }

    /// The source of a new ghost track, one for all children (Gauss.source).
    fn source(&mut self) -> MixtureId {
        let (track, world) = (self.track, self.world);
        let mixtures = &mut self.objects.mixtures;
        *self
            .made
            .source
            .get_or_insert_with(|| mixtures.insert(ghost_source(track, &world.params, &world.shapes)))
    }

    /// A ghost's source found again: updated with the position (one per source).
    fn found_source(&mut self, ph: MixtureId) -> (MixtureId, f64) {
        if let Some(&(_, hit)) = self.made.found_sources.iter().find(|(k, _)| *k == ph) {
            return hit;
        }
        // the source differs in this child: a new object
        let objects = &mut *self.objects;
        let mut new = objects.mixtures.get(ph).fork(&mut objects.densities);
        let l = new.update(
            &mut objects.densities,
            self.track.seg,
            self.track.z,
            self.world.params.white,
            None,
        );
        let hit = (objects.mixtures.insert(new), l);
        self.made.found_sources.push((ph, hit));
        hit
    }

    /// The track starts (or is found) on this person: the new mixture and the log factor (once per
    /// object). The rate of the start, or the chance of finding it there, where the person is given
    /// z: over the posterior, int p(x) f(x) N(z | x) dx = N(z) E[f(x) | z].
    fn attach(&mut self, obj: Obj) -> (Option<MixtureId>, f64) {
        if let Some(&(_, hit)) = self.made.attached.iter().find(|(k, _)| *k == obj) {
            return hit;
        }
        let (world, track) = (self.world, self.track);
        let p = &world.params;
        let origin = world.sights[self.frame.si].origin;
        let objects = &mut *self.objects;
        let hit = match obj {
            Obj::Mixture(m) => {
                // somebody with tracks: the person differs in this child, a new object
                let mut new = objects.mixtures.get(m).fork(&mut objects.densities);
                if !new.slots.contains_key(&track.seg) {
                    new.add_track(track.seg, track.var, p.const_share);
                }
                let l = new.update(&mut objects.densities, track.seg, track.z, p.white, None);
                new.in_sight(world, origin);
                let l = if l > f64::NEG_INFINITY {
                    let dlog = self.start_chance(&mut new);
                    l + new.reweigh(dlog, 0.0)
                } else {
                    l
                };
                (Some(self.objects.mixtures.insert(new)), l)
            }
            Obj::Density(_) if world.tiles.n == 0 => (None, f64::NEG_INFINITY),
            Obj::Density(d) => {
                let f = match &self.chance {
                    Chance::New { start, .. } => *start,
                    Chance::Again { near, .. } => TileFactors::same(near),
                };
                match from_tiles(world, &mut objects.densities, d, f, track) {
                    Some((mut new, l)) => {
                        new.in_sight(world, origin);
                        (Some(objects.mixtures.insert(new)), l)
                    }
                    None => (None, f64::NEG_INFINITY),
                }
            }
        };
        self.made.attached.push((obj, hit));
        hit
    }

    /// The log factor per component of the track starting on the person (or being found on them)
    /// where they are now (Tracker._branch's logf); a start also tells the levels of detectability.
    fn start_chance(&self, mix: &mut Mixture) -> [f64; 2] {
        let (world, frame) = (self.world, self.frame);
        match &self.chance {
            Chance::Again { lost, .. } => near_mixture(world, frame.si, mix, lost.z).map(f64::ln),
            Chance::New { .. } => {
                let p = &world.params;
                let (lw, ls) = (p.track_life[WALK], p.track_life[STILL]);
                let mut skip = frame.skip.to_vec();
                skip.push(self.track.seg);
                let spots = self.tracks.spots_without(&skip);
                let r = mixture_rates(world, self.tracks, &spots, frame.si, mix, &skip);
                let kappa = &world.shapes.kappa;
                let (still, walk): (Vec<f64>, f64) = if frame.censored {
                    (
                        kappa.iter().map(|&k| expect(&r[STILL].map(|x| k * x * ls))).collect(),
                        expect(&r[WALK].map(|x| x * lw)),
                    )
                } else {
                    (
                        kappa
                            .iter()
                            .map(|&k| expect(&r[STILL].map(|x| (k * x * frame.last).exp_m1())))
                            .collect(),
                        expect(&r[WALK].map(|x| (x * frame.last).exp_m1())),
                    )
                };
                mix.kappa_weigh(&still, walk)
            }
        }
    }
}
