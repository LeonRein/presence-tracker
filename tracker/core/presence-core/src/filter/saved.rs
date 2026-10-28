//! The people's saved state in its file format (Tracker.people_state, MODEL.md 5.3; hidden.Hidden.to_dict
//! and from_dict): per density its masses as shares of the whole in float16, shares below SAVE_CUT as 0,
//! zlib, base64 - 6 kB for a person instead of 80 kB as float32 (7.10. 08:06, 9 densities); over all 18 000
//! masses of a person the rounding stays below 5e-4. What 0.38.1 wrote, the core reads, and the reverse
//! (the zlib bytes may differ). A state fits only tiles with the same fingerprint (World::fingerprint).

use half::f16;
use serde_json::{Map, Value, json};

use crate::filter::PeopleState;
use crate::filter::densities::{Amp, DensityData, DensityShape, Kind};
use crate::formats::{field, number, object, pack, unpack};
use crate::learn::LearnedError;

/// Shares of a density below this are saved as 0 (hidden.SAVE_CUT).
const SAVE_CUT: f64 = 1e-6;

impl PeopleState {
    /// The file format at the time `t` of the last move of everybody, on the tiles of `fingerprint`.
    /// Err if a density is no density (a mass not finite or negative).
    pub fn to_json(&self, t: f64, fingerprint: &str) -> Result<Value, String> {
        let densities = self.densities.iter().map(density_json).collect::<Result<Vec<_>, _>>()?;
        let hyps: Vec<Value> = self
            .hyps
            .iter()
            .map(|(logw, people, unknown)| json!({"logw": logw, "people": people, "unknown": unknown}))
            .collect();
        Ok(json!({"t": t, "tiles": fingerprint, "densities": densities, "hyps": hyps}))
    }

    /// The saved time and state of the file format, if it was saved on tiles of this fingerprint and
    /// shape; what is not read (a field, packed numbers, another size) is named. The unknown ones are
    /// the densities a hypothesis names as its unknown ones; a person's masses are scaled to sum 1 when
    /// restored. Whether the state makes sense (weights, kinds, ranges) PeopleState::check decides.
    pub fn from_json(v: &Value, shape: &DensityShape, fingerprint: &str) -> Result<(f64, Self), LearnedError> {
        let m = object(v, "people")?;
        if field(m, "tiles")?.as_str() != Some(fingerprint) {
            return Err(LearnedError::OtherTiles);
        }
        let t = number(field(m, "t")?, "t", f64::NEG_INFINITY)?;
        let hyps = list(field(m, "hyps")?, "hyps")?
            .iter()
            .map(|h| {
                let h = object(h, "hyps")?;
                let people = list(field(h, "people")?, "people")?
                    .iter()
                    .map(|i| index(i, "people"))
                    .collect::<Result<_, _>>()?;
                Ok((
                    number(field(h, "logw")?, "logw", f64::NEG_INFINITY)?,
                    people,
                    index(field(h, "unknown")?, "unknown")?,
                ))
            })
            .collect::<Result<Vec<_>, LearnedError>>()?;
        let densities = list(field(m, "densities")?, "densities")?
            .iter()
            .enumerate()
            .map(|(k, d)| {
                let kind = if hyps.iter().any(|&(_, _, u)| u == k) {
                    Kind::Intensity
                } else {
                    Kind::Person
                };
                read_density(object(d, "densities")?, shape, kind)
            })
            .collect::<Result<_, _>>()?;
        Ok((t, Self { hyps, densities }))
    }
}

fn list<'a>(v: &'a Value, name: &'static str) -> Result<&'a Vec<Value>, LearnedError> {
    v.as_array().ok_or(LearnedError::Field(name))
}

fn index(v: &Value, name: &'static str) -> Result<usize, LearnedError> {
    v.as_u64()
        .and_then(|i| usize::try_from(i).ok())
        .ok_or(LearnedError::Field(name))
}

/// numpy's astype("<f2"): rounded to the nearest float16, ties to even, then packed.
fn pack_f16(values: impl Iterator<Item = f64>) -> String {
    let bytes: Vec<u8> = values.flat_map(|x| f16::from_f64(x).to_le_bytes()).collect();
    pack(&bytes, 9)
}

/// n float16 of packed text, each finite and not negative, times `scale` (else Err naming the field).
fn unpack_f16(text: &Value, name: &'static str, n: usize, scale: f64) -> Result<Vec<f64>, LearnedError> {
    let text = text.as_str().ok_or(LearnedError::Field(name))?;
    let values: Vec<f64> = unpack(text, 2 * n)?
        .as_chunks::<2>()
        .0
        .iter()
        .map(|&b| f16::from_le_bytes(b).to_f64() * scale)
        .collect();
    if values.iter().all(|x| x.is_finite() && *x >= 0.0) {
        Ok(values)
    } else {
        Err(LearnedError::Field(name))
    }
}

