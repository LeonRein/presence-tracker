//! The motion of the people without a track over the tiles (MODEL.md 3.1-3.4, 5.3):
//! the stays, walking between the tiles in ticks, the stays in the regions without a sensor, coming
//! home and newcomers. Walking all densities on the same tiles is one matrix product per tick: it
//! reads the matrix of walking once for all of them.
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use faer::linalg::matmul::matmul;
use faer::{Accum, MatMut, MatRef, Par};

use super::stay::{StayStep, stays};
use crate::filter::densities::{Densities, DensityId, DensityMut, Kind};
use crate::params::Params;
use crate::world::{Tiles, World};

/// Moves each density by its own step dt: the stays, then the walkers in ticks of
/// the tiles' tick (each density keeps the time since its last tick), those with the same number
/// of ticks together; then the regions and the newcomers.
pub fn move_densities(world: &World, densities: &mut Densities, movers: &[(DensityId, f64)]) {
    let (tiles, p, shapes) = (&world.tiles, &world.params, &world.shapes);
    if tiles.n > 0 {
        let mut by_ticks: Vec<(usize, Vec<DensityId>)> = Vec::new();
        for &(id, dt) in movers {
            let step = StayStep::new(p.stop_rate(), p.kappa_switch, shapes, dt);
            let d = densities.get_mut(id);
            let (levels, n) = (d.shape.levels, d.shape.tiles);
            let keep = d
                .amp
                .as_mut()
                .map(|a| a.keep.get_or_insert_with(|| vec![1.0; levels * n]).as_mut_slice());
            stays(d.walk, d.still, keep, &step, shapes);
            *d.clock += dt;
            let mut ticks = 0;
            while *d.clock >= tiles.tick {
                *d.clock -= tiles.tick;
                ticks += 1;
            }
            if ticks > 0 {
                match by_ticks.iter_mut().find(|(k, _)| *k == ticks) {
                    Some((_, ids)) => ids.push(id),
                    None => by_ticks.push((ticks, vec![id])),
                }
            }
        }
        for (ticks, ids) in &by_ticks {
            walk_ticks(tiles, densities, ids, *ticks);
        }
    }
    let ways = tiles.ways();
    for &(id, dt) in movers {
        let mut d = densities.get_mut(id);
        if tiles.regions > 0 || ways > 0 {
            regions(tiles, p, &mut d, dt);
        }
        if d.kind == Kind::Intensity {
            arrive(tiles, p, &mut d, dt);
        }
    }
}

