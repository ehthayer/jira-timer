"""jt-idle-monitor - Screen lock monitor for Jira Timer.

Runs as a launchd agent, checking every minute if the screen is locked.
If locked for more than IDLE_THRESHOLD minutes, auto-pauses the timer.
"""

import json
import os
import tempfile
import time
from pathlib import Path
from typing import NamedTuple, Optional

import Quartz
from loguru import logger

from jira_timer import cli as _cli

# Configuration
STATE_FILE = Path.home() / ".jira-timer.json"
IDLE_STATE_FILE = Path.home() / ".jira-timer-idle.json"
IDLE_THRESHOLD_MINUTES = 15
LOG_DIR = Path.home() / "Library" / "Logs" / "jira-timer"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "jt-idle-monitor.log"

# Configure loguru: remove default stderr sink, add rotating log file
logger.remove()
logger.add(
    LOG_FILE,
    rotation="1 MB",
    retention="7 days",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {message}",
)

def is_screen_locked():
    """Check if the macOS screen is locked via Quartz.

    pyobjc-framework-Quartz is a hard dependency of the uv tool install,
    so the previous pgrep ScreenSaverEngine fallback has been removed —
    it couldn't detect locks from hardened auto-lock policies that skip
    the screensaver, so its presence silently masked the lock.
    """
    session_dict = Quartz.CGSessionCopyCurrentDictionary()
    if session_dict:
        locked = bool(session_dict.get("CGSSessionScreenIsLocked", False))
        logger.debug("Quartz screen locked: {}", locked)
        return locked
    logger.debug("Quartz session dict is None, assuming unlocked")
    return False

def read_timer_state():
    """Read the main timer state via the cli helpers (atomic + coerced)."""
    # Align STATE_FILE override for tests that monkeypatch this module's STATE_FILE.
    _cli.STATE_FILE = STATE_FILE
    state = _cli.load_state()
    logger.debug(
        "Timer state: ticket={}, start_time={}, accumulated={}, paused={}",
        state.get("ticket"), state.get("start_time"),
        state.get("accumulated"), state.get("paused"),
    )
    return state


def write_timer_state(state):
    """Write the main timer state atomically with mode 0600."""
    _cli.STATE_FILE = STATE_FILE
    _cli.save_state(state)
    logger.debug(
        "Wrote timer state: paused={}, accumulated={}",
        state.get("paused"), state.get("accumulated"),
    )


def read_idle_state():
    """Read the idle monitor state."""
    if not IDLE_STATE_FILE.exists():
        logger.debug("Idle state file does not exist, using defaults")
        return {"locked_since": None, "paused_at": None}
    try:
        with open(IDLE_STATE_FILE) as f:
            state = json.load(f)
        logger.debug(
            "Idle state: locked_since={}, paused_at={}",
            state.get("locked_since"), state.get("paused_at"),
        )
        return state
    except (OSError, json.JSONDecodeError) as e:
        logger.error("Failed to read idle state: {}", e)
        return {"locked_since": None, "paused_at": None}


def write_idle_state(state):
    """Write idle state atomically with mode 0600 (temp file + os.replace)."""
    IDLE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".jira-timer-idle.", suffix=".tmp", dir=str(IDLE_STATE_FILE.parent)
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, IDLE_STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    logger.debug(
        "Wrote idle state: locked_since={}, paused_at={}",
        state.get("locked_since"), state.get("paused_at"),
    )

def send_notification(title, message):
    """Send a macOS notification."""
    logger.info("Notification: {} - {}", title, message)
    try:
        import subprocess
        # Try terminal-notifier first
        result = subprocess.run(
            ['terminal-notifier', '-title', title, '-message', message, '-sound', 'default'],
            capture_output=True
        )
        if result.returncode != 0:
            # Fallback to osascript. The script is built from a static
            # template and the user-controlled pieces (title, message) are
            # embedded via AppleScript string literals with proper escaping
            # so that embedded quotes / backslashes can't break out.
            def _as_escape(s: str) -> str:
                return s.replace("\\", "\\\\").replace('"', '\\"')
            script = (
                f'display notification "{_as_escape(message)}" '
                f'with title "{_as_escape(title)}"'
            )
            subprocess.run(['osascript', '-e', script])
    except Exception as e:
        logger.error("Failed to send notification: {}", e)

