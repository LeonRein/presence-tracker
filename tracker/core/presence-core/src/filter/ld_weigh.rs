//! The LD2410C's energies weigh the hypotheses and the people (MODEL.md 4.3; filter.Tracker._ld_weigh,
//! _ld_points, _ld_apply): mean = background + what the people put in. Not a product over the people:
//! per hypothesis the people one after the other, each given those before at what they put in
//! afterwards (the ones with tracks first, the unknown ones last; exact for people at known places).
//! Each person is then weighed given all the others, mixed over the hypotheses holding them (the
//! marginals of JIPDA); the echo sources compete with the unseen people.
//!
//! The steps, each on what it reads and what it changes: the amplitudes settled (`settle`, `&mut
//! Objects`); where the sensor sees each object (`tile_points`, `mixture_points`, `&Densities`); the
//! hypotheses' chains of people (`Chains`, a tree of prefixes: hypotheses that begin with the same
//! people share their ratios); each object given the others (`marginal`), applied to it (`apply`,
//! `&mut Objects`); the echo sources (`echo_factors`, then `&mut LdSensor`).

use std::collections::BTreeMap;

use crate::filter::densities::{Densities, DensityId, Kind, StillFactors, TileFactors};
use crate::filter::holders::{Holders, Obj};
use crate::filter::hyp::Hyp;
use crate::filter::mixture::Mixture;
use crate::filter::objects::Objects;
use crate::filter::{Filter, NotFinite};
use crate::ld2410::likelihood::{Prepared, Ratio, Scratch, Stats};
use crate::ld2410::profile::expected;
use crate::ld2410::sensor::LdSensor;
use crate::ld2410::{CELLS, FLOOR, LOG_CAP, cell_values};
use crate::params::{STILL, WALK};
use crate::vmath::{at_least, logsumexp, nan_max, pairwise_sum};
use crate::world::{LdSight, World};

type Cells = [f64; CELLS];

/// What the background (still learned in Python, phase 8) learns from a weighing (MODEL.md 4.3,
/// docs/rust-core.md 2.0): per hypothesis the mean the people, the echo sources and the background
/// put in before these frames, the hypotheses' weights before, P(nobody in view), P(no echo source
/// on) and the sources begun, judged with these frames, the frames, and since when the housing's
/// LD2450 has had nothing in sight.
#[derive(Debug)]
pub struct LdLearn {
    pub mus: Vec<Cells>,
    pub prior_w: Vec<f64>,
    pub nobody: f64,
    pub echo_off: f64,
    pub began: f64,
    pub stats: Stats,
    pub target_t: f64,
}

/// How a density's standing mass is weighed at the LD2410C's tiles.
#[derive(Debug, Clone)]
enum Standing {
    /// nothing measured: the still mass of a tile with one factor (g = 1)
    Unmeasured,
    /// level by level of the amplitude in the tiles `big`, at the mean amplitude in the others
    Measured { big: Vec<bool> },
}

/// Where an LD2410C sees a person (or the unknown ones): the masses of the points and what each puts
/// into its cells (scaled by the cells' share of now); how a density at its tiles is weighed (the
/// object, or a person's part gone through a door; None: no density there).
#[derive(Debug)]
struct Points {
    mass: Vec<f64>,
    put: Vec<Cells>,
    tiles: Option<Standing>,
}

impl Points {
    fn none() -> Self {
        Self {
            mass: Vec::new(),
            put: Vec::new(),
            tiles: None,
        }
    }

    fn seen(&self) -> bool {
        !self.mass.is_empty() && pairwise_sum(&self.mass) > 1e-12
    }

    /// sum_i m_i S_i: what they put in.
    fn put_in(&self) -> Cells {
        let mut out = [0.0; CELLS];
        for (m, row) in self.mass.iter().zip(&self.put) {
            for (o, x) in out.iter_mut().zip(row) {
                *o += m * x;
            }
        }
        out
    }

    fn scale(&mut self, now: &Cells) {
        for row in &mut self.put {
            for (x, n) in row.iter_mut().zip(now) {
                *x *= n;
            }
        }
    }
}

