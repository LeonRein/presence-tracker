//! The Bayes filter of the presence tracker (MODEL.md) as a pure core: no files, no network, no
//! clock (every time is an argument), no threads, nothing random. docs/rust-core.md describes the
//! data model and the cut to the Python shell.

pub mod filter;
pub mod geometry;
pub mod ld2410;
pub mod ld2450;
pub mod motion;
pub mod params;
pub mod sensors;
pub mod tracker;
pub mod vmath;
pub mod world;

#[cfg(test)]
mod ld_reference;
#[cfg(test)]
pub mod testing;
