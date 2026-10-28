//! Lernen pausieren (pause.py, MODEL.md 10): the intervals in which nothing is learned. Input of the
//! core: the app switches them (Home Assistant), the tools know some beforehand; the tracking goes on.

use super::LearnedError;

/// [start, end); end None: still paused.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Pause {
    pub start: f64,
    pub end: Option<f64>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Pauses(Vec<Pause>);

impl Pauses {
    /// Err for a time that is no number or an interval that ends before it starts.
    pub fn new(pauses: Vec<Pause>) -> Result<Self, LearnedError> {
        let sound = |p: &Pause| p.start.is_finite() && p.end.is_none_or(|e| e.is_finite() && e >= p.start);
        if pauses.iter().all(sound) {
            Ok(Self(pauses))
        } else {
            Err(LearnedError::Field("pauses"))
        }
    }

    /// Whether any time of [t0, t1] is in a pause (Pauses.paused).
    pub fn paused(&self, t0: f64, t1: f64) -> bool {
        self.0.iter().any(|p| p.start <= t1 && p.end.is_none_or(|e| t0 < e))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_pause_holds_from_its_start_until_before_its_end() {
        let p = Pauses::new(vec![
            Pause {
                start: 10.0,
                end: Some(20.0),
            },
            Pause { start: 50.0, end: None },
        ])
        .expect("sound");
        assert!(!p.paused(0.0, 9.9) && p.paused(0.0, 10.0) && p.paused(19.9, 19.9));
        assert!(!p.paused(20.0, 20.0) && !p.paused(20.0, 49.0));
        assert!(p.paused(1e9, 1e9), "still paused");
        assert!(!Pauses::default().paused(0.0, 1e9));
        let wrong = Pause {
            start: 5.0,
            end: Some(4.0),
        };
        assert_eq!(Pauses::new(vec![wrong]), Err(LearnedError::Field("pauses")));
        assert!(
            Pauses::new(vec![Pause {
                start: f64::NAN,
                end: None
            }])
            .is_err()
        );
    }
}
