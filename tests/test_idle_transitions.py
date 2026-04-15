"""Exhaustive tests for the idle-monitor state machine.

The function under test is pure: given (now, locked, timer_state, idle_state),
it returns a Transition describing what to persist and which notifications to
send. No I/O, no Quartz, no clocks.
"""

from jira_timer.idle_monitor import IDLE_THRESHOLD_MINUTES, compute_transition

THRESHOLD = IDLE_THRESHOLD_MINUTES * 60
EMPTY_IDLE = {"locked_since": None, "paused_at": None}
NOW = 1_700_000_000


def timer(ticket="ENG-1", start_time=None, accumulated=0, paused=False, reason=None):
    return {
        "ticket": ticket,
        "start_time": start_time,
        "accumulated": accumulated,
        "paused": paused,
        "paused_reason": reason,
    }


# ---------- Case A: no active ticket ----------

class TestNoActiveTicket:
    def test_no_state_clears_idle(self):
        t = compute_transition(NOW, locked=False, timer_state=None, idle_state={"locked_since": 100})
        assert t.timer_state is None
        assert t.idle_state == EMPTY_IDLE
        assert t.notifications == []

    def test_empty_ticket_clears_idle(self):
        t = compute_transition(NOW, locked=True, timer_state=timer(ticket=None), idle_state={"locked_since": 100})
        assert t.timer_state is None
        assert t.idle_state == EMPTY_IDLE
        assert t.notifications == []


# ---------- Case B: timer already paused (start_time is None) ----------

class TestTimerAlreadyPaused:
    def test_unlock_after_idle_pause_sends_welcome_back(self):
        t = compute_transition(
            NOW, locked=False,
            timer_state=timer(start_time=None, accumulated=3600, paused=True, reason="idle"),
            idle_state={"locked_since": NOW - 2000, "paused_at": NOW - 1000},
        )
        assert t.timer_state is None
        assert t.idle_state == EMPTY_IDLE
        assert len(t.notifications) == 1
        assert t.notifications[0][0] == "Jira Timer"
        assert "Welcome back" in t.notifications[0][1]
        assert "ENG-1" in t.notifications[0][1]

    def test_paused_but_still_locked_no_notification(self):
        t = compute_transition(
            NOW, locked=True,
            timer_state=timer(start_time=None, accumulated=3600, paused=True),
            idle_state={"locked_since": NOW - 2000, "paused_at": NOW - 1000},
        )
        assert t.notifications == []
        assert t.idle_state == EMPTY_IDLE

    def test_paused_manually_unlock_no_welcome_back(self):
        """If paused_at is not set (manual stop, not idle), no welcome-back."""
        t = compute_transition(
            NOW, locked=False,
            timer_state=timer(start_time=None, accumulated=3600, paused=True, reason=None),
            idle_state=EMPTY_IDLE,
        )
        assert t.notifications == []


# ---------- Case C: timer running + screen locked ----------

