//! The LD2410C's energies weigh the hypotheses and the people (MODEL.md 4.3; filter.Tracker._ld_weigh,
//! _ld_points, _ld_apply): mean = background + what the people put in. Not a product over the people:
//! per hypothesis the people one after the other, each given those before at what they put in
//! afterwards (the ones with tracks first, the unknown ones last; exact for people at known places).
//! Each person is then weighed given all the others, mixed over the hypotheses holding them (the
//! marginals of JIPDA); the echo sources compete with the unseen people.

use crate::filter::densities::{Densities, DensityId, DensityShape, Kind, StillFactors, TileFactors};
use crate::filter::holders::Obj;
use crate::filter::hyp::Hyp;
use crate::filter::mixture::Mixture;
use crate::filter::{Filter, NotFinite};
use crate::ld2410::echoes::Echoes;
use crate::ld2410::likelihood::{Ratio, Stats};
use crate::ld2410::profile::expected;
use crate::ld2410::{CELLS, FLOOR, LOG_CAP, cell_values};
use crate::params::{STILL, WALK};
use crate::vmath::{logsumexp, nan_max, pairwise_sum};
use crate::world::{LdSight, World};

type Cells = [f64; CELLS];

/// What the filter keeps per LD2410C between its weighings.
#[derive(Debug, Default, Clone)]
pub struct LdState {
    /// its frames since they were last weighed
    pub stats: Option<Stats>,
    /// what the people put into its cells lately (the still ones lag)
    memory: Option<Cells>,
    /// its echo sources
    echoes: Option<Echoes>,
}

/// What the background (still learned in Python, phase 8) learns from a weighing (MODEL.md 4.3,
/// docs/rust-core.md 2.0): per hypothesis the mean the people, the echo sources and the background
/// put in before these frames, the hypotheses' weights before, P(nobody in view), P(no echo source
/// on) and the sources begun, judged with these frames, and the frames.
#[derive(Debug)]
pub struct LdLearn {
    pub mus: Vec<Cells>,
    pub prior_w: Vec<f64>,
    pub nobody: f64,
    pub echo_off: f64,
    pub began: f64,
    pub stats: Stats,
}

/// Where an LD2410C sees a person (or the unknown ones): the masses of the points and what each puts
/// into its cells (scaled by the cells' share of now); for a density with a measured amplitude which
/// of its tiles are weighed level by level.
#[derive(Debug)]
struct Points {
    mass: Vec<f64>,
    put: Vec<Cells>,
    big: Vec<bool>,
}

impl Points {
    fn seen(&self) -> bool {
        !self.mass.is_empty() && pairwise_sum(&self.mass) > 1e-12
    }
}

/// The mean and the masses of a density in the tiles this LD2410C sees (_ld_points' tiles, _ld_split):
/// walking, and standing at each level of the amplitude of their stay - level by level in the tiles
/// with enough of a measured stay, at its mean amplitude in the others; nothing measured: g = 1.
/// Reading the amplitude settles it.
fn tile_points(world: &World, ld: &LdSight, densities: &mut Densities, id: DensityId, share: f64) -> Points {
    let tiles = &ld.tiles;
    let m = tiles.idx.len();
    if m == 0 {
        return Points {
            mass: Vec::new(),
            put: Vec::new(),
            big: Vec::new(),
        };
    }
    let shape = densities.shape();
    let measured = densities.measured(id);
    let d = densities.get(id);
    let walk: Vec<f64> = tiles.idx.iter().map(|&c| share * d.walk[c]).collect();
    if !measured {
        // g = 1 everywhere: the walkers, then the still mass
        let mut mass = walk;
        mass.extend(tiles.idx.iter().map(|&c| share * d.still_at(&shape, c)));
        let put = tiles.walk.iter().chain(&tiles.still).copied().collect();
        return Points {
            mass,
            put,
            big: Vec::new(),
        };
    }
    let a1 = shape.amps + 1;
    let still = d.amp_mass(&shape, &tiles.idx); // (A + 1) x m
    let ms: Vec<f64> = (0..m).map(|j| (0..a1).fold(0.0, |s, a| s + still[a * m + j])).collect();
    let big: Vec<bool> = (0..m)
        .map(|j| share * (ms[j] - still[(a1 - 1) * m + j]) > world.params.ld_tile_mass)
        .collect();
    let amp = tile_amp(world);
    let mut mass = walk;
    let mut put: Vec<Cells> = tiles.walk.clone(); // the walkers put in their profile
    for (a, &g) in amp.iter().enumerate() {
        for j in (0..m).filter(|&j| big[j]) {
            mass.push(share * still[a * m + j]);
            put.push(tiles.still[j].map(|x| g * x));
        }
    }
    for j in (0..m).filter(|&j| !big[j]) {
        // at its mean amplitude
        let num = (0..a1).fold(0.0, |s, a| s + amp[a] * still[a * m + j]);
        let g = if ms[j] > 0.0 { num / ms[j] } else { 1.0 };
        mass.push(share * ms[j]);
        put.push(tiles.still[j].map(|x| g * x));
    }
    Points { mass, put, big }
}