/// The motion over a step dt much longer than a walk (a data gap of minutes to days, MODEL.md 5.3;
/// hidden.Hidden.leap): standing people get up as in a move; every walk is taken whole, wherever it
/// ends (Tiles::walk_end) - stopped in a tile, beginning a new stay, gone into a region or out. The
/// stays begun in the step have their detectability anew and nothing measured.
pub fn leap(world: &World, densities: &mut Densities, id: DensityId, dt: f64) {
    let (tiles, p, shapes) = (&world.tiles, &world.params, &world.shapes);
    let (n, levels) = (tiles.n, shapes.levels());
    let mut kept = Vec::new();
    let mut d = densities.get_mut(id);
    if n > 0 {
        let up: Vec<f64> = shapes.go.iter().map(|&go| -(-go * dt).exp_m1()).collect();
        // per kind of stay the still mass per tile, and who gets up
        let s: Vec<f64> = (0..shapes.kinds())
            .flat_map(|l| {
                let rows = &d.still[l * levels * n..][..levels * n];
                let mut acc = rows[..n].to_vec();
                for row in rows.chunks_exact(n).skip(1) {
                    for (a, x) in acc.iter_mut().zip(row) {
                        *a += x;
                    }
                }
                acc
            })
            .collect();
        let rise: Vec<f64> = s.iter().enumerate().map(|(i, x)| up[i / n] * x).collect();
        let q = -(-p.kappa_switch * dt).exp_m1();
        for (lk, row) in d.still.chunks_exact_mut(n).enumerate() {
            let f = (1.0 - up[lk / levels]) * (1.0 - q);
            for x in row {
                *x *= f;
            }
        }
        kept = d.shape.still_per_level(d.still);
        for (lk, row) in d.still.chunks_exact_mut(n).enumerate() {
            let (l, k) = (lk / levels, lk % levels);
            for (c, x) in row.iter_mut().enumerate() {
                *x += shapes.kappa_w[k] * (q * (s[l * n + c] - rise[l * n + c]));
            }
        }
        let mut risen = rise[..n].to_vec();
        for row in rise.chunks_exact(n).skip(1) {
            for (a, r) in risen.iter_mut().zip(row) {
                *a += r;
            }
        }
        for (w, r) in d.walk.iter_mut().zip(&risen) {
            *w += r;
        }
    }
    if tiles.regions > 0 || tiles.ways() > 0 {
        regions(tiles, p, &mut d, dt);
    }
    if d.kind == Kind::Intensity {
        arrive(tiles, p, &mut d, dt);
    }
    if n == 0 {
        return;
    }
    let rows = n + tiles.regions + 1;
    let walk_end = tiles.walk_end(p);
    let end: Vec<f64> = (0..rows)
        .map(|i| {
            walk_end[i * n..][..n]
                .iter()
                .zip(&*d.walk)
                .fold(0.0, |s, (a, w)| s + a * w)
        })
        .collect();
    d.walk.fill(0.0);
    let prior = shapes.stay_prior(false);
    for (lk, row) in d.still.chunks_exact_mut(n).enumerate() {
        for (x, e) in row.iter_mut().zip(&end[..n]) {
            *x += prior[lk] * e;
        }
    }
    d.settle_amp();
    if let Some(amp) = d.amp.as_ref() {
        // the stays begun in the step: nothing measured (g = 1)
        let (amps, now) = (d.shape.amps, d.shape.still_per_level(d.still));
        let mut num = amp.levels.clone();
        for k in 0..levels {
            for a in 0..=amps {
                let fresh = if a == amps { 1.0 } else { 0.0 };
                let row = &mut num[(k * (amps + 1) + a) * n..][..n];
                for (c, g) in row.iter_mut().enumerate() {
                    let (kc, nc) = (kept[k * n + c], now[k * n + c]);
                    *g = kc * *g + (nc - kc) * fresh;
                }
            }
        }
        d.set_amp(&num);
    }
    for (r, e) in end[n..n + tiles.regions].iter().enumerate() {
        d.region[r * tiles.ages] += e;
    }
    *d.out += end[n + tiles.regions];
}

/// `ticks` ticks of walking for these densities together: per tick what walks through a door goes
/// into its region (the stay there begins) or out, the rest moves between the tiles.
fn walk_ticks(tiles: &Tiles, densities: &mut Densities, ids: &[DensityId], ticks: usize) {
    let (n, m, outs) = (tiles.n, ids.len(), tiles.regions + 1);
    let mut walk: Vec<f64> = ids
        .iter()
        .flat_map(|&id| densities.get(id).walk.iter().copied())
        .collect();
    let mut next = vec![0.0; m * n];
    let mut gone = vec![0.0; m * outs];
    let walk_t = MatRef::from_row_major_slice(&tiles.walk_t, n, n);
    let into_t = MatRef::from_row_major_slice(&tiles.into_t, n, outs);
    for _ in 0..ticks {
        let w = MatRef::from_row_major_slice(&walk, m, n);
        matmul(
            MatMut::from_row_major_slice_mut(&mut gone, m, outs),
            Accum::Replace,
            w,
            into_t,
            1.0,
            Par::Seq,
        );
        matmul(
            MatMut::from_row_major_slice_mut(&mut next, m, n),
            Accum::Replace,
            w,
            walk_t,
            1.0,
            Par::Seq,
        );
        for (&id, g) in ids.iter().zip(gone.chunks_exact(outs)) {
            let d = densities.get_mut(id);
            let ages = d.shape.ages;
            for (r, &v) in g[..tiles.regions].iter().enumerate() {
                d.region[r * ages] += v;
            }
            *d.out += g[tiles.regions];
        }
        std::mem::swap(&mut walk, &mut next);
    }
    for (&id, row) in ids.iter().zip(walk.chunks_exact(n)) {
        densities.get_mut(id).walk.copy_from_slice(row);
    }
}

