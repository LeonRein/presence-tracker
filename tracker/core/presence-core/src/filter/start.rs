//! Where the people are when the filter starts (MODEL.md 5.3): nothing known, known people at given
//! places (tests and tools), or a saved state (filter.Tracker.reset_people, restore_people).

use crate::filter::densities::{Densities, DensityData, DensityId, DensityMut, DensityShape, Kind};
use crate::filter::hyp::Hyp;
use crate::filter::mixture::max2;
use crate::filter::{Filter, PeopleState};
use crate::vmath::{logsumexp, pairwise_sum};
use crate::world::World;

/// Where a known person is at a start (Tracker.reset_people).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Start {
    /// nothing known of them: in view, behind a door or out (one density for all of them)
    Anywhere,
    /// at this place (observed, regions..., outside)
    Place(usize),
}

/// Standing in view anywhere alike, the stay seen at a random time, times `share` (the prior of a
/// stay's kind and detectability, hidden.Hidden.anywhere / at_place), in numpy's order.
fn standing_anywhere(d: &mut DensityMut, world: &World, share: Option<f64>) {
    let (tiles, shapes) = (&world.tiles, &world.shapes);
    let total = pairwise_sum(&tiles.area);
    let levels = shapes.kappa_w.len();
    for (l, &go) in shapes.go_w_ongoing.iter().enumerate() {
        for (k, &kappa) in shapes.kappa_w.iter().enumerate() {
            let prior = go * kappa;
            let p = share.map_or(prior, |s| s * prior);
            let row = (l * levels + k) * tiles.n;
            for (x, &a) in d.still[row..row + tiles.n].iter_mut().zip(&tiles.area) {
                *x += p * (a / total);
            }
        }
    }
}

/// Nothing known (Hidden.anywhere): standing in view (a third), behind a door (a third: each region
/// alike, the stay seen at a random time), or out (the rest); a person normalized, the unknown ones
/// expecting `people` (Undetected.anywhere).
fn anywhere(densities: &mut Densities, world: &World, kind: Kind, people: f64) -> DensityId {
    let id = densities.alloc(kind);
    let mut d = densities.get_mut(id);
    let (view, mut regions) = (1.0 / 3.0, 1.0 / 3.0);
    if world.tiles.n > 0 {
        standing_anywhere(&mut d, world, Some(view));
    } else {
        regions += view;
    }
    let (count, ages) = (world.tiles.regions, world.tiles.ages);
    for (stays, start) in d
        .region
        .chunks_mut(ages.max(1))
        .zip(world.plan.region_start.chunks(ages.max(1)))
    {
        for (x, &s) in stays.iter_mut().zip(start) {
            *x = regions / count as f64 * s;
        }
    }
    let total = pairwise_sum(d.walk) + pairwise_sum(d.still) + pairwise_sum(d.region) + *d.out;
    *d.out = max2(1.0 - total, 0.0);
    d.normalize();
    if kind == Kind::Intensity {
        d.scale(people);
    }
    id
}

/// A known person at a place (Hidden.at_place): in view standing anywhere alike, in a region at the
/// start of a stay seen at a random time, or out.
fn at_place(densities: &mut Densities, world: &World, place: usize) -> DensityId {
    let id = densities.alloc(Kind::Person);
    let mut d = densities.get_mut(id);
    let (regions, ages) = (world.tiles.regions, world.tiles.ages);
    if place == 0 && world.tiles.n > 0 {
        standing_anywhere(&mut d, world, None);
    } else if place == 0 || place > regions || regions == 0 {
        *d.out = 1.0;
    } else {
        let r = place - 1;
        d.region[r * ages..][..ages].copy_from_slice(&world.plan.region_start[r * ages..][..ages]);
    }
    d.normalize();
    id
}

impl PeopleState {
    /// Whether the state fits a filter of this shape: hypotheses with finite weights, their people
    /// persons and their unknown ones intensities, the arrays as long as the shape says (Err names what
    /// does not).
    pub fn check(&self, shape: &DensityShape) -> Result<(), String> {
        let kind = |i: usize| self.densities.get(i).map(|d| d.kind);
        let fits = |d: &DensityData| {
            d.walk.len() == shape.tiles
                && d.still.len() == shape.still_len()
                && d.region.len() == shape.region_len()
                && d.amp
                    .as_ref()
                    .is_none_or(|a| a.levels.len() == shape.amp_len() && a.keep.is_none())
        };
        if self.hyps.is_empty() {
            Err("no hypotheses".into())
        } else if !self.hyps.iter().all(|(logw, _, _)| logw.is_finite()) {
            Err("a weight that is no number".into())
        } else if !self.hyps.iter().all(|(_, people, unknown)| {
            kind(*unknown) == Some(Kind::Intensity) && people.iter().all(|&i| kind(i) == Some(Kind::Person))
        }) {
            Err("a person that is no density of one, or unknown ones that are none".into())
        } else if !self.densities.iter().all(fits) {
            Err("a density that does not fit the tiles".into())
        } else {
            Ok(())
        }
    }
}

