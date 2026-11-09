//! Störquellen (MODEL.md 4.5): Objekte, die keine Personen sind und an einem festen Ort immer wieder
//! Spuren eines LD2450 machen (ein Luftentfeuchter, ein Spiegelbild) - Clutter-Generatoren als eigene
//! Klasse neben den Personen, mit eigener Geburt, eigenem Überleben und eigener Erfassung (D_Mahler2011
//! Abschn. III-A, Gl. 13-18), im PMBM wie die Personen (B_GarciaFernandez2018): die bekannten als
//! Bernoullis je Hypothese, die unbekannten als Poisson-Intensität, gleich über den Blick ihres Sensors.
//! Eine Störquelle gehört zu einem LD2450; die anderen Sensoren und das LD2410C sehen sie nicht. Sie geht
//! nicht, braucht keine Tür, gibt kein Licht und zählt nicht.
//!
//! Ihr Zustand je Achse: wo sie ist (y) und, solange sie eine Spur hat, deren Versatz auf ihr (c, o) wie
//! auf einer Person (MODEL.md 4.1: z = y + c + o + w); ein Kalman-Filter über (y, c, o). y zittert wie der Ort
//! einer Stehenden (3.1), geht aber nie.

use crate::filter::Filter;
use crate::filter::mixture::{Kalman, NewTrack, SegId, cut_to_sight};
use crate::geometry::Point;
use crate::ld2450::rates::TrackSpot;
use crate::params::STILL;
use crate::sensors::{SensorState, live};
use crate::world::World;

/// log(2 pi)
const LOG2PI: f64 = 1.8378770664093453;

/// Eine Störquelle, wie die Hypothesen sie nennen: die Spur, mit der sie zuerst gesehen wurde. So ist
/// „diese Spur ist eine neue Störquelle“ in allen Hypothesen dieselbe (5.6: zusammengelegt wird nach ihr).
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub struct ClutterId(pub u64);

/// Eine bekannte Störquelle (ein Bernoulli): ihr Sensor, wie wahrscheinlich es sie gibt, je Achse das Mittel
/// von (y, c, o) und deren Kovarianz (3 x 3); c und o sind 0, solange sie keine Spur hat.
#[derive(Debug, Clone, PartialEq)]
pub struct Clutter {
    pub si: usize,
    pub r: f64,
    pub mean: [[f64; 3]; 2],
    pub cov: [[f64; 9]; 2],
    /// ihre Spur und die Varianz ihres wandernden Versatzes o je Achse, bis wann o vorgerückt ist
    pub track: Option<(SegId, f64)>,
    pub t: f64,
}

impl Clutter {
    /// Eine neue Störquelle aus der ersten Position ihrer Spur: gleich wahrscheinlich überall im Blick,
    /// also y = z - c - o - w (wie die Quelle eines Geists, mixture::ghost_source).
    pub fn born(si: usize, track: NewTrack, t: f64, world: &World) -> Self {
        let p = &world.params;
        let (vc, vo) = (track.var * p.const_share, track.var * (1.0 - p.const_share));
        let s2 = track.var + p.white * p.white;
        let cov = [s2, -vc, -vo, -vc, vc, 0.0, -vo, 0.0, vo];
        let mut new = Self {
            si,
            r: 1.0,
            mean: [[track.z[0], 0.0, 0.0], [track.z[1], 0.0, 0.0]],
            cov: [cov, cov],
            track: Some((track.seg, vo)),
            t,
        };
        new.in_sight(world);
        new
    }

    /// Was ihr Sensor misst, ist in seiner Sicht: der Radar sieht nicht durch Wände, auch keine Störquelle (MODEL.md
    /// 4.1 „Wer gemessen wird, ist in Sicht“); derselbe Schnitt wie für eine Person (mixture::cut_to_sight).
    fn in_sight(&mut self, world: &World) {
        let mut g = Kalman {
            mean: self.mean.concat(),
            cov: self.cov.concat(),
        };
        cut_to_sight(world, world.sights[self.si].origin, &mut g);
        for ax in 0..2 {
            let (mean, cov) = g.axis(ax);
            self.mean[ax].copy_from_slice(mean);
            self.cov[ax].copy_from_slice(cov);
        }
    }

    pub fn pos(&self) -> Point {
        [self.mean[0][0], self.mean[1][0]]
    }

    /// Eine Spur beginnt auf ihr (oder wird auf ihr wiedergefunden, dann mit ihrem Versatz): die Störquelle
    /// danach und der Log der Dichte von z.
    pub fn attach(&self, track: NewTrack, t: f64, world: &World) -> (Self, f64) {
        let p = &world.params;
        let mut new = self.clone();
        if new.track.is_none() {
            let (vc, vo) = (track.var * p.const_share, track.var * (1.0 - p.const_share));
            for cov in &mut new.cov {
                (cov[4], cov[8]) = (vc, vo);
            }
            new.jitter(world, t);
            new.track = Some((track.seg, vo));
        }
        let l = new.update(track.z, t, world);
        (new, l)
    }