/// The stays in the regions without a sensor: a stay ends by its hazard and
/// who comes out leaves through one of the region's doors alike - walking into view at a door, or
/// out of the house through one of its ways out (none at all: the stay goes on); coming home by
/// every way in alike; the stays age.
fn regions(tiles: &Tiles, p: &Params, d: &mut DensityMut, dt: f64) {
    let ages = tiles.ages;
    let ways = tiles.ways();
    let arrive = -(-p.arrive_rate * ways as f64 * dt).exp_m1();
    for (r, doors) in tiles.region_doors.iter().enumerate() {
        let row = &mut d.region[r * ages..(r + 1) * ages];
        let mut left = 0.0;
        for (v, &rate) in row.iter_mut().zip(&tiles.region_rates[r * ages..]) {
            let e = *v * -(-rate * dt).exp_m1();
            *v -= e;
            left += e;
        }
        let exits = tiles.exits[r];
        let ways_r = doors.len() + exits;
        if ways_r == 0 {
            row[0] += left;
        } else {
            for &c in doors {
                d.walk[c] += left / ways_r as f64;
            }
            *d.out += left * exits as f64 / ways_r as f64;
        }
    }
    if ways > 0 && *d.out > 0.0 {
        let come = *d.out * arrive;
        *d.out -= come;
        for (r, &exits) in tiles.exits.iter().enumerate() {
            d.region[r * ages] += come * exits as f64 / ways as f64;
        }
        for &c in &tiles.entries {
            d.walk[c] += come / ways as f64;
        }
    }
    for row in d.region.chunks_exact_mut(ages) {
        let mut prev = 0.0;
        for (a, v) in row.iter_mut().enumerate() {
            let older = if a + 1 < ages {
                *v * -(-dt / tiles.widths[a]).exp_m1()
            } else {
                0.0
            };
            *v = *v - older + prev;
            prev = older;
        }
    }
}

