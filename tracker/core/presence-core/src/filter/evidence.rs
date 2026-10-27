//! What a sensor's frame says (MODEL.md 4.1, 4.2; filter.Tracker._evidence): no ghost track was born
//! meanwhile, its tracks were measured, lost, held without being found again or ended, and nothing
//! was seen before a start. New tracks and lost ones found again branch the hypotheses (branch.rs).

use crate::filter::Filter;
use crate::filter::densities::{TileFactors, TileFactorsBuf};
use crate::filter::holders::{Holders, Obj};
use crate::filter::hyp::{Hyp, Owner};
use crate::filter::merge::odds_shifted;
use crate::filter::mixture::{Mixture, MixtureId, SegId, UNIT_W};
use crate::filter::objects::{Objects, ReleaseCache};
use crate::filter::step::TileRates;
use crate::filter::tracks::{Lost, TrackInfo, Tracks};
use crate::geometry::Point;
use crate::ld2450::rates::TrackSpot;
use crate::params::{LostKind, Params, STILL, WALK};
use crate::sensors::{SensorSet, SensorState};
use crate::vmath::{logsumexp, pairwise_sum};
use crate::world::World;

/// The expectation over a component's sigma points of values at them (Tracker._expect).
pub fn expect(values: &[f64; 5]) -> f64 {
    values.iter().zip(UNIT_W).fold(0.0, |s, (v, w)| s + v * w)
}

/// P(the sensor has not found its lost target again within u s | the person stayed where it was
/// held): measured, a log-normal time to finding it again, and a small share never
/// (Tracker._no_find).
pub fn no_find(p: &Params, kind: LostKind, u: f64) -> f64 {
    if u <= 0.0 {
        return 1.0;
    }
    let [median, spread, never] = p.refind.of(kind);
    let later = 0.5 * libm::erfc((u.ln() - median.ln()) / (spread * 2f64.sqrt()));
    never + (1.0 - never) * later
}

/// A track that ended, for what the shell learns from it (the ghost map, MODEL.md 4.2): where it
/// began, P(ghost) as judged now without what the map said at its birth, how long it lived, and
/// when it was born.
#[derive(Debug, Clone, Copy)]
pub struct Ended {
    pub z0: Point,
    pub p_ghost: f64,
    pub life: f64,
    pub born: f64,
}

impl Filter {
    /// Step 1 of a frame: no ghost track was born in delta s (echoes come with walkers in view);
    /// `ghost_total` is the rate of ghost births anywhere sensor si sees, read before the map
    /// learns this time.
    pub fn watch(&mut self, world: &World, si: usize, delta: f64, ghost_total: f64) {
        let Filter {
            walkers,
            hyps,
            objects,
            holders,
            ..
        } = self;
        let walkers = walkers.get(si, || count_walkers(hyps, objects, holders, world, si));
        let (echo, area) = (world.params.ghost_echo, world.sights[si].area);
        for (hy, w) in hyps.iter_mut().zip(walkers) {
            hy.logw -= (ghost_total + echo * w * area) * delta;
        }
    }

    /// Step 2: a measurement of a track that was not lost. It sits at z = x + c + o + w on its
    /// source (a person or a ghost's source): the exact Kalman update per component; a ghost lives
    /// on (Tracker._measured). Its positions are used every pos_every s.
    pub fn measured(&mut self, world: &World, sensors: &[SensorState], seg: SegId, z: Point, t: f64) {
        let info = *self.tracks.get(seg).expect("a live track");
        let lives: Vec<f64> = world.params.ghost_types.iter().map(|&[_, life]| life).collect();
        for hy in &mut self.hyps {
            if hy.is_ghost(seg) {
                ghost_life(hy, seg, &lives, t - info.ghost_t());
            }
        }
        self.tracks.update(seg, |i| i.gt = Some(t));
        if t - info.zt < world.params.pos_every {
            return;
        }
        // the people and ghosts' sources with this track
        let on: Vec<(usize, MixtureId)> = self
            .holders
            .entries(true)
            .filter_map(|(entry, obj)| match obj {
                Obj::Mixture(m) if self.objects.mixtures.get(m).slots.contains_key(&seg) => Some((entry, m)),
                _ => None,
            })
            .collect();
        let origin = world.sights[info.si].origin;
        for (entry, id) in on {
            for d in self.sync(world, sensors, id, t) {
                self.holders.book(&mut self.hyps, entry, d);
            }
            let objects = &mut self.objects;
            let mix = objects.mixtures.get_mut(id);
            let dl = mix.update(&mut objects.densities, seg, z, world.params.white, None);
            mix.in_sight(world, origin);
            self.holders.book(&mut self.hyps, entry, dl);
        }
        self.tracks.update(seg, |i| (i.t, i.zt, i.z) = (t, t, z));
    }

