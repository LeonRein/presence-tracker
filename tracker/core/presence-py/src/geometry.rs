//! The sensor geometry of a configuration for the simulation and the tests (MODEL.md 2, 4.1): the core's, so
//! that they see what the filter sees. Over lists of points.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::config::config_from;
use presence_core::config::{Config, SensorConfig};
use presence_core::geometry::{Point, line_of_sight};
use presence_core::plan::Plan;

/// The sensors and walls of a configuration (its JSON), read-only.
#[pyclass(module = "presence_core", name = "Geometry", frozen)]
pub struct PyGeometry {
    config: Config,
    /// the walls that block the radar: real walls without their door openings
    walls: Vec<[Point; 2]>,
}

impl PyGeometry {
    fn sensor(&self, id: &str) -> PyResult<&SensorConfig> {
        self.config
            .sensors
            .iter()
            .find(|s| s.id == id)
            .ok_or_else(|| PyValueError::new_err(format!("no sensor {id}")))
    }
}

// PyO3 extracts the arguments of a method as owned values
#[allow(clippy::needless_pass_by_value)]
#[pymethods]
impl PyGeometry {
    #[new]
    fn new(config: &str) -> PyResult<Self> {
        let config = config_from(config)?;
        Ok(Self {
            walls: Plan::new(&config).walls,
            config,
        })
    }

    /// The points in the sensor's frame on the floor (x right, y forward), as it measures them (its scale
    /// taken out).
    fn to_local(&self, sensor: &str, points: Vec<Point>) -> PyResult<Vec<Point>> {
        let s = self.sensor(sensor)?;
        Ok(points.iter().map(|&p| s.to_local(p)).collect())
    }

    /// Per point: in the sensor's field of view and no wall between.
    #[pyo3(signature = (sensor, points, margin=0.0))]
    fn sees(&self, sensor: &str, points: Vec<Point>, margin: f64) -> PyResult<Vec<bool>> {
        let s = self.sensor(sensor)?;
        Ok(points.iter().map(|&p| s.sees(p, &self.walls, margin)).collect())
    }

    /// Per point: no wall between it and origin (door openings are open).
    fn in_sight(&self, origin: Point, points: Vec<Point>) -> Vec<bool> {
        points.iter().map(|&p| line_of_sight(origin, p, &self.walls)).collect()
    }
}
