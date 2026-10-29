//! The fit: EM for the mixture weights (person or echo per track, right or wrong per pair), Gauss-Newton
//! for the poses; the cluster-robust covariance and the second mode of a heading.

use faer::linalg::matmul::matmul;
use faer::linalg::solvers::Solve;
use faer::{Accum, Mat, Par};

use super::problem::Problem;
use super::{BLOCK, C, MIN_RULER, OUTLIER_AREA, PRIOR_LNSCALE, PRIOR_MU, clip, median, py_max, search};
use crate::vmath::{logaddexp, pairwise_sum, py_sum};

/// Gauss-Newton steps at most, and when they have converged.
const ITERATIONS: usize = 60;
const TOL: f64 = 1e-6;
/// the step of the numerical derivatives
const DELTA: f64 = 1e-5;

/// Per time a weight of the data (the jackknife leaves stretches out).
pub(super) type Weights = dyn Fn(f64) -> f64;

/// Who the points and pairs belong to at a pose (EM's responsibilities): per sensor with points, per point
/// and per track P(person); per pair P(right).
struct Resp {
    plan: Vec<Vec<f64>>,
    track: Vec<Vec<f64>>,
    pair: Vec<Vec<f64>>,
}

/// Per sensor with points: the nearest free point of each and the unit normal there.
type Anchors = Vec<(Vec<C>, Vec<C>)>;

pub(super) struct Fit<'p, 'a> {
    p: &'p Problem<'a>,
    use_pairs: bool,
    use_plan: bool,
    /// (sensor, scale) held fixed
    hold: Vec<(usize, f64)>,
    mu: f64,
    tau: f64,
    /// the sensors measured against another one by at least MIN_RULER agreeing pairs
    pub(super) rulers: Vec<usize>,
    /// per sensor: its agreeing pairs (with all others)
    pub(super) agree: Vec<f64>,
    /// per sensor with points: the scale the floor plan is judged with (see run)
    kref: Vec<Option<f64>>,
    /// per point and per pair: the weight of its time
    wp: Vec<Vec<f64>>,
    wq: Vec<Vec<f64>>,
    eps_plan: Vec<f64>,
    eps_pair: Vec<f64>,
    pub(super) phi: Vec<f64>,
    pub(super) ll: f64,
    pub(super) cov: Mat<f64>,
    pub(super) cov_laplace: Mat<f64>,
}

/// Jᵀ J of a matrix.
fn gram(j: &Mat<f64>) -> Mat<f64> {
    let mut out = Mat::zeros(j.ncols(), j.ncols());
    matmul(out.as_mut(), Accum::Replace, j.transpose(), j.as_ref(), 1.0, Par::Seq);
    out
}

fn product(a: &Mat<f64>, b: &Mat<f64>) -> Mat<f64> {
    let mut out = Mat::zeros(a.nrows(), b.ncols());
    matmul(out.as_mut(), Accum::Replace, a.as_ref(), b.as_ref(), 1.0, Par::Seq);
    out
}

/// The inverse of a + 1e-9 I (np.linalg.inv: LU with partial pivoting against the identity).
fn inverse_damped(a: &Mat<f64>) -> Mat<f64> {
    let n = a.nrows();
    let damped = Mat::from_fn(n, n, |i, k| a[(i, k)] + if i == k { 1e-9 } else { 0.0 });
    damped.partial_piv_lu().solve(Mat::<f64>::identity(n, n))
}

impl<'p, 'a> Fit<'p, 'a> {
    pub(super) fn new(p: &'p Problem<'a>, use_pairs: bool, use_plan: bool, weights: Option<&Weights>) -> Self {
        let weigh = |t: &[f64]| t.iter().map(|&t| weights.map_or(1.0, |w| w(t))).collect::<Vec<f64>>();
        let n = p.config.sensors.len();
        Self {
            p,
            use_pairs,
            use_plan: use_plan && p.space.rooms,
            hold: Vec::new(),
            mu: PRIOR_MU,
            tau: PRIOR_LNSCALE,
            rulers: Vec::new(),
            agree: vec![0.0; n],
            kref: vec![None; p.points.len()],
            wp: p.points.iter().map(|pt| weigh(&pt.t)).collect(),
            wq: p.pairs.iter().map(|q| weigh(&q.t)).collect(),
            eps_plan: vec![0.3; p.points.len()],
            eps_pair: vec![0.3; p.pairs.len()],
            phi: Vec::new(),
            ll: f64::NAN,
            cov: Mat::zeros(0, 0),
            cov_laplace: Mat::zeros(0, 0),
        }
    }