/// The levels of the amplitude of a stay, and g = 1 for nothing measured (Shapes.tile_amp).
fn tile_amp(world: &World) -> Vec<f64> {
    world.shapes.amp.iter().copied().chain([1.0]).collect()
}

/// Where an LD2410C sees a person with a track (_ld_points): the standing component per level of its
/// amplitude and the walking one at their means, a component out of its view with no mass; with the
/// part gone through a door as a density.
fn mixture_points(world: &World, ld: &LdSight, densities: &mut Densities, mix: &Mixture) -> Points {
    let w = mix.weights();
    let place = |mode: usize| ld.place(mix.modes[mode].pos(), &world.rooms);
    let (ps, pw) = (place(STILL), place(WALK));
    let row_s = expected(&world.params, STILL, ps.angle, ps.slant, ps.sight);
    let row_w = expected(&world.params, WALK, pw.angle, pw.slant, pw.sight);
    let seen = |row: &Cells| if nan_max(row) > FLOOR { 1.0 } else { 0.0 };
    let (vs, vw) = (seen(&row_s), seen(&row_w));
    let aw = mix.amw(world.shapes.levels());
    let mut mass: Vec<f64> = aw.iter().map(|&a| (1.0 - mix.a) * (w[STILL] * a * vs)).collect();
    mass.push((1.0 - mix.a) * (w[WALK] * vw));
    let mut put: Vec<Cells> = world.shapes.amp.iter().map(|&g| row_s.map(|x| g * x)).collect();
    put.push(row_w);
    let mut big = Vec::new();
    if let Some(away) = mix.away.filter(|_| mix.a > 0.0) {
        let part = tile_points(world, ld, densities, away, mix.a);
        mass.extend(part.mass);
        put.extend(part.put);
        big = part.big;
    }
    Points { mass, put, big }
}

/// Bit-exact key of a vector of energies (for the caches by what the others put in).
fn key(v: &Cells) -> [u64; CELLS] {
    v.map(f64::to_bits)
}

/// A person after those before them in a hypothesis (by their entries): the log of their
/// normalizer and what they put in afterwards (_ld_weigh's memo).
type Before = ((usize, Vec<usize>), (f64, Cells));

/// A person's ratios against what the others put in, computed once per (object, what the others put
/// in) (_ld_weigh's cache).
#[derive(Default)]
struct Ratios {
    people: Vec<((usize, [u64; CELLS]), Ratio)>,
    echoes: Vec<([u64; CELLS], f64)>,
}

impl Filter {
    /// The frames sensor si's LD2410C gathered since its last weighing, made if none.
    pub fn ld_gather(&mut self, si: usize) -> &mut Stats {
        self.ld[si].stats.get_or_insert_with(Stats::default)
    }

    /// What sensor si's LD2410C gathered since its last weighing.
    pub fn ld_stats(&self, si: usize) -> Option<&Stats> {
        self.ld[si].stats.as_ref()
    }

    /// What the people put into sensor si's cells lately, and its echo sources' probabilities.
    #[cfg(test)]
    pub(crate) fn ld_memory_echo(&self, si: usize) -> (Option<&Cells>, Option<&[f64]>) {
        let ld = &self.ld[si];
        (ld.memory.as_ref(), ld.echoes.as_ref().map(|e| &e.p[..]))
    }

    /// The LD2410C of sensor si starts over (it changed: recalibrated, moved).
    pub fn ld_reset(&mut self, si: usize) {
        self.ld[si] = LdState::default();
    }

