# jt - Jira Timer

A **macOS-only** CLI tool to track time spent on Jira tickets with start/stop functionality, zsh prompt integration, and automatic idle detection (via Quartz screen-lock API + launchd).

## Installation

### Prerequisites

1. Install [uv](https://docs.astral.sh/uv/) (Python package/tool manager) and jira-cli:
   ```bash
   brew install uv jira-cli
   ```

2. Create an API token at https://id.atlassian.com/manage-profile/security/api-tokens and add to `~/.zshrc`:
   ```bash
   echo 'export JIRA_API_TOKEN="your-token-here"' >> ~/.zshrc
   source ~/.zshrc
   ```

3. Configure jira-cli with your Jira instance:
   ```bash
   jira init
   ```

### Install jt

From the repo root:
```bash
./install.sh
```

This runs `uv tool install .`, which creates an isolated venv with all dependencies (loguru, pyobjc-framework-Quartz) and installs the `jt` and `jt-idle-monitor` entry points into `~/.local/bin/`. It also installs the zsh plugin and loads the launchd agent. Ensure `~/.local/bin` is in your PATH.

To upgrade after pulling new changes: re-run `./install.sh` (or `uv tool install --force .`).

### Enable Prompt Integration

Add `jira-timer` to your Oh My Zsh plugins in `~/.zshrc`:
```bash
plugins=(git kube-aliases jira-timer)
```

Restart your terminal or run `source ~/.zshrc`.

## Usage

### Basic Commands

```bash
jt start ENG-123        # Start tracking time on a ticket
jt start                # Resume paused ticket (if one exists)
jt stop                 # Pause timer, bank accumulated time
jt status               # Show current ticket and time
jt log "what I did"     # Log time to Jira worklog and reset
jt discard              # Discard accumulated time without logging
```

### Advanced Commands

```bash
jt start ENG-123 --back 30m    # Backdate start by 30 minutes
jt start ENG-123 --at 09:30    # Start from specific time today
jt switch ENG-456              # Stop current, start new ticket
jt refresh                     # Force refresh Jira status cache
jt move                        # Move current ticket to In Progress
```

### Logging Options

```bash
jt log                         # Log with 15-minute rounding (default)
jt log --exact                 # Log exact accumulated time
jt log --remaining 2h          # Also update remaining estimate
jt log "Fixed auth bug"        # Log with comment
```

## Prompt Integration

When a timer is active, your zsh prompt shows:

```
➜ mydir git:(main)                    ⏱ ENG-123 1:23:45
```

| Icon | Meaning |
|------|---------|
| ⏱ (green) | Timer running |
| ⏸ (yellow) | Timer paused with banked time |
| ⚠ (yellow/red) | Ticket not in "In Progress" status |

## Workflow Example

```bash
# Morning - start work
jt start ENG-123

# Lunch break
jt stop

# Back from lunch - resume
jt start                    # Resumes ENG-123

# Context switch to urgent bug
jt switch ENG-456           # Stops ENG-123, starts ENG-456

# End of day - log time
jt log "Fixed authentication bug"
```

## Features

- **Time Rounding**: Logs are rounded to nearest 15 minutes by default
- **Status Checking**: Warns if ticket isn't "In Progress" and offers to move it
- **Status Caching**: Jira status cached for 5 minutes to reduce API calls
- **Idle Detection**: Auto-pauses timer after 15 minutes of screen lock
- **Idle Monitor Logging**: Structured log at `~/Library/Logs/jira-timer/jt-idle-monitor.log` (loguru, 1 MB rotation, 7-day retention)
- **Context Switch Protection**: Warns before starting new ticket if timer running

## Configuration

Settings stored in `~/.jira-timer.json`:

```json
{
  "config": {
    "rounding": 15,
    "roundDirection": "nearest"
  }
}
```

- `rounding`: Minutes to round to (0 = no rounding)
- `roundDirection`: "up", "down", or "nearest"
