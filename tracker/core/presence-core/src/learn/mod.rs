//! What the app learns while it runs (MODEL.md 4.2, 4.3, 6 "Ziel"): where each sensor starts ghost
//! tracks, what each LD2410C sees without anybody, where walks go; nothing while learning is paused
//! (MODEL.md 10, "Lernen pausieren"). They learn at the places of the Python filter
//! (docs/rust-core.md 2.0); the tracker lends each step the learner it needs next to the filter.

pub mod destination;
pub mod ghost_map;
pub mod pauses;

use std::fmt;

use serde_json::{Value, json};

use crate::ld2410::background::Background;
use crate::world::World;
use destination::Destinations;
use ghost_map::GhostMap;

/// The three learners, each lent alone to the step that learns (docs/rust-core.md 2.0).
#[derive(Debug, Clone, PartialEq)]
pub struct Learned {
    pub ghosts: GhostMap,
    pub background: Background,
    pub destinations: Destinations,
}

impl Learned {
    /// Nothing learned yet: the ghost map's prior, the LD2410C's prior background, no walks.
    pub fn new(world: &World) -> Self {
        let mut ghosts = GhostMap::from_prior(&world.params);
        ghosts.follow(&world.plan);
        let mut background = Background::default();
        background.follow(&world.plan);
        Self {
            ghosts,
            background,
            destinations: Destinations::new(&world.destinations),
        }
    }

    /// Follow a new world: the parts of the sensors that hang where they hung stay (a ghost map learned
    /// with another prior, a destination map of another plan start over).
    pub fn follow(&mut self, world: &World) {
        if !self.ghosts.learned_with(&world.params) {
            self.ghosts = GhostMap::from_prior(&world.params);
        }
        self.ghosts.follow(&world.plan);
        self.background.follow(&world.plan);
        self.destinations.follow(&world.destinations);
    }

    /// The three in their saved formats (Tracker.learned).
    pub fn to_json(&self) -> Value {
        json!({"ghost_map": self.ghosts.to_json(), "ld_background": self.background.to_json(),
               "dest_map": self.destinations.map().to_json()})
    }
}

/// Why a saved part of what was learned is not taken (the files are input from outside).
#[derive(Debug, Clone, PartialEq)]
pub enum LearnedError {
    /// this field is missing or no number of the allowed range
    Field(&'static str),
    /// a ghost map of another grid (cell, range)
    Grid,
    /// this field of a sensor's part has another size or a value out of range
    Part { field: &'static str, sensor: String },
    /// packed numbers that cannot be read
    Packed(PackedFault),
    /// a destination map of another floor plan (its rooms, doors, regions)
    OtherPlan,
}

impl fmt::Display for LearnedError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Field(name) => write!(f, "{name} is missing or out of range"),
            Self::Grid => write!(f, "a ghost map of another grid"),
            Self::Part { field, sensor } => {
                write!(f, "{field} of sensor {sensor} has another size or a value out of range")
            }
            Self::Packed(fault) => write!(f, "the packed numbers: {fault}"),
            Self::OtherPlan => write!(f, "a destination map of another floor plan"),
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