/// The amplitudes of the densities the LD2410C reads, settled (reading them settled them in the
/// Python filter): the held densities and the parts of persons gone through a door, if it sees any
/// tile.
fn settle(ld: &LdSight, objects: &mut Objects, holders: &Holders) {
    if ld.tiles.idx.is_empty() {
        return;
    }
    for (_, obj) in holders.people() {
        let id = match obj {
            Obj::Density(id) => Some(id),
            Obj::Mixture(m) => {
                let mix = objects.mixtures.get(m);
                mix.away.filter(|_| mix.a > 0.0)
            }
        };
        if let Some(id) = id {
            objects.densities.measured(id);
        }
    }
}

/// The mean and the masses of a density in the tiles this LD2410C sees (_ld_points' tiles, _ld_split):
/// walking, and standing at each level `amp` of the amplitude of their stay - level by level in the
/// tiles with enough of a measured stay, at its mean amplitude in the others; nothing measured: g = 1.
fn tile_points(world: &World, ld: &LdSight, densities: &Densities, amp: &[f64], id: DensityId, share: f64) -> Points {
    let tiles = &ld.tiles;
    let m = tiles.idx.len();
    let shape = densities.shape();
    let d = densities.get(id);
    let mut mass: Vec<f64> = tiles.idx.iter().map(|&c| share * d.walk[c]).collect();
    if d.amp.is_none() {
        // g = 1 everywhere: the walkers, then the still mass
        mass.extend(tiles.idx.iter().map(|&c| share * d.still_at(&shape, c)));
        let put = tiles.walk.iter().chain(&tiles.still).copied().collect();
        return Points {
            mass,
            put,
            tiles: Some(Standing::Unmeasured),
        };
    }
    let a1 = shape.amps + 1;
    let still = d.amp_mass(&shape, &tiles.idx); // (A + 1) x m
    let ms: Vec<f64> = (0..m).map(|j| (0..a1).fold(0.0, |s, a| s + still[a * m + j])).collect();
    let big: Vec<bool> = (0..m)
        .map(|j| share * (ms[j] - still[(a1 - 1) * m + j]) > world.params.ld_tile_mass)
        .collect();
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
    Points {
        mass,
        put,
        tiles: Some(Standing::Measured { big }),
    }
}

/// Where an LD2410C sees a person with a track (_ld_points): the standing component per level of its
/// amplitude and the walking one at their means, a component out of its view with no mass; with the
/// part gone through a door as a density.
fn mixture_points(world: &World, ld: &LdSight, densities: &Densities, amp: &[f64], mix: &Mixture) -> Points {
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
    let mut tiles = None;
    if let Some(away) = mix.away.filter(|_| mix.a > 0.0 && !ld.tiles.idx.is_empty()) {
        let part = tile_points(world, ld, densities, amp, away, mix.a);
        mass.extend(part.mass);
        put.extend(part.put);
        tiles = part.tiles;
    }
    Points { mass, put, tiles }
}

/// What a weighing reads: the frames' likelihood, the background with what the people put in before
/// (the still energies lag), per entry in the holders its object and its points, the echo sources'.
struct LdContext<'a> {
    prep: Prepared<'a>,
    b0: Cells,
    objs: Vec<Obj>,
    points: Vec<Points>,
    /// per entry: in view at all
    seen: Vec<bool>,
    /// per entry: the unknown ones (an intensity, at most one of them in view)
    unknown: Vec<bool>,
    echo: Points,
    scratch: Scratch,
}

impl LdContext<'_> {
    fn mu0(&self, rest: &Cells) -> Cells {
        std::array::from_fn(|c| self.b0[c] + rest[c])
    }

    /// Object e given that the others put in `rest`.
    fn ratio(&mut self, e: usize, rest: &Cells) -> Ratio {
        let mu0 = self.mu0(rest);
        let pts = &self.points[e];
        self.prep
            .ratio(&mut self.scratch, &pts.mass, &pts.put, &mu0, self.unknown[e])
    }

    /// The echo sources given that the people put in `rest`.
    fn echo_ratio(&mut self, rest: &Cells) -> Ratio {
        let mu0 = self.mu0(rest);
        self.prep
            .ratio(&mut self.scratch, &self.echo.mass, &self.echo.put, &mu0, false)
    }
}

