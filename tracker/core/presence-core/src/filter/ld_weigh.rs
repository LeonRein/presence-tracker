//! The LD2410C's energies weigh the hypotheses and the people (MODEL.md 4.3; filter.Tracker._ld_weigh,
//! _ld_points, _ld_apply): mean = background + what the people put in. Not a product over the people:
//! per hypothesis the people one after the other, each given those before at what they put in
//! afterwards (the ones with tracks first, the unknown ones last; exact for people at known places).
//! Each person is then weighed given all the others, mixed over the hypotheses holding them (the
//! marginals of JIPDA); the echo sources are the alternative to the unseen people, as clutter to a
//! target.
//!
//! The steps, each on what it reads and what it changes: the amplitudes settled (`settle`, `&mut
//! Objects`); where the sensor sees each object (`tile_points`, `mixture_points`, `&Densities`); the
//! hypotheses' chains of people (`Chains`, a tree of prefixes: hypotheses that begin with the same
//! people share their ratios); each object given the others (`marginal`), applied to it (`apply`,
//! `&mut Objects`); the echo sources (`echo_factors`, then `&mut LdSensor`).

use std::borrow::Cow;
use std::collections::BTreeMap;
use std::f64::consts::{LN_2, PI};

use crate::filter::densities::{Densities, DensityId, Kind, StillFactors, TileFactors};
use crate::filter::holders::{Holders, Obj};
use crate::filter::hyp::Hyp;
use crate::filter::mixture::Mixture;
use crate::filter::objects::Objects;
use crate::filter::{Filter, NotFinite};
use crate::geometry::Point;
use crate::ld2410::likelihood::{Prepared, Ratio, Scratch, Stats};
use crate::ld2410::profile::expected;
use crate::ld2410::sensor::LdSensor;
use crate::ld2410::{CELLS, FLOOR, LOG_CAP, cell_values};
use crate::params::{STILL, WALK};
use crate::vmath::{at_least, logsumexp, nan_max, pairwise_sum};
use crate::world::{LdSight, World};

type Cells = [f64; CELLS];

/// Still mass at a place below this stands nobody in a group with those after it.
const DEPOSIT: f64 = 1e-6;

/// What the background (still learned in Python, phase 8) learns from a weighing (MODEL.md 4.3,
/// docs/rust-core.md 2.0): per hypothesis the mean the people, the echo sources and the background
/// put in before these frames, the hypotheses' weights before, P(nobody in view), P(no echo source
/// on) and the sources begun, judged with these frames, the frames, and since when the housing's
/// LD2450 has had nothing in sight. `tracked`: per hypothesis the mean of the background and the people
/// with tracks alone (what the background learns from once the housing's LD2450 has been quiet long).
#[derive(Debug)]
pub struct LdLearn {
    pub mus: Vec<Cells>,
    pub tracked: Vec<Cells>,
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
/// object, or a person's part gone through a door; None: no density there); per point the place it
/// stands at (an index into `spots`; None: walking) and its amplitude g, for the group it may stand in
/// (MODEL.md 4.3 "Gruppen").
#[derive(Debug)]
struct Points {
    mass: Vec<f64>,
    put: Vec<Cells>,
    tiles: Option<Standing>,
    at: Vec<Option<(usize, f64)>>,
    spots: Vec<Point>,
}

/// Still people at a place, for those after them (MODEL.md 4.3 "Gruppen"): the place (range and bearing from the
/// sensor, `Group::polar`), their mass and mass x amplitude.
#[derive(Debug, Clone, Copy)]
struct Stand {
    at: [f64; 2],
    mass: f64,
    gmass: f64,
}

/// Per spot of an object: the share of it with somebody before it at one place, and their mean amplitude.
type With = Vec<(f64, f64)>;

impl Points {
    fn none() -> Self {
        Self {
            mass: Vec::new(),
            put: Vec::new(),
            tiles: None,
            at: Vec::new(),
            spots: Vec::new(),
        }
    }

    fn seen(&self) -> bool {
        !self.mass.is_empty() && pairwise_sum(&self.mass) > 1e-12
    }