def format_duration(seconds):
    """Format seconds to human-readable duration."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


class Transition(NamedTuple):
    """Planned outcome of one idle-monitor tick.

    - timer_state: new timer state to persist, or None if unchanged.
    - idle_state:  new idle state to persist (always written if not None).
    - notifications: list of (title, message) tuples to surface to the user.
    """
    timer_state: Optional[dict]
    idle_state: Optional[dict]
    notifications: list


def compute_transition(
    now: int,
    locked: bool,
    timer_state: Optional[dict],
    idle_state: dict,
    threshold_seconds: int = IDLE_THRESHOLD_MINUTES * 60,
) -> Transition:
    """Pure state-machine for one idle-monitor tick.

    Inputs are snapshots; outputs describe what the caller should write and
    which notifications to send. No I/O, no clock reads, no side effects —
    suitable for exhaustive testing.
    """
    empty_idle = {"locked_since": None, "paused_at": None}

    # Case A: no active ticket — clear idle state, nothing else.
    if not timer_state or not timer_state.get("ticket"):
        return Transition(None, empty_idle, [])

    ticket = timer_state["ticket"]
    start_time = timer_state.get("start_time")

    # Case B: timer already paused/stopped.
    if not start_time:
        notifications = []
        if not locked and idle_state.get("paused_at"):
            accumulated = timer_state.get("accumulated", 0)
            notifications.append((
                "Jira Timer",
                f"Welcome back! {ticket} paused at {format_duration(accumulated)}",
            ))
        return Transition(None, empty_idle, notifications)

    # Case C: timer running + screen locked.
    if locked:
        if not idle_state.get("locked_since"):
            return Transition(
                None,
                {**idle_state, "locked_since": now},
                [],
            )

        locked_duration = now - idle_state["locked_since"]
        if locked_duration >= threshold_seconds and not idle_state.get("paused_at"):
            elapsed_at_lock = idle_state["locked_since"] - int(start_time)
            accumulated = timer_state.get("accumulated", 0)
            total_at_pause = accumulated + elapsed_at_lock
            new_timer = {
                **timer_state,
                "start_time": None,
                "accumulated": total_at_pause,
                "paused": True,
                "paused_reason": "idle",
            }
            new_idle = {**idle_state, "paused_at": now}
            notifications = [(
                "Jira Timer Paused",
                f"Timer paused at {format_duration(total_at_pause)} "
                f"(idle {threshold_seconds // 60}m)",
            )]
            return Transition(new_timer, new_idle, notifications)

        # Still locked, under threshold — no change.
        return Transition(None, None, [])

    # Case D: timer running + screen unlocked.
    if idle_state.get("locked_since"):
        notifications = []
        if idle_state.get("paused_at"):
            accumulated = timer_state.get("accumulated", 0)
            idle_minutes = (now - idle_state["locked_since"]) // 60
            notifications.append((
                "Jira Timer",
                f"Welcome back! {ticket} paused at {format_duration(accumulated)} "
                f"(was idle {idle_minutes}m)",
            ))
        return Transition(None, empty_idle, notifications)

    return Transition(None, None, [])


def main():
    now = int(time.time())
    locked = is_screen_locked()
    logger.info("Check: locked={}", locked)

    idle_state = read_idle_state()
    timer_state = read_timer_state()

    transition = compute_transition(now, locked, timer_state, idle_state)

    if transition.timer_state is not None:
        ticket = transition.timer_state.get("ticket")
        accumulated = transition.timer_state.get("accumulated")
        logger.info(
            "Auto-pausing {}: accumulated={}s ({})",
            ticket, accumulated, format_duration(accumulated),
        )
        write_timer_state(transition.timer_state)

    if transition.idle_state is not None:
        write_idle_state(transition.idle_state)

    for title, message in transition.notifications:
        send_notification(title, message)

if __name__ == '__main__':
    main()