/// An object after those before it in a hypothesis.
#[derive(Debug)]
struct Node {
    parent: Option<usize>,
    entry: usize,
    /// it given those before
    ratio: Ratio,
    /// what it and those before put in
    after: Cells,
}

/// The people of every hypothesis one after the other, as a tree of prefixes.
struct Chains {
    nodes: Vec<Node>,
    /// per hypothesis its nodes from the first
    paths: Vec<Vec<usize>>,
    /// the echo sources' log normalizer given what the people put in up to a node (None: nobody),
    /// computed when asked
    echo_after: BTreeMap<Option<usize>, f64>,
    /// an object of a hypothesis but its last one given the others: by (the hypothesis' last
    /// node, its entry) its ratio and the echo sources' log normalizer
    rest: BTreeMap<(usize, usize), (Ratio, f64)>,
}

impl Chains {
    /// Per hypothesis its objects in view in the order the LD2410C takes them, each given those
    /// before.
    fn build(ctx: &mut LdContext, hyps: &[Hyp], holders: &Holders) -> Self {
        let mut nodes: Vec<Node> = Vec::new();
        let mut children: BTreeMap<(Option<usize>, usize), usize> = BTreeMap::new();
        let paths = hyps
            .iter()
            .map(|hy| {
                let mut path = Vec::new();
                let (mut at, mut sum) = (None, [0.0; CELLS]);
                for e in objects_of(hy).map(|obj| holders.entry(obj)) {
                    if !ctx.seen[e] {
                        continue;
                    }
                    let node = *children.entry((at, e)).or_insert_with(|| {
                        let ratio = ctx.ratio(e, &sum);
                        let after = std::array::from_fn(|c| sum[c] + ratio.post[c]);
                        nodes.push(Node {
                            parent: at,
                            entry: e,
                            ratio,
                            after,
                        });
                        nodes.len() - 1
                    });
                    path.push(node);
                    (at, sum) = (Some(node), nodes[node].after);
                }
                path
            })
            .collect();
        Self {
            nodes,
            paths,
            echo_after: BTreeMap::new(),
            rest: BTreeMap::new(),
        }
    }

    fn leaf(&self, h: usize) -> Option<usize> {
        self.paths[h].last().copied()
    }

    /// What all of hypothesis h's people put in.
    fn total(&self, h: usize) -> Cells {
        self.leaf(h).map_or([0.0; CELLS], |n| self.nodes[n].after)
    }

    fn echo_norm(&mut self, ctx: &mut LdContext, upto: Option<usize>) -> f64 {
        if let Some(&norm) = self.echo_after.get(&upto) {
            return norm;
        }
        let rest = upto.map_or([0.0; CELLS], |n| self.nodes[n].after);
        let norm = ctx.echo_ratio(&rest).norm;
        self.echo_after.insert(upto, norm);
        norm
    }

    /// Per hypothesis the log factor of the frames given its people and the echo sources (the
    /// background's not included).
    fn logf(&mut self, ctx: &mut LdContext) -> Vec<f64> {
        (0..self.paths.len())
            .map(|h| {
                let people = self.paths[h].iter().fold(0.0, |s, &n| s + self.nodes[n].ratio.norm);
                people + self.echo_norm(ctx, self.leaf(h))
            })
            .collect()
    }

    /// Per hypothesis what the people with tracks put in, and the others as they were before these
    /// frames (the echo sources compete with the unseen people).
    fn tracked(&self, ctx: &LdContext, hyps: &[Hyp], holders: &Holders) -> Vec<Cells> {
        hyps.iter()
            .zip(&self.paths)
            .map(|(hy, path)| {
                let mut with_tracks = [0.0; CELLS];
                for node in path.iter().map(|&n| &self.nodes[n]) {
                    if matches!(ctx.objs[node.entry], Obj::Mixture(_)) {
                        for (x, y) in with_tracks.iter_mut().zip(&node.ratio.post) {
                            *x += y;
                        }
                    }
                }
                let mut without = [0.0; CELLS];
                let known = hy.hidden.iter().chain([&hy.unknown]);
                for e in known.map(|&d| holders.entry(Obj::Density(d))) {
                    if ctx.seen[e] {
                        for (x, y) in without.iter_mut().zip(&ctx.points[e].put_in()) {
                            *x += y;
                        }
                    }
                }
                std::array::from_fn(|c| with_tracks[c] + without[c])
            })
            .collect()
    }

