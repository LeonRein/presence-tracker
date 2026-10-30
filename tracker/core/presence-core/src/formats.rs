//! The saved formats (docs/rust-core.md 1; MODEL.md 4.2, 4.3, 5.3, 6): what 0.31.1 wrote, the core
//! reads, and what it writes, 0.31.1 reads. Files come from outside: read with named errors, sizes
//! checked before anything is allocated. Here the pieces they share: numbers packed as base64 of zlib,
//! the fingerprints (sha1 as Python's hashlib over numpy's bytes), the roundings of numpy and Python.

use std::io::{Read, Write};

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use flate2::Compression;
use flate2::read::ZlibDecoder;
use flate2::write::ZlibEncoder;
use serde_json::{Map, Value};

use crate::learn::{LearnedError, PackedFault};

/// numpy's round(x, decimals): rint(x 10^d) / 10^d.
pub fn round_numpy(x: f64, decimals: i32) -> f64 {
    let f = 10f64.powi(decimals);
    (x * f).round_ties_even() / f
}

/// Python's round(x, decimals): the decimal closest to x's exact value (ties to even).
pub fn round_python(x: f64, decimals: usize) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{x:.decimals$}").parse().unwrap_or(x)
}

/// base64 of zlib (Python's zlib.compress at `level`).
pub fn pack(bytes: &[u8], level: u32) -> String {
    let mut z = ZlibEncoder::new(Vec::new(), Compression::new(level));
    z.write_all(bytes).expect("writing to memory");
    STANDARD.encode(z.finish().expect("writing to memory"))
}

/// The inverse of pack, with exactly `len` bytes (else Err: never more is read than that).
pub fn unpack(text: &str, len: usize) -> Result<Vec<u8>, LearnedError> {
    let z = STANDARD
        .decode(text)
        .map_err(|_| LearnedError::Packed(PackedFault::Base64))?;
    let mut out = Vec::with_capacity(len);
    ZlibDecoder::new(z.as_slice())
        .take(len as u64 + 1)
        .read_to_end(&mut out)
        .map_err(|_| LearnedError::Packed(PackedFault::Zlib))?;
    if out.len() == len {
        Ok(out)
    } else {
        Err(LearnedError::Packed(PackedFault::Length))
    }
}

/// sha1 over numpy's bytes of float64 arrays and text, its first 16 hex digits (the fingerprints of
/// tiling.Tiling and destination.Geometry).
#[derive(Default)]
pub struct Fingerprint(sha1_smol::Sha1);

impl Fingerprint {
    pub fn floats(&mut self, values: impl IntoIterator<Item = f64>) -> &mut Self {
        for v in values {
            self.0.update(&v.to_le_bytes());
        }
        self
    }

    pub fn text(&mut self, text: &str) -> &mut Self {
        self.0.update(text.as_bytes());
        self
    }

    pub fn hex(&self) -> String {
        self.0.digest().to_string()[..16].to_string()
    }
}

// --------------------------------------------------------------- reading JSON with named errors

pub(crate) fn object<'a>(v: &'a Value, name: &'static str) -> Result<&'a Map<String, Value>, LearnedError> {
    v.as_object().ok_or(LearnedError::Field(name))
}

pub(crate) fn field<'a>(m: &'a Map<String, Value>, name: &'static str) -> Result<&'a Value, LearnedError> {
    m.get(name).ok_or(LearnedError::Field(name))
}

/// A finite number at least `min`.
pub(crate) fn number(v: &Value, name: &'static str, min: f64) -> Result<f64, LearnedError> {
    v.as_f64()
        .filter(|x| x.is_finite() && *x >= min)
        .ok_or(LearnedError::Field(name))
}

/// A list of exactly `n` finite numbers at least `min`.
pub(crate) fn numbers(v: &Value, name: &'static str, n: usize, min: f64) -> Result<Vec<f64>, LearnedError> {
    let list = v.as_array().filter(|a| a.len() == n).ok_or(LearnedError::Field(name))?;
    list.iter().map(|x| number(x, name, min)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundings_are_numpys_and_pythons() {
        assert_eq!(round_numpy(2.5, 0), 2.0);
        assert_eq!(round_numpy(0.12345, 3), 0.123);
        assert_eq!(round_numpy(-1.0005, 3), -1.0); // 1000.4999... after the product
        // Python: round(0.03125, 4) == 0.0312 (the exact tie to even), round(2.675, 2) == 2.67 (below the tie)
        assert_eq!(round_python(0.03125, 4), 0.0312);
        assert_eq!(round_python(2.675, 2), 2.67);
        assert_eq!(round_python(1.23456789, 3), 1.235);
    }

    #[test]
    fn packed_numbers_come_back_and_nothing_else_does() {
        let bytes: Vec<u8> = (0..1000u32).map(|i| (i % 7) as u8).collect();
        let text = pack(&bytes, 9);
        assert_eq!(unpack(&text, 1000).expect("readable"), bytes);
        assert_eq!(unpack(&text, 999), Err(LearnedError::Packed(PackedFault::Length)));
        assert_eq!(unpack(&text, 1001), Err(LearnedError::Packed(PackedFault::Length)));
        assert_eq!(unpack("%%", 3), Err(LearnedError::Packed(PackedFault::Base64)));
        assert_eq!(
            unpack(&STANDARD.encode(b"abc"), 3),
            Err(LearnedError::Packed(PackedFault::Zlib))
        );
        // Python: base64.b64encode(zlib.compress(bytes(3))) reads as three zeros
        assert_eq!(unpack("eJxjYGAAAAADAAE=", 3).expect("readable"), vec![0, 0, 0]);
    }

    #[test]
    fn a_fingerprint_is_hashlibs() {
        // Python: hashlib.sha1(np.array([1.0, 2.5]).tobytes() + b"a|b").hexdigest()[:16]
        let got = Fingerprint::default().floats([1.0, 2.5]).text("a|b").hex();
        assert_eq!(got, "f0a3ada5db5d9263");
    }
}
