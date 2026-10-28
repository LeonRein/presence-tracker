//! The LD2410C's energies per 0.75 m gate (MODEL.md 4.3; ld2410.py): what a person puts into them,
//! their likelihood, the echo sources. Per frame 9 "moving" and 9 "still" energies (0..100) for the
//! rings of 0.75 m slant distance; still in rings 0 and 1 is always 0. The 16 cells used here are
//! moving 0..8 and still 2..8. Each energy ~ Gamma(shape alpha, mean mu), 100 censored, with mu =
//! background + sum over the people of their expected energy (incoherent superposition).

pub mod background;
pub mod echoes;
pub mod likelihood;
pub mod profile;
pub mod sensor;

/// m: a gate's depth
pub const GATE: f64 = 0.75;
/// moving 0..8, still 2..8
pub const CELLS: usize = 16;
/// the moving cells (the rest are still ones)
pub const MOVING: usize = 9;
/// energies at 100 are capped: censored above this
pub const CAP: f64 = 99.5;
/// expected energy below this: the tile is out of the LD2410C's view
pub const FLOOR: f64 = 0.05;
/// likelihood ratios are capped at e^this (keeps the densities finite)
pub const LOG_CAP: f64 = 600.0;

/// The 16 cells' values from one for the moving cells and one for the still cells (ld2410.cell_values).
pub fn cell_values(moving: f64, still: f64) -> [f64; CELLS] {
    std::array::from_fn(|c| if c < MOVING { moving } else { still })
}

/// m: the centre of each cell's gate (ld2410.CENTRE)
pub fn centre(c: usize) -> f64 {
    let gate = if c < MOVING { c } else { c - MOVING + 2 };
    (gate as f64 + 0.5) * GATE
}