    /// Eine Position ihrer Spur: o wandert seit der letzten (OU, tau), dann das Kalman-Update mit z = y + c +
    /// o + w und der Schnitt auf die Sicht ihres Sensors; gibt den Log der Vorhersagedichte von z zurück.
    pub fn update(&mut self, z: Point, t: f64, world: &World) -> f64 {
        let p = &world.params;
        let (_, vo) = self.track.expect("a Störquelle with a track");
        let dt = (t - self.t).max(0.0);
        let a = (-dt / p.tau).exp();
        let wander = vo * (1.0 - a * a);
        self.t = t;
        let mut ll = 0.0;
        for ((mean, cov), &za) in self.mean.iter_mut().zip(&mut self.cov).zip(&z) {
            cov[0] += p.still_noise * p.still_noise * dt;
            mean[2] *= a;
            for i in 0..3 {
                cov[i * 3 + 2] *= a;
                cov[2 * 3 + i] *= a;
            }
            cov[8] += wander;
            // H = (1, 1, 1): P H' sind die Zeilensummen
            let ph = [0, 1, 2].map(|i| cov[i * 3] + cov[i * 3 + 1] + cov[i * 3 + 2]);
            let s = ph[0] + ph[1] + ph[2] + p.white * p.white;
            let e = za - (mean[0] + mean[1] + mean[2]);
            ll += -0.5 * e * e / s - 0.5 * s.ln() - 0.5 * LOG2PI;
            for i in 0..3 {
                mean[i] += ph[i] / s * e;
                for j in 0..3 {
                    cov[i * 3 + j] -= ph[i] * ph[j] / s;
                }
            }
        }
        self.in_sight(world);
        ll
    }

    /// Ohne Spur bis t: ihr Ort zittert wie der einer Stehenden (MODEL.md 3.1; wie beim Geist unterscheidet sie
    /// sich von einer Person nicht durch ein anderes Rauschen, 4.2), aber sie geht nie.
    fn jitter(&mut self, world: &World, t: f64) {
        let q = world.params.still_noise.powi(2) * (t - self.t).max(0.0);
        for cov in &mut self.cov {
            cov[0] += q;
        }
        self.t = t;
    }

    /// Ihre Spur endete (oder gehört jetzt einem anderen): der Versatz wird herausintegriert.
    pub fn drop_track(&mut self) {
        self.track = None;
        for (mean, cov) in self.mean.iter_mut().zip(&mut self.cov) {
            (mean[1], mean[2]) = (0.0, 0.0);
            let vy = cov[0];
            *cov = [0.0; 9];
            cov[0] = vy;
        }
    }

    /// Die Rate (1/s), mit der ihr Sensor eine Spur auf ihr beginnt: clutter_acquire, gleich wo im Blick sie ist
    /// (die feste Erfassung der Clutter-Generatoren, D_Mahler2011 Abschn. III-A; gemessen an der Stelle selbst),
    /// weniger neben seinen laufenden Zielen (Auflösung, MODEL.md 4.1), an ihrem Mittel. `spots`: je Sensor, wo
    /// seine laufenden Spuren sind.
    pub fn rate(&self, world: &World, spots: &[Vec<TrackSpot>]) -> f64 {
        let (sight, at) = (&world.sights[self.si], self.pos());
        world.params.clutter_acquire[0] * world.rates.mask(sight, at, &spots[self.si], STILL)
    }

    /// Wie gut ihr Sensor ein bei z gehaltenes Ziel auf ihr wiederfinden kann (MODEL.md 4.1): der Kern um die Stelle
    /// über ihren Ort (wie evidence::near_mixture für eine Person; ohne Sicht wie in `rate`, so wie der Geist sein
    /// Ziel mit 1 wiederfindet).
    pub fn near(&self, world: &World, z: Point) -> f64 {
        let r2 = world.params.find_radius.powi(2);
        (0..2).fold(1.0, |k, a| {
            let (v, d) = (r2 + self.cov[a][0], self.mean[a][0] - z[a]);
            k * (r2 / v).sqrt() * (-0.5 * d * d / v).exp()
        })
    }

