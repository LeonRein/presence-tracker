//! Keeping the hypotheses few (MODEL.md 5.1, 5.6): those that say the same about every live track
//! are one, their mixture; the strongest are kept until what is dropped weighs at most hyp_mass,
//! and no live track loses an alternative; the weights are normalized and their sum is the step's
//! factor of the evidence (MODEL.md 7).
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use crate::filter::densities::DensityId;
use crate::filter::hyp::{Hyp, Owner, alike};
use crate::filter::mixture::{Mixture, MixtureId, SegId, mix_mixtures};
use crate::filter::objects::Objects;
use crate::filter::{Filter, NotFinite};
use crate::params::Params;
use crate::vmath::logsumexp;

/// The parts of a mixture of objects held by several hypotheses: each object once, with the summed
/// weight of those holding it, in the order of first appearance.
fn distinct<T: PartialEq + Copy>(parts: impl IntoIterator<Item = (f64, T)>) -> Vec<(f64, T)> {
    let mut out: Vec<(f64, T)> = Vec::new();
    for (w, o) in parts {
        match out.iter_mut().find(|(_, x)| *x == o) {
            Some((acc, _)) => *acc += w,
            None => out.push((0.0 + w, o)), // a sum from 0, as Python's: -0.0 becomes 0.0
        }
    }
    out
}

/// The permutations of 0..n in lexicographic order, as itertools.permutations(range(n)).
fn permutations(n: usize) -> impl Iterator<Item = Vec<usize>> {
    let mut next = Some((0..n).collect::<Vec<_>>());
    std::iter::from_fn(move || {
        let current = next.take()?;
        if let Some(i) = (1..n).rev().find(|&i| current[i - 1] < current[i]) {
            let mut p = current.clone(); // the next permutation, made from this one
            let j = (i..n)
                .rev()
                .find(|&j| p[j] > p[i - 1])
                .expect("a larger one after i - 1");
            p.swap(i - 1, j);
            p[i..].reverse();
            next = Some(p);
        }
        Some(current)
    })
}

impl Filter {
    /// The hypotheses' weights to sum 1; their sum (and the share dropped just before by the cut)
    /// is this step's factor of the evidence.
    pub fn normalize(&mut self, dropped: f64) -> Result<(), NotFinite> {
        let total = logsumexp(&self.hyps.iter().map(|h| h.logw).collect::<Vec<_>>());
        if total.is_nan() || total == f64::INFINITY {
            return Err(NotFinite(total));
        }
        if total == f64::NEG_INFINITY {
            // nothing explains the data (should not happen): keep the hypotheses, equal weights
            let each = -(self.hyps.len() as f64).ln();
            for h in &mut self.hyps {
                h.logw = each;
            }
            return Ok(());
        }
        for h in &mut self.hyps {
            h.logw -= total;
        }
        let cut = if dropped > 0.0 {
            -(-dropped.min(1.0 - 1e-16)).ln_1p()
        } else {
            0.0
        };
        self.loglik += total + cut;
        self.loglik_cut += cut;
        Ok(())
    }

    /// cut_hyps: what is dropped counts in the evidence.
    pub fn cut(&mut self, p: &Params) -> Result<(), NotFinite> {
        self.void_cache();
        let cut = cut_hyps(&mut self.hyps, p);
        if !matches!(cut, Ok(Cut::Nothing)) {
            self.rebuild();
        }
        self.after_cut(cut?)
    }

    /// The weights normalized after a cut that dropped some (nothing explains the data: kept).
    fn after_cut(&mut self, cut: Cut) -> Result<(), NotFinite> {
        match cut {
            Cut::Dropped(dropped) => self.normalize(dropped),
            Cut::Nothing | Cut::Unexplained => Ok(()),
        }
    }

    /// cut, then the strongest first.
    pub fn prune(&mut self, p: &Params) -> Result<(), NotFinite> {
        self.cut(p)?;
        sort_strongest_first(&mut self.hyps);
        self.rebuild();
        Ok(())
    }

