//! One hypothesis (MODEL.md 5.1): its weight; per live track whose it is - a person's (a group) or
//! a ghost's; per ghost track the log likelihood of its life per kind of ghost and its source (a
//! Gaussian mixture); the people with tracks, the known ones without (one density each) and the
//! unknown ones. It holds indices into the filter's pools, never the objects (docs/rust-core.md 2.2).

use crate::filter::densities::DensityId;
use crate::filter::holders::Obj;
use crate::filter::mixture::{MixtureId, SegId};
use crate::filter::small_map::SmallMap;

/// A person with tracks, as the hypotheses name them. Only a label: the same person may have
/// another id in another hypothesis.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct GroupId(pub u64);

/// Whose a live track is.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Owner {
    Person(GroupId),
    Ghost,
}

/// A ghost track: the log likelihood of its life so far per kind of ghost (MODEL.md 4.2), and its
/// source.
#[derive(Debug, Clone, PartialEq)]
pub struct Ghost {
    pub life: Vec<f64>,
    pub source: MixtureId,
}

/// A hypothesis. Clone copies its bookkeeping, never the objects: a child of a branch shares them
/// with its parent until what differs makes a new one (Hyp.child).
#[derive(Debug, Clone, PartialEq)]
pub struct Hyp {
    pub logw: f64,
    /// per live track whose it is, in the order the tracks came
    pub tracks: SmallMap<SegId, Owner>,
    pub ghosts: SmallMap<SegId, Ghost>,
    /// the people with tracks
    pub people: SmallMap<GroupId, MixtureId>,
    /// the known people without a track (Bernoullis); one density may stand for several
    pub hidden: Vec<DensityId>,
    /// the people nobody knows of (a Poisson intensity)
    pub unknown: DensityId,
}

/// What a hypothesis says about the live tracks (Hyp.key): which of them are one person's, and
/// which are ghosts. Both sorted, so hypotheses that say the same have equal keys.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HypKey {
    groups: Vec<Vec<SegId>>,
    ghosts: Vec<SegId>,
}

impl Hyp {
    /// The person of group gid is now this object: a mixture (with tracks), or a density on the
    /// tiles (without any, among the known ones then).
    pub fn set_person(&mut self, gid: GroupId, obj: Obj) {
        match obj {
            Obj::Mixture(m) => self.people.insert(gid, m),
            Obj::Density(d) => {
                self.people.remove(&gid);
                self.hidden.push(d);
            }
        }
    }

    /// The hypothesis with nobody known and the unknown ones.
    pub fn new(logw: f64, hidden: Vec<DensityId>, unknown: DensityId) -> Self {
        Self {
            logw,
            tracks: SmallMap::default(),
            ghosts: SmallMap::default(),
            people: SmallMap::default(),
            hidden,
            unknown,
        }
    }

    /// A copy with another weight (Hyp.child).
    pub fn child(&self, logw: f64) -> Self {
        Self { logw, ..self.clone() }
    }

    /// Per person with tracks the live tracks the hypothesis says are theirs, in the order of the
    /// tracks (Hyp.group_segs).
    pub fn group_segs(&self) -> SmallMap<GroupId, Vec<SegId>> {
        let mut out: SmallMap<GroupId, Vec<SegId>> = SmallMap::default();
        for (&seg, owner) in self.tracks.iter() {
            if let Owner::Person(gid) = *owner {
                match out.get_mut(&gid) {
                    Some(segs) => segs.push(seg),
                    None => out.insert(gid, vec![seg]),
                }
            }
        }
        out
    }

    /// The person a track is, if a person's.
    pub fn owner_of(&self, seg: SegId) -> Option<Owner> {
        self.tracks.get(&seg).copied()
    }

    pub fn is_ghost(&self, seg: SegId) -> bool {
        self.tracks.get(&seg) == Some(&Owner::Ghost)
    }

    /// What it says about the live tracks: hypotheses that differ only in the known people without
    /// a track (how many, where) are merged (MODEL.md 5.6).
    pub fn key(&self) -> HypKey {
        let mut groups: Vec<Vec<SegId>> = self
            .group_segs()
            .into_values()
            .map(|mut segs| {
                segs.sort_unstable();
                segs
            })
            .collect();
        groups.sort_unstable();
        let mut ghosts: Vec<SegId> = self
            .tracks
            .iter()
            .filter(|(_, o)| **o == Owner::Ghost)
            .map(|(&s, _)| s)
            .collect();
        ghosts.sort_unstable();
        HypKey { groups, ghosts }
    }
}

/// The hypotheses grouped by their key in the order of their first appearance: per group the
/// indices of its members, in order.
pub fn alike(hyps: &[Hyp]) -> Vec<Vec<usize>> {
    let keys: Vec<HypKey> = hyps.iter().map(Hyp::key).collect();
    let mut groups: Vec<(usize, Vec<usize>)> = Vec::new();
    for (h, key) in keys.iter().enumerate() {
        match groups.iter_mut().find(|(first, _)| keys[*first] == *key) {
            Some((_, members)) => members.push(h),
            None => groups.push((h, vec![h])),
        }
    }
    groups.into_iter().map(|(_, members)| members).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filter::pool::PoolId;

    fn hyp(tracks: &[(u64, Option<u64>)]) -> Hyp {
        let mut h = Hyp::new(0.0, Vec::new(), DensityId::from_index(0));
        for &(seg, gid) in tracks {
            let owner = gid.map_or(Owner::Ghost, |g| Owner::Person(GroupId(g)));
            h.tracks.insert(SegId(seg), owner);
        }
        h
    }

    #[test]
    fn the_key_says_which_tracks_go_together_not_who() {
        let a = hyp(&[(1, Some(7)), (2, None), (3, Some(7)), (4, Some(9))]);
        let b = hyp(&[(4, Some(1)), (3, Some(2)), (1, Some(2)), (2, None)]);
        assert_eq!(a.key(), b.key());
        let c = hyp(&[(1, Some(7)), (2, None), (3, Some(8)), (4, Some(9))]);
        assert_ne!(a.key(), c.key());
        assert_eq!(alike(&[a, c, b]), vec![vec![0, 2], vec![1]]);
    }
}