    /// The sensors whose LD2410C has gathered long enough to be weighed, in the order of their index.
    pub fn ld_due(&self, every: f64) -> Vec<usize> {
        (0..self.ld.len())
            .filter(|&si| self.ld[si].stats.as_ref().is_some_and(|st| st.time >= every))
            .collect()
    }

    /// Weighs the hypotheses and the people by the LD2410C si's frames since its last weighing,
    /// given its background b (16) and its echo sources' rate, both read before (MODEL.md 4.3; the
    /// learning order of docs/rust-core.md 2.0). Returns what the background learns if `learn`;
    /// normalizes. None if there were no frames.
    pub fn ld_weigh(
        &mut self,
        world: &World,
        si: usize,
        b: &Cells,
        rate: f64,
        learn: bool,
    ) -> Result<Option<LdLearn>, NotFinite> {
        let Some(st) = self.ld[si].stats.take() else {
            return Ok(None);
        };
        let p = &world.params;
        let ld = &world.ld[si];
        let prep = st.prepare(&world.ld_lik);
        // the still energies lag (the firmware smooths them; time constant ld_memory): over these
        // frames they are what the people put in before (the memory M) and only by 1 - wm what they
        // put in now. The moving ones follow at once.
        let memory = self.ld[si].memory;
        let a = (-st.time / p.ld_memory).exp();
        let wm = if memory.is_some() {
            p.ld_memory / st.time * (1.0 - a)
        } else {
            0.0
        };
        let now = cell_values(1.0, 1.0 - wm);
        let b0: Cells = match memory {
            Some(mem) => std::array::from_fn(|c| b[c] + wm * mem[c] * if now[c] < 1.0 { 1.0 } else { 0.0 }),
            None => *b,
        };
        // where it sees each object (by its entry in the holders)
        let entries: Vec<(usize, Obj)> = self.holders.entries(false).collect();
        let mut points: Vec<Points> = Vec::with_capacity(entries.len());
        for &(_, obj) in &entries {
            let objects = &mut self.objects;
            let mut pts = match obj {
                Obj::Density(id) => {
                    let r = objects.densities.get(id).r;
                    tile_points(world, ld, &mut objects.densities, id, r)
                }
                Obj::Mixture(m) => mixture_points(world, ld, &mut objects.densities, objects.mixtures.get(m)),
            };
            for row in &mut pts.put {
                for (x, n) in row.iter_mut().zip(&now) {
                    *x *= n;
                }
            }
            points.push(pts);
        }
        let entry_of = |obj: Obj| entries.iter().position(|&(_, o)| o == obj).expect("held");
        let seen: Vec<bool> = points.iter().map(Points::seen).collect();
        let prior_w = self.hyp_weights();
        let echo = self.ld[si].echoes.get_or_insert_with(|| Echoes::new(p));
        echo.predict(p, st.time, rate);
        let (e_mass, e_put) = echo.points(&now);
        let densities = &self.objects.densities;
        let unknown: Vec<bool> = entries
            .iter()
            .map(|&(_, obj)| matches!(obj, Obj::Density(d) if densities.get(d).kind == Kind::Intensity))
            .collect();
        // what the background learns: the people as the filter saw them before these frames, per
        // hypothesis the mean they and the background put in, and P(nobody in view)
        let mut mus = Vec::new();
        let mut nobody = 0.0;
        if learn {
            for (h, hy) in self.hyps.iter().enumerate() {
                let (mut mu, mut none) = (b0, 1.0);
                for obj in objects_of(hy) {
                    let e = entry_of(obj);
                    if !seen[e] {
                        continue;
                    }
                    let pts = &points[e];
                    let put = put_in(&pts.mass, &pts.put);
                    for (x, y) in mu.iter_mut().zip(&put) {
                        *x += y;
                    }
                    let mass = pairwise_sum(&pts.mass);
                    none *= if unknown[e] {
                        1.0 / (1.0 + mass)
                    } else {
                        (1.0 - mass).max(0.0)
                    };
                }
                mus.push(mu);
                nobody += prior_w[h] * none;
            }
        }
        // per hypothesis the people one after the other, the echo sources last
        let mut ratios = Ratios::default();
        let mut memo: Vec<Before> = Vec::new();
        let n = self.hyps.len();
        let mut logf = vec![0.0; n];
        let mut totals: Vec<Cells> = Vec::with_capacity(n);
        let mut owns: Vec<Vec<(Obj, Cells, Cells)>> = Vec::with_capacity(n);
        let mut tracked: Vec<Cells> = Vec::with_capacity(n);
        // the index of the ratio of object e (entry) given what the others put in, in the cache
        let ratio_of = |ratios: &mut Ratios, e: usize, rest: &Cells| -> usize {
            if let Some(i) = ratios.people.iter().position(|(k, _)| *k == (e, key(rest))) {
                return i;
            }
            let mu0: Cells = std::array::from_fn(|c| b0[c] + rest[c]);
            let r = prep.ratio(&points[e].mass, &points[e].put, &mu0, unknown[e]);
            ratios.people.push(((e, key(rest)), r));
            ratios.people.len() - 1
        };
        let echo_norm = |ratios: &mut Ratios, rest: &Cells| -> f64 {
            if let Some(&(_, norm)) = ratios.echoes.iter().find(|(k, _)| *k == key(rest)) {
                return norm;
            }
            let mu0: Cells = std::array::from_fn(|c| b0[c] + rest[c]);
            let norm = prep.ratio(&e_mass, &e_put, &mu0, false).norm;
            ratios.echoes.push((key(rest), norm));
            norm
        };
        for (h, hy) in self.hyps.iter().enumerate() {
            let mut sum: Cells = [0.0; CELLS];
            let mut prefix: Vec<usize> = Vec::new();
            let mut own: Vec<(Obj, Cells, Cells)> = Vec::new();
            for obj in objects_of(hy) {
                let e = entry_of(obj);
                if !seen[e] {
                    continue;
                }
                let k = (e, prefix.clone()); // the key of what came before
                let (norm, post) = match memo.iter().find(|(x, _)| *x == k) {
                    Some(&(_, hit)) => hit,
                    None => {
                        let i = ratio_of(&mut ratios, e, &sum);
                        let r = &ratios.people[i].1;
                        memo.push((k, (r.norm, r.post)));
                        (r.norm, r.post)
                    }
                };
                logf[h] += norm;
                own.push((obj, post, sum));
                for (x, y) in sum.iter_mut().zip(&post) {
                    *x += y;
                }
                prefix.push(e);
            }
            logf[h] += echo_norm(&mut ratios, &sum);
            // what the people with tracks put in, and the others as they were before these frames
            // (the echo sources compete with the unseen people)
            let mut with_tracks: Cells = [0.0; CELLS];
            for (obj, post, _) in &own {
                if matches!(obj, Obj::Mixture(_)) {
                    for (x, y) in with_tracks.iter_mut().zip(post) {
                        *x += y;
                    }
                }
            }
            let mut without: Cells = [0.0; CELLS];
            for obj in hy.hidden.iter().chain([&hy.unknown]).map(|&d| Obj::Density(d)) {
                let e = entry_of(obj);
                if seen[e] {
                    let put = put_in(&points[e].mass, &points[e].put);
                    for (x, y) in without.iter_mut().zip(&put) {
                        *x += y;
                    }
                }
            }
            tracked.push(std::array::from_fn::<f64, CELLS, _>(|c| with_tracks[c] + without[c]));
            totals.push(sum);
            owns.push(own);
        }
        let base = prep.loglik(&st, &b0);
        for (hy, l) in self.hyps.iter_mut().zip(&logf) {
            hy.logw += base + l;
        }
        let lw: Vec<f64> = prior_w.iter().zip(&logf).map(|(w, l)| w.max(1e-300).ln() + l).collect();
        let top = nan_max(&lw);
        let post_w: Vec<f64> = lw.iter().map(|l| (l - top).exp()).collect();
        let total = pairwise_sum(&post_w);
        let current: Cells = std::array::from_fn(|c| {
            totals.iter().zip(&post_w).fold(0.0, |s, (t, w)| s + w / total * t[c]) / now[c].max(1e-9)
        });
        self.ld[si].memory = Some(match memory {
            Some(mem) => std::array::from_fn(|c| a * mem[c] + (1.0 - a) * current[c]),
            None => current,
        });
        let echo = self.ld[si].echoes.as_ref().expect("made above");
        let log_off = echo.off().max(1e-300).ln();
        // each person given the others, mixed over the hypotheses holding them
        for (e, &(entry, obj)) in entries.iter().enumerate() {
            if !seen[e] {
                continue;
            }
            let mut hs: Vec<usize> = self.holders.refs(entry).to_vec();
            hs.dedup(); // the hypotheses holding it in order, each once
            let q: Vec<f64> = hs.iter().map(|&h| post_w[h]).collect();
            let qs = pairwise_sum(&q);
            if qs <= 0.0 {
                continue;
            }
            let intensity = unknown[e];
            let k = points[e].mass.len();
            let mut parts: Vec<Vec<f64>> = Vec::with_capacity(hs.len());
            let mut outs = Vec::new();
            for (&h, qh) in hs.iter().zip(&q) {
                let qh = qh / qs;
                let own = &owns[h];
                let at = own.iter().position(|(o, _, _)| *o == obj).expect("one of its people");
                let rest: Cells = if at == own.len() - 1 {
                    own[at].2
                } else {
                    std::array::from_fn(|c| totals[h][c] - own[at].1[c])
                };
                let i = ratio_of(&mut ratios, e, &rest);
                // an unseen person and an echo source are alternatives (both rare): where the person is
                // not, an echo may explain the energies - the person's view weighed against that
                let ze = echo_norm(&mut ratios, &rest);
                let r = &ratios.people[i].1;
                let lq = qh.max(1e-300).ln() - r.norm;
                if intensity {
                    parts.push(r.lr.iter().map(|l| lq + l + log_off - ze).collect()); // outside stays
                } else {
                    parts.push(r.lr.iter().map(|l| lq + l + log_off).collect());
                    outs.push(lq + ze);
                }
            }
            let mut f: Vec<f64> = (0..k)
                .map(|i| {
                    let col: Vec<f64> = parts.iter().map(|p| p[i]).collect();
                    let top = nan_max(&col);
                    top + col.iter().fold(0.0, |s, x| s + (x - top).exp()).ln()
                })
                .collect();
            if !intensity {
                // a person: only the shape changes (outside: 1); what is out of its view stays
                let out = logsumexp(&outs);
                for (x, m) in f.iter_mut().zip(&points[e].mass) {
                    *x = if *m > 0.0 { *x - out } else { 0.0 };
                }
            }
            for x in &mut f {
                *x = x.clamp(-700.0, LOG_CAP);
            }
            self.ld_apply(world, si, obj, &f, &points[e].big);
        }
        // the echo sources given the people with tracks and the unseen ones as they were, mixed over
        // the hypotheses
        let qs = pairwise_sum(&post_w);
        let (mut parts, mut outs) = (Vec::new(), Vec::new());
        for (h, w) in post_w.iter().enumerate() {
            let qh = w / qs;
            if qh < 1e-12 {
                continue;
            }
            let mu0: Cells = std::array::from_fn(|c| b0[c] + tracked[h][c]);
            let r = prep.ratio(&e_mass, &e_put, &mu0, false);
            let lq = qh.ln() - r.norm;
            parts.push(r.lr.iter().map(|l| lq + l).collect::<Vec<_>>());
            outs.push(lq);
        }
        let out = logsumexp(&outs);
        let f: Vec<f64> = (0..e_mass.len())
            .map(|i| {
                let col: Vec<f64> = parts.iter().map(|p| p[i]).collect();
                let top = nan_max(&col);
                (top + col.iter().fold(0.0, |s, x| s + (x - top).exp()).ln() - out).clamp(-700.0, LOG_CAP)
            })
            .collect();
        let echo = self.ld[si].echoes.as_mut().expect("made above");
        echo.weigh(&f);
        let learned = learn.then(|| LdLearn {
            mus,
            prior_w,
            nobody,
            echo_off: echo.off(),
            began: echo.began(),
            stats: st,
        });
        self.normalize(0.0)?;
        Ok(learned)
    }