/// One density as saved (Hidden.to_dict): the masses by their sum (`scale`, 1 if 0), the amplitudes of
/// the stays only if a track measured some (their levels as they are).
fn density_json(d: &DensityData) -> Result<Value, String> {
    let masses = || d.walk.iter().chain(&d.still).chain(&d.region).chain([&d.out]);
    if !masses().all(|x| x.is_finite() && *x >= 0.0) {
        return Err("a density that is no density".into());
    }
    let total = crate::vmath::pairwise_sum(&d.walk)
        + crate::vmath::pairwise_sum(&d.still)
        + crate::vmath::pairwise_sum(&d.region)
        + d.out;
    let scale = if total > 0.0 { total } else { 1.0 };
    let shares = |a: &[f64]| {
        pack_f16(a.iter().map(|&x| {
            let share = x / scale;
            if share < SAVE_CUT { 0.0 } else { share }
        }))
    };
    let mut out = Map::new();
    out.insert("walk".into(), shares(&d.walk).into());
    out.insert("still".into(), shares(&d.still).into());
    out.insert("region".into(), shares(&d.region).into());
    if let Some(amp) = &d.amp {
        out.insert("amp".into(), pack_f16(amp.levels.iter().copied()).into());
    }
    out.insert("out".into(), d.out.into());
    out.insert("scale".into(), scale.into());
    out.insert("r".into(), d.r.into());
    Ok(Value::Object(out))
}

