# Architecture

## Components

```
┌─────────────────────────────────────────────────────────────────┐
│                         User Terminal                            │
├─────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐    ┌──────────────────┐    ┌───────────────┐ │
│  │  jt (CLI)    │    │  Oh My Zsh       │    │  launchd      │ │
│  │  Python      │    │  Plugin          │    │  Agent        │ │
│  └──────┬───────┘    └────────┬─────────┘    └───────┬───────┘ │
│         │                     │                      │          │
│         ▼                     ▼                      ▼          │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │              ~/.jira-timer.json (State File)                ││
│  └─────────────────────────────────────────────────────────────┘│
│         │                                            │          │
│         ▼                                            ▼          │
│  ┌──────────────┐                  ┌──────────────────────────┐ │
│  │  jira-cli    │ ─▶ Jira Cloud    │ ~/.jira-timer-idle.json  │ │
│  └──────────────┘                  └──────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## Files

| File | Purpose |
|------|---------|
| `~/.local/bin/jt` | Main CLI tool (Python) |
| `~/.local/bin/jt-idle-monitor` | Screen lock detector (Python) |
| `~/.jira-timer.json` | Runtime state |
| `~/.jira-timer-idle.json` | Idle monitor state (lock tracking) |
| `~/.oh-my-zsh/custom/plugins/jira-timer/jira-timer.plugin.zsh` | Prompt integration |
| `~/Library/LaunchAgents/com.jira-timer.idle-monitor.plist` | launchd config for idle monitor |
| `<project-dir>/jt-idle-monitor.log` | Idle monitor log (loguru, 1 MB rotation, 7-day retention) |

## State File Schema

`~/.jira-timer.json`:

```json
{
  "ticket": "ENG-123",
  "start_time": 1705123456,
  "accumulated": 3600,
  "paused": false,
  "paused_reason": "idle",
  "status_cache": {
    "ENG-123": {
      "status": "In Progress",
      "timestamp": 1705123456
    }
  },
  "config": {
    "rounding": 15,
    "roundDirection": "nearest"
  }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `ticket` | string/null | Current ticket ID |
| `start_time` | int/null | Unix timestamp when timer started (null if paused) |
| `accumulated` | int | Seconds accumulated across sessions |
| `paused` | bool | Whether timer was auto-paused by idle detection |
| `paused_reason` | string/null | Why timer was paused (e.g. `"idle"`) |
| `status_cache` | object | Cached Jira statuses (5-minute TTL) |
| `config` | object | User preferences |

### Idle State File

`~/.jira-timer-idle.json`:

```json
{
  "locked_since": 1705123456,
  "paused_at": 1705123456
}
```

| Field | Type | Description |
|-------|------|-------------|
| `locked_since` | int/null | Unix timestamp when screen lock was first detected |
| `paused_at` | int/null | Unix timestamp when idle auto-pause was triggered |

Both fields are cleared when the screen is unlocked or the timer is resumed.

## jt CLI (`~/.local/bin/jt`)

Python script (~630 lines) providing all timer commands.

### Key Functions

| Function | Purpose |
|----------|---------|
| `load_jira_token()` | Reads JIRA_API_TOKEN from env or ~/.zshrc |
| `load_state()` / `save_state()` | JSON state file I/O |
| `validate_ticket_id()` | Format check, Jira existence check, and user confirmation |
| `get_jira_status()` | Fetches ticket status via jira-cli with caching |
| `run_jira_cmd()` | Subprocess wrapper for jira-cli |
| `format_duration()` | Converts seconds to "1h 23m" format |
| `format_jira_duration()` | Converts seconds to Jira format "1h23m" |
| `round_seconds()` | Rounds to nearest N minutes |
| `parse_duration()` | Parses "1h30m" strings to seconds |

### Ticket Validation

`validate_ticket_id()` runs on `start` and `switch` (skipped on resume). Three checks:

1. **Format** — must match `PROJECT-123` pattern (`^[A-Z][A-Z0-9]+-\d+$`)
2. **Existence** — calls `jira issue view` and fails if ticket not found
3. **Confirmation** — shows ticket summary and prompts `Start timer? [y/N]`

### Jira Integration

Uses `jira-cli` (ankitpokhrel/jira-cli) for all Jira operations:

```bash
# View ticket (for status)
jira issue view ENG-123 --plain

# Log worklog
jira issue worklog add ENG-123 "2h30m" --comment "..." --no-input

# Move ticket
jira issue move ENG-123 "In Progress"
```

## Oh My Zsh Plugin

`~/.oh-my-zsh/custom/plugins/jira-timer/jira-timer.plugin.zsh`

### Prompt Function

`jira_timer_prompt_info()` - Called on each prompt render:

1. Reads `~/.jira-timer.json`
2. Calculates elapsed time if timer running
3. Returns formatted string with color codes:
   - Green `⏱` - running
   - Yellow `⏸` - paused with accumulated time
   - Yellow/Red `⚠` - wrong Jira status

### Auto-Refresh

Uses `TMOUT=1` and `TRAPALRM` to refresh prompt every second when timer is running. Only triggers refresh if an active timer exists (checks state file).

### RPROMPT Integration

Automatically prepends to existing RPROMPT:
```zsh
RPROMPT='$(jira_timer_prompt_info) '"$RPROMPT"
```

## Idle Monitor (`~/.local/bin/jt-idle-monitor`)

Python script run by launchd every 60 seconds. Uses loguru for structured logging.

### Screen Lock Detection

Uses macOS Quartz framework (falls back to `pgrep ScreenSaverEngine` if Quartz unavailable):
```python
import Quartz
session = Quartz.CGSessionCopyCurrentDictionary()
is_locked = session.get('CGSSessionScreenIsLocked', False)
```

### Behavior

Each invocation follows this decision tree:

1. **No active ticket** → clear idle state, exit
2. **Timer already paused** (`start_time` is null):
   - If screen unlocked and `paused_at` is set → send "welcome back" notification
   - Clear idle state (`locked_since` and `paused_at`), exit
3. **Timer running + screen locked**:
   - First lock detection → record `locked_since`, exit
   - Subsequent checks → calculate `locked_duration`
   - If `locked_duration >= 15 min` and not already paused:
     - Set `accumulated` to elapsed time at lock point (not current time)
     - Set `start_time = null`, `paused = true`, `paused_reason = "idle"`
     - Record `paused_at` in idle state
     - Send "Timer Paused" notification
4. **Timer running + screen unlocked**:
   - If `locked_since` is set (was recently locked):
     - If `paused_at` is set → send "welcome back" notification
     - Clear idle state

### Logging

Uses loguru with a rotating log file at `<project-dir>/jt-idle-monitor.log`:

- **Rotation**: 1 MB
- **Retention**: 7 days
- **Levels**: DEBUG (state reads/writes), INFO (each check cycle, decisions), ERROR (failures)

### launchd Configuration

`~/Library/LaunchAgents/com.jira-timer.idle-monitor.plist`:

```xml
<dict>
    <key>Label</key>
    <string>com.jira-timer.idle-monitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/eht/.local/bin/jt-idle-monitor</string>
    </array>
    <key>StartInterval</key>
    <integer>60</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/jt-idle-monitor.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/jt-idle-monitor.err</string>
</dict>
```

### Management

```bash
# Check status
launchctl list | grep jira-timer

# Reload
launchctl unload ~/Library/LaunchAgents/com.jira-timer.idle-monitor.plist
launchctl load ~/Library/LaunchAgents/com.jira-timer.idle-monitor.plist

# View application log (structured, loguru)
tail -f <project-dir>/jt-idle-monitor.log

# View launchd stdout/stderr (unstructured, for crash diagnostics)
tail -f /tmp/jt-idle-monitor.log
tail -f /tmp/jt-idle-monitor.err
```

## Time Calculations

### Accumulated Time

```
total_seconds = accumulated + (now - start_time)
```

- `accumulated`: Persisted seconds from previous sessions
- `start_time`: When current session started (null if paused)

### Rounding (on log)

Default: round to nearest 15 minutes

```python
interval = 15 * 60  # 900 seconds
remainder = total % interval
if remainder >= interval / 2:
    rounded = total + interval - remainder
else:
    rounded = total - remainder
```

### Jira Worklog Format

Converts seconds to Jira duration string:
- 5400 seconds → "1h 30m"
- 7200 seconds → "2h"
- 900 seconds → "15m"
- <60 seconds → "1m" (minimum)