/// Newcomers at the ways in, and the people out of the house forgotten now and then (only the
/// people nobody knows of; hidden.Undetected._arrive, MODEL.md 3.4).
fn arrive(tiles: &Tiles, p: &Params, d: &mut DensityMut, dt: f64) {
    *d.out *= (-p.forget_rate * dt).exp();
    if tiles.ways() > 0 {
        let come = p.guest_rate * dt;
        for (r, &exits) in tiles.exits.iter().enumerate() {
            d.region[r * tiles.ages] += come * exits as f64;
        }
        for &c in &tiles.entries {
            d.walk[c] += come;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::filter::densities::DensityShape;
    use crate::testing::{config, wall};

    /// Two rooms of 3 x 3 m side by side: "a" with a sensor, "b" behind a wall with a door at (3, 1.5)
    /// and nobody seeing it (a closed region), and a door from "a" out of the house at (0, 1.5).
    fn with_a_region() -> World {
        let walls = [
            wall([0.0, 0.0], [6.0, 0.0]),
            wall([6.0, 0.0], [6.0, 3.0]),
            wall([6.0, 3.0], [0.0, 3.0]),
            wall([0.0, 3.0], [0.0, 0.0]),
            wall([3.0, 0.0], [3.0, 3.0]),
        ];
        let c = config(
            &[("sa", "a", [0.05, 0.05], 45.0)],
            r#"[{"id": "a", "name": "A", "kind": "room", "shape": "rect", "points": [[0, 0], [3, 3]]},
                {"id": "b", "name": "B", "kind": "room", "shape": "rect", "points": [[3, 0], [6, 3]]}]"#,
            &format!("[{}]", walls.join(", ")),
            r#"[{"id": "d", "x": 3.0, "y": 1.5, "width": 0.9}, {"id": "out", "x": 0.0, "y": 1.5, "width": 0.9}]"#,
        );
        World::build(&c, Params::default()).expect("a world")
    }

    fn total(d: &DensityMut) -> f64 {
        d.walk.iter().chain(d.still.iter()).chain(d.region.iter()).sum::<f64>() + *d.out
    }

    #[test]
    fn a_person_walking_through_the_doors_into_the_region_and_out_keeps_their_mass() {
        // MODEL.md 3.1-3.3: stays, walking, the region and out of the house only move a person's mass
        let world = with_a_region();
        let t = &world.tiles;
        assert_eq!(
            (t.regions, t.exits.clone(), t.region_doors[0].is_empty()),
            (1, vec![0], false)
        );
        assert!(!t.entries.is_empty(), "the door out of the house");
        let s: DensityShape = world.density_shape();
        let mut densities = Densities::new(s);
        let id = densities.alloc(Kind::Person);
        {
            let d = densities.get_mut(id);
            d.walk.fill(0.4 / s.tiles as f64);
            d.still.fill(0.4 / s.still_len() as f64);
            d.region[0] = 0.1;
            *d.out = 0.1;
        }
        for _ in 0..50 {
            move_densities(&world, &mut densities, &[(id, 0.2)]);
        }
        let d = densities.get_mut(id);
        assert!(
            d.region[0] < 0.1 && d.region[1..].iter().sum::<f64>() > 0.0,
            "the stay aged"
        );
        assert!(*d.out != 0.1, "somebody went out or came home");
        // the tick's matrix itself loses up to ~2e-12 per tile and tick (expm, entries below 1e-12
        // cut): the move may lose no more than that over its 100 ticks
        let (n, outs) = (t.n, t.regions + 1);
        let defect = (0..n)
            .map(|c| {
                (t.walk_t[c * n..][..n]
                    .iter()
                    .chain(&t.into_t[c * outs..][..outs])
                    .sum::<f64>()
                    - 1.0)
                    .abs()
            })
            .fold(0.0, f64::max);
        assert!(defect < 1e-11, "{defect}");
        assert!(
            (total(&d) - 1.0).abs() <= 100.0 * defect + 1e-14,
            "{} ({defect})",
            total(&d)
        );
    }

    #[test]
    fn a_fresh_stay_in_a_region_ends_by_its_hazard_and_ages_by_its_bin_width() {
        // MODEL.md 3.3: of mass 1 at age 0, 1 - exp(-rate_0 dt) comes out through the region's doors
        // (it is closed: none out of the house); the rest moves on to age 1 with 1 - exp(-dt / width_0)
        let world = with_a_region();
        let (t, p) = (&world.tiles, &world.params);
        // the hazard of the log-normal stay over the first bin (median 120 s, spread 1.5)
        let survival = |x: f64| 0.5 * libm::erfc((x.ln() - 120f64.ln()) / (1.5 * 2f64.sqrt()));
        let (rate, width) = (t.region_rates[0], t.widths[0]);
        assert!((rate - -survival(width).ln() / width).abs() < 1e-12 * rate, "{rate}");
        let mut densities = Densities::new(world.density_shape());
        let id = densities.alloc(Kind::Person);
        let mut d = densities.get_mut(id);
        d.region[0] = 1.0;
        let dt = 1.5;
        regions(t, p, &mut d, dt);
        let (stays, older) = ((-rate * dt).exp(), 1.0 - (-dt / width).exp());
        assert!((d.region[0] - stays * (1.0 - older)).abs() < 1e-15, "{}", d.region[0]);
        assert!((d.region[1] - stays * older).abs() < 1e-15, "{}", d.region[1]);
        assert!(d.region[2..].iter().all(|&v| v == 0.0) && *d.out == 0.0);
        let doors = &t.region_doors[0];
        for &c in doors {
            assert!((d.walk[c] - (1.0 - stays) / doors.len() as f64).abs() < 1e-15);
        }
        assert_eq!(d.walk.iter().filter(|&&w| w > 0.0).count(), doors.len());
    }
}