/// One density of the file (Hidden.from_dict): the masses times `scale`; the amplitudes per level and
/// tile normalized over the amplitudes, the prior (g = 1) where nothing is left; r of the unknown ones 1.
fn read_density(d: &Map<String, Value>, shape: &DensityShape, kind: Kind) -> Result<DensityData, LearnedError> {
    let scale = number(field(d, "scale")?, "scale", 0.0).and_then(|s| {
        if s > 0.0 {
            Ok(s)
        } else {
            Err(LearnedError::Field("scale"))
        }
    })?;
    let amp = match d.get("amp") {
        None => None,
        Some(text) => {
            let mut levels = unpack_f16(text, "amp", shape.amp_len(), 1.0)?;
            let prior = shape.amp_prior();
            for k in 0..shape.levels {
                for c in 0..shape.tiles {
                    let at = |a: usize| shape.amp_row(k, a).start + c;
                    let total: f64 = (0..=shape.amps).map(|a| levels[at(a)]).sum();
                    for a in 0..=shape.amps {
                        levels[at(a)] = if total > 0.0 {
                            levels[at(a)] / total
                        } else {
                            prior[at(a)]
                        };
                    }
                }
            }
            Some(Amp { levels, keep: None })
        }
    };
    let r = match (kind, d.get("r")) {
        (Kind::Person, Some(r)) => number(r, "r", f64::NEG_INFINITY)?,
        _ => 1.0,
    };
    Ok(DensityData {
        walk: unpack_f16(field(d, "walk")?, "walk", shape.tiles, scale)?,
        still: unpack_f16(field(d, "still")?, "still", shape.still_len(), scale)?,
        region: unpack_f16(field(d, "region")?, "region", shape.region_len(), scale)?,
        out: number(field(d, "out")?, "out", 0.0)?,
        clock: 0.0,
        r,
        kind,
        amp,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn shape() -> DensityShape {
        DensityShape {
            tiles: 3,
            kinds: 2,
            levels: 2,
            regions: 1,
            ages: 2,
            amps: 1,
        }
    }

    fn person(shape: &DensityShape) -> DensityData {
        let mut still = vec![0.0; shape.still_len()];
        still[1] = 0.6;
        still[4] = 1e-9; // below SAVE_CUT of the whole: saved as 0
        let mut levels = shape.amp_prior();
        levels[shape.amp_row(1, 0).start + 1] = 0.75; // a measured amplitude at level 1, tile 1
        levels[shape.amp_row(1, 1).start + 1] = 0.25;
        DensityData {
            walk: vec![0.1, 0.0, 0.0],
            still,
            region: vec![0.2, 0.1],
            out: 0.0,
            clock: 0.0,
            r: 0.5,
            kind: Kind::Person,
            amp: Some(Amp { levels, keep: None }),
        }
    }

    fn unknown(shape: &DensityShape) -> DensityData {
        DensityData {
            walk: vec![0.0; shape.tiles],
            still: vec![0.0; shape.still_len()],
            region: vec![0.0; shape.region_len()],
            out: 0.5,
            clock: 0.0,
            r: 1.0,
            kind: Kind::Intensity,
            amp: None,
        }
    }

    #[test]
    fn the_float16_are_numpys() {
        // numpy: np.array([1/3, 0.1, 65519.0, 1e-8, 2**-24 * 1.5]).astype("<f2").view("<u2")
        let bits: Vec<u16> = [1.0 / 3.0, 0.1, 65519.0, 1e-8, 2f64.powi(-24) * 1.5]
            .iter()
            .map(|&x| f16::from_f64(x).to_bits())
            .collect();
        assert_eq!(bits, vec![0x3555, 0x2e66, 0x7bff, 0x0000, 0x0002]);
    }

    #[test]
    fn a_saved_state_comes_back_on_its_tiles_only() {
        let s = shape();
        let state = PeopleState {
            hyps: vec![(-0.1, vec![0], 1), (-2.3, vec![], 1)],
            densities: vec![person(&s), unknown(&s)],
        };
        let saved = state.to_json(12.5, "abc").expect("a state");
        assert_eq!(saved["densities"][0]["scale"], 0.1 + 0.6 + 1e-9 + 0.2 + 0.1); // the sum, as numpy sums
        let (t, back) = PeopleState::from_json(&saved, &s, "abc").expect("its tiles");
        assert_eq!(t, 12.5);
        assert_eq!(back.hyps, state.hyps);
        let (p, u) = (&back.densities[0], &back.densities[1]);
        assert_eq!(
            (p.kind, u.kind, p.r, u.r, u.out),
            (Kind::Person, Kind::Intensity, 0.5, 1.0, 0.5)
        );
        assert_eq!(p.still[4], 0.0);
        for (a, b) in p
            .walk
            .iter()
            .chain(&p.still)
            .zip(person(&s).walk.iter().chain(&person(&s).still))
        {
            assert!((a - b).abs() <= 5e-4 * b + SAVE_CUT, "{a} {b}");
        }
        let amp = &p.amp.as_ref().expect("measured").levels;
        assert_eq!(
            (amp[s.amp_row(1, 0).start + 1], amp[s.amp_row(1, 1).start + 1]),
            (0.75, 0.25)
        );
        assert_eq!(
            PeopleState::from_json(&saved, &s, "abd").err(),
            Some(LearnedError::OtherTiles)
        );
    }

    #[test]
    fn what_cannot_be_read_is_named() {
        let s = shape();
        let state = PeopleState {
            hyps: vec![(0.0, vec![0], 1)],
            densities: vec![person(&s), unknown(&s)],
        };
        let saved = state.to_json(0.0, "f").expect("a state");
        let broken = |path: &[&str], v: Value| {
            let mut b = saved.clone();
            let mut at = &mut b;
            for k in path {
                at = match k.parse::<usize>() {
                    Ok(i) => &mut at[i],
                    Err(_) => &mut at[*k],
                };
            }
            *at = v;
            PeopleState::from_json(&b, &s, "f").err()
        };
        let other = PeopleState::from_json(
            &state.to_json(0.0, "f").expect("a state"),
            &DensityShape { tiles: 4, ..s },
            "f",
        );
        assert_eq!(
            other.err(),
            Some(LearnedError::Packed(crate::learn::PackedFault::Length))
        );
        assert_eq!(
            broken(&["densities", "0", "scale"], json!(0.0)),
            Some(LearnedError::Field("scale"))
        );
        assert_eq!(
            broken(&["densities", "0", "walk"], json!("%%")),
            Some(LearnedError::Packed(crate::learn::PackedFault::Base64))
        );
        assert_eq!(
            broken(&["densities", "0", "out"], json!(-1.0)),
            Some(LearnedError::Field("out"))
        );
        assert_eq!(
            broken(&["hyps", "0", "logw"], json!("x")),
            Some(LearnedError::Field("logw"))
        );
        assert_eq!(
            broken(&["hyps", "0", "people"], json!([-1])),
            Some(LearnedError::Field("people"))
        );
        assert_eq!(broken(&["t"], Value::Null), Some(LearnedError::Field("t")));
        // a mass that does not fit float16 is no mass
        let inf = pack_f16([f64::INFINITY, 0.0, 0.0].into_iter());
        assert_eq!(
            broken(&["densities", "0", "walk"], json!(inf)),
            Some(LearnedError::Field("walk"))
        );
        // nothing left of the amplitudes at a tile: nothing measured there (g = 1)
        let zero = pack_f16(std::iter::repeat_n(0.0, s.amp_len()));
        let mut b = saved.clone();
        b["densities"][0]["amp"] = json!(zero);
        let (_, back) = PeopleState::from_json(&b, &s, "f").expect("readable");
        assert_eq!(back.densities[0].amp.as_ref().expect("given").levels, s.amp_prior());
    }

    #[test]
    fn a_density_that_is_no_density_is_not_saved() {
        let s = shape();
        let mut p = person(&s);
        p.walk[0] = f64::NAN;
        let state = PeopleState {
            hyps: vec![(0.0, vec![0], 1)],
            densities: vec![p, unknown(&s)],
        };
        assert!(state.to_json(0.0, "f").is_err());
    }
}