    /// Hypotheses that now say the same about every live track are one.
    pub fn merge(&mut self) {
        self.void_cache();
        self.objects.merge_alike(&mut self.hyps);
        self.rebuild();
    }

    /// The children of a branch are the hypotheses: cut, those alike merged, cut again, the
    /// strongest first (Tracker._take: set, cut, merge, prune). The first cut is decided on the
    /// merged weights before the mixtures are made: what is dropped is never mixed. The holders are
    /// built once, at the end (also if a weight is not finite).
    pub fn take(&mut self, children: Vec<Hyp>, p: &Params) -> Result<(), NotFinite> {
        self.hyps = children;
        let taken = self.take_steps(p);
        self.rebuild();
        taken
    }

    fn take_steps(&mut self, p: &Params) -> Result<(), NotFinite> {
        self.void_cache();
        let cut = cut_hyps(&mut self.hyps, p)?;
        self.after_cut(cut)?;
        self.void_cache();
        self.objects.merge_alike(&mut self.hyps);
        self.void_cache();
        let cut = cut_hyps(&mut self.hyps, p)?;
        self.after_cut(cut)?;
        sort_strongest_first(&mut self.hyps);
        Ok(())
    }
}

/// What a cut did.
#[derive(Debug, Clone, Copy, PartialEq)]
enum Cut {
    /// nothing to drop
    Nothing,
    /// dropped hypotheses of this share of the weight
    Dropped(f64),
    /// nothing explains the data (should not happen): the strongest max_hyps kept as they are
    Unexplained,
}

/// The strongest first, the equal ones in their order (numpy's argsort(-logw, kind="stable")).
fn sort_strongest_first(hyps: &mut [Hyp]) {
    hyps.sort_by(|a, b| b.logw.total_cmp(&a.logw));
}

/// Keeps the strongest hypotheses until what is dropped weighs at most hyp_mass of the whole, and
/// at most max_hyps of them (B_Vo2017). Each is weighed as it will be after merging,
/// by the sum of those alike; the kept ones stay in their order. No live track loses an alternative
/// altogether: of the dropped ones, the strongest that holds an alternative the kept ones lack
/// stays (MODEL.md 5.1).
fn cut_hyps(hyps: &mut Vec<Hyp>, p: &Params) -> Result<Cut, NotFinite> {
    let groups = alike(hyps);
    let glw: Vec<f64> = groups
        .iter()
        .map(|g| logsumexp(&g.iter().map(|&h| hyps[h].logw).collect::<Vec<_>>()))
        .collect();
    // strongest first, stable (numpy's argsort(-glw, kind="stable"))
    let mut order: Vec<usize> = (0..groups.len()).collect();
    order.sort_by(|&a, &b| glw[b].total_cmp(&glw[a]));
    let lw: Vec<f64> = order.iter().map(|&i| glw[i]).collect();
    let total = logsumexp(&lw);
    if total.is_nan() {
        return Err(NotFinite(total));
    }
    let mut chosen: Vec<usize>;
    let mut dropped = 0.0;
    if total == f64::NEG_INFINITY {
        // nothing explains the data (should not happen): keep them, see normalize
        chosen = order[..order.len().min(p.max_hyps)].to_vec();
    } else {
        let w: Vec<f64> = lw.iter().map(|x| (x - total).exp()).collect();
        // from the weakest up, as long as the dropped ones together stay within hyp_mass
        let mut acc = 0.0;
        let within = w
            .iter()
            .rev()
            .take_while(|&&x| {
                acc += x;
                acc <= p.hyp_mass
            })
            .count();
        let n = (w.len() - within).max(1).min(p.max_hyps);
        if n == w.len() {
            return Ok(Cut::Nothing);
        }
        let alternatives = |i: usize| -> Vec<(SegId, bool)> {
            groups[i]
                .iter()
                .flat_map(|&h| hyps[h].tracks.iter().map(|(&s, &o)| (s, o == Owner::Ghost)))
                .collect()
        };
        chosen = order[..n].to_vec();
        let mut held: Vec<(SegId, bool)> = chosen.iter().flat_map(|&i| alternatives(i)).collect();
        for &i in &order[n..] {
            let new: Vec<(SegId, bool)> = alternatives(i).into_iter().filter(|a| !held.contains(a)).collect();
            if new.iter().any(|&(s, g)| held.contains(&(s, !g))) {
                chosen.push(i);
                held.extend(new);
            }
        }
        dropped = (0..w.len())
            .filter(|&k| !chosen.contains(&order[k]))
            .fold(0.0, |s, k| s + w[k]);
    }
    let mut kept = vec![false; hyps.len()];
    for &i in &chosen {
        for &h in &groups[i] {
            kept[h] = true;
        }
    }
    keep_marked(hyps, &kept);
    Ok(if total == f64::NEG_INFINITY {
        Cut::Unexplained
    } else {
        Cut::Dropped(dropped)
    })
}