    /// The sensor lost a track and holds it where it was (MODEL.md 4.1).
    pub fn lost(&mut self, seg: SegId, kind: LostKind, z: Point, t: f64) {
        self.tracks.update(seg, |i| i.lost = Some(Lost { kind, t, z, u: 0.0 }));
    }

    /// A lost track, still held: not found again on anybody near where it is held, nor on the ghost
    /// it may be, nor on a reflection there (MODEL.md 4.1; Tracker._held).
    pub fn held(&mut self, world: &World, si: usize, seg: SegId, z: Point, t: f64) {
        let p = &world.params;
        let lost = self.tracks.get(seg).and_then(|i| i.lost).expect("a lost track");
        let (u0, u1) = (lost.u, t - lost.t);
        let ratio = no_find(p, lost.kind, u1) / no_find(p, lost.kind, u0).max(1e-300);
        self.tracks.update(seg, |i| i.lost.as_mut().expect("lost").u = u1);
        let lr = ratio.max(1e-300).ln();
        if lr < 0.0 {
            let n = world.tiles.n;
            let f: Vec<f64> = if n > 0 {
                near_tiles(world, si, lost.z).iter().map(|k| (k * lr).exp()).collect()
            } else {
                Vec::new()
            };
            let f = TileFactors::same(&f);
            let objects = &mut self.objects;
            for (entry, obj) in self.holders.entries(false) {
                let dl = match obj {
                    Obj::Density(id) => objects.densities.get_mut(id).weigh(f),
                    Obj::Mixture(id) => {
                        let mix = objects.mixtures.get_mut(id);
                        let da = match mix.away {
                            Some(away) if n > 0 => objects.densities.get_mut(away).weigh(f),
                            _ => 0.0,
                        };
                        let k = near_mixture(world, si, mix, lost.z);
                        mix.reweigh([k[0] * lr, k[1] * lr], da)
                    }
                };
                self.holders.book(&mut self.hyps, entry, dl);
            }
            for hy in &mut self.hyps {
                let ghost = if hy.is_ghost(seg) { 1.0 } else { 0.0 };
                hy.logw += (p.find_clutter + ghost) * lr;
            }
        }
        self.tracks.update(seg, |i| i.lost.as_mut().expect("lost").z = z);
    }

    /// The sensor dropped the track (Tracker._end). For a person this follows from not finding it
    /// again (counted while it was held); a ghost died: its hazard. A person left without tracks
    /// goes to the people without one. Returns what the ghost map learns from it.
    pub fn end(&mut self, world: &World, seg: SegId, t: f64, data_lost: bool) -> Ended {
        let w = self.hyp_weights();
        let p_ghost = w
            .iter()
            .zip(&self.hyps)
            .filter(|(_, hy)| hy.is_ghost(seg))
            .fold(0.0, |s, (wi, _)| s + wi);
        let info = self.tracks.end(seg);
        // the map entered the odds once, as a factor at the birth: taken out
        let p_ghost = odds_shifted(p_ghost, -info.map_odds);
        let life = info.lost.map_or(info.ghost_t(), |l| l.t) - info.born;
        let ghost_end = ghost_end(&world.params, &info, t);
        let mut cache = ReleaseCache::new();
        for hy in &mut self.hyps {
            let owner = hy
                .tracks
                .get(&seg)
                .copied()
                .expect("every hypothesis says whose a live track is");
            let ghost = hy.ghosts.remove(&seg);
            match owner {
                Owner::Ghost => {
                    if let (false, Some(end), Some(mut g)) = (data_lost, &ghost_end, ghost) {
                        let before = logsumexp(&g.life);
                        for (l, e) in g.life.iter_mut().zip(end) {
                            *l += e;
                        }
                        hy.logw += logsumexp(&g.life) - before;
                    }
                }
                Owner::Person(gid) => self.objects.release(world, hy, gid, seg, &mut cache),
            }
            hy.tracks.remove(&seg);
        }
        self.rebuild();
        Ended {
            z0: info.z0,
            p_ghost,
            life,
            born: info.born,
        }
    }

