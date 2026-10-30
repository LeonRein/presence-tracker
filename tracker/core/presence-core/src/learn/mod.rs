//! What the app learns while it runs (MODEL.md 4.3, 6 "Ziel"): what each LD2410C sees without anybody,
//! where walks go; nothing while learning is paused
//! (MODEL.md 10, "Lernen pausieren"). They learn at the places of the Python filter
//! (docs/rust-core.md 2.0); the tracker lends each step the learner it needs next to the filter.

pub mod destination;
pub mod pauses;

use std::fmt;

use serde_json::{Value, json};

use crate::ld2410::background::Background;
use crate::world::World;
use destination::Destinations;

/// The two learners, each lent alone to the step that learns (docs/rust-core.md 2.0), and the key of the
/// geometry they learn under (World::geometry).
#[derive(Debug, Clone, PartialEq)]
pub struct Learned {
    pub background: Background,
    pub destinations: Destinations,
    geometry: String,
}

impl Learned {
    /// Nothing learned yet: the LD2410C's prior background, no walks.
    pub fn new(world: &World) -> Self {
        let mut background = Background::default();
        background.follow(&world.plan);
        Self {
            background,
            destinations: Destinations::new(&world.destinations),
            geometry: world.geometry.clone(),
        }
    }

    /// Follow a new world: under another geometry (a sensor placed, moved, turned, read otherwise, the walls,
    /// doors or zones changed: Config::geometry_key) all of it starts over, for every sensor; under the same
    /// one it stays (a destination map of another grid starts over).
    pub fn follow(&mut self, world: &World) {
        if self.geometry != world.geometry {
            *self = Self::new(world);
            return;
        }
        self.background.follow(&world.plan);
        self.destinations.follow(&world.destinations);
    }

    /// The two in their saved formats (Tracker.learned).
    pub fn to_json(&self) -> Value {
        json!({"ld_background": self.background.to_json(), "dest_map": self.destinations.map().to_json()})
    }
}

/// Why a saved part of what was learned is not taken (the files are input from outside).
#[derive(Debug, Clone, PartialEq)]
pub enum LearnedError {
    /// this field is missing or no number of the allowed range
    Field(&'static str),
    /// this field of a sensor's part has another size or a value out of range
    Part { field: &'static str, sensor: String },
    /// packed numbers that cannot be read
    Packed(PackedFault),
    /// a destination map of another floor plan (its rooms, doors, regions)
    OtherPlan,
    /// a saved state of the people on other tiles (another floor plan or model)
    OtherTiles,
}

impl fmt::Display for LearnedError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Field(name) => write!(f, "{name} is missing or out of range"),
            Self::Part { field, sensor } => {
                write!(f, "{field} of sensor {sensor} has another size or a value out of range")
            }
            Self::Packed(fault) => write!(f, "the packed numbers: {fault}"),
            Self::OtherPlan => write!(f, "a destination map of another floor plan"),
            Self::OtherTiles => write!(f, "a saved state of other tiles"),
        }
    }
}

impl std::error::Error for LearnedError {}

/// What is wrong with numbers packed as base64 of zlib.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PackedFault {
    Base64,
    Zlib,
    /// more or fewer bytes than the shape says
    Length,
    /// a number that cannot be what it counts (not finite, negative)
    Value,
}

impl fmt::Display for PackedFault {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::Base64 => "no base64",
            Self::Zlib => "no zlib",
            Self::Length => "another number of values",
            Self::Value => "a value out of range",
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::testing;

    #[test]
    fn what_was_learned_holds_under_its_geometry_only() {
        let w = testing::flat();
        let mut learned = Learned::new(&w);
        learned.background.hold(60.0);
        let kept = learned.clone();
        // the same geometry (a sensor switched off, a name, a parameter): all of it stays
        learned.follow(&w);
        assert_eq!(learned, kept);
        // another one (a sensor moved, a wall): all of it starts over, for every sensor
        let mut moved = testing::flat();
        moved.geometry = "another".into();
        learned.follow(&moved);
        assert_eq!(learned, Learned::new(&moved));
        assert_ne!(learned, kept);
    }
}