    /// Multiplies a person (or the unknown ones) by exp(logf) at their points (elsewhere by 1); logf
    /// is capped, so the factors stay finite (_ld_apply). Standing in a tile weighed level by level:
    /// the factor per detectability is the sum over the levels of the amplitude given it, and the
    /// levels given it are reweighed (Swerling III, MODEL.md 4.3).
    fn ld_apply(&mut self, world: &World, si: usize, obj: Obj, logf: &[f64], big: &[bool]) {
        let idx = &world.ld[si].tiles.idx;
        match obj {
            Obj::Density(id) => {
                apply_tiles(&mut self.objects.densities, id, idx, logf, big);
            }
            Obj::Mixture(m) => {
                let amps = world.shapes.amps();
                let (a, away) = {
                    let mix = self.objects.mixtures.get(m);
                    (mix.a, mix.away)
                };
                let d_away = match away {
                    Some(away) if a > 0.0 && !idx.is_empty() => {
                        apply_tiles(&mut self.objects.densities, away, idx, &logf[amps + 1..], big)
                    }
                    _ => 0.0,
                };
                let mix = self.objects.mixtures.get_mut(m);
                // the amplitude's levels: their probabilities, and the factor of the standing component
                let top = nan_max(&logf[..amps]);
                let f: Vec<f64> = logf[..amps].iter().map(|x| (x - top).exp()).collect();
                let ka: Vec<f64> = mix.ka.iter().enumerate().map(|(i, v)| v * f[i % amps]).collect();
                let s = pairwise_sum(&ka);
                if s > 0.0 {
                    mix.ka = ka.iter().map(|v| v / s).collect();
                }
                let standing = if s > 0.0 { top + s.ln() } else { f64::NEG_INFINITY };
                mix.reweigh([standing, logf[amps]], d_away);
            }
        }
    }
}