    /// Step 3: the first frame of a sensor (or after lost data). Who has no track of it now was not
    /// tracked by it - with the stationary share 1 / (1 + rate x life of a track) (Tracker._censor).
    pub fn censor(&mut self, world: &World, si: usize) {
        let (p, kappa) = (&world.params, &world.shapes.kappa);
        let (lw, ls) = (p.track_life[WALK], p.track_life[STILL]);
        let mut rates = TileRates::new(world, self.tracks.spots());
        let [rs, rw] = rates.get(si);
        let f = TileFactorsBuf {
            walk: rw.iter().map(|r| 1.0 / (1.0 + r * lw)).collect(),
            still_per_level: kappa
                .iter()
                .flat_map(|&k| rs.iter().map(move |r| 1.0 / (1.0 + k * r * ls)))
                .collect(),
        };
        let objects = &mut self.objects;
        for (entry, obj) in self.holders.entries(false) {
            let d = match obj {
                Obj::Density(id) => objects.densities.get_mut(id).weigh(f.view()),
                Obj::Mixture(id) => {
                    let mix = objects.mixtures.get_mut(id);
                    let r = mixture_rates(world, &self.tracks, self.tracks.spots(), si, mix, &[]);
                    let da = match mix.away {
                        Some(away) => objects.densities.get_mut(away).weigh(f.view()),
                        None => 0.0,
                    };
                    let still: Vec<f64> = kappa
                        .iter()
                        .map(|&k| expect(&r[STILL].map(|x| 1.0 / (1.0 + k * x * ls))))
                        .collect();
                    let walk = expect(&r[WALK].map(|x| 1.0 / (1.0 + x * lw)));
                    let dlog = mix.kappa_weigh(&still, walk);
                    mix.reweigh(dlog, da)
                }
            };
            self.holders.book(&mut self.hyps, entry, d);
        }
    }
}

/// Per hypothesis the expected number of walkers in view of sensor si (Tracker._count_walkers).
pub(super) fn count_walkers(hyps: &[Hyp], objects: &Objects, holders: &Holders, world: &World, si: usize) -> Vec<f64> {
    let n = world.tiles.n;
    let seen: Vec<f64> = world.sights[si]
        .tiles
        .iter()
        .map(|&g| if g > 0.1 { 1.0 } else { 0.0 })
        .collect();
    let grid = &world.sights[si].grid;
    let mut val = vec![0.0; hyps.len()];
    for (entry, obj) in holders.entries(false) {
        let v = match obj {
            Obj::Density(id) if n > 0 => objects.densities.get(id).walking_in(&seen),
            Obj::Density(_) => 0.0,
            Obj::Mixture(id) => {
                let mix = objects.mixtures.get(id);
                let pts = mix.sigma(world, true)[WALK];
                let inside = expect(&pts.map(|p| if grid.at(p) > 0.1 { 1.0 } else { 0.0 }));
                let mut v = (1.0 - mix.a) * mix.walking() * inside;
                if let Some(away) = mix.away.filter(|_| n > 0) {
                    v += mix.a * objects.densities.get(away).walking_in(&seen);
                }
                v
            }
        };
        for &h in holders.refs(entry) {
            val[h] += v;
        }
    }
    val
}

/// The ghost's life goes on by dt: per kind of ghost its survival, the hypothesis weighed by the
/// change of their mixture (Tracker._ghost_life).
fn ghost_life(hy: &mut Hyp, seg: SegId, lives: &[f64], dt: f64) {
    let g = hy.ghosts.get_mut(&seg).expect("a ghost track's life");
    let before = logsumexp(&g.life);
    for (l, life) in g.life.iter_mut().zip(lives) {
        *l += -dt / life;
    }
    hy.logw += logsumexp(&g.life) - before;
}