    /// The still mass per spot after weighing (`lr`, `norm`: the ratio of the points; None: before), where it is
    /// above `DEPOSIT`: who stands there for those after it.
    fn deposits(&self, lr: Option<(&[f64], f64)>, group: &Group) -> Vec<Stand> {
        let mut per = vec![(0.0, 0.0); self.spots.len()];
        for (k, (&m, at)) in self.mass.iter().zip(&self.at).enumerate() {
            if let (Some((s, g)), true) = (at, m > 0.0) {
                let w = match lr {
                    Some((lr, norm)) => m * (lr[k] - norm).exp(),
                    None => m,
                };
                per[*s].0 += w;
                per[*s].1 += w * g;
            }
        }
        self.spots
            .iter()
            .zip(per)
            .filter(|&(_, (m, _))| m > DEPOSIT)
            .map(|(&p, (mass, gmass))| Stand {
                at: group.polar(p),
                mass,
                gmass,
            })
            .collect()
    }

    /// Per spot, who stands with it before it (`stand`), at one place as far as the housing's LD2450 does not
    /// resolve it from them (still people, `resolution`; A_Svensson2012 eq. 18-20), at most one; None: nobody.
    fn with_given(&self, stand: &[Stand], group: &Group) -> Option<With> {
        if stand.is_empty() || group.m2 >= 2.0 || self.spots.is_empty() {
            return None;
        }
        let mut any = false;
        let with: With = self
            .spots
            .iter()
            .map(|&p| {
                let at = group.polar(p);
                let (m, gm) = stand.iter().fold((0.0, 0.0), |(m, gm), st| {
                    let c = group.together(at, st.at);
                    (m + c * st.mass, gm + c * st.gmass)
                });
                if m > 1e-12 {
                    any = true;
                    (m.min(1.0), gm / m)
                } else {
                    (0.0, 1.0)
                }
            })
            .collect();
        any.then_some(with)
    }

    /// What each point puts in given who stands with it before (`with`): a group of n at one place gives
    /// g m(n) S, m(n) = 1 + (n - 1)(m2 - 1), g the group's amplitude (MODEL.md 4.3 "Gruppen"). A standing point
    /// puts in its own g S as far as nobody before stands with it, and (m2 - 1) g' S with the amplitude g' of
    /// those who do.
    fn put_with(&self, with: Option<&With>, group: &Group) -> Cow<'_, [Cells]> {
        let Some(with) = with else {
            return Cow::Borrowed(&self.put);
        };
        let more = group.m2 - 1.0;
        Cow::Owned(
            self.put
                .iter()
                .zip(&self.at)
                .map(|(row, at)| match at {
                    Some((s, g)) if with[*s].0 > 0.0 => {
                        let (o, gb) = with[*s];
                        let f = 1.0 - o + o * more * gb / g;
                        row.map(|x| x * f)
                    }
                    _ => *row,
                })
                .collect(),
        )
    }

