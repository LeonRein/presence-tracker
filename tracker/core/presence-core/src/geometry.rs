//! Plane geometry of the plan (MODEL.md 2): walls as segments, moves mirrored at them; the outlines of
//! the zones.
//!
//! Numbers and their order follow the Python core of 0.38.1 (deleted in 0.39; its results are test fixtures).

use crate::vmath::py_hypot;

pub type Point = [f64; 2];

/// The walls of the plan as segments (x0, y0, x1, y1), each with its bounding box (world.World.walls,
/// wall_box).
#[derive(Debug, Default)]
pub struct Walls {
    segs: Vec<[f64; 4]>,
    boxes: Vec<[f64; 4]>,
}

impl Walls {
    pub fn new(segs: Vec<[f64; 4]>) -> Self {
        let boxes = segs
            .iter()
            .map(|&[x0, y0, x1, y1]| [x0.min(x1), y0.min(y1), x0.max(x1), y0.max(y1)])
            .collect();
        Self { segs, boxes }
    }

    pub fn is_empty(&self) -> bool {
        self.segs.is_empty()
    }

    /// Per move p0 -> p1, does it cross a wall? Only
    /// walls whose box meets the box of all moves are tried.
    pub fn crosses(&self, p0: &[Point], p1: &[Point]) -> Vec<bool> {
        let mut out = vec![false; p0.len()];
        if p0.is_empty() || self.segs.is_empty() {
            return out;
        }
        let (mut lo0, mut lo1, mut hi0, mut hi1) = (f64::INFINITY, f64::INFINITY, f64::NEG_INFINITY, f64::NEG_INFINITY);
        for (a, b) in p0.iter().zip(p1) {
            lo0 = lo0.min(a[0]).min(b[0]);
            hi0 = hi0.max(a[0]).max(b[0]);
            lo1 = lo1.min(a[1]).min(b[1]);
            hi1 = hi1.max(a[1]).max(b[1]);
        }
        for (&[ax, ay, bx, by], bx_) in self.segs.iter().zip(&self.boxes) {
            if !(bx_[0] <= hi0 && bx_[2] >= lo0 && bx_[1] <= hi1 && bx_[3] >= lo1) {
                continue;
            }
            for ((o, &[px, py]), &[qx, qy]) in out.iter_mut().zip(p0).zip(p1) {
                if *o {
                    continue;
                }
                let d1 = (bx - ax) * (py - ay) - (by - ay) * (px - ax);
                let d2 = (bx - ax) * (qy - ay) - (by - ay) * (qx - ax);
                let d3 = (qx - px) * (ay - py) - (qy - py) * (ax - px);
                let d4 = (qx - px) * (by - py) - (qy - py) * (bx - px);
                *o = d1 * d2 < 0.0 && d3 * d4 < 0.0;
            }
        }
        out
    }

    /// world.World.reflect: each move p0 -> p1 mirrored at the first wall it
    /// crosses, into `out`, with that wall's unit normal in `normal` ([0, 0]: none). Returns whether
    /// any move met a wall. Only walls whose box meets the box of all moves are tried.
    pub fn reflect(&self, p0: &[Point], p1: &[Point], out: &mut [Point], normal: &mut [Point]) -> bool {
        out.copy_from_slice(p1);
        normal.fill([0.0, 0.0]);
        if p1.is_empty() || self.segs.is_empty() {
            return false;
        }
        let (mut lo0, mut lo1, mut hi0, mut hi1) = (f64::INFINITY, f64::INFINITY, f64::NEG_INFINITY, f64::NEG_INFINITY);
        for (a, b) in p0.iter().zip(p1) {
            lo0 = lo0.min(a[0]).min(b[0]);
            hi0 = hi0.max(a[0]).max(b[0]);
            lo1 = lo1.min(a[1]).min(b[1]);
            hi1 = hi1.max(a[1]).max(b[1]);
        }
        let mut any = false;
        for (i, (a, b)) in p0.iter().zip(p1).enumerate() {
            let (dx, dy) = (b[0] - a[0], b[1] - a[1]);
            let mut first: Option<(f64, usize)> = None;
            for (w, (seg, bx)) in self.segs.iter().zip(&self.boxes).enumerate() {
                if !(bx[0] <= hi0 && bx[2] >= lo0 && bx[1] <= hi1 && bx[3] >= lo1) {
                    continue;
                }
                let (ex, ey) = (seg[2] - seg[0], seg[3] - seg[1]);
                let (wx, wy) = (seg[0] - a[0], seg[1] - a[1]);
                let den = dx * ey - dy * ex;
                if den == 0.0 {
                    continue;
                }
                let t = (wx * ey - wy * ex) / den;
                let u = (wx * dy - wy * dx) / den;
                if t > 0.0 && t < 1.0 && u > 0.0 && u < 1.0 && first.is_none_or(|(best, _)| t < best) {
                    first = Some((t, w));
                }
            }
            let Some((_, w)) = first else { continue };
            let seg = &self.segs[w];
            let (ex, ey) = (seg[2] - seg[0], seg[3] - seg[1]);
            let h = ex.hypot(ey);
            let (nx, ny) = (-ey / h, ex / h);
            let off = (b[0] - seg[0]) * nx + (b[1] - seg[1]) * ny;
            out[i] = [out[i][0] - 2.0 * off * nx, out[i][1] - 2.0 * off * ny];
            normal[i] = [nx, ny];
            any = true;
        }
        any
    }
}