    /// These sensors' scales held fixed (a profile).
    pub(super) fn holding(mut self, hold: Vec<(usize, f64)>) -> Self {
        self.hold = hold;
        self
    }

    /// The distance of a sensor's points to its free space at phi.
    fn plan_terms(&self, phi: &[f64], e: usize) -> (Vec<C>, Vec<(C, f64)>) {
        let pt = &self.p.points[e];
        let w = self.p.world(phi, pt.si, &pt.z, self.kref[e]);
        let field = self.p.space.fields[pt.si].as_ref();
        let near = w.iter().map(|&w| field.map_or((w, 0.0), |f| f.lookup(w))).collect();
        (w, near)
    }

    /// The log posterior (up to a constant).
    pub(super) fn loglik(&self, phi: &[f64]) -> f64 {
        self.loglik_resp(phi).0
    }

    /// The log posterior and the responsibilities.
    fn loglik_resp(&self, phi: &[f64]) -> (f64, Resp) {
        let mut total = 0.0;
        let mut resp = Resp {
            plan: vec![Vec::new(); self.p.points.len()],
            track: vec![Vec::new(); self.p.points.len()],
            pair: vec![Vec::new(); self.p.pairs.len()],
        };
        if self.use_plan {
            // per LD2450 track: its owner is a person (every point where it can be seen) or an echo
            // (anywhere in the view), MODEL.md 2 and 4.2
            for (e, pt) in self.p.points.iter().enumerate() {
                if pt.ntracks == 0 {
                    continue;
                }
                let Some(field) = self.p.space.fields[pt.si].as_ref() else {
                    continue;
                };
                let (_, near) = self.plan_terms(phi, e);
                let (eps, w) = (self.eps_plan[e], &self.wp[e]);
                let (area, view) = (field.area.ln(), -self.p.space.views[pt.si].ln());
                let mut person = vec![0.0; pt.ntracks];
                let mut echo = vec![0.0; pt.ntracks];
                for (k, &tr) in pt.track.iter().enumerate() {
                    let d = near[k].1 / pt.sigma[k];
                    person[tr] += w[k] * (-0.5 * (d * d) - area);
                    echo[tr] += w[k] * view;
                }
                let both: Vec<f64> = person
                    .iter_mut()
                    .zip(&mut echo)
                    .map(|(p, x)| {
                        *p += (1.0 - eps).ln();
                        *x += eps.ln();
                        logaddexp(*p, *x)
                    })
                    .collect();
                total += pairwise_sum(&both);
                let r: Vec<f64> = person.iter().zip(&both).map(|(p, b)| (p - b).exp()).collect();
                resp.plan[e] = pt.track.iter().map(|&tr| r[tr]).collect();
                resp.track[e] = r;
            }
        }
        if self.use_pairs {
            for (i, q) in self.p.pairs.iter().enumerate() {
                let (wa, wb) = (self.p.world(phi, q.a, &q.za, None), self.p.world(phi, q.b, &q.zb, None));
                let eps = self.eps_pair[i];
                let wrong = eps / OUTLIER_AREA;
                let person: Vec<f64> = (0..q.za.len())
                    .map(|k| {
                        let r = ((wa[k] - wb[k]) / q.sigma[k]).abs();
                        (1.0 - eps) * (-0.5 * (r * r)).exp() / (2.0 * std::f64::consts::PI * (q.sigma[k] * q.sigma[k]))
                    })
                    .collect();
                let terms: Vec<f64> = person
                    .iter()
                    .zip(&self.wq[i])
                    .map(|(p, w)| w * (p + wrong).ln())
                    .collect();
                total += pairwise_sum(&terms);
                resp.pair[i] = person.iter().map(|p| p / (p + wrong)).collect();
            }
        }
        let prior: Vec<f64> = self
            .p
            .prior_residuals(phi, self.mu, self.tau)
            .iter()
            .map(|r| r * r)
            .collect();
        total -= 0.5 * pairwise_sum(&prior);
        total -= 0.5
            * py_sum(
                self.hold
                    .iter()
                    .map(|&(si, k)| ((self.p.lnscale(phi, si) - k.ln()) / 1e-4).powi(2)),
            );
        (total, resp)
    }