    /// sum_i m_i S_i given who stands with them before: what they put in before these frames.
    fn put_in(&self, with: Option<&With>, group: &Group) -> Cells {
        let put = self.put_with(with, group);
        let mut out = [0.0; CELLS];
        for (m, row) in self.mass.iter().zip(put.iter()) {
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
    let spots: Vec<Point> = tiles.idx.iter().map(|&c| world.tiles.centers[c]).collect();
    let mut at: Vec<Option<(usize, f64)>> = vec![None; m];
    if d.amp.is_none() {
        // g = 1 everywhere: the walkers, then the still mass
        mass.extend(tiles.idx.iter().map(|&c| share * d.still_at(&shape, c)));
        at.extend((0..m).map(|j| Some((j, 1.0))));
        let put = tiles.walk.iter().chain(&tiles.still).copied().collect();
        return Points {
            mass,
            put,
            tiles: Some(Standing::Unmeasured),
            at,
            spots,
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
            at.push(Some((j, g)));
        }
    }
    for j in (0..m).filter(|&j| !big[j]) {
        // at its mean amplitude
        let num = (0..a1).fold(0.0, |s, a| s + amp[a] * still[a * m + j]);
        let g = if ms[j] > 0.0 { num / ms[j] } else { 1.0 };
        mass.push(share * ms[j]);
        put.push(tiles.still[j].map(|x| g * x));
        at.push(Some((j, g)));
    }
    Points {
        mass,
        put,
        tiles: Some(Standing::Measured { big }),
        at,
        spots,
    }
}

/// Where an LD2410C sees a person with a track (_ld_points): the standing component per level of its
/// amplitude and the walking one at their means, a component out of its view with no mass; with the
/// part gone through a door as a density.
fn mixture_points(world: &World, ld: &LdSight, densities: &Densities, amp: &[f64], mix: &Mixture) -> Points {
    let w = mix.weights();
    let place = |mode: usize| ld.place(mix.modes[mode].pos());
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
    let mut at: Vec<Option<(usize, f64)>> = world.shapes.amp.iter().map(|&g| Some((0, g))).collect();
    at.push(None);
    let mut spots = vec![mix.modes[STILL].pos()];
    let mut tiles = None;
    if let Some(away) = mix.away.filter(|_| mix.a > 0.0 && !ld.tiles.idx.is_empty()) {
        let part = tile_points(world, ld, densities, amp, away, mix.a);
        mass.extend(part.mass);
        put.extend(part.put);
        at.extend(part.at.iter().map(|a| a.map(|(s, g)| (s + 1, g))));
        spots.extend(part.spots);
        tiles = part.tiles;
    }
    Points {
        mass,
        put,
        tiles,
        at,
        spots,
    }
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
    group: Group,
}

/// People standing together for an LD2410C (MODEL.md 4.3 "Gruppen"): what two at one place give in the profile of
/// one, m(2) (`ld_group`), and where they are one place for it: unresolved by the LD2450 in its housing.
struct Group {
    m2: f64,
    at: Point,
    /// m: still people this far apart (range, cross) are resolved half of the time (`resolution`)
    res: [f64; 2],
}

impl Group {
    /// A place as the sensor sees it: range and bearing.
    fn polar(&self, p: Point) -> [f64; 2] {
        let (dx, dy) = (p[0] - self.at[0], p[1] - self.at[1]);
        [dx.hypot(dy), dy.atan2(dx)]
    }

    /// The share of two places (`polar`) at one place: 1 - P_R (the form of `RateModel::mask`, the still people's
    /// scale); 0 beyond three times the range's scale.
    fn together(&self, p: [f64; 2], q: [f64; 2]) -> f64 {
        let dr = (p[0] - q[0]) / self.res[0];
        if dr.abs() > 3.0 {
            return 0.0;
        }
        let mut a = p[1] - q[1];
        if a > PI {
            a -= 2.0 * PI;
        } else if a <= -PI {
            a += 2.0 * PI;
        }
        let dc = 0.5 * (p[0] + q[0]) * a / self.res[1];
        let x = dr * dr + dc * dc;
        if x > 25.0 { 0.0 } else { (-LN_2 * x).exp() }
    }
}

impl LdContext<'_> {
    fn mu0(&self, rest: &Cells) -> Cells {
        std::array::from_fn(|c| self.b0[c] + rest[c])
    }

    /// Object e given that the others put in `rest` and who stands with it before it (`with`).
    fn ratio(&mut self, e: usize, rest: &Cells, with: Option<&With>) -> Ratio {
        let mu0 = self.mu0(rest);
        let pts = &self.points[e];
        let put = pts.put_with(with, &self.group);
        self.prep.ratio(&mut self.scratch, &pts.mass, &put, &mu0)
    }

    /// The echo sources given that the people put in `rest`.
    fn echo_ratio(&mut self, rest: &Cells) -> Ratio {
        let mu0 = self.mu0(rest);
        self.prep
            .ratio(&mut self.scratch, &self.echo.mass, &self.echo.put, &mu0)
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
    /// where it and those before stand, after weighing (their still mass by place)
    stand: Vec<Stand>,
    /// who stands with it before it
    with: Option<With>,
    /// what it puts in before these frames, given who stands with it before it
    before: Cells,
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
                        let stand: &[Stand] = at.map_or(&[], |n: usize| &nodes[n].stand);
                        let pts = &ctx.points[e];
                        let with = pts.with_given(stand, &ctx.group);
                        let before = pts.put_in(with.as_ref(), &ctx.group);
                        let mut stand = stand.to_vec();
                        let ratio = ctx.ratio(e, &sum, with.as_ref());
                        let after = std::array::from_fn(|c| sum[c] + ratio.post[c]);
                        stand.extend(ctx.points[e].deposits(Some((&ratio.lr, ratio.norm)), &ctx.group));
                        nodes.push(Node {
                            parent: at,
                            entry: e,
                            ratio,
                            after,
                            stand,
                            with,
                            before,
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

    /// Per hypothesis what the known people put in: those with tracks, those without as they were
    /// before these frames. A source is what they do not explain; the unknown ones are its
    /// alternative and not counted (MODEL.md 4.3).
    fn known(&self, ctx: &LdContext, hyps: &[Hyp]) -> Vec<Cells> {
        hyps.iter()
            .zip(&self.paths)
            .map(|(hy, path)| {
                let mut known = [0.0; CELLS];
                for node in path.iter().map(|&n| &self.nodes[n]) {
                    if matches!(ctx.objs[node.entry], Obj::Mixture(_)) {
                        for (x, y) in known.iter_mut().zip(&node.ratio.post) {
                            *x += y;
                        }
                    }
                }
                for node in path.iter().map(|&n| &self.nodes[n]) {
                    let hidden = match ctx.objs[node.entry] {
                        Obj::Density(d) => hy.hidden.contains(&d),
                        Obj::Mixture(_) => false,
                    };
                    if hidden {
                        for (x, y) in known.iter_mut().zip(&node.before) {
                            *x += y;
                        }
                    }
                }
                known
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
            let ratio = ctx.ratio(e, &rest, self.nodes[node].with.as_ref());
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
fn marginal(ctx: &mut LdContext, chains: &mut Chains, holders: &Holders, e: usize, post_w: &[f64]) -> Option<Vec<f64>> {
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
        // an unseen person and an echo source are alternatives (both rare), as target and clutter: where
        // the person is not, a source may explain the energies, not worse than nothing does (a source
        // that explains nothing is no evidence for a person)
        let ze = ze.max(0.0);
        let lq = at_least(qh / qs, 1e-300).ln() - norm;
        if intensity {
            parts.push(lr.iter().map(|l| lq + l - ze).collect()); // outside stays
        } else {
            parts.push(lr.iter().map(|l| lq + l).collect());
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

/// The echo sources' log factors given the known people, mixed over the hypotheses.
fn echo_factors(ctx: &mut LdContext, known: &[Cells], post_w: &[f64]) -> Vec<f64> {
    let qs = pairwise_sum(post_w);
    let (mut parts, mut outs) = (Vec::new(), Vec::new());
    for (w, rest) in post_w.iter().zip(known) {
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
/// these frames and the background put in (each given who stands with it before it, as in its chain), the same
/// of the people with tracks alone, and P(nobody in view) mixed over the hypotheses.
fn before(ctx: &LdContext, chains: &Chains, prior_w: &[f64]) -> (Vec<Cells>, Vec<Cells>, f64) {
    let mut nobody = 0.0;
    let (mus, tracked) = chains
        .paths
        .iter()
        .zip(prior_w)
        .map(|(path, w)| {
            let (mut mu, mut tracked, mut none) = (ctx.b0, ctx.b0, 1.0);
            for node in path.iter().map(|&n| &chains.nodes[n]) {
                let put = &node.before;
                for (x, y) in mu.iter_mut().zip(put) {
                    *x += y;
                }
                if matches!(ctx.objs[node.entry], Obj::Mixture(_)) {
                    for (x, y) in tracked.iter_mut().zip(put) {
                        *x += y;
                    }
                }
                none *= (1.0 - pairwise_sum(&ctx.points[node.entry].mass)).max(0.0);
            }
            nobody += w * none;
            (mu, tracked)
        })
        .unzip();
    (mus, tracked, nobody)
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
            group: Group {
                m2: p.ld_group,
                at: ld.at,
                res: p.resolution[STILL],
            },
        };
        // per hypothesis the people one after the other, the echo sources last
        let mut chains = Chains::build(&mut ctx, &self.hyps, &self.holders);
        let (mus, tracked, nobody) = before(&ctx, &chains, &prior_w);
        let logf = chains.logf(&mut ctx);
        let known = chains.known(&ctx, &self.hyps);
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
        for e in 0..ctx.objs.len() {
            if !ctx.seen[e] {
                continue;
            }
            if let Some(f) = marginal(&mut ctx, &mut chains, &self.holders, e, &post_w) {
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
        let f = echo_factors(&mut ctx, &known, &post_w);
        sensor.echoes.weigh(&f);
        let learned = LdLearn {
            mus,
            tracked,
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
    fn energies_alone_tell_of_at_most_one_unknown_person_in_view() {
        let mut world = testing::flat();
        world.params.ld_echo_amps = vec![1e-9]; // sources that explain nothing: none to compete
        let ld = &world.ld[0];
        let j = ld.tiles.idx.len() / 2;
        let c = ld.tiles.idx[j];
        let mut f = filter_with(&world, c);
        let unknown = f.hyps()[0].unknown;
        f.objects_mut().densities.get_mut(unknown).walk[c] = 0.1;
        f.set_hyps(vec![Hyp::new(0.0, Vec::new(), unknown)]);
        let mut sensor = LdSensor::new(&world.params);
        // the energies of three people, no echo sources to compete
        let three: Cells = std::array::from_fn(|k| (B[k] + 3.0 * ld.tiles.walk[j][k]).round().min(100.0));
        for _ in 0..30 {
            for _ in 0..12 {
                sensor.stats.add(&three, 0.089, false);
            }
            f.ld_weigh(&world, 0, &mut sensor, &B, 1e-30).expect("finite");
        }
        let there = f.objects().densities().get(unknown).total();
        assert!(there > 0.5 && there <= 1.0 + 0.01 + 1e-12, "{there}"); // in view, and the 0.01 out of the house
    }

    /// Two people standing in the test world's tile c with existence r (each a density of its own, in this order),
    /// in one hypothesis; the unknown ones nearly nobody.
    fn two_at(world: &World, c: usize, r: [f64; 2]) -> (Filter, [DensityId; 2]) {
        let mut f = filter_with(world, c);
        let first = f.hyps()[0].hidden[0];
        let unknown = f.hyps()[0].unknown;
        let objects = f.objects_mut();
        let data = objects.densities.data(first);
        let second = objects.densities.insert(DensityData { r: r[1], ..data.clone() });
        let first = objects.densities.insert(DensityData { r: r[0], ..data });
        f.set_hyps(vec![Hyp::new(0.0, vec![first, second], unknown)]);
        (f, [first, second])
    }

    /// `seconds` of frames (one weighing per 12 frames) with the energies e; the echo sources explain nothing.
    fn weigh_for(world: &World, f: &mut Filter, e: &Cells, seconds: usize) {
        let mut sensor = LdSensor::new(&world.params);
        for _ in 0..seconds {
            for _ in 0..12 {
                sensor.stats.add(e, 0.089, false);
            }
            f.ld_weigh(world, 0, &mut sensor, &B, 1e-30).expect("finite");
        }
    }

    #[test]
    fn two_at_one_place_reading_the_energy_of_one_keep_the_second() {
        // MODEL.md 4.3 "Gruppen": two at one place give hardly more than one (m(2) 1.0-1.15, sofa 11.11.). The
        // energies of one person cannot refute an unresolved second beside the first; as two adding up (m(2) = 2,
        // up to 0.65) they did within a minute
        let mut world = testing::flat();
        world.params.ld_echo_amps = vec![1e-9];
        let ld = &world.ld[0];
        let j = ld.tiles.idx.len() / 2;
        let c = ld.tiles.idx[j];
        let one: Cells = std::array::from_fn(|k| (B[k] + ld.tiles.still[j][k]).round().min(100.0));
        let second = |m2: f64| {
            let mut world = testing::flat();
            world.params.ld_echo_amps = vec![1e-9];
            world.params.ld_group = m2;
            let (mut f, [_, second]) = two_at(&world, c, [0.999, 0.9]);
            weigh_for(&world, &mut f, &one, 60);
            f.objects().densities().get(second).r
        };
        let (group, added) = (second(world.params.ld_group), second(2.0));
        assert!(group > 0.8, "the second {group}");
        assert!(added < 0.3, "the second if two added up {added}");
        // and the first is not pushed out instead
        let (mut f, [first, _]) = two_at(&world, c, [0.9, 0.9]);
        weigh_for(&world, &mut f, &one, 60);
        assert!(f.objects().densities().get(first).r > 0.95);
    }

    #[test]
    fn one_alone_reading_nothing_is_refuted_and_two_do_not_shield_each_other() {
        // nobody beside her: the background's energies refute her as before; two at one place reading nothing go
        // together (the first by the full profile, the second by what is left of the first, MODEL.md 4.3 "Gruppen")
        let mut world = testing::flat();
        world.params.ld_echo_amps = vec![1e-9];
        let ld = &world.ld[0];
        let j = ld.tiles.idx.len() / 2;
        let c = ld.tiles.idx[j];
        let nothing: Cells = std::array::from_fn(|k| B[k].round());
        let mut f = filter_with(&world, c);
        let alone = f.hyps()[0].hidden[0];
        *f.objects_mut().densities.get_mut(alone).r = 0.9;
        let unknown = f.hyps()[0].unknown;
        f.set_hyps(vec![Hyp::new(0.0, vec![alone], unknown)]);
        weigh_for(&world, &mut f, &nothing, 20);
        let r_alone = f.objects().densities().get(alone).r;
        assert!(r_alone < 0.05, "alone {r_alone}");
        // the same as if people added up: nobody stands with her
        let mut added = testing::flat();
        added.params.ld_echo_amps = vec![1e-9];
        added.params.ld_group = 2.0;
        let mut g = filter_with(&added, c);
        let a = g.hyps()[0].hidden[0];
        *g.objects_mut().densities.get_mut(a).r = 0.9;
        g.set_hyps(vec![Hyp::new(0.0, vec![a], unknown)]);
        weigh_for(&added, &mut g, &nothing, 20);
        assert!((g.objects().densities().get(a).r - r_alone).abs() < 1e-9 * r_alone.max(1e-300));
        let (mut f, both) = two_at(&world, c, [0.9, 0.9]);
        weigh_for(&world, &mut f, &nothing, 40);
        for d in both {
            let r = f.objects().densities().get(d).r;
            assert!(r < 0.05, "together {r}");
        }
    }

    /// An LD2410C with an echo source on, whichever: as if frames had called for one.
    fn with_a_source_on(p: &crate::params::Params) -> LdSensor {
        let mut sensor = LdSensor::new(p);
        sensor.echoes.predict(p, 1.0, 1.0 / 3600.0);
        let on = sensor.echoes.probs().len() - 1;
        sensor.echoes.weigh(&vec![50.0; on]);
        assert!(sensor.echoes.off() < 1e-9);
        sensor
    }

    #[test]
    fn a_source_on_does_not_push_a_person_without_a_track_out_of_view() {
        // MODEL.md 4.3: the source is the alternative in the denominator, not a factor on the person
        // (until 0.54 P(no source): -4.6 log per second and sensor, 10 "Echoquellen als Alternative")
        let world = testing::flat();
        let ld = &world.ld[0];
        let j = ld.tiles.idx.len() / 2;
        let mut f = filter_with(&world, ld.tiles.idx[j]);
        let person = f.hyps()[0].hidden[0];
        let unknown = f.hyps()[0].unknown;
        let inside = f.objects().densities().get(person).total();
        *f.objects_mut().densities.get_mut(person).out = inside; // half of it out of the house
        f.set_hyps(vec![Hyp::new(0.0, vec![person], unknown)]);
        let mut sensor = with_a_source_on(&world.params);
        let e: Cells = std::array::from_fn(|k| (B[k] + ld.tiles.still[j][k]).round().min(100.0));
        for _ in 0..5 {
            for _ in 0..12 {
                sensor.stats.add(&e, 0.089, false);
            }
            f.ld_weigh(&world, 0, &mut sensor, &B, 1e-30).expect("finite");
        }
        let d = f.objects().densities().get(person);
        let share = (d.total() - d.out) / d.total();
        assert!(share > 0.25, "{share}");
    }

    #[test]
    fn a_source_that_explains_nothing_is_no_evidence_for_a_person() {
        // the frames are the background's, the sources on all give too much: without the bound on
        // the alternative the unknown ones grew by what the sources lose (10 "Echoquellen als
        // Alternative"; the correction of 0.27 that made up people in the bed)
        let world = testing::flat();
        let ld = &world.ld[0];
        let total = |j: usize| ld.tiles.walk[j].iter().chain(&ld.tiles.still[j]).sum::<f64>();
        let j = (0..ld.tiles.idx.len())
            .min_by(|&a, &b| total(a).total_cmp(&total(b)))
            .expect("tiles");
        let c = ld.tiles.idx[j];
        let mut f = filter_with(&world, c);
        let unknown = f.hyps()[0].unknown;
        f.objects_mut().densities.get_mut(unknown).walk[c] = 0.01;
        f.set_hyps(vec![Hyp::new(0.0, Vec::new(), unknown)]);
        let before = f.objects().densities().get(unknown).total();
        let mut sensor = with_a_source_on(&world.params);
        let e: Cells = std::array::from_fn(|k| B[k].round());
        for _ in 0..12 {
            sensor.stats.add(&e, 0.089, false);
        }
        f.ld_weigh(&world, 0, &mut sensor, &B, 1e-30).expect("finite");
        let after = f.objects().densities().get(unknown).total();
        assert!(after <= before * (1.0 + 1e-9), "{before} -> {after}");
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
