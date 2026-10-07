//! Pure hold-to-talk gesture state machine. Time is passed in (milliseconds on
//! any monotonic clock) so every rule is deterministic to test.
//!
//! Idle -> Armed (key-down; mic opens now, nothing earlier exists)
//!      -> Recording (still held after [`ARMED_MS`]; the Armed audio is kept)
//!      -> Transcribing (release, or the stop tap in lock mode)
//!      -> Extracting -> Review | Saved | Error
//! A release inside the Armed interval is an accidental tap: nothing is created.
//! Two such taps within [`DOUBLE_TAP_MS`] lock recording on; one more key-down stops.
use serde::Serialize;

pub const ARMED_MS: u64 = 150;
pub const DOUBLE_TAP_MS: u64 = 400;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum State {
    Idle,
    Armed,
    Recording,
    Transcribing,
    Extracting,
    Review,
    Saved,
    Error,
}

/// What the controller must do with the microphone / work in flight.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    None,
    /// Open the microphone and start a fresh buffer.
    StartMic,
    /// Close the microphone and drop the buffer; no capture is created.
    Discard,
    /// Armed -> Recording (UI only).
    Promoted,
    /// Close the microphone, encode and upload exactly one capture.
    Finish,
    /// Abandon in-flight transcription/extraction.
    CancelWork,
}

#[derive(Debug)]
pub struct Machine {
    state: State,
    locked: bool,
    down: bool,
    since: u64,
    last_tap_up: Option<u64>,
    second_tap: bool,
}

impl Default for Machine {
    fn default() -> Self {
        Self {
            state: State::Idle,
            locked: false,
            down: false,
            since: 0,
            last_tap_up: None,
            second_tap: false,
        }
    }
}

impl Machine {
    pub fn state(&self) -> State {
        self.state
    }

    pub fn locked(&self) -> bool {
        self.locked
    }

    pub fn key_down(&mut self, now: u64) -> Action {
        if self.down {
            return Action::None; // key repeat
        }
        match self.state {
            State::Idle | State::Review | State::Saved | State::Error => {
                self.down = true;
                self.state = State::Armed;
                self.locked = false;
                self.since = now;
                self.second_tap = self
                    .last_tap_up
                    .is_some_and(|t| now.saturating_sub(t) <= DOUBLE_TAP_MS);
                Action::StartMic
            }
            State::Recording if self.locked => {
                self.down = true;
                self.locked = false;
                self.state = State::Transcribing;
                Action::Finish
            }
            // Armed/Recording while held, or busy transcribing: one capture at a time.
            _ => Action::None,
        }
    }

    pub fn key_up(&mut self, now: u64) -> Action {
        if !self.down {
            return Action::None; // duplicate or stray release
        }
        self.down = false;
        match self.state {
            State::Armed if now.saturating_sub(self.since) < ARMED_MS => {
                if self.second_tap {
                    self.second_tap = false;
                    self.last_tap_up = None;
                    self.locked = true;
                    self.state = State::Recording;
                    Action::Promoted
                } else {
                    self.last_tap_up = Some(now);
                    self.state = State::Idle;
                    Action::Discard
                }
            }
            // Armed past the interval but the tick has not run yet.
            State::Armed | State::Recording if !self.locked => {
                self.state = State::Transcribing;
                Action::Finish
            }
            _ => Action::None,
        }
    }

    pub fn tick(&mut self, now: u64) -> Action {
        if self.state == State::Armed && self.down && now.saturating_sub(self.since) >= ARMED_MS {
            self.state = State::Recording;
            self.second_tap = false;
            self.last_tap_up = None;
            return Action::Promoted;
        }
        Action::None
    }

    /// The buffer filled up: stop and send what was captured.
    pub fn force_finish(&mut self) -> Action {
        if self.state == State::Recording {
            self.locked = false;
            self.state = State::Transcribing;
            return Action::Finish;
        }
        Action::None
    }

    pub fn cancel(&mut self) -> Action {
        self.down = false;
        self.locked = false;
        self.second_tap = false;
        self.last_tap_up = None;
        let prev = std::mem::replace(&mut self.state, State::Idle);
        match prev {
            State::Armed | State::Recording => Action::Discard,
            State::Transcribing | State::Extracting => Action::CancelWork,
            _ => Action::None,
        }
    }

    /// Server-driven progress; only valid while work is in flight.
    pub fn progress(&mut self, to: State) -> bool {
        let ok = matches!(self.state, State::Transcribing | State::Extracting)
            && matches!(
                to,
                State::Transcribing | State::Extracting | State::Review | State::Saved
            );
        if ok {
            self.state = to;
        }
        ok
    }