    /// The residuals of Gauss-Newton at fixed responsibilities and anchors, and the time of each (NaN for
    /// the priors), for the robust covariance.
    fn residuals(&self, phi: &[f64], resp: &Resp, anchors: &Anchors) -> (Vec<f64>, Vec<f64>) {
        let mut r = self.p.prior_residuals(phi, self.mu, self.tau);
        r.extend(
            self.hold
                .iter()
                .map(|&(si, k)| (self.p.lnscale(phi, si) - k.ln()) / 1e-4),
        );
        let mut ts = vec![f64::NAN; r.len()];
        if self.use_plan {
            for (e, pt) in self.p.points.iter().enumerate() {
                if resp.plan[e].is_empty() {
                    continue; // no tracks
                }
                let w = self.p.world(phi, pt.si, &pt.z, self.kref[e]);
                let (b, n) = &anchors[e];
                // one-sided spring to the nearest free point, along the normal of that edge
                for k in 0..w.len() {
                    let x = w[k] - b[k];
                    let dist = x.re * n[k].re - x.im * -n[k].im;
                    let g = (resp.plan[e][k] * self.wp[e][k]).sqrt();
                    r.push(g * dist.max(0.0) / pt.sigma[k]);
                }
                ts.extend(&pt.t);
            }
        }
        if self.use_pairs {
            for (i, q) in self.p.pairs.iter().enumerate() {
                let (wa, wb) = (self.p.world(phi, q.a, &q.za, None), self.p.world(phi, q.b, &q.zb, None));
                let d: Vec<(f64, C)> = (0..q.za.len())
                    .map(|k| ((resp.pair[i][k] * self.wq[i][k]).sqrt(), (wa[k] - wb[k]) / q.sigma[k]))
                    .collect();
                r.extend(d.iter().map(|(g, x)| g * x.re));
                r.extend(d.iter().map(|(g, x)| g * x.im));
                ts.extend(&q.t);
                ts.extend(&q.t);
            }
        }
        (r, ts)
    }

    /// mu of the LD2450 scales from the sensors measured against another one (rulers: at least MIN_RULER
    /// agreeing pairs), their median: one sensor with wrong pairs (echoes that walk along with the people)
    /// doesn't move it. tau stays as measured: estimated from a handful of sensors, it grew with such a
    /// sensor and freed it (kitchen, 7.10.: scale 1.9-2.5).
    fn hyper(&mut self, phi: &[f64], resp: &Resp) {
        self.agree.fill(0.0);
        if self.use_pairs {
            for (i, q) in self.p.pairs.iter().enumerate() {
                let n: Vec<f64> = resp.pair[i].iter().zip(&self.wq[i]).map(|(r, w)| r * w).collect();
                let n = pairwise_sum(&n);
                self.agree[q.a] += n;
                self.agree[q.b] += n;
            }
        }
        self.rulers = self
            .p
            .points
            .iter()
            .map(|pt| pt.si)
            .filter(|&si| self.agree[si] >= MIN_RULER)
            .collect();
        let lk: Vec<f64> = self.rulers.iter().map(|&si| self.p.lnscale(phi, si)).collect();
        if lk.len() >= 2 {
            self.mu = median(&lk);
        }
    }

    fn anchors(&self, phi: &[f64]) -> Anchors {
        (0..self.p.points.len())
            .map(|e| {
                if !self.use_plan {
                    return (Vec::new(), Vec::new());
                }
                let (w, near) = self.plan_terms(phi, e);
                w.iter()
                    .zip(near)
                    .map(|(&w, (b, d))| {
                        let n = if d > 1e-9 { (w - b) / d.max(1e-9) } else { C::default() };
                        (b, n)
                    })
                    .unzip()
            })
            .collect()
    }

    /// The Jacobian of the residuals by central differences.
    fn jacobian(&self, phi: &[f64], resp: &Resp, anchors: &Anchors, rows: usize) -> Mat<f64> {
        let mut j = Mat::zeros(rows, phi.len());
        for c in 0..phi.len() {
            let (mut up, mut down) = (phi.to_vec(), phi.to_vec());
            up[c] += DELTA;
            down[c] -= DELTA;
            let (ru, _) = self.residuals(&up, resp, anchors);
            let (rd, _) = self.residuals(&down, resp, anchors);
            for (r, (u, d)) in ru.iter().zip(&rd).enumerate() {
                j[(r, c)] = (u - d) / (2.0 * DELTA);
            }
        }
        j
    }

    /// The scales the floor plan judges with: those of now (see run).
    fn set_kref(&mut self, phi: &[f64]) {
        self.kref = self
            .p
            .points
            .iter()
            .map(|pt| Some(self.p.pose(phi, pt.si, None).0.abs()))
            .collect();
    }