    /// Object e in hypothesis h given its others: its log ratios per point, its log normalizer, and
    /// the echo sources' log normalizer given the others.
    fn given_others(&mut self, ctx: &mut LdContext, h: usize, e: usize) -> (&[f64], f64, f64) {
        let path = &self.paths[h];
        let at = path
            .iter()
            .position(|&n| self.nodes[n].entry == e)
            .expect("one of its people");
        let (node, leaf) = (path[at], path[path.len() - 1]);
        if node == leaf {
            // the last one: given those before, as in its chain
            let ze = self.echo_norm(ctx, self.nodes[node].parent);
            let r = &self.nodes[node].ratio;
            return (&r.lr, r.norm, ze);
        }
        if !self.rest.contains_key(&(leaf, e)) {
            let (total, own) = (&self.nodes[leaf].after, &self.nodes[node].ratio.post);
            let rest: Cells = std::array::from_fn(|c| total[c] - own[c]);
            let ratio = ctx.ratio(e, &rest);
            let ze = ctx.echo_ratio(&rest).norm;
            self.rest.insert((leaf, e), (ratio, ze));
        }
        let (r, ze) = &self.rest[&(leaf, e)];
        (&r.lr, r.norm, *ze)
    }
}

/// log sum_p exp(parts[p][i]), by the largest (NaN wins).
fn column_logsumexp(parts: &[Vec<f64>], i: usize) -> f64 {
    let top = parts.iter().fold(f64::NEG_INFINITY, |t, p| {
        let x = p[i];
        if t.is_nan() || x.is_nan() { f64::NAN } else { t.max(x) }
    });
    top + parts.iter().fold(0.0, |s, p| s + (p[i] - top).exp()).ln()
}

/// Object e given all the others, mixed over the hypotheses holding it (by post_w): its log factor
/// per point, capped. None if no hypothesis holding it has weight.
fn marginal(
    ctx: &mut LdContext,
    chains: &mut Chains,
    holders: &Holders,
    e: usize,
    post_w: &[f64],
    log_off: f64,
) -> Option<Vec<f64>> {
    let hs: Vec<usize> = holders.holding(e).collect();
    let q: Vec<f64> = hs.iter().map(|&h| post_w[h]).collect();
    let qs = pairwise_sum(&q);
    if qs <= 0.0 {
        return None;
    }
    let intensity = ctx.unknown[e];
    let mut parts: Vec<Vec<f64>> = Vec::with_capacity(hs.len());
    let mut outs = Vec::new();
    for (&h, qh) in hs.iter().zip(&q) {
        let (lr, norm, ze) = chains.given_others(ctx, h, e);
        // an unseen person and an echo source are alternatives (both rare): where the person is not,
        // an echo may explain the energies - the person's view weighed against that
        let lq = at_least(qh / qs, 1e-300).ln() - norm;
        if intensity {
            parts.push(lr.iter().map(|l| lq + l + log_off - ze).collect()); // outside stays
        } else {
            parts.push(lr.iter().map(|l| lq + l + log_off).collect());
            outs.push(lq + ze);
        }
    }
    let mass = &ctx.points[e].mass;
    let mut f: Vec<f64> = (0..mass.len()).map(|i| column_logsumexp(&parts, i)).collect();
    if !intensity {
        // a person: only the shape changes (outside: 1); what is out of its view stays
        let out = logsumexp(&outs);
        for (x, m) in f.iter_mut().zip(mass) {
            *x = if *m > 0.0 { *x - out } else { 0.0 };
        }
    }
    for x in &mut f {
        *x = x.clamp(-700.0, LOG_CAP);
    }
    Some(f)
}