    /// Die Mischung [(w_i, Störquelle_i)] derselben Störquelle aus mehreren Hypothesen (MODEL.md 5.6): w_i die
    /// Gewichte der Hypothesen (summiert 1), wo sie fehlt, gibt es sie nicht (r = 0). Momentenabgleich mit
    /// w_i r_i.
    pub fn mix(parts: &[(f64, &Clutter)]) -> Clutter {
        let r: f64 = parts.iter().map(|(w, c)| w * c.r).sum();
        let first = parts[0].1;
        let mut out = Clutter { r, ..first.clone() };
        if r <= 0.0 {
            return out;
        }
        for ax in 0..2 {
            let mut mean = [0.0; 3];
            for (w, c) in parts {
                for (m, x) in mean.iter_mut().zip(&c.mean[ax]) {
                    *m += w * c.r / r * x;
                }
            }
            let mut cov = [0.0; 9];
            for (w, c) in parts {
                let d = [0, 1, 2].map(|i| c.mean[ax][i] - mean[i]);
                for i in 0..3 {
                    for j in 0..3 {
                        cov[i * 3 + j] += w * c.r / r * (c.cov[ax][i * 3 + j] + d[i] * d[j]);
                    }
                }
            }
            (out.mean[ax], out.cov[ax]) = (mean, cov);
        }
        out
    }
}

impl Filter {
    /// Die Störquellen ohne Spur von t0 bis t (MODEL.md 4.5): Jede überlebt mit e^(-dt / clutter_life); wo ihr
    /// Sensor lebt, hat er keine Spur auf ihr begonnen: `1 - r + r e^(-a dt)` (die Bernoulli-Aktualisierung wie
    /// bei einer bekannten Person ohne Spur, 5.5).
    pub(super) fn clutter_flush(&mut self, world: &World, sensors: &[SensorState], t0: f64, t: f64) {
        let dt = t - t0;
        let watching = live(sensors, t);
        let survive = (-dt / world.params.clutter_life).exp();
        for hy in &mut self.hyps {
            for c in hy.clutter.values_mut().filter(|c| c.track.is_none()) {
                c.r *= survive;
                c.jitter(world, t);
                if watching.contains(c.si) {
                    let miss = (-c.rate(world, self.tracks.spots()) * dt).exp();
                    let f = 1.0 - c.r + c.r * miss;
                    hy.logw += f.ln();
                    c.r *= miss / f;
                }
            }
        }
    }

    /// Der erste Frame von Sensor si (oder nach einer Lücke): Seine Störquellen ohne Spur hatten keine, mit dem
    /// stationären Anteil 1 / (1 + a T) (T: die Lebensdauer einer Spur auf einer Störquelle; wie censor).
    pub(super) fn clutter_censor(&mut self, world: &World, si: usize) {
        let life = world.params.clutter_acquire[1];
        for hy in &mut self.hyps {
            for c in hy.clutter.values_mut().filter(|c| c.si == si && c.track.is_none()) {
                let miss = 1.0 / (1.0 + c.rate(world, self.tracks.spots()) * life);
                let f = 1.0 - c.r + c.r * miss;
                hy.logw += f.ln();
                c.r *= miss / f;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn track(z: Point) -> NewTrack {
        NewTrack {
            seg: SegId(1),
            z,
            var: 0.04,
        }
    }

    #[test]
    fn a_new_track_on_it_is_the_kalman_update_of_y_plus_the_offsets() {
        // die Vorhersagedichte je Achse N(z; y, P_y + q + vc + vo + w^2), danach liegt y zwischen Ort und Messung
        let world = crate::testing::flat();
        let p = &world.params;
        let mut c = Clutter::born(0, track([1.0, 2.0]), 0.0, &world);
        c.drop_track();
        let vy = c.cov[0][0];
        let (new, l) = c.attach(
            NewTrack {
                seg: SegId(2),
                ..track([1.3, 2.0])
            },
            5.0,
            &world,
        );
        // y zittert bis zur neuen Spur 5 s lang wie eine Stehende
        let s = vy + p.still_noise.powi(2) * 5.0 + 0.04 + p.white * p.white;
        let want = 2.0 * (-0.5 * s.ln() - 0.5 * LOG2PI) - 0.5 * 0.09 / s;
        assert!((l - want).abs() < 1e-12, "{l} against {want}");
        assert!(new.pos()[0] > 1.0 && new.pos()[0] < 1.3 && new.cov[0][0] < vy + p.still_noise.powi(2) * 5.0);
        assert_eq!(new.track.map(|(s, _)| s), Some(SegId(2)));
    }

    #[test]
    fn hypotheses_without_it_mix_in_as_nobody() {
        let world = crate::testing::flat();
        let mut a = Clutter::born(0, track([1.0, 2.0]), 0.0, &world);
        a.drop_track();
        let mut b = a.clone();
        b.mean[0][0] = 2.0;
        let m = Clutter::mix(&[(0.25, &a), (0.25, &b)]);
        assert_eq!(m.r, 0.5);
        assert!((m.pos()[0] - 1.5).abs() < 1e-12);
        assert!((m.cov[0][0] - (a.cov[0][0] + 0.25)).abs() < 1e-12);
    }
}
