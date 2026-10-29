//! The sensor geometry of a configuration for the shell's calibration and simulation (MODEL.md 2, 4.1):
//! the core's, so that both see what the filter sees. Over lists of points, since the calibration asks
//! for many at once.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

use crate::config::config_from;
use presence_core::config::{Config, Mount, SensorConfig};
use presence_core::geometry::{Point, line_of_sight, segments_cross};
use presence_core::plan::{Plan, ZoneIdx, wall_lines};

/// The sensors, walls and rooms of a configuration (its JSON), read-only.
#[pyclass(module = "presence_core", name = "Geometry", frozen)]
pub struct PyGeometry {
    config: Config,
    /// the walls that block the radar: real walls without their door openings
    walls: Vec<[Point; 2]>,
    /// the real walls with their doors
    lines: Vec<[Point; 2]>,
    /// the rooms in which the sensors may see people of the home (not closed, not outside)
    open: Vec<usize>,
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
        let plan = Plan::new(&config);
        let closed = |k: usize| plan.closed.contains(&ZoneIdx(k));
        let open = (0..config.zones.len())
            .filter(|&k| config.zones[k].kind.is_room() && !closed(k))
            .collect();
        Ok(Self {
            lines: wall_lines(&config),
            walls: plan.walls,
            open,
            config,
        })
    }

    /// The points in the sensor's frame on the floor (x right, y forward), as it measures them (its scale
    /// taken out).
    fn to_local(&self, sensor: &str, points: Vec<Point>) -> PyResult<Vec<Point>> {
        let s = self.sensor(sensor)?;
        Ok(points.iter().map(|&p| s.to_local(p)).collect())
    }

    /// Per point: within the sensor's field of view and range, grown by margin.
    #[pyo3(signature = (sensor, points, margin=0.0))]
    fn in_fov(&self, sensor: &str, points: Vec<Point>, margin: f64) -> PyResult<Vec<bool>> {
        let s = self.sensor(sensor)?;
        Ok(points.iter().map(|&p| s.in_fov(p, margin)).collect())
    }

    /// Where the sensor's sight lines start (a little in front of it).
    fn sight_origin(&self, sensor: &str) -> PyResult<Point> {
        Ok(self.sensor(sensor)?.sight_origin())
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

    /// Per point: in a room where the sensors may see people of the home, and in sight of origin.
    fn free(&self, origin: Point, points: Vec<Point>) -> Vec<bool> {
        let zones = &self.config.zones;
        points
            .iter()
            .map(|&p| self.open.iter().any(|&k| zones[k].contains(p, 0.0)) && line_of_sight(origin, p, &self.walls))
            .collect()
    }

    /// Per step p -> q: whether it crosses the line of a real wall, and whether it crosses a wall where
    /// it has no door.
    fn crossings(&self, p: Vec<Point>, q: Vec<Point>) -> (Vec<bool>, Vec<bool>) {
        let crosses =
            |segments: &[[Point; 2]], a: Point, b: Point| segments.iter().any(|&[s, e]| segments_cross(a, b, s, e));
        p.iter()
            .zip(&q)
            .map(|(&a, &b)| (crosses(&self.lines, a, b), crosses(&self.walls, a, b)))
            .unzip()
    }

    /// The sensor's raw targets (m) on the floor in its own frame, before its scale (Mount::on_floor; the
    /// calibration's), with its mirror or the given one.
    #[pyo3(signature = (sensor, points, mirror=None))]
    fn on_floor(&self, sensor: &str, points: Vec<Point>, mirror: Option<bool>) -> PyResult<Vec<Point>> {
        let s = self.sensor(sensor)?;
        let mount = Mount {
            mirror: mirror.unwrap_or(s.mirror),
            ..s.mount()
        };
        let target_height = self.config.params.target_height;
        Ok(points.iter().map(|&p| mount.on_floor(p, target_height)).collect())
    }
}