/// The echo sources' log factors given the people with tracks and the unseen ones as they were,
/// mixed over the hypotheses.
fn echo_factors(ctx: &mut LdContext, tracked: &[Cells], post_w: &[f64]) -> Vec<f64> {
    let qs = pairwise_sum(post_w);
    let (mut parts, mut outs) = (Vec::new(), Vec::new());
    for (w, rest) in post_w.iter().zip(tracked) {
        let qh = w / qs;
        if qh < 1e-12 {
            continue;
        }
        let r = ctx.echo_ratio(rest);
        let lq = qh.ln() - r.norm;
        parts.push(r.lr.iter().map(|l| lq + l).collect::<Vec<_>>());
        outs.push(lq);
    }
    let out = logsumexp(&outs);
    (0..ctx.echo.mass.len())
        .map(|i| (column_logsumexp(&parts, i) - out).clamp(-700.0, LOG_CAP))
        .collect()
}

/// Multiplies a person (or the unknown ones) by exp(logf) at their points (elsewhere by 1); logf is
/// capped, so the factors stay finite (_ld_apply).
fn apply(objects: &mut Objects, world: &World, ld: &LdSight, obj: Obj, logf: &[f64], tiles: Option<&Standing>) {
    let idx = &ld.tiles.idx;
    match obj {
        Obj::Density(id) => {
            apply_tiles(
                &mut objects.densities,
                id,
                idx,
                logf,
                tiles.expect("a density's points"),
            );
        }
        Obj::Mixture(m) => {
            let amps = world.shapes.amps();
            let mix = objects.mixtures.get_mut(m);
            let d_away = match (mix.away, tiles) {
                (Some(away), Some(standing)) => {
                    apply_tiles(&mut objects.densities, away, idx, &logf[amps + 1..], standing)
                }
                _ => 0.0,
            };
            let standing = mix.weigh_amplitude(&logf[..amps]);
            mix.reweigh([standing, logf[amps]], d_away);
        }
    }
}