    /// Any state can reach Error; the next key-down or cancel recovers to Idle.
    pub fn fail(&mut self) {
        self.down = false;
        self.locked = false;
        self.state = State::Error;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hold_then_release_makes_one_capture() {
        let mut m = Machine::default();
        assert_eq!(m.key_down(0), Action::StartMic);
        assert_eq!(m.state(), State::Armed);
        assert_eq!(m.tick(149), Action::None);
        assert_eq!(m.tick(150), Action::Promoted);
        assert_eq!(m.state(), State::Recording);
        assert_eq!(m.key_up(900), Action::Finish);
        assert_eq!(m.state(), State::Transcribing);
        assert_eq!(m.key_up(901), Action::None, "duplicate release");
    }

    #[test]
    fn short_tap_creates_nothing() {
        let mut m = Machine::default();
        m.key_down(1000);
        assert_eq!(m.key_up(1149), Action::Discard);
        assert_eq!(m.state(), State::Idle);
        assert_eq!(m.tick(2000), Action::None);
    }

    #[test]
    fn tap_boundary_is_exactly_the_armed_interval() {
        let mut m = Machine::default();
        m.key_down(0);
        assert_eq!(m.key_up(150), Action::Finish, "150ms held counts as a hold");
    }

    #[test]
    fn release_before_tick_still_finishes_a_real_hold() {
        let mut m = Machine::default();
        m.key_down(0);
        assert_eq!(m.key_up(400), Action::Finish);
    }

    #[test]
    fn double_tap_locks_and_next_press_stops() {
        let mut m = Machine::default();
        m.key_down(0);
        assert_eq!(m.key_up(80), Action::Discard);
        assert_eq!(m.key_down(300), Action::StartMic);
        assert_eq!(m.key_up(380), Action::Promoted);
        assert!(m.locked());
        assert_eq!(m.state(), State::Recording);
        assert_eq!(m.tick(5000), Action::None);
        assert_eq!(m.key_down(6000), Action::Finish);
        assert_eq!(m.key_up(6050), Action::None, "release of the stop tap");
        assert_eq!(m.state(), State::Transcribing);
        assert!(!m.locked());
    }

    #[test]
    fn slow_second_tap_does_not_lock() {
        let mut m = Machine::default();
        m.key_down(0);
        m.key_up(80);
        m.key_down(80 + DOUBLE_TAP_MS + 1);
        assert_eq!(m.key_up(80 + DOUBLE_TAP_MS + 50), Action::Discard);
        assert!(!m.locked());
    }

    #[test]
    fn second_press_held_is_a_normal_hold() {
        let mut m = Machine::default();
        m.key_down(0);
        m.key_up(50);
        m.key_down(200);
        assert_eq!(m.tick(350), Action::Promoted);
        assert!(!m.locked());
        assert_eq!(m.key_up(800), Action::Finish);
    }

    #[test]
    fn key_repeat_is_ignored() {
        let mut m = Machine::default();
        assert_eq!(m.key_down(0), Action::StartMic);
        assert_eq!(m.key_down(30), Action::None);
        assert_eq!(m.key_down(60), Action::None);
    }

    #[test]
    fn cancel_from_every_state_returns_to_idle() {
        let mut m = Machine::default();
        m.key_down(0);
        assert_eq!(m.cancel(), Action::Discard);
        assert_eq!(m.state(), State::Idle);
        assert_eq!(m.key_up(10), Action::None, "late release after cancel");

        m.key_down(1000);
        m.tick(1200);
        assert_eq!(m.cancel(), Action::Discard);

        // locked recording
        m.key_down(2000);
        m.key_up(2050);
        m.key_down(2100);
        assert_eq!(m.key_up(2150), Action::Promoted);
        assert_eq!(m.cancel(), Action::Discard);
        assert!(!m.locked());

        m.key_down(3000);
        m.tick(3200);
        m.key_up(3300);
        assert_eq!(m.cancel(), Action::CancelWork);
        assert_eq!(m.state(), State::Idle);
    }

    #[test]
    fn cancel_does_not_arm_a_double_tap() {
        let mut m = Machine::default();
        m.key_down(0);
        m.key_up(50); // tap
        m.cancel();
        m.key_down(100);
        assert_eq!(
            m.key_up(150),
            Action::Discard,
            "cancel cleared the tap memory"
        );
    }

    #[test]
    fn busy_states_ignore_new_presses() {
        let mut m = Machine::default();
        m.key_down(0);
        m.tick(200);
        m.key_up(300);
        assert_eq!(m.key_down(400), Action::None);
        assert!(m.progress(State::Extracting));
        assert_eq!(m.key_down(500), Action::None);
    }

    #[test]
    fn progress_only_while_work_is_in_flight() {
        let mut m = Machine::default();
        assert!(!m.progress(State::Review));
        m.key_down(0);
        assert!(!m.progress(State::Extracting), "armed is not in flight");
    }

    #[test]
    fn every_state_reaches_error_and_recovers() {
        let drive: [fn(&mut Machine); 8] = [
            |_| {},
            |m| {
                m.key_down(0);
            },
            |m| {
                m.key_down(0);
                m.tick(200);
            },
            |m| {
                m.key_down(0);
                m.tick(200);
                m.key_up(300);
            },
            |m| {
                m.key_down(0);
                m.tick(200);
                m.key_up(300);
                m.progress(State::Extracting);
            },
            |m| {
                m.key_down(0);
                m.tick(200);
                m.key_up(300);
                m.progress(State::Review);
            },
            |m| {
                m.key_down(0);
                m.tick(200);
                m.key_up(300);
                m.progress(State::Saved);
            },
            |m| m.fail(),
        ];
        for d in drive {
            let mut m = Machine::default();
            d(&mut m);
            m.fail();
            assert_eq!(m.state(), State::Error);
            assert_eq!(
                m.key_down(10_000),
                Action::StartMic,
                "recovers on next press"
            );
            assert_eq!(m.state(), State::Armed);
        }
        let mut m = Machine::default();
        m.fail();
        m.cancel();
        assert_eq!(m.state(), State::Idle);
    }

    #[test]
    fn force_finish_only_when_recording() {
        let mut m = Machine::default();
        assert_eq!(m.force_finish(), Action::None);
        m.key_down(0);
        m.tick(150);
        assert_eq!(m.force_finish(), Action::Finish);
        assert_eq!(m.state(), State::Transcribing);
    }
}
