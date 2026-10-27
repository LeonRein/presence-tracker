//! Which hypotheses hold each object (docs/rust-core.md 2.2; filter.Tracker._objects): built anew
//! whenever the hypotheses change, as compressed rows - the objects in the order of their first
//! appearance over the hypotheses (per hypothesis its people with tracks, the known ones without,
//! the unknown ones, then the sources of its ghost tracks), each with the hypotheses holding it, as
//! often as they hold it. The order sets the order in which sums over the objects run.

use crate::filter::densities::DensityId;
use crate::filter::hyp::Hyp;
use crate::filter::mixture::MixtureId;
use crate::filter::pool::PoolId;

/// An object the hypotheses hold.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Obj {
    Mixture(MixtureId),
    Density(DensityId),
}

#[derive(Debug, Default)]
pub struct Holders {
    objects: Vec<Obj>,
    /// the source of a ghost track (not a person)
    source: Vec<bool>,
    ptr: Vec<usize>,
    hyps: Vec<usize>,
    /// per mixture and density (by its index) its entry, NONE if nobody holds it
    by_mixture: Vec<usize>,
    by_density: Vec<usize>,
}

const NONE: usize = usize::MAX;

/// The same objects held by the same hypotheses (the tables by index follow from them; their length
/// is the pools' capacity when built).
impl PartialEq for Holders {
    fn eq(&self, other: &Self) -> bool {
        (&self.objects, &self.source, &self.ptr, &self.hyps) == (&other.objects, &other.source, &other.ptr, &other.hyps)
    }
}

impl Holders {
    /// `mixtures`, `densities`: one more than the largest index of each (for tables by index).
    pub fn build(hyps: &[Hyp], mixtures: usize, densities: usize) -> Self {
        // what a hypothesis holds, and whether as a ghost's source
        fn held(hy: &Hyp) -> impl Iterator<Item = (Obj, bool)> + '_ {
            let people = hy.people.values().map(|&m| (Obj::Mixture(m), false));
            let hidden = hy.hidden.iter().chain([&hy.unknown]).map(|&d| (Obj::Density(d), false));
            let sources = hy.ghosts.values().map(|g| (Obj::Mixture(g.source), true));
            people.chain(hidden).chain(sources)
        }
        let (mut by_mixture, mut by_density) = (vec![NONE; mixtures], vec![NONE; densities]);
        let (mut objects, mut source, mut count) = (Vec::new(), Vec::new(), Vec::new());
        // the entries in the order of first appearance, and how often each is held
        for hy in hyps {
            for (obj, src) in held(hy) {
                let slot = match obj {
                    Obj::Mixture(m) => &mut by_mixture[m.index()],
                    Obj::Density(d) => &mut by_density[d.index()],
                };
                if *slot == NONE {
                    *slot = objects.len();
                    objects.push(obj);
                    source.push(src);
                    count.push(0);
                }
                count[*slot] += 1;
            }
        }
        let mut ptr = Vec::with_capacity(objects.len() + 1);
        ptr.push(0);
        for c in &count {
            ptr.push(ptr[ptr.len() - 1] + c);
        }
        // then who holds them, in the order of the hypotheses
        let mut at = ptr[..objects.len()].to_vec();
        let mut flat = vec![0; ptr[objects.len()]];
        for (h, hy) in hyps.iter().enumerate() {
            for (obj, _) in held(hy) {
                let e = match obj {
                    Obj::Mixture(m) => by_mixture[m.index()],
                    Obj::Density(d) => by_density[d.index()],
                };
                flat[at[e]] = h;
                at[e] += 1;
            }
        }
        Self {
            objects,
            source,
            ptr,
            hyps: flat,
            by_mixture,
            by_density,
        }
    }

    /// The number of entries (objects held, sources of ghost tracks too).
    pub fn len(&self) -> usize {
        self.objects.len()
    }

    pub fn is_empty(&self) -> bool {
        self.objects.is_empty()
    }

    /// The objects by entry.
    pub fn objects(&self) -> &[Obj] {
        &self.objects
    }

    pub fn entries(&self, sources: bool) -> impl Iterator<Item = (usize, Obj)> + '_ {
        self.objects
            .iter()
            .copied()
            .enumerate()
            .filter(move |&(e, _)| sources || !self.source[e])
    }

    /// The hypotheses holding an entry's object, as often as they hold it.
    /// The entry of an object a hypothesis holds.
    pub fn entry(&self, obj: Obj) -> usize {
        let e = match obj {
            Obj::Mixture(m) => self.by_mixture.get(m.index()),
            Obj::Density(d) => self.by_density.get(d.index()),
        };
        e.copied().filter(|&e| e != NONE).expect("held")
    }

    /// The hypotheses holding an entry, each once, in their order (`refs` are sorted: the
    /// hypotheses are gone through in order).
    pub fn holding(&self, entry: usize) -> impl Iterator<Item = usize> + '_ {
        let refs = self.refs(entry);
        refs.iter()
            .enumerate()
            .filter(move |&(i, h)| i == 0 || refs[i - 1] != *h)
            .map(|(_, &h)| h)
    }

    pub fn refs(&self, entry: usize) -> &[usize] {
        &self.hyps[self.ptr[entry]..self.ptr[entry + 1]]
    }

    /// A log factor of an entry's object goes to the hypotheses holding it. Every weighing of the
    /// objects books through here.
    pub fn book(&self, hyps: &mut [Hyp], entry: usize, d: f64) {
        for &h in self.refs(entry) {
            hyps[h].logw += d;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filter::hyp::{Ghost, GroupId};
    use crate::filter::mixture::SegId;

    #[test]
    fn objects_come_in_the_order_they_first_appear() {
        let (d, m) = (DensityId::from_index, MixtureId::from_index);
        let mut a = Hyp::new(0.0, vec![d(3), d(3)], d(0));
        a.people.insert(GroupId(1), m(2));
        let mut b = Hyp::new(0.0, vec![d(4)], d(0));
        b.people.insert(GroupId(7), m(2));
        b.ghosts.insert(
            SegId(5),
            Ghost {
                life: vec![0.0],
                source: m(1),
            },
        );
        let h = Holders::build(&[a, b], 3, 5);
        let all: Vec<(Obj, Vec<usize>)> = h.entries(true).map(|(e, o)| (o, h.refs(e).to_vec())).collect();
        assert_eq!(
            all,
            vec![
                (Obj::Mixture(m(2)), vec![0, 1]),
                (Obj::Density(d(3)), vec![0, 0]),
                (Obj::Density(d(0)), vec![0, 1]),
                (Obj::Density(d(4)), vec![1]),
                (Obj::Mixture(m(1)), vec![1]),
            ]
        );
        assert_eq!(h.entry(Obj::Density(d(4))), 3);
        assert_eq!(h.holding(1).collect::<Vec<_>>(), vec![0], "each hypothesis once");
        assert_eq!(h.holding(0).collect::<Vec<_>>(), vec![0, 1]);
        assert_eq!(h.entries(false).count(), 4);
        let mut hyps = [Hyp::new(0.0, Vec::new(), d(0)), Hyp::new(1.0, Vec::new(), d(0))];
        h.book(&mut hyps, 1, 0.5);
        assert_eq!((hyps[0].logw, hyps[1].logw), (1.0, 1.0));
    }
}