    /// Coarse start: per fitted sensor (most walking points first), the best heading on a 5 deg grid
    /// within +-90 deg of the drawn one, the others held. Gauss-Newton only finds the nearest mode, and the
    /// floor plan has several.
    fn search(&self, phi: &[f64]) -> Vec<f64> {
        let mut phi = phi.to_vec();
        let npts = |si: usize| self.p.points.iter().find(|pt| pt.si == si).map_or(0, |pt| pt.z.len());
        let mut order = self.p.fit.clone();
        let id = |si: usize| self.p.config.sensors[si].id.as_str();
        order.sort_by(|&a, &b| npts(b).cmp(&npts(a)).then(id(a).cmp(id(b))));
        for si in order {
            let Some(i) = self.p.slot(si) else { continue };
            let mut best: Option<(f64, f64)> = None;
            for dh in search() {
                let mut cand = phi.clone();
                cand[i] = dh;
                let ll = self.loglik(&cand);
                if best.is_none_or(|(b, _)| ll > b) {
                    best = Some((ll, dh));
                }
            }
            if let Some((_, dh)) = best {
                phi[i] = dh;
            }
        }
        phi
    }

    /// Fits from phi (None: the drawn poses), with a coarse search first.
    pub(super) fn run(&mut self, phi: Option<&[f64]>, search: bool) -> Vec<f64> {
        let mut phi = phi.map_or_else(|| self.p.start(), <[f64]>::to_vec);
        if search {
            phi = self.search(&phi);
        }
        let n = phi.len();
        let mut last = f64::NEG_INFINITY;
        let mut h = Mat::<f64>::identity(n, n);
        for _ in 0..ITERATIONS {
            // The floor plan judges heading and position at the scale of now, it doesn't set the scale:
            // rotation and shift keep areas, so "uniform on the visible space" is a proper density for
            // them; for the scale it is not. The scale comes from pairs and handovers and the scales of
            // the other LD2450s.
            self.set_kref(&phi);
            let (_, resp) = self.loglik_resp(&phi);
            self.hyper(&phi, &resp);
            self.m_step(&resp);
            let (ll, resp) = self.loglik_resp(&phi);
            // Gauss-Newton step with the anchors (nearest free point, normal) of now
            let anchors = self.anchors(&phi);
            let (r0, _) = self.residuals(&phi, &resp, &anchors);
            let j = self.jacobian(&phi, &resp, &anchors, r0.len());
            h = gram(&j);
            let r = Mat::from_fn(r0.len(), 1, |i, _| r0[i]);
            let g = product(&j.transpose().to_owned(), &r);
            let damped = Mat::from_fn(n, n, |i, k| h[(i, k)] + if i == k { 1e-9 } else { 0.0 });
            let x = damped.partial_piv_lu().solve(g);
            let step: Vec<f64> = (0..n).map(|i| -x[(i, 0)]).collect();
            // backtracking on the true log posterior
            let mut a = 1.0;
            let mut cand = None;
            while a > 1e-3 {
                let c: Vec<f64> = phi.iter().zip(&step).map(|(p, s)| p + a * s).collect();
                if self.loglik(&c) >= ll - 1e-9 {
                    cand = Some(c);
                    break;
                }
                a /= 2.0;
            }
            if let Some(c) = cand {
                phi = c;
            }
            let moved = step.iter().map(|s| (a * s).abs()).fold(f64::NEG_INFINITY, f64::max);
            if (ll - last).abs() < TOL * f64::max(1.0, ll.abs()) && moved < 1e-5 {
                break;
            }
            last = ll;
        }
        self.ll = self.loglik(&phi);
        self.cov_laplace = inverse_damped(&h);
        self.cov = self.robust_cov(&phi);
        self.phi.clone_from(&phi);
        phi
    }

    /// M step for the mixture weights.
    fn m_step(&mut self, resp: &Resp) {
        if self.use_plan {
            for (e, pt) in self.p.points.iter().enumerate() {
                if pt.ntracks == 0 || resp.track[e].is_empty() {
                    continue;
                }
                let (mut wt, mut count) = (vec![0.0; pt.ntracks], vec![0usize; pt.ntracks]);
                for (k, &tr) in pt.track.iter().enumerate() {
                    wt[tr] += self.wp[e][k];
                    count[tr] += 1;
                }
                for (w, &c) in wt.iter_mut().zip(&count) {
                    *w /= c.max(1) as f64;
                }
                let kept: Vec<f64> = resp.track[e].iter().zip(&wt).map(|(r, w)| r * w).collect();
                let share = pairwise_sum(&kept) / f64::max(pairwise_sum(&wt), 1e-9);
                self.eps_plan[e] = clip(1.0 - share, 0.01, 0.95);
            }
        }
        if self.use_pairs {
            for i in 0..self.p.pairs.len() {
                let kept: Vec<f64> = resp.pair[i].iter().zip(&self.wq[i]).map(|(r, w)| r * w).collect();
                let share = pairwise_sum(&kept) / f64::max(pairwise_sum(&self.wq[i]), 1e-9);
                self.eps_pair[i] = clip(1.0 - share, 0.01, 0.99);
            }
        }
    }