impl Objects {
    /// Each set of hypotheses that say the same about every live track becomes their mixture, in
    /// the place of the first of them (MODEL.md 5.6). People held by the same
    /// object in all of them stay as they are; the others are mixed per person (exact for one, the
    /// multi-Bernoulli approximation for more): the people without a track paired by the least L1
    /// distance, a person missing in some of them mixed with nobody (r = 0).
    pub fn merge_alike(&mut self, hyps: &mut Vec<Hyp>) {
        let groups = alike(hyps);
        if groups.len() == hyps.len() {
            return;
        }
        let mut keep = vec![true; hyps.len()];
        for members in groups.iter().filter(|m| m.len() > 1) {
            let mixed = self.mix_hyps(&members.iter().map(|&h| &hyps[h]).collect::<Vec<_>>());
            hyps[members[0]] = mixed;
            for &h in &members[1..] {
                keep[h] = false;
            }
        }
        keep_marked(hyps, &keep);
    }

    fn mix_hyps(&mut self, hs: &[&Hyp]) -> Hyp {
        let lw: Vec<f64> = hs.iter().map(|h| h.logw).collect();
        let tot = logsumexp(&lw);
        let wn: Vec<f64> = if tot > f64::NEG_INFINITY {
            lw.iter().map(|l| (l - tot).exp()).collect()
        } else {
            vec![1.0 / hs.len() as f64; hs.len()]
        };
        let base = hs[0];
        let mut new = base.child(tot);
        // the people with tracks: per group of tracks the mixtures of all of them
        let segs_of: Vec<_> = hs.iter().map(|h| h.group_segs()).collect();
        for (gid, segs) in segs_of[0].iter() {
            let same = |other: &Vec<SegId>| other.len() == segs.len() && other.iter().all(|s| segs.contains(s));
            let objs: Vec<MixtureId> = hs
                .iter()
                .zip(&segs_of)
                .map(|(h, gs)| {
                    let (g2, _) = gs.iter().find(|(_, v)| same(v)).expect("the same group of tracks");
                    *h.people.get(g2).expect("a person with tracks")
                })
                .collect();
            if objs.iter().all(|&o| o == objs[0]) {
                continue;
            }
            let id = self.mix_mixtures(&distinct(wn.iter().copied().zip(objs)));
            new.people.insert(*gid, id);
        }
        // the ghosts' sources
        for (seg, _) in base.ghosts.iter() {
            let sources = wn
                .iter()
                .zip(hs)
                .map(|(&w, h)| (w, h.ghosts.get(seg).expect("the same ghost track").source));
            let parts = distinct(sources);
            if parts.len() > 1 {
                let id = self.mix_mixtures(&parts);
                new.ghosts.get_mut(seg).expect("the base's own ghost").source = id;
            }
        }
        // people without a track: the objects all hold stay, the others are paired and mixed
        let mut common: Vec<DensityId> = base.hidden.clone();
        for h in &hs[1..] {
            let mut rest = h.hidden.clone();
            common.retain(|u| match rest.iter().position(|r| r == u) {
                Some(at) => {
                    rest.remove(at);
                    true
                }
                None => false,
            });
        }
        let mut rests: Vec<Vec<DensityId>> = hs
            .iter()
            .map(|h| {
                let mut rest = h.hidden.clone();
                for u in &common {
                    let at = rest.iter().position(|r| r == u).expect("held by all");
                    rest.remove(at);
                }
                rest
            })
            .collect();
        // a hypothesis with fewer of them: the missing ones do not exist there (r = 0)
        let size = rests.iter().map(Vec::len).max().unwrap_or(0);
        if rests.iter().any(|r| r.len() < size) {
            let nobody = self.densities.nobody();
            for r in &mut rests {
                r.resize(size, nobody);
            }
        }
        for k in 1..rests.len() {
            rests[k] = self.pair(&rests[0], &rests[k]);
        }
        let mixed: Vec<DensityId> = (0..size)
            .map(|j| self.mix_densities(&distinct(wn.iter().zip(&rests).map(|(&w, r)| (w, r[j])))))
            .collect();
        new.hidden = common;
        new.hidden
            .extend(mixed.into_iter().filter(|&u| self.densities.get(u).r > 0.0));
        new.unknown = self.mix_densities(&distinct(wn.iter().zip(hs).map(|(&w, h)| (w, h.unknown))));
        new
    }

