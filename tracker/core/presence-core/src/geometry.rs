//! Plane geometry of the plan (MODEL.md 2): walls as segments, moves mirrored at them; the outlines of
//! the zones.

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

    /// Per move p0 -> p1, does it cross a wall (world.World.crosses_wall, kernels.crosses_wall)? Only
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

    /// world.World.reflect (kernels.reflect): each move p0 -> p1 mirrored at the first wall it
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
        bounds: Option<[f64; 4]>,
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
        let bounds = (!corners.is_empty()).then(|| {
            corners.iter().fold(
                [f64::INFINITY, f64::INFINITY, f64::NEG_INFINITY, f64::NEG_INFINITY],
                |[x0, y0, x1, y1], &[x, y]| [x0.min(x), y0.min(y), x1.max(x), y1.max(y)],
            )
        });
        Self::Polygon { corners, bounds }
    }

    /// Whether the point is inside (geometry.Shape.contains without a margin; the outline grown or
    /// shrunk by one comes with the plan, phase 6). A circle by Rust's hypot (CPython's own may
    /// differ in the last bit: only a point on the outline could tell).
    pub fn contains(&self, [x, y]: Point) -> bool {
        match self {
            Self::Rect { lo, hi } => lo[0] <= x && x <= hi[0] && lo[1] <= y && y <= hi[1],
            Self::Circle { center, radius } => (x - center[0]).hypot(y - center[1]) <= *radius,
            Self::Polygon { corners, bounds } => {
                // far outside its box a point is not inside, whatever the edges say
                let far = 1e-6;
                if let Some([x0, y0, x1, y1]) = *bounds
                    && (x < x0 - far || x > x1 + far || y < y0 - far || y > y1 + far)
                {
                    return false;
                }
                in_polygon([x, y], corners)
            }
        }
    }
}

/// Even-odd rule (geometry.point_in_polygon): the edges crossed by a ray from the point to the right.
fn in_polygon([x, y]: Point, corners: &[Point]) -> bool {
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
        assert!(rect.contains([0.0, 1.0]) && rect.contains([2.0, 2.0]) && !rect.contains([2.0001, 2.0]));
        let circle = Shape::Circle {
            center: [1.0, 1.0],
            radius: 0.5,
        };
        assert!(circle.contains([1.5, 1.0]) && !circle.contains([1.4, 1.4]));
        // an L: the notch is outside
        let l = Shape::polygon(vec![
            [0.0, 0.0],
            [2.0, 0.0],
            [2.0, 1.0],
            [1.0, 1.0],
            [1.0, 2.0],
            [0.0, 2.0],
        ]);
        assert!(l.contains([0.5, 1.5]) && l.contains([1.5, 0.5]) && !l.contains([1.5, 1.5]));
        assert!(!l.contains([3.0, 0.5]) && !Shape::polygon(Vec::new()).contains([0.0, 0.0]));
    }
}