    /// Cluster-robust covariance (Liang & Zeger 1986): H^-1 (B + H_prior) H^-1 with B the sum over BLOCK-s
    /// blocks of the outer products of the blocks' scores. Equal to the Laplace covariance H^-1 for
    /// independent errors, wider where the points of a block share their error; a sensor without data
    /// keeps its prior.
    fn robust_cov(&mut self, phi: &[f64]) -> Mat<f64> {
        self.set_kref(phi);
        let (_, resp) = self.loglik_resp(phi);
        let anchors = self.anchors(phi);
        let (r0, ts) = self.residuals(phi, &resp, &anchors);
        let j = self.jacobian(phi, &resp, &anchors, r0.len());
        let n = phi.len();
        let h = gram(&j);
        let prior: Vec<usize> = (0..ts.len()).filter(|&i| ts[i].is_nan()).collect();
        let jp = Mat::from_fn(prior.len(), n, |i, c| j[(prior[i], c)]);
        let hp = gram(&jp);
        // the scores summed per block, the blocks in the order of their number
        let data: Vec<usize> = (0..ts.len()).filter(|&i| !ts[i].is_nan()).collect();
        let block: Vec<f64> = data.iter().map(|&i| (ts[i] / BLOCK).floor()).collect();
        let mut ids = block.clone();
        ids.sort_by(f64::total_cmp);
        ids.dedup();
        let mut g = Mat::<f64>::zeros(ids.len(), n);
        for (k, &i) in data.iter().enumerate() {
            let b = ids.partition_point(|&x| x < block[k]);
            for c in 0..n {
                g[(b, c)] += j[(i, c)] * r0[i];
            }
        }
        let mut middle = gram(&g);
        for i in 0..n {
            for c in 0..n {
                middle[(i, c)] += hp[(i, c)];
            }
        }
        let hi = inverse_damped(&h);
        product(&product(&hi, &middle), &hi)
    }

    /// Standard deviations of the heading (deg) and scale of a fitted sensor: the robust one and the
    /// Laplace one.
    pub(super) fn sd(&self, si: usize) -> Sd {
        let i = self.p.slot(si).unwrap_or_default();
        let k = self.p.pose(&self.phi, si, None).0.abs();
        let root = |x: f64| py_max(x, 0.0).sqrt();
        Sd {
            heading: root(self.cov[(i, i)]).to_degrees(),
            scale: k * root(self.cov[(i + 1, i + 1)]),
        }
    }

    /// The variance inflation of the robust covariance of a sensor's heading (at least 1).
    pub(super) fn inflation(&self, si: usize) -> f64 {
        let i = self.p.slot(si).unwrap_or_default();
        py_max(self.cov[(i, i)] / py_max(self.cov_laplace[(i, i)], 1e-12), 1.0)
    }

    /// The best other heading of this sensor (at least 15 deg away, the rest held) and how much worse it
    /// is, in log posterior divided by the variance inflation of the robust covariance (adjusted
    /// likelihood ratio, Chandler & Bate 2007): small = a second mode.
    pub(super) fn second_mode(&self, si: usize) -> (Option<f64>, f64) {
        let i = self.p.slot(si).unwrap_or_default();
        let mut best: Option<(f64, f64)> = None;
        for dh in search() {
            if (dh - self.phi[i]).abs() < 15.0_f64.to_radians() {
                continue;
            }
            let mut cand = self.phi.clone();
            cand[i] = dh;
            let ll = self.loglik(&cand);
            if best.is_none_or(|(b, _)| ll > b) {
                best = Some((ll, dh));
            }
        }
        let Some((ll, dh)) = best else {
            return (None, f64::INFINITY);
        };
        let heading = self.p.config.sensors[si].heading;
        (
            Some(super::py_mod(heading + dh.to_degrees(), 360.0)),
            (self.ll - ll) / self.inflation(si),
        )
    }

    /// The mean of the LD2450 scales' prior (ln) and its spread.
    pub(super) fn scale_prior(&self) -> (f64, f64) {
        (self.mu, self.tau)
    }
}

/// Standard deviations of a fitted sensor's heading (deg) and scale.
pub(super) struct Sd {
    pub(super) heading: f64,
    pub(super) scale: f64,
}