    /// The mixture of [(w_i, mixture_i)] as a new object.
    pub fn mix_mixtures(&mut self, parts: &[(f64, MixtureId)]) -> MixtureId {
        let refs: Vec<(f64, &Mixture)> = parts.iter().map(|&(w, m)| (w, self.mixtures.get(m))).collect();
        let mix = mix_mixtures(&refs, &mut self.densities);
        self.mixtures.insert(mix)
    }

    /// One density stays itself; several are mixed as a new one.
    pub fn mix_densities(&mut self, parts: &[(f64, DensityId)]) -> DensityId {
        match parts {
            [(_, only)] => *only,
            _ => self.densities.mixture(parts),
        }
    }

    /// `other` reordered so that other[j] is the person most like base[j]: people without a track
    /// are exchangeable, so the pairing with the least summed L1 distance (MODEL.md 5.6;
    /// Tracker._pair). At a tie the first permutation in itertools' order.
    pub fn pair(&self, base: &[DensityId], other: &[DensityId]) -> Vec<DensityId> {
        if base.len() <= 1 || base.len() > 6 {
            // more than six never happens in a household
            return other.to_vec();
        }
        let cost: Vec<Vec<f64>> = base
            .iter()
            .map(|&a| other.iter().map(|&b| self.densities.distance(a, b)).collect())
            .collect();
        let mut best: Option<(f64, Vec<usize>)> = None;
        for p in permutations(other.len()) {
            let c = p.iter().enumerate().fold(0.0, |s, (i, &j)| s + cost[i][j]);
            if best.as_ref().is_none_or(|(b, _)| c < *b) {
                best = Some((c, p));
            }
        }
        let (_, best) = best.expect("at least one permutation");
        best.iter().map(|&j| other[j]).collect()
    }
}

/// Keeps the hypotheses marked to be kept, in their order.
fn keep_marked(hyps: &mut Vec<Hyp>, keep: &[bool]) {
    let mut marks = keep.iter();
    hyps.retain(|_| marks.next().copied().unwrap_or(false));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn permutations_come_in_itertools_order() {
        let all: Vec<Vec<usize>> = permutations(3).collect();
        assert_eq!(
            all,
            [[0, 1, 2], [0, 2, 1], [1, 0, 2], [1, 2, 0], [2, 0, 1], [2, 1, 0]].map(|p| p.to_vec())
        );
        assert_eq!(permutations(0).count(), 1);
    }
}
