//! What the app learns while it runs (MODEL.md 4.2, 4.3, 6 "Ziel"): where each sensor starts ghost
//! tracks, what each LD2410C sees without anybody, where walks go; nothing while learning is paused
//! (MODEL.md 10, "Lernen pausieren"). They learn at the places of the Python filter
//! (docs/rust-core.md 2.0); the tracker lends each step the learner it needs next to the filter.

pub mod destination;
pub mod ghost_map;
pub mod pauses;

use std::fmt;

use serde_json::{Value, json};

use crate::config::SensorConfig;
use crate::ld2410::background::Background;
use crate::world::World;
use destination::DestMap;
use ghost_map::GhostMap;

/// m: a sensor whose drawn place or height moved more than this hangs elsewhere (model.REMOUNT_SHIFT)
const REMOUNT_SHIFT: f64 = 0.5;
/// degrees: ... or whose heading turned more than this (model.REMOUNT_TURN)
const REMOUNT_TURN: f64 = 30.0;

/// How a sensor hangs and how its reports are read (ghostmap.pose_of): what the learned parts of a
/// sensor are kept in.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Pose {
    pub x: f64,
    pub y: f64,
    /// degrees from the +x axis, counterclockwise
    pub heading: f64,
    pub height: f64,
    pub mirror: bool,
    pub scale: f64,
}

impl Pose {
    pub fn of(s: &SensorConfig) -> Self {
        Self {
            x: s.x,
            y: s.y,
            heading: s.heading,
            height: s.height,
            mirror: s.mirror,
            scale: s.scale,
        }
    }

    /// Whether a sensor that hung at `old` hangs elsewhere now (model.remounted): moved or turned;
    /// scale and mirror are how its measurements are read, never a remount.
    pub fn remounted(&self, old: &Pose) -> bool {
        let shift = ((old.x - self.x).powi(2) + (old.y - self.y).powi(2) + (old.height - self.height).powi(2)).sqrt();
        let turn = ((old.heading - self.heading + 180.0).rem_euclid(360.0) - 180.0).abs();
        shift > REMOUNT_SHIFT || turn > REMOUNT_TURN
    }
}

/// The three learners, each lent alone to the step that learns (docs/rust-core.md 2.0).
#[derive(Debug, Clone, PartialEq)]
pub struct Learned {
    pub ghosts: GhostMap,
    pub background: Background,
    pub destinations: DestMap,
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
            destinations: DestMap::new(&world.destinations),
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
        if !self.destinations.fits(&world.destinations) {
            self.destinations = DestMap::new(&world.destinations);
        }
    }

    /// The three in their saved formats (Tracker.learned).
    pub fn to_json(&self) -> Value {
        json!({"ghost_map": self.ghosts.to_json(), "ld_background": self.background.to_json(),
               "dest_map": self.destinations.to_json()})
    }
}

/// Why a saved part of what was learned is not taken (the files are input from outside).
#[derive(Debug, Clone, PartialEq)]
pub enum LearnedError {
    /// not JSON, or not the expected kind of value at this key
    Json(String),
    /// this field is missing or no number of the allowed range
    Field(&'static str),
    /// a ghost map of another grid (cell, range)
    Grid,
    /// a part of this sensor does not have the expected size
    Size { field: &'static str, sensor: String },
    /// the destination map's packed counts cannot be read or have another shape
    Packed(&'static str),
    /// a destination map of another floor plan (its rooms, doors, regions)
    OtherPlan,
}

impl fmt::Display for LearnedError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Json(e) => write!(f, "not readable: {e}"),
            Self::Field(name) => write!(f, "{name} is missing or out of range"),
            Self::Grid => write!(f, "a ghost map of another grid"),
            Self::Size { field, sensor } => write!(f, "{field} of sensor {sensor} has another size"),
            Self::Packed(what) => write!(f, "the packed counts: {what}"),
            Self::OtherPlan => write!(f, "a destination map of another floor plan"),
        }
    }
}

impl std::error::Error for LearnedError {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_remount_is_a_move_or_a_turn_not_a_new_scale_or_mirror() {
        let p = Pose {
            x: 1.0,
            y: 2.0,
            heading: 350.0,
            height: 2.0,
            mirror: false,
            scale: 1.0,
        };
        let read_anew = Pose {
            mirror: true,
            scale: 1.1,
            heading: 15.0, // 25 degrees across north
            ..p
        };
        assert!(!read_anew.remounted(&p));
        assert!(Pose { heading: 25.0, ..p }.remounted(&p));
        assert!(Pose { x: 1.4, y: 2.4, ..p }.remounted(&p));
        assert!(
            !Pose {
                x: 1.3,
                height: 2.3,
                ..p
            }
            .remounted(&p)
        );
    }
}