/// Per kind of ghost, the log factor of its track ending now beyond what the person's alternative
/// gets (MODEL.md 4.2; Tracker._ghost_end): the source either still there and not found again,
/// S_d(u) S(u), or died at some tau < u before being found again, int f_d(tau) S(tau) dtau - the
/// S(u) of a held track times S_d(u) + int f_d S / S(u). None if it was not held first.
fn ghost_end(p: &Params, info: &TrackInfo, t: f64) -> Option<Vec<f64>> {
    let lost = info.lost?;
    let u = t - lost.t;
    if u <= 0.0 {
        return None;
    }
    let s_u = no_find(p, lost.kind, u).max(1e-300);
    // tau = u x [0, geomspace(1e-4, 1, 48)] (numpy's: 10^linspace, the ends exact): S drops within
    // 0.1-0.3 s
    let n = 48;
    let step = 4.0 / (n - 1) as f64;
    let mut tau = vec![0.0];
    tau.extend((0..n).map(|i| {
        let g = match i {
            0 => 1e-4,
            _ if i == n - 1 => 1.0,
            _ => 10f64.powf(i as f64 * step + -4.0),
        };
        u * g
    }));
    let s_tau: Vec<f64> = tau.iter().map(|&x| no_find(p, lost.kind, x)).collect();
    Some(
        p.ghost_types
            .iter()
            .map(|&[_, life]| {
                let f: Vec<f64> = tau
                    .iter()
                    .zip(&s_tau)
                    .map(|(x, s)| (-x / life).exp() / life * s)
                    .collect();
                let terms: Vec<f64> = (0..n).map(|i| (f[i + 1] + f[i]) * (tau[i + 1] - tau[i])).collect();
                let died = pairwise_sum(&terms) / 2.0;
                ((-u / life).exp() + died / s_u).ln()
            })
            .collect(),
    )
}

/// How well sensor si could find its target held at z on somebody in each tile (Tracker._kk_tiles):
/// its sight times the kernel of finding it near the spot, averaged over the tile.
pub fn near_tiles(world: &World, si: usize, z: Point) -> Vec<f64> {
    let (tiles, grid) = (&world.tiles, &world.sights[si].grid);
    let r2 = world.params.find_radius.powf(2.0);
    let at: Vec<f64> = tiles
        .points
        .iter()
        .map(|&q| {
            let (dx, dy) = (q[0] - z[0], q[1] - z[1]);
            grid.at(q) * (-0.5 * (dx * dx + dy * dy) / r2).exp()
        })
        .collect();
    tiles.average(&at)
}

/// Per component E[sight x kernel] (Tracker._kk_gauss): the kernel exactly, the sight at the sigma
/// points of the position weighted by it.
pub fn near_mixture(world: &World, si: usize, mix: &Mixture, z: Point) -> [f64; 2] {
    let (k, pts) = mix.near_sigma(world, z, world.params.find_radius);
    let grid = &world.sights[si].grid;
    [0, 1].map(|m| k[m] * expect(&pts[m].map(|p| grid.at(p))))
}

/// The rates of sensor si per component [STILL, WALK] at its sigma points; 0 where the person has
/// a measuring track of this sensor - one person, one track per sensor (Tracker._gauss_rates).
/// `spots`: the sensors' tracks without those to skip (also skipped as measuring tracks here).
pub fn mixture_rates(
    world: &World,
    tracks: &Tracks,
    spots: &[Vec<TrackSpot>],
    si: usize,
    mix: &Mixture,
    skip: &[SegId],
) -> [[f64; 5]; 2] {
    let measuring = mix
        .slots
        .keys()
        .any(|&s| !skip.contains(&s) && tracks.measuring(s) && tracks.get(s).is_some_and(|i| i.si == si));
    if measuring {
        return [[0.0; 5]; 2];
    }
    let users: SensorSet = [si].into_iter().collect();
    world
        .rates
        .mixture_rates(&world.sights, spots, users, &mix.sigma(world, true))
}