impl Filter {
    /// Start over (Tracker.reset_people): nothing known about the people - no known people, the
    /// unknown ones anywhere (start_unknown of them expected, MODEL.md 5.3) -, or known people at
    /// these places and nobody unknown. Clear first (Tracker::clear).
    pub fn start_over(&mut self, world: &World, people: Option<&[Start]>) {
        let densities = &mut self.objects.densities;
        let (hidden, unknown) = match people {
            None => {
                let unknown = anywhere(densities, world, Kind::Intensity, world.params.start_unknown);
                (Vec::new(), unknown)
            }
            Some(people) => {
                // the people nothing is known of are one density, held as often as they are
                let mut shared: Option<DensityId> = None;
                let hidden = people
                    .iter()
                    .map(|&start| match start {
                        Start::Anywhere => *shared.get_or_insert_with(|| anywhere(densities, world, Kind::Person, 1.0)),
                        Start::Place(place) => at_place(densities, world, place),
                    })
                    .collect();
                (hidden, densities.alloc(Kind::Intensity))
            }
        };
        self.set_hyps(vec![Hyp::new(0.0, hidden, unknown)]);
    }

    /// Start from a saved state, checked (PeopleState::check), its weights normalized (Tracker.
    /// restore_people). Clear first (Tracker::clear).
    pub fn restore(&mut self, state: PeopleState) {
        let total = logsumexp(&state.hyps.iter().map(|&(logw, _, _)| logw).collect::<Vec<_>>());
        let ids: Vec<DensityId> = state
            .densities
            .into_iter()
            .map(|d| self.objects.densities.insert(d))
            .collect();
        let hyps = state
            .hyps
            .into_iter()
            .map(|(logw, people, unknown)| {
                Hyp::new(logw - total, people.iter().map(|&i| ids[i]).collect(), ids[unknown])
            })
            .collect();
        self.set_hyps(hyps);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filter::densities::DensityShape;
    use crate::testing;

    fn total(f: &Filter, d: DensityId) -> f64 {
        f.objects().densities().get(d).total()
    }

    #[test]
    fn nothing_known_is_no_known_people_and_the_unknown_ones_expected() {
        let world = testing::flat();
        let mut f = Filter::new(world.density_shape(), world.sights.len());
        f.start_over(&world, None);
        let hy = &f.hyps()[0];
        assert!(hy.hidden.is_empty() && hy.people.is_empty());
        // no regions: a third in view (anywhere alike), the rest out; start_unknown expected
        let d = f.objects().densities().get(hy.unknown);
        assert_eq!(d.kind, Kind::Intensity);
        assert!((d.total() - world.params.start_unknown).abs() < 1e-12);
        assert!((pairwise_sum(d.still) - world.params.start_unknown / 3.0).abs() < 1e-12);
        f.check().expect("the books");
    }

    #[test]
    fn known_people_at_their_places_the_unknown_ones_none() {
        let world = testing::flat();
        let mut f = Filter::new(world.density_shape(), world.sights.len());
        let outside = world.tiles.regions + 1;
        f.start_over(
            &world,
            Some(&[Start::Anywhere, Start::Place(0), Start::Anywhere, Start::Place(outside)]),
        );
        let hy = f.hyps()[0].clone();
        // the people nothing is known of are one density
        assert_eq!(hy.hidden[0], hy.hidden[2]);
        assert_eq!(f.objects().densities().len(), 4);
        assert!(hy.hidden.iter().all(|&d| (total(&f, d) - 1.0).abs() < 1e-12));
        assert_eq!(f.objects().densities().get(hy.hidden[3]).out, 1.0);
        assert_eq!(total(&f, hy.unknown), 0.0);
        f.check().expect("the books");
    }

    #[test]
    fn a_saved_state_that_does_not_fit_changes_nothing() {
        let world = testing::flat();
        let shape: DensityShape = world.density_shape();
        let mut f = Filter::new(shape, world.sights.len());
        f.start_over(&world, Some(&[Start::Place(0)]));
        let PeopleState { hyps, densities } = f.people_state(&world);
        let state = |hyps: Vec<(f64, Vec<usize>, usize)>| PeopleState {
            hyps,
            densities: densities.clone(),
        };
        let (logw, people, unknown) = hyps[0].clone();
        for (bad, what) in [
            (state(Vec::new()), "no hypotheses"),
            (
                state(vec![(f64::NAN, people.clone(), unknown)]),
                "a weight that is no number",
            ),
            (state(vec![(logw, vec![unknown], unknown)]), "unknown ones as a person"),
            (
                state(vec![(logw, people.clone(), people[0])]),
                "a person as the unknown ones",
            ),
            (state(vec![(logw, vec![9], unknown)]), "an index beyond the densities"),
        ] {
            assert!(bad.check(&shape).is_err(), "{what}");
        }
        let mut short = state(hyps);
        short.densities[0].walk.pop();
        assert!(short.check(&shape).is_err(), "a density that does not fit");
        // what fits: the weights normalized
        let ok = state(vec![(logw + 3.0, people, unknown), (logw + 3.0, Vec::new(), unknown)]);
        ok.check(&shape).expect("fits");
        f.restore(ok);
        assert!(f.hyps().iter().all(|h| (h.logw + 2f64.ln()).abs() < 1e-15));
    }
}