class TestLockedRunning:
    def test_just_locked_records_locked_since(self):
        t = compute_transition(
            NOW, locked=True,
            timer_state=timer(start_time=NOW - 600, accumulated=0),
            idle_state=EMPTY_IDLE,
        )
        assert t.timer_state is None
        assert t.idle_state == {"locked_since": NOW, "paused_at": None}
        assert t.notifications == []

    def test_locked_below_threshold_no_change(self):
        locked_since = NOW - (THRESHOLD - 60)  # 1m under threshold
        t = compute_transition(
            NOW, locked=True,
            timer_state=timer(start_time=NOW - 1200, accumulated=0),
            idle_state={"locked_since": locked_since, "paused_at": None},
        )
        assert t.timer_state is None
        assert t.idle_state is None
        assert t.notifications == []

    def test_locked_over_threshold_triggers_auto_pause(self):
        start_time = NOW - 1800
        locked_since = NOW - THRESHOLD - 60  # 1m over threshold
        t = compute_transition(
            NOW, locked=True,
            timer_state=timer(start_time=start_time, accumulated=100),
            idle_state={"locked_since": locked_since, "paused_at": None},
        )
        assert t.timer_state is not None
        assert t.timer_state["start_time"] is None
        assert t.timer_state["paused"] is True
        assert t.timer_state["paused_reason"] == "idle"
        # total = accumulated + (locked_since - start_time)
        assert t.timer_state["accumulated"] == 100 + (locked_since - start_time)
        assert t.idle_state == {"locked_since": locked_since, "paused_at": NOW}
        assert len(t.notifications) == 1
        assert t.notifications[0][0] == "Jira Timer Paused"

    def test_locked_over_threshold_already_paused_no_duplicate(self):
        """If paused_at is already set, don't re-pause."""
        locked_since = NOW - THRESHOLD - 600
        t = compute_transition(
            NOW, locked=True,
            timer_state=timer(start_time=NOW - 3600, accumulated=0),
            idle_state={"locked_since": locked_since, "paused_at": NOW - 300},
        )
        assert t.timer_state is None
        assert t.notifications == []

    def test_pause_uses_lock_time_not_unlock_time(self):
        """Critical property: time banked is elapsed AT lock, not AT pause check.
        Otherwise every tick past threshold would inflate the banked time."""
        start_time = NOW - 2000
        locked_since = NOW - 1500  # locked 500s after start
        t = compute_transition(
            NOW, locked=True,
            timer_state=timer(start_time=start_time, accumulated=0),
            idle_state={"locked_since": locked_since, "paused_at": None},
        )
        assert t.timer_state["accumulated"] == 500


# ---------- Case D: timer running + screen unlocked ----------

class TestUnlockedRunning:
    def test_unlock_after_idle_pause_welcome_back(self):
        t = compute_transition(
            NOW, locked=False,
            timer_state=timer(start_time=NOW - 7200, accumulated=0),
            idle_state={"locked_since": NOW - 1800, "paused_at": NOW - 1000},
        )
        assert t.idle_state == EMPTY_IDLE
        assert len(t.notifications) == 1
        assert "Welcome back" in t.notifications[0][1]
        assert "was idle" in t.notifications[0][1]

    def test_unlock_before_threshold_clears_without_notification(self):
        t = compute_transition(
            NOW, locked=False,
            timer_state=timer(start_time=NOW - 600, accumulated=0),
            idle_state={"locked_since": NOW - 120, "paused_at": None},
        )
        assert t.idle_state == EMPTY_IDLE
        assert t.notifications == []

    def test_unlock_with_no_prior_lock_is_noop(self):
        t = compute_transition(
            NOW, locked=False,
            timer_state=timer(start_time=NOW - 600, accumulated=0),
            idle_state=EMPTY_IDLE,
        )
        assert t.timer_state is None
        assert t.idle_state is None
        assert t.notifications == []


# ---------- Threshold boundary ----------

class TestThreshold:
    def test_exactly_at_threshold_triggers_pause(self):
        locked_since = NOW - THRESHOLD
        t = compute_transition(
            NOW, locked=True,
            timer_state=timer(start_time=NOW - 3600, accumulated=0),
            idle_state={"locked_since": locked_since, "paused_at": None},
        )
        assert t.timer_state is not None

    def test_one_second_under_threshold_does_not_pause(self):
        locked_since = NOW - (THRESHOLD - 1)
        t = compute_transition(
            NOW, locked=True,
            timer_state=timer(start_time=NOW - 3600, accumulated=0),
            idle_state={"locked_since": locked_since, "paused_at": None},
        )
        assert t.timer_state is None

    def test_custom_threshold_respected(self):
        t = compute_transition(
            NOW, locked=True,
            timer_state=timer(start_time=NOW - 600, accumulated=0),
            idle_state={"locked_since": NOW - 120, "paused_at": None},
            threshold_seconds=60,
        )
        assert t.timer_state is not None