/// A density's factors at the LD2410C's tiles (_ld_apply's tiles): walkers exp(logf); standing per
/// level of the amplitude in the tiles weighed so (reweighing the levels: Swerling III, MODEL.md
/// 4.3), at the mean amplitude in the others; nothing measured: one factor for the still mass.
/// Returns weigh_at's factor.
fn apply_tiles(densities: &mut Densities, id: DensityId, idx: &[usize], g: &[f64], standing: &Standing) -> f64 {
    let n = idx.len();
    let big = match standing {
        Standing::Unmeasured => {
            let e: Vec<f64> = g.iter().map(|x| x.exp()).collect();
            return densities.get_mut(id).weigh_at(
                idx,
                TileFactors {
                    walk: &e[..n],
                    still: StillFactors::PerTile(&e[n..]),
                },
            );
        }
        Standing::Measured { big } => big,
    };
    let shape = densities.shape();
    let a1 = shape.amps + 1;
    let fw: Vec<f64> = g[..n].iter().map(|x| x.exp()).collect();
    let ib: Vec<usize> = (0..n).filter(|&j| big[j]).collect();
    let nb = ib.len();
    let mut fs = vec![0.0; shape.levels * n];
    let tail = &g[n + a1 * nb..];
    {
        let d = densities.get_mut(id);
        let levels = &mut d.amp.as_mut().expect("measured").levels;
        let (mut e, mut amp) = (vec![0.0; a1], vec![0.0; a1]);
        for (jb, &j) in ib.iter().enumerate() {
            let c = idx[j];
            for (a, x) in e.iter_mut().enumerate() {
                *x = g[n + a * nb + jb];
            }
            let top = nan_max(&e);
            for x in &mut e {
                *x = (*x - top).exp();
            }
            for k in 0..shape.levels {
                for (a, x) in amp.iter_mut().enumerate() {
                    *x = levels[shape.amp_row(k, a)][c] * e[a];
                }
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

/// The objects of a hypothesis in the order the LD2410C takes them: the people with tracks, the
/// known ones without, the unknown ones.
fn objects_of(hy: &Hyp) -> impl Iterator<Item = Obj> + '_ {
    hy.people
        .values()
        .map(|&m| Obj::Mixture(m))
        .chain(hy.hidden.iter().chain([&hy.unknown]).map(|&d| Obj::Density(d)))
}

/// What the background learns: per hypothesis the mean the people as the filter saw them before
/// these frames and the background put in, and P(nobody in view) mixed over the hypotheses.
fn before(ctx: &LdContext, hyps: &[Hyp], holders: &Holders, prior_w: &[f64]) -> (Vec<Cells>, f64) {
    let mut nobody = 0.0;
    let mus = hyps
        .iter()
        .zip(prior_w)
        .map(|(hy, w)| {
            let (mut mu, mut none) = (ctx.b0, 1.0);
            for e in objects_of(hy).map(|obj| holders.entry(obj)) {
                if !ctx.seen[e] {
                    continue;
                }
                let pts = &ctx.points[e];
                for (x, y) in mu.iter_mut().zip(&pts.put_in()) {
                    *x += y;
                }
                let mass = pairwise_sum(&pts.mass);
                none *= if ctx.unknown[e] {
                    1.0 / (1.0 + mass)
                } else {
                    (1.0 - mass).max(0.0)
                };
            }
            nobody += w * none;
            mu
        })
        .collect();
    (mus, nobody)
}

impl Filter {
    /// Weighs the hypotheses and the people by the frames the LD2410C of sensor si gathered since
    /// its last weighing, given its background b and its echo sources' rate, both read before
    /// (MODEL.md 4.3; the learning order of docs/rust-core.md 2.0); normalizes. Returns what the
    /// background learns; None if there were no frames.
    pub fn ld_weigh(
        &mut self,
        world: &World,
        si: usize,
        sensor: &mut LdSensor,
        b: &Cells,
        rate: f64,
    ) -> Result<Option<LdLearn>, NotFinite> {
        if !(sensor.stats.time > 0.0) {
            return Ok(None);
        }
        let st = std::mem::take(&mut sensor.stats); // the frames are used up
        let p = &world.params;
        let ld = &world.ld[si];
        // the still energies lag (the firmware smooths them; time constant ld_memory): over these
        // frames they are what the people put in before (the memory M) and only by 1 - wm what they
        // put in now. The moving ones follow at once.
        let memory = sensor.memory;
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
        settle(ld, &mut self.objects, &self.holders);
        let amp: Vec<f64> = world.shapes.amp.iter().copied().chain([1.0]).collect(); // and g = 1: nothing measured
        let Objects { mixtures, densities } = &self.objects;
        // by entry in the holders; the sources of ghost tracks put nothing in
        let objs: Vec<Obj> = self.holders.entries().map(|(_, obj)| obj).collect();
        let mut points: Vec<Points> = objs.iter().map(|_| Points::none()).collect();
        for (e, obj) in self.holders.people() {
            let pts = &mut points[e];
            *pts = match obj {
                Obj::Density(id) => tile_points(world, ld, densities, &amp, id, densities.get(id).r),
                Obj::Mixture(m) => mixture_points(world, ld, densities, &amp, mixtures.get(m)),
            };
            pts.scale(&now);
        }
        let unknown = objs
            .iter()
            .map(|&obj| matches!(obj, Obj::Density(d) if densities.get(d).kind == Kind::Intensity))
            .collect();
        let prior_w = self.hyp_weights();
        sensor.echoes.predict(p, st.time, rate);
        let (mass, put) = sensor.echoes.points(&now);
        let mut ctx = LdContext {
            prep: st.prepare(&world.ld_lik),
            b0,
            objs,
            seen: points.iter().map(Points::seen).collect(),
            points,
            unknown,
            echo: Points {
                mass,
                put,
                ..Points::none()
            },
            scratch: Scratch::default(),
        };
        let (mus, nobody) = before(&ctx, &self.hyps, &self.holders, &prior_w);
        // per hypothesis the people one after the other, the echo sources last
        let mut chains = Chains::build(&mut ctx, &self.hyps, &self.holders);
        let logf = chains.logf(&mut ctx);
        let tracked = chains.tracked(&ctx, &self.hyps, &self.holders);
        let base = ctx.prep.loglik(&st, &b0);
        for (hy, l) in self.hyps.iter_mut().zip(&logf) {
            hy.logw += base + l;
        }
        let lw: Vec<f64> = prior_w
            .iter()
            .zip(&logf)
            .map(|(w, l)| at_least(*w, 1e-300).ln() + l)
            .collect();
        let top = nan_max(&lw);
        let post_w: Vec<f64> = lw.iter().map(|l| (l - top).exp()).collect();
        let total = pairwise_sum(&post_w);
        let current: Cells = std::array::from_fn(|c| {
            (0..post_w.len()).fold(0.0, |s, h| s + post_w[h] / total * chains.total(h)[c]) / now[c].max(1e-9)
        });
        sensor.memory = Some(match memory {
            Some(mem) => std::array::from_fn(|c| a * mem[c] + (1.0 - a) * current[c]),
            None => current,
        });
        // each person given the others, mixed over the hypotheses holding them
        let log_off = at_least(sensor.echoes.off(), 1e-300).ln();
        for e in 0..ctx.objs.len() {
            if !ctx.seen[e] {
                continue;
            }
            if let Some(f) = marginal(&mut ctx, &mut chains, &self.holders, e, &post_w, log_off) {
                apply(
                    &mut self.objects,
                    world,
                    ld,
                    ctx.objs[e],
                    &f,
                    ctx.points[e].tiles.as_ref(),
                );
            }
        }
        let f = echo_factors(&mut ctx, &tracked, &post_w);
        sensor.echoes.weigh(&f);
        let learned = LdLearn {
            mus,
            prior_w,
            nobody,
            echo_off: sensor.echoes.off(),
            began: sensor.echoes.began(),
            stats: st,
            target_t: sensor.collect.target_t,
        };
        self.normalize(0.0)?;
        Ok(Some(learned))
    }
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

    const B: Cells = [
        13.0, 9.0, 4.5, 4.5, 4.5, 4.5, 4.5, 4.5, 4.5, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.0,
    ];

    #[test]
    fn the_energies_of_somebody_there_favour_the_hypothesis_with_them() {
        let world = testing::flat();
        let ld = &world.ld[0];
        let j = ld.tiles.idx.len() / 2; // a tile it sees
        let c = ld.tiles.idx[j];
        for (somebody, wins) in [(true, 0), (false, 1)] {
            let mut f = filter_with(&world, c);
            let mut sensor = LdSensor::new(&world.params);
            let e: Cells = std::array::from_fn(|k| {
                let put = if somebody { ld.tiles.still[j][k] } else { 0.0 };
                (B[k] + put).round().min(100.0)
            });
            for _ in 0..12 {
                sensor.stats.add(&e, 0.089, false);
            }
            let learned = f
                .ld_weigh(&world, 0, &mut sensor, &B, 1.0 / 10800.0)
                .expect("finite")
                .expect("frames");
            let w = f.hyp_weights();
            assert!(w[wins] > 0.6, "somebody {somebody}: {w:?}");
            assert_eq!(learned.mus.len(), 2);
            assert!(learned.nobody > 0.0 && learned.nobody < 1.0);
            assert_eq!(sensor.stats().time, 0.0, "the frames are used up");
            f.merge(); // the two say the same about the (no) tracks
            f.check().expect("after weighing");
        }
    }

    #[test]
    fn the_same_frames_weigh_the_same_bits() {
        let world = testing::flat();
        let c = world.ld[0].tiles.idx[3];
        let run = || {
            let mut f = filter_with(&world, c);
            let mut sensor = LdSensor::new(&world.params);
            let mut rng = testing::Rng::new(3);
            for round in 0..4 {
                for _ in 0..12 {
                    let e: Cells = std::array::from_fn(|k| (B[k] + rng.range(0.0, 20.0)).round());
                    sensor.stats.add(&e, 0.089, round % 2 == 0);
                }
                f.ld_weigh(&world, 0, &mut sensor, &B, 1.0 / 10800.0).expect("finite");
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
