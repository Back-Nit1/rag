//! HUD window policy: when the floating `hud` window is shown or hidden, and
//! what the webview may ask of the main window. The decision logic is pure
//! (time is passed in) so it is tested without a window.
use crate::machine::State;
use std::time::Duration;

/// Window label; must match `capabilities/hud.json` and `apps/web` (`#/hud`).
pub const LABEL: &str = "hud";
/// Route inside the web build. Fragment routing works with `WebviewUrl::App`
/// (the path is `Url::join`ed, which keeps the fragment).
pub const ROUTE: &str = "index.html#/hud";
pub const WIDTH: f64 = 380.0;
pub const HEIGHT: f64 = 190.0;
/// How long Review / Saved / Error stay visible before the HUD hides itself.
/// Long enough to read an error or notice "ready to review"; the durable
/// record lives in the main window, so a short dwell loses nothing.
pub const DWELL: Duration = Duration::from_secs(6);
/// Event the main window listens to; payload is [`OpenReview`].
pub const OPEN_REVIEW_EVENT: &str = "hud:open-review";

/// Minimal payload: the capture to focus, if the HUD knows one.
#[derive(Clone, Debug, serde::Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct OpenReview {
    pub capture_id: Option<String>,
}

/// Entity ids are 3..=128 chars (`EntityId` in packages/contracts). We also
/// restrict the alphabet: the value is only ever forwarded to our own main
/// window, and a narrow charset keeps it inert there.
pub fn valid_entity_id(s: &str) -> bool {
    (3..=128).contains(&s.len())
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Visibility {
    Show,
    Hide,
}

fn terminal(s: State) -> bool {
    matches!(s, State::Review | State::Saved | State::Error)
}

/// Tracks visibility and the dwell timer. `now` is any monotonic millisecond clock.
#[derive(Debug, Default)]
pub struct Plan {
    visible: bool,
    /// Terminal state currently on screen (or already dismissed) and when it appeared.
    terminal: Option<(State, u64)>,
}

impl Plan {
    /// A new snapshot arrived. Returns the visibility change to apply, if any.
    pub fn on_state(&mut self, state: State, now: u64) -> Option<Visibility> {
        match state {
            State::Idle => {
                self.terminal = None;
                self.set(false)
            }
            State::Armed | State::Recording | State::Transcribing | State::Extracting => {
                // Never auto-hide while capturing or processing.
                self.terminal = None;
                self.set(true)
            }
            _ => match self.terminal {
                // Same terminal state again (or already dismissed): do not re-show.
                Some((s, _)) if s == state => None,
                _ => {
                    self.terminal = Some((state, now));
                    self.set(true)
                }
            },
        }
    }

    /// Dwell check; call once the dwell may have elapsed.
    pub fn on_tick(&mut self, now: u64) -> Option<Visibility> {
        match self.terminal {
            Some((s, since))
                if terminal(s) && now.saturating_sub(since) >= DWELL.as_millis() as u64 =>
            {
                self.set(false)
            }
            _ => None,
        }
    }

    fn set(&mut self, visible: bool) -> Option<Visibility> {
        if self.visible == visible {
            return None;
        }
        self.visible = visible;
        Some(if visible {
            Visibility::Show
        } else {
            Visibility::Hide
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const D: u64 = 6_000;

    #[test]
    fn dwell_is_what_the_tests_assume() {
        assert_eq!(DWELL.as_millis() as u64, D);
    }

    #[test]
    fn idle_stays_hidden() {
        let mut p = Plan::default();
        assert_eq!(p.on_state(State::Idle, 0), None);
        assert_eq!(p.on_tick(10 * D), None);
    }

    #[test]
    fn shows_when_leaving_idle_and_never_hides_while_busy() {
        let mut p = Plan::default();
        assert_eq!(p.on_state(State::Armed, 0), Some(Visibility::Show));
        for (i, s) in [
            State::Recording,
            State::Recording,
            State::Transcribing,
            State::Extracting,
        ]
        .into_iter()
        .enumerate()
        {
            assert_eq!(p.on_state(s, i as u64), None);
        }
        assert_eq!(p.on_tick(100 * D), None, "busy states never time out");
    }

    #[test]
    fn terminal_states_hide_after_the_dwell_only() {
        for s in [State::Review, State::Saved, State::Error] {
            let mut p = Plan::default();
            p.on_state(State::Extracting, 0);
            assert_eq!(p.on_state(s, 1_000), None, "already visible");
            assert_eq!(p.on_tick(1_000 + D - 1), None);
            assert_eq!(p.on_tick(1_000 + D), Some(Visibility::Hide));
            assert_eq!(p.on_tick(1_000 + 2 * D), None, "hide once");
        }
    }

    #[test]
    fn terminal_straight_from_idle_shows_then_hides() {
        let mut p = Plan::default();
        assert_eq!(p.on_state(State::Error, 50), Some(Visibility::Show));
        assert_eq!(p.on_tick(50 + D), Some(Visibility::Hide));
    }

    #[test]
    fn repeated_terminal_snapshot_neither_reshows_nor_extends() {
        let mut p = Plan::default();
        p.on_state(State::Review, 0);
        assert_eq!(p.on_state(State::Review, D - 1), None);
        assert_eq!(p.on_tick(D), Some(Visibility::Hide));
        assert_eq!(
            p.on_state(State::Review, D + 1),
            None,
            "dismissed stays dismissed"
        );
    }

    #[test]
    fn new_capture_after_dismissal_shows_again_and_old_timer_cannot_hide_it() {
        let mut p = Plan::default();
        p.on_state(State::Review, 0);
        assert_eq!(p.on_tick(D), Some(Visibility::Hide));
        assert_eq!(p.on_state(State::Armed, D + 10), Some(Visibility::Show));
        assert_eq!(p.on_state(State::Recording, D + 20), None);
        assert_eq!(p.on_tick(D + 30), None);
        assert_eq!(p.on_tick(50 * D), None, "recording never hides");
    }

    #[test]
    fn stale_timer_does_not_cut_a_newer_terminal_dwell_short() {
        let mut p = Plan::default();
        p.on_state(State::Error, 0);
        p.on_state(State::Armed, 1_000);
        p.on_state(State::Review, 3_000);
        // timer thread of the first Error fires at D: Review is only 3s old.
        assert_eq!(p.on_tick(D), None);
        assert_eq!(p.on_tick(3_000 + D), Some(Visibility::Hide));
    }

    #[test]
    fn cancel_to_idle_hides_immediately() {
        let mut p = Plan::default();
        p.on_state(State::Transcribing, 0);
        assert_eq!(p.on_state(State::Idle, 5), Some(Visibility::Hide));
    }

    #[test]
    fn entity_id_validation() {
        assert!(valid_entity_id("cap_0123abcd"));
        assert!(valid_entity_id("abc"));
        assert!(!valid_entity_id("ab"));
        assert!(!valid_entity_id(&"a".repeat(129)));
        assert!(valid_entity_id(&"a".repeat(128)));
        for bad in [
            "a b c", "cap/../x", "cap_1;x", "cap_é", "cap\n1", "<b>x</b>",
        ] {
            assert!(!valid_entity_id(bad), "{bad}");
        }
    }

    #[test]
    fn open_review_payload_is_camel_case_and_minimal() {
        let j = serde_json::to_value(OpenReview {
            capture_id: Some("cap_1".into()),
        })
        .unwrap();
        assert_eq!(j, serde_json::json!({"captureId": "cap_1"}));
    }
}