/// The distance from a point to a segment a-b (by CPython's hypot, as the Python plan had it).
pub fn distance_to_segment([x, y]: Point, [ax, ay]: Point, [bx, by]: Point) -> f64 {
    let (dx, dy) = (bx - ax, by - ay);
    let length2 = dx * dx + dy * dy;
    let t = if length2 == 0.0 {
        0.0
    } else {
        (((x - ax) * dx + (y - ay) * dy) / length2).clamp(0.0, 1.0)
    };
    py_hypot(x - ax - t * dx, y - ay - t * dy)
}

/// Whether the open segments p1-p2 and q1-q2 cross; touching ends do not (geometry.segments_intersect).
pub fn segments_cross(p1: Point, p2: Point, q1: Point, q2: Point) -> bool {
    let orient = |a: Point, b: Point, c: Point| (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
    orient(q1, q2, p1) * orient(q1, q2, p2) < 0.0 && orient(p1, p2, q1) * orient(p1, p2, q2) < 0.0
}

/// Nothing of these segments between a and b.
pub fn line_of_sight(a: Point, b: Point, segments: &[[Point; 2]]) -> bool {
    !segments.iter().any(|&[s, e]| segments_cross(a, b, s, e))
}

/// A zone's outline (geometry.Shape): a rectangle by two corners, a circle, or a polygon.
#[derive(Debug, Clone, PartialEq)]
pub enum Shape {
    Rect {
        lo: Point,
        hi: Point,
    },
    Circle {
        center: Point,
        radius: f64,
    },
    /// with its bounding box (None without corners)
    Polygon {
        corners: Vec<Point>,
        bbox: Option<[f64; 4]>,
    },
}

impl Shape {
    /// A rectangle by any two opposite corners.
    pub fn rect(a: Point, b: Point) -> Self {
        Self::Rect {
            lo: [a[0].min(b[0]), a[1].min(b[1])],
            hi: [a[0].max(b[0]), a[1].max(b[1])],
        }
    }

    pub fn polygon(corners: Vec<Point>) -> Self {
        let bbox = (!corners.is_empty()).then(|| bounds_of(&corners));
        Self::Polygon { corners, bbox }
    }

    /// Whether the point is inside the outline grown by `margin` (shrunk if negative;
    /// geometry.Shape.contains).
    pub fn contains(&self, [x, y]: Point, margin: f64) -> bool {
        match self {
            Self::Rect { lo, hi } => {
                lo[0] - margin <= x && x <= hi[0] + margin && lo[1] - margin <= y && y <= hi[1] + margin
            }
            Self::Circle { center, radius } => py_hypot(x - center[0], y - center[1]) <= radius + margin,
            Self::Polygon { corners, bbox } => {
                // far outside its box a point is not inside, whatever the edges say
                let far = margin.max(0.0) + 1e-6;
                if let Some([x0, y0, x1, y1]) = *bbox
                    && (x < x0 - far || x > x1 + far || y < y0 - far || y > y1 + far)
                {
                    return false;
                }
                let inside = in_polygon([x, y], corners);
                if margin == 0.0 {
                    return inside;
                }
                let edge = self.edge_distance([x, y]);
                if inside {
                    margin > 0.0 || edge >= -margin
                } else {
                    edge <= margin
                }
            }
        }
    }

    /// The distance to the nearest edge of the outline (of a polygon its corners, Shape.edge_distance).
    fn edge_distance(&self, p: Point) -> f64 {
        let ring = self.outline(32);
        let edges = ring.iter().zip(ring.iter().cycle().skip(1));
        edges
            .map(|(&a, &b)| distance_to_segment(p, a, b))
            .fold(f64::INFINITY, f64::min)
    }

    /// The outline as corners: a circle as `segments` of them (Shape.outline).
    pub fn outline(&self, segments: usize) -> Vec<Point> {
        match self {
            Self::Rect { lo, hi } => vec![*lo, [hi[0], lo[1]], *hi, [lo[0], hi[1]]],
            Self::Circle { center, radius } => (0..segments)
                .map(|i| {
                    let a = 2.0 * std::f64::consts::PI * i as f64 / segments as f64;
                    [center[0] + radius * a.cos(), center[1] + radius * a.sin()]
                })
                .collect(),
            Self::Polygon { corners, .. } => corners.clone(),
        }
    }

    /// (x0, y0, x1, y1) of the outline (Shape.bounds: a circle by its 32 corners).
    pub fn bounds(&self) -> [f64; 4] {
        bounds_of(&self.outline(32))
    }

    /// m^2 inside the outline (a polygon by the shoelace formula, either orientation).
    pub fn area(&self) -> f64 {
        match self {
            Self::Rect { lo, hi } => (hi[0] - lo[0]) * (hi[1] - lo[1]),
            Self::Circle { radius, .. } => std::f64::consts::PI * radius * radius,
            Self::Polygon { corners, .. } => {
                let ring = corners.iter().zip(corners.iter().cycle().skip(1));
                ring.map(|(a, b)| a[0] * b[1] - b[0] * a[1]).sum::<f64>().abs() / 2.0
            }
        }
    }
}

fn bounds_of(corners: &[Point]) -> [f64; 4] {
    corners.iter().fold(
        [f64::INFINITY, f64::INFINITY, f64::NEG_INFINITY, f64::NEG_INFINITY],
        |[x0, y0, x1, y1], &[x, y]| [x0.min(x), y0.min(y), x1.max(x), y1.max(y)],
    )
}

/// Even-odd rule (geometry.point_in_polygon): the edges crossed by a ray from the point to the right.
pub(crate) fn in_polygon([x, y]: Point, corners: &[Point]) -> bool {
    let mut inside = false;
    let mut j = corners.len().wrapping_sub(1);
    for (i, &[xi, yi]) in corners.iter().enumerate() {
        let [xj, yj] = corners[j];
        if (yi > y) != (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi {
            inside = !inside;
        }
        j = i;
    }
    inside
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_move_across_a_wall_is_mirrored() {
        let walls = Walls::new(vec![[1.0, -1.0, 1.0, 1.0]]);
        let (mut out, mut normal) = ([[0.0; 2]; 2], [[0.0; 2]; 2]);
        let hit = walls.reflect(
            &[[0.0, 0.0], [0.0, 0.5]],
            &[[1.5, 0.0], [0.5, 0.5]],
            &mut out,
            &mut normal,
        );
        assert!(hit);
        assert!((out[0][0] - 0.5).abs() < 1e-15 && out[0][1] == 0.0);
        assert_eq!(normal[0], [-1.0, 0.0]);
        assert_eq!(out[1], [0.5, 0.5]);
        assert_eq!(normal[1], [0.0, 0.0]);
        assert_eq!(
            walls.crosses(&[[0.0, 0.0], [0.0, 0.5]], &[[1.5, 0.0], [0.5, 0.5]]),
            [true, false]
        );
    }

    #[test]
    fn a_point_is_inside_an_outline_as_the_python_plan_says() {
        let rect = Shape::rect([2.0, 3.0], [0.0, 1.0]);
        assert!(rect.contains([0.0, 1.0], 0.0) && rect.contains([2.0, 2.0], 0.0) && !rect.contains([2.0001, 2.0], 0.0));
        let circle = Shape::Circle {
            center: [1.0, 1.0],
            radius: 0.5,
        };
        assert!(circle.contains([1.5, 1.0], 0.0) && !circle.contains([1.4, 1.4], 0.0));
        // an L: the notch is outside
        let l = Shape::polygon(vec![
            [0.0, 0.0],
            [2.0, 0.0],
            [2.0, 1.0],
            [1.0, 1.0],
            [1.0, 2.0],
            [0.0, 2.0],
        ]);
        assert!(l.contains([0.5, 1.5], 0.0) && l.contains([1.5, 0.5], 0.0) && !l.contains([1.5, 1.5], 0.0));
        assert!(!l.contains([3.0, 0.5], 0.0) && !Shape::polygon(Vec::new()).contains([0.0, 0.0], 0.0));
        // grown and shrunk: the notch's corner (1, 1) is 0.5 m from (1.5, 1.5)
        assert!(l.contains([1.5, 1.5], 0.51) && !l.contains([1.5, 1.5], 0.49));
        assert!(l.contains([0.5, 0.5], -0.49) && !l.contains([0.5, 0.5], -0.51));
        assert!(rect.contains([2.2, 2.0], 0.3) && !rect.contains([1.8, 2.0], -0.3));
        assert_eq!(circle.bounds().map(|v| (v * 1e9).round() / 1e9), [0.5, 0.5, 1.5, 1.5]);
    }
}