/// The objects of a hypothesis in the order the LD2410C takes them: the people with tracks, the
/// known ones without, the unknown ones.
fn objects_of(hy: &Hyp) -> impl Iterator<Item = Obj> + '_ {
    hy.people
        .values()
        .map(|&m| Obj::Mixture(m))
        .chain(hy.hidden.iter().chain([&hy.unknown]).map(|&d| Obj::Density(d)))
}

/// sum_i m_i S_i: what masses at points put in.
fn put_in(mass: &[f64], put: &[Cells]) -> Cells {
    let mut out = [0.0; CELLS];
    for (m, row) in mass.iter().zip(put) {
        for (o, x) in out.iter_mut().zip(row) {
            *o += m * x;
        }
    }
    out
}

/// A density's factors at the LD2410C's tiles (_ld_apply's tiles): walkers exp(logf), standing per
/// level of the amplitude in the tiles `big` (reweighing the levels), at the mean amplitude in the
/// others; nothing measured: one factor for the still mass. Returns weigh_at's factor.
fn apply_tiles(densities: &mut Densities, id: DensityId, idx: &[usize], g: &[f64], big: &[bool]) -> f64 {
    let n = idx.len();
    let shape: DensityShape = densities.shape();
    let measured = densities.get(id).amp.is_some(); // settled when its points were made
    if !measured {
        let e: Vec<f64> = g.iter().map(|x| x.exp()).collect();
        return densities.get_mut(id).weigh_at(
            idx,
            TileFactors {
                walk: &e[..n],
                still: StillFactors::PerTile(&e[n..]),
            },
        );
    }
    let a1 = shape.amps + 1;
    let fw: Vec<f64> = g[..n].iter().map(|x| x.exp()).collect();
    let ib: Vec<usize> = (0..n).filter(|&j| big[j]).collect();
    let nb = ib.len();
    let mut fs = vec![0.0; shape.levels * n];
    let tail = &g[n + a1 * nb..];
    {
        let d = densities.get_mut(id);
        let levels = &mut d.amp.as_mut().expect("measured").levels;
        for (jb, &j) in ib.iter().enumerate() {
            let c = idx[j];
            let gs: Vec<f64> = (0..a1).map(|a| g[n + a * nb + jb]).collect();
            let top = nan_max(&gs);
            let e: Vec<f64> = gs.iter().map(|x| (x - top).exp()).collect();
            for k in 0..shape.levels {
                let amp: Vec<f64> = (0..a1).map(|a| levels[shape.amp_row(k, a)][c] * e[a]).collect();
                let tot = amp.iter().fold(0.0, |s, x| s + x);
                if tot > 0.0 {
                    for (a, x) in amp.iter().enumerate() {
                        levels[shape.amp_row(k, a)][c] = x / tot;
                    }
                }
                fs[k * n + j] = tot * top.exp();
            }
        }
    }
    for (i, j) in (0..n).filter(|&j| !big[j]).enumerate() {
        let f = tail[i].exp();
        for k in 0..shape.levels {
            fs[k * n + j] = f;
        }
    }
    densities.get_mut(id).weigh_at(
        idx,
        TileFactors {
            walk: &fw,
            still: StillFactors::PerLevel(&fs),
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filter::densities::DensityData;
    use crate::testing;

    /// A person standing in the test world's tile c, or nobody (the unknown ones only, few).
    fn filter_with(world: &World, c: usize) -> Filter {
        let shape = world.density_shape();
        let mut f = Filter::new(shape, world.sights.len());
        let empty = DensityData {
            walk: vec![0.0; shape.tiles],
            still: vec![0.0; shape.still_len()],
            region: vec![0.0; shape.region_len()],
            out: 0.0,
            clock: 0.0,
            r: 1.0,
            kind: Kind::Intensity,
            amp: None,
        };
        let objects = f.objects_mut();
        let unknown = objects.densities.insert(DensityData {
            out: 0.01,
            ..empty.clone() // the unknown ones: nearly nobody, out of the house
        });
        let mut person = DensityData {
            kind: Kind::Person,
            ..empty
        };
        for l in 0..shape.kinds {
            for k in 0..shape.levels {
                person.still[shape.still_row(l, k)][c] = 0.25;
            }
        }
        let person = objects.densities.insert(person);
        f.set_hyps(vec![
            Hyp::new(0.5f64.ln(), vec![person], unknown),
            Hyp::new(0.5f64.ln(), Vec::new(), unknown),
        ]);
        f
    }

    #[test]
    fn the_energies_of_somebody_there_favour_the_hypothesis_with_them() {
        let world = testing::flat();
        let ld = &world.ld[0];
        let j = ld.tiles.idx.len() / 2; // a tile it sees
        let c = ld.tiles.idx[j];
        let b = [
            13.0, 9.0, 4.5, 4.5, 4.5, 4.5, 4.5, 4.5, 4.5, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0,
        ];
        for (somebody, wins) in [(true, 0), (false, 1)] {
            let mut f = filter_with(&world, c);
            let e: Cells = std::array::from_fn(|k| {
                let put = if somebody { ld.tiles.still[j][k] } else { 0.0 };
                (b[k] + put).round().min(100.0)
            });
            for _ in 0..12 {
                f.ld_gather(0).add(&e, 0.089, false);
            }
            let learned = f
                .ld_weigh(&world, 0, &b, 1.0 / 10800.0, true)
                .expect("finite")
                .expect("frames");
            let w = f.hyp_weights();
            assert!(w[wins] > 0.6, "somebody {somebody}: {w:?}");
            assert_eq!(learned.mus.len(), 2);
            assert!(learned.nobody > 0.0 && learned.nobody < 1.0);
            assert!(f.ld_stats(0).is_none(), "the frames are used up");
            f.merge(); // the two say the same about the (no) tracks
            f.check().expect("after weighing");
        }
    }

    #[test]
    fn the_same_frames_weigh_the_same_bits() {
        let world = testing::flat();
        let c = world.ld[0].tiles.idx[3];
        let b = [
            13.0, 9.0, 4.5, 4.5, 4.5, 4.5, 4.5, 4.5, 4.5, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0,
        ];
        let run = || {
            let mut f = filter_with(&world, c);
            let mut rng = testing::Rng::new(3);
            for round in 0..4 {
                for _ in 0..12 {
                    let e: Cells = std::array::from_fn(|k| (b[k] + rng.range(0.0, 20.0)).round());
                    f.ld_gather(0).add(&e, 0.089, round % 2 == 0);
                }
                f.ld_weigh(&world, 0, &b, 1.0 / 10800.0, true).expect("finite");
            }
            format!(
                "{:?} {:?}",
                f.hyps(),
                f.objects()
                    .densities()
                    .ids()
                    .iter()
                    .map(|&d| f.objects().densities().data(d))
                    .collect::<Vec<_>>()
            )
        };
        assert_eq!(run(), run());
    }
}
