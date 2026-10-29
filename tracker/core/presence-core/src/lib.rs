//! The Bayes filter of the presence tracker (MODEL.md) as a pure core: no files, no network, no
//! clock (every time is an argument), no threads, nothing random. docs/rust-core.md describes the
//! data model and the cut to the Python shell.

pub mod config;
pub mod filter;
pub mod formats;
pub mod frames;
pub mod geometry;
pub mod ld2410;
pub mod ld2450;
pub mod learn;
pub(crate) mod linalg;
pub(crate) mod motion;
pub mod outputs;
pub mod params;
pub mod plan;
pub(crate) mod room_seen;
pub use room_seen::SILENT_AFTER;
pub mod sensors;
pub mod tracker;
pub(crate) mod vmath;
pub mod world;

#[cfg(test)]
mod ld_reference;
#[cfg(test)]
pub mod testing;
