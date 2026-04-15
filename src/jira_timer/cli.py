"""jt - CLI tool to track time spent on Jira tickets."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Configuration
STATE_FILE = Path.home() / ".jira-timer.json"
CONFIG_ROUNDING = 15  # minutes
CONFIG_ROUND_DIRECTION = "nearest"  # up, down, nearest
STATUS_CACHE_SECONDS = 300  # 5 minutes

# Colors
class Colors:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    CYAN = '\033[0;36m'
    NC = '\033[0m'  # No Color

def load_jira_token():
    """Load JIRA_API_TOKEN from environment or .zshrc"""
    token = os.environ.get('JIRA_API_TOKEN')
    if token:
        return token

    zshrc = Path.home() / ".zshrc"
    if zshrc.exists():
        content = zshrc.read_text()
        match = re.search(r'^export JIRA_API_TOKEN=(.+)$', content, re.MULTILINE)
        if match:
            token = match.group(1).strip().strip('"').strip("'")
            os.environ['JIRA_API_TOKEN'] = token
            return token
    return None

def init_state():
    """Initialize state file if it doesn't exist"""
    if not STATE_FILE.exists():
        state = {
            "ticket": None,
            "start_time": None,
            "accumulated": 0,
            "paused": False,
            "status_cache": {},
            "config": {
                "rounding": CONFIG_ROUNDING,
                "roundDirection": CONFIG_ROUND_DIRECTION
            }
        }
        save_state(state)
    return load_state()

def load_state():
    """Load state from file"""
    if not STATE_FILE.exists():
        return init_state()
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except:
        return init_state()

def save_state(state):
    """Save state to file"""
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

def format_duration(seconds):
    """Format seconds to human readable"""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours}h {minutes:02d}m"
    elif minutes > 0:
        return f"{minutes}m {secs:02d}s"
    else:
        return f"{secs}s"

def format_jira_duration(seconds):
    """Format seconds to Jira duration format"""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60

    if hours > 0 and minutes > 0:
        return f"{hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h"
    elif minutes > 0:
        return f"{minutes}m"
    else:
        return "1m"  # Minimum 1 minute

def round_seconds(seconds, interval_minutes=CONFIG_ROUNDING, direction=CONFIG_ROUND_DIRECTION):
    """Round seconds to nearest interval"""
    if interval_minutes == 0:
        return seconds

    interval_seconds = interval_minutes * 60
    remainder = seconds % interval_seconds

    if direction == "up":
        return seconds + interval_seconds - remainder if remainder > 0 else seconds
    elif direction == "down":
        return seconds - remainder
    else:  # nearest
        if remainder >= interval_seconds / 2:
            return seconds + interval_seconds - remainder
        else:
            return seconds - remainder

def parse_duration(duration_str):
    """Parse duration string (e.g., 30m, 1h15m) to seconds"""
    seconds = 0

    hours = re.search(r'(\d+)h', duration_str)
    if hours:
        seconds += int(hours.group(1)) * 3600

    minutes = re.search(r'(\d+)m', duration_str)
    if minutes:
        seconds += int(minutes.group(1)) * 60

    secs = re.search(r'(\d+)s', duration_str)
    if secs:
        seconds += int(secs.group(1))

    return seconds

def validate_ticket_id(ticket):
    """Validate ticket format, verify it exists in Jira, and confirm with user.
    Also parses status, caches it, and offers to move to In Progress."""
    if re.match(r'^[a-zA-Z][a-zA-Z0-9]+-\d+$', ticket):
        ticket = ticket.upper()
    else:
        print(f"{Colors.RED}Invalid ticket ID: {ticket}{Colors.NC}")
        print(f"Expected format: PROJECT-123 (e.g., ENG-456, DATA-78)")
        sys.exit(1)

    # Verify ticket exists in Jira
    result = run_jira_cmd(['issue', 'view', ticket, '--plain'])
    if result.returncode != 0:
        print(f"{Colors.RED}Ticket {ticket} not found in Jira{Colors.NC}")
        sys.exit(1)

    # Extract summary — jira-cli --plain outputs it as "# Summary text" on its own line
    summary = None
    match = re.search(r'(?m)^\s*#\s+(.+)$', result.stdout)
    if match:
        summary = match.group(1).strip()

    # Parse and cache status from the same API response
    output = result.stdout
    status_patterns = [
        'In Progress', 'To Do', 'Done', 'In Review', 'Blocked',
        'Open', 'Closed', 'In Development', 'Doing', 'Ready',
        'Backlog', 'Selected for Development', 'Code Review'
    ]
    status = "Unknown"
    for pattern in status_patterns:
        if pattern in output:
            status = pattern
            break

    state = load_state()
    if 'status_cache' not in state:
        state['status_cache'] = {}
    state['status_cache'][ticket] = {
        'status': status,
        'timestamp': time.time()
    }
    save_state(state)

    # Show ticket info with status
    if summary:
        print(f"{Colors.CYAN}{ticket}: {summary}{Colors.NC}")
    else:
        print(f"{Colors.CYAN}{ticket}{Colors.NC}")

    if is_in_progress(status):
        print(f"{Colors.GREEN}Status: {status}{Colors.NC}")
    else:
        print(f"{Colors.YELLOW}Status: {status}{Colors.NC}")

    try:
        confirm = input("Start timer? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        sys.exit(0)

    if confirm != 'y':
        print("Cancelled.")
        sys.exit(0)

    # Offer to move to In Progress if needed
    if not is_in_progress(status):
        print(f"{Colors.YELLOW}{ticket} is \"{status}\" - move to In Progress? [y/N]{Colors.NC}")
        try:
            move_confirm = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            move_confirm = 'n'

        if move_confirm == 'y':
            print(f"{Colors.CYAN}Moving {ticket} to In Progress...{Colors.NC}")
            move_result = run_jira_cmd(['issue', 'move', ticket, 'In Progress'])
            if move_result.returncode != 0:
                print(f"{Colors.YELLOW}Could not move ticket (may need manual transition){Colors.NC}")
            else:
                get_jira_status(ticket, force=True)

    return ticket

def run_jira_cmd(args, capture=True):
    """Run a jira CLI command"""
    load_jira_token()
    try:
        result = subprocess.run(
            ['jira'] + args,
            capture_output=capture,
            text=True
        )
        return result
    except FileNotFoundError:
        print(f"{Colors.RED}Error: jira-cli not found. Install with: brew install jira-cli{Colors.NC}")
        sys.exit(1)

def get_jira_status(ticket, force=False):
    """Check Jira ticket status with caching"""
    state = load_state()
    cache = state.get('status_cache', {})

    # Check cache first
    if not force and ticket in cache:
        entry = cache[ticket]
        if time.time() - entry.get('timestamp', 0) < STATUS_CACHE_SECONDS:
            return entry.get('status', 'Unknown')

    # Fetch from Jira
    result = run_jira_cmd(['issue', 'view', ticket, '--plain'])

    if result.returncode != 0:
        return "Unknown"

    # Parse status from output - look for common status names
    output = result.stdout
    status_patterns = [
        'In Progress', 'To Do', 'Done', 'In Review', 'Blocked',
        'Open', 'Closed', 'In Development', 'Doing', 'Ready',
        'Backlog', 'Selected for Development', 'Code Review'
    ]

    status = "Unknown"
    for pattern in status_patterns:
        if pattern in output:
            status = pattern
            break

    # Update cache
    if 'status_cache' not in state:
        state['status_cache'] = {}
    state['status_cache'][ticket] = {
        'status': status,
        'timestamp': time.time()
    }
    save_state(state)

    return status

def is_in_progress(status):
    """Check if ticket is in an 'in progress' state"""
    in_progress_statuses = [
        'in progress', 'in development', 'doing', 'working', 'active', 'code review'
    ]
    return status.lower() in in_progress_statuses

def pick_in_progress_ticket():
    """Fetch in-progress tickets assigned to current user and let them pick one."""
    print(f"{Colors.CYAN}Fetching your In Progress tickets...{Colors.NC}")

    # Get current user
    result = run_jira_cmd(['me'], capture=True)
    if result.returncode != 0:
        print(f"{Colors.RED}Could not determine current Jira user{Colors.NC}")
        sys.exit(1)
    user = result.stdout.strip()

    # Fetch in-progress tickets
    result = run_jira_cmd([
        'issue', 'list',
        '-s', 'In Progress',
        '-a', user,
        '--plain', '--columns', 'KEY,SUMMARY', '--no-truncate'
    ], capture=True)

    if result.returncode != 0:
        print(f"{Colors.RED}Failed to fetch tickets from Jira{Colors.NC}")
        sys.exit(1)

    # Parse output (skip header line)
    lines = result.stdout.strip().split('\n')
    tickets = []
    for line in lines[1:]:  # skip header
        parts = line.split('\t', 1)
        if len(parts) == 2:
            tickets.append((parts[0].strip(), parts[1].strip()))

    if not tickets:
        print(f"{Colors.YELLOW}No In Progress tickets found{Colors.NC}")
        sys.exit(0)

    # Display choices
    print()
    for i, (key, summary) in enumerate(tickets, 1):
        print(f"  {i}) {Colors.CYAN}{key}{Colors.NC} {summary}")
    print(f"  0) Cancel")
    print()

    try:
        choice = input("Select ticket: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled.")
        sys.exit(0)

    if choice == '0' or choice == '':
        print("Cancelled.")
        sys.exit(0)

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(tickets):
            return tickets[idx][0]
        else:
            print(f"{Colors.RED}Invalid choice{Colors.NC}")
            sys.exit(1)
    except ValueError:
        print(f"{Colors.RED}Invalid choice{Colors.NC}")
        sys.exit(1)

def cmd_start(args, _validated=False):
    """Start tracking time on a ticket"""
    ticket = None
    back_duration = None
    at_time = None

    # Parse arguments
    i = 0
    while i < len(args):
        if args[i] == '--back' and i + 1 < len(args):
            back_duration = args[i + 1]
            i += 2
        elif args[i] == '--at' and i + 1 < len(args):
            at_time = args[i + 1]
            i += 2
        elif not args[i].startswith('-'):
            ticket = args[i]
            i += 1
        else:
            print(f"{Colors.RED}Unknown option: {args[i]}{Colors.NC}")
            sys.exit(1)

    state = init_state()

    # Handle "in-progress" / "ip" keyword: pick from assigned in-progress tickets
    if ticket and ticket.lower() in ('in-progress', 'ip'):
        ticket = pick_in_progress_ticket()
        _validated = True  # Already confirmed by user selection

    # If no ticket specified, resume paused ticket if there is one
    if not ticket:
        if state.get('ticket') and not state.get('start_time'):
            ticket = state['ticket']
            print(f"{Colors.CYAN}Resuming {ticket}...{Colors.NC}")
        else:
            print(f"{Colors.RED}Usage: jt start JIRA-123 [--back 30m] [--at 09:30]{Colors.NC}")
            sys.exit(1)
    elif not _validated:
        ticket = validate_ticket_id(ticket)

    # Check if timer already running
    if state.get('ticket') and state.get('start_time'):
        elapsed = int(time.time()) - state['start_time']
        total = state.get('accumulated', 0) + elapsed

        print(f"{Colors.YELLOW}Timer already running on {state['ticket']} ({format_duration(total)}){Colors.NC}")
        print()
        print("What do you want to do?")
        print(f"  1) Stop {state['ticket']} and start {ticket}")
        print(f"  2) Keep {state['ticket']}, cancel this command")
        print(f"  3) Switch (stop {state['ticket']}, then start {ticket})")
        print()

        try:
            choice = input("Choice [1/2/3]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            sys.exit(0)

        if choice == '1' or choice == '3':
            cmd_stop_internal(state)
            state = load_state()
            # Clear for new ticket
            state['ticket'] = None
            state['start_time'] = None
            state['accumulated'] = 0
            save_state(state)
        elif choice == '2':
            print("Cancelled.")
            sys.exit(0)
        else:
            print("Invalid choice. Cancelled.")
            sys.exit(1)

    # Calculate start time
    start_time = int(time.time())

    if back_duration:
        back_seconds = parse_duration(back_duration)
        start_time -= back_seconds
        print(f"{Colors.CYAN}Backdating start by {back_duration}{Colors.NC}")
    elif at_time:
        # Parse HH:MM format
        try:
            from datetime import datetime
            today = datetime.now().date()
            t = datetime.strptime(at_time, "%H:%M").time()
            dt = datetime.combine(today, t)
            start_time = int(dt.timestamp())
            print(f"{Colors.CYAN}Starting from {at_time}{Colors.NC}")
        except ValueError:
            print(f"{Colors.RED}Invalid time format. Use HH:MM{Colors.NC}")
            sys.exit(1)

    # Check Jira status (skip if validate_ticket_id already handled it)
    if not _validated:
        print(f"{Colors.CYAN}Checking Jira status...{Colors.NC}")
        status = get_jira_status(ticket)

        if not is_in_progress(status):
            print(f"{Colors.YELLOW}{ticket} is \"{status}\" - move to In Progress? [y/N]{Colors.NC}")
            try:
                confirm = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                confirm = 'n'

            if confirm == 'y':
                print(f"{Colors.CYAN}Moving {ticket} to In Progress...{Colors.NC}")
                result = run_jira_cmd(['issue', 'move', ticket, 'In Progress'])
                if result.returncode != 0:
                    print(f"{Colors.YELLOW}Could not move ticket (may need manual transition){Colors.NC}")
                else:
                    # Refresh cache
                    get_jira_status(ticket, force=True)

    # Start the timer
    state = load_state()
    is_resume = state.get('ticket') == ticket and state.get('accumulated', 0) > 0
    state['ticket'] = ticket
    state['start_time'] = start_time
    if not is_resume:
        state['accumulated'] = 0
    state['paused'] = False
    save_state(state)

    if is_resume:
        print(f"{Colors.GREEN}Resumed timer on {ticket} ({format_duration(state['accumulated'])} banked){Colors.NC}")
    else:
        print(f"{Colors.GREEN}Started timer on {ticket}{Colors.NC}")

def cmd_stop_internal(state=None):
    """Internal stop - doesn't print, for use by other commands"""
    if state is None:
        state = load_state()

    if not state.get('ticket') or not state.get('start_time'):
        return

    elapsed = int(time.time()) - state['start_time']
    total = state.get('accumulated', 0) + elapsed

    state['start_time'] = None
    state['accumulated'] = total
    save_state(state)

def cmd_stop(args):
    """Stop the current session"""
    state = load_state()

    if not state.get('ticket'):
        print(f"{Colors.YELLOW}No timer running{Colors.NC}")
        return

    if not state.get('start_time'):
        print(f"{Colors.YELLOW}Timer already stopped. Accumulated: {format_duration(state.get('accumulated', 0))}{Colors.NC}")
        return

    elapsed = int(time.time()) - state['start_time']
    total = state.get('accumulated', 0) + elapsed

    state['start_time'] = None
    state['accumulated'] = total
    save_state(state)

    print(f"{Colors.GREEN}Stopped timer on {state['ticket']}{Colors.NC}")
    print(f"Session: {format_duration(elapsed)}")
    print(f"Total accumulated: {format_duration(total)}")

def cmd_status(args):
    """Show current timer status"""
    state = load_state()

    if not state.get('ticket'):
        print(f"{Colors.YELLOW}No active timer{Colors.NC}")
        return

    print(f"{Colors.CYAN}Ticket:{Colors.NC} {state['ticket']}")

    if state.get('start_time'):
        elapsed = int(time.time()) - state['start_time']
        total = state.get('accumulated', 0) + elapsed
        print(f"{Colors.CYAN}Status:{Colors.NC} {Colors.GREEN}Running{Colors.NC}")
        print(f"{Colors.CYAN}Current session:{Colors.NC} {format_duration(elapsed)}")
        print(f"{Colors.CYAN}Total accumulated:{Colors.NC} {format_duration(total)}")
    else:
        print(f"{Colors.CYAN}Status:{Colors.NC} {Colors.YELLOW}Paused{Colors.NC}")
        print(f"{Colors.CYAN}Total accumulated:{Colors.NC} {format_duration(state.get('accumulated', 0))}")

    # Show Jira status
    jira_status = get_jira_status(state['ticket'])
    if is_in_progress(jira_status):
        print(f"{Colors.CYAN}Jira status:{Colors.NC} {Colors.GREEN}{jira_status}{Colors.NC}")
    else:
        print(f"{Colors.CYAN}Jira status:{Colors.NC} {Colors.YELLOW}{jira_status}{Colors.NC} (not In Progress)")

def cmd_log(args):
    """Log accumulated time to Jira"""
    comment = None
    remaining = None
    exact = False

    # Parse arguments
    i = 0
    while i < len(args):
        if args[i] == '--remaining' and i + 1 < len(args):
            remaining = args[i + 1]
            i += 2
        elif args[i] == '--exact':
            exact = True
            i += 1
        elif not args[i].startswith('-'):
            comment = args[i]
            i += 1
        else:
            print(f"{Colors.RED}Unknown option: {args[i]}{Colors.NC}")
            sys.exit(1)

    state = load_state()

    if not state.get('ticket'):
        print(f"{Colors.YELLOW}No timer to log{Colors.NC}")
        return

    # Calculate total time
    total = state.get('accumulated', 0)
    if state.get('start_time'):
        elapsed = int(time.time()) - state['start_time']
        total += elapsed

    if total < 60:
        print(f"{Colors.YELLOW}Less than 1 minute tracked. Nothing to log.{Colors.NC}")
        return

    # Round if not exact
    logged_seconds = total
    if not exact and CONFIG_ROUNDING > 0:
        logged_seconds = round_seconds(total, CONFIG_ROUNDING, CONFIG_ROUND_DIRECTION)
        if logged_seconds != total:
            print(f"{Colors.CYAN}Accumulated: {format_duration(total)} → Logging as {format_duration(logged_seconds)} (rounded to {CONFIG_ROUNDING}m){Colors.NC}")

    jira_duration = format_jira_duration(logged_seconds)
    ticket = state['ticket']

    print(f"{Colors.CYAN}Logging {jira_duration} to {ticket}...{Colors.NC}")

    # Build jira command
    cmd = ['issue', 'worklog', 'add', ticket, jira_duration, '--no-input']
    if comment:
        cmd.extend(['--comment', comment])

    result = run_jira_cmd(cmd, capture=False)

    if result.returncode == 0:
        print(f"{Colors.GREEN}Logged {jira_duration} to {ticket}{Colors.NC}")

        # Update remaining estimate if specified
        if remaining:
            print(f"{Colors.CYAN}Updating remaining estimate to {remaining}...{Colors.NC}")
            result = run_jira_cmd([
                'issue', 'edit', ticket,
                '--custom', f'timetracking={{"remainingEstimate":"{remaining}"}}',
                '--no-input'
            ])
            if result.returncode != 0:
                print(f"{Colors.YELLOW}Could not update remaining estimate{Colors.NC}")

        # Reset state
        state['ticket'] = None
        state['start_time'] = None
        state['accumulated'] = 0
        state['paused'] = False
        save_state(state)
    else:
        print(f"{Colors.RED}Failed to log time. Timer state preserved.{Colors.NC}")
        sys.exit(1)

def cmd_set(args):
    """Set accumulated time to a specific duration"""
    state = load_state()

    if not state.get('ticket'):
        print(f"{Colors.YELLOW}No active timer{Colors.NC}")
        return

    if not args:
        print(f"{Colors.RED}Usage: jt set <duration>{Colors.NC}")
        print(f"Examples: jt set 0, jt set 30m, jt set 1h15m")
        sys.exit(1)

    duration_str = args[0]

    # Handle "0" as a special case
    if duration_str == '0':
        new_seconds = 0
    else:
        new_seconds = parse_duration(duration_str)

    # Calculate current total for display
    current_total = state.get('accumulated', 0)
    if state.get('start_time'):
        elapsed = int(time.time()) - state['start_time']
        current_total += elapsed

    # If setting to 0, prompt for confirmation (like discard did)
    if new_seconds == 0 and current_total > 0:
        print(f"{Colors.YELLOW}Reset {format_duration(current_total)} on {state['ticket']} to 0? [y/N]{Colors.NC}")
        try:
            confirm = input().strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return

        if confirm != 'y':
            print("Cancelled.")
            return

    # Set the new accumulated time
    # If timer is running, reset start_time to now so elapsed starts fresh
    if state.get('start_time'):
        state['start_time'] = int(time.time())
    state['accumulated'] = new_seconds
    save_state(state)

    if new_seconds == 0:
        print(f"{Colors.GREEN}Reset timer on {state['ticket']} to 0{Colors.NC}")
    else:
        print(f"{Colors.GREEN}Set timer on {state['ticket']} to {format_duration(new_seconds)}{Colors.NC}")

def cmd_switch(args):
    """Stop current and start new ticket"""
    if not args:
        print(f"{Colors.RED}Usage: jt switch JIRA-123{Colors.NC}")
        sys.exit(1)

    new_ticket = args[0]
    new_ticket = validate_ticket_id(new_ticket)
    state = load_state()

    if state.get('ticket'):
        cmd_stop_internal(state)
        state = load_state()
        old_ticket = state['ticket']
        acc = state.get('accumulated', 0)
        print(f"{Colors.CYAN}Stopped {old_ticket} ({format_duration(acc)}){Colors.NC}")

        # Offer to log time if there's meaningful accumulated time
        if acc >= 60:
            logged_seconds = round_seconds(acc, CONFIG_ROUNDING, CONFIG_ROUND_DIRECTION)
            jira_duration = format_jira_duration(logged_seconds)

            try:
                choice = input(f"Log {jira_duration} to {old_ticket}? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                choice = 'n'

            if choice == 'y':
                print(f"{Colors.CYAN}Logging {jira_duration} to {old_ticket}...{Colors.NC}")
                result = run_jira_cmd(
                    ['issue', 'worklog', 'add', old_ticket, jira_duration, '--no-input'],
                    capture=False
                )
                if result.returncode == 0:
                    print(f"{Colors.GREEN}Logged {jira_duration} to {old_ticket}{Colors.NC}")
                else:
                    print(f"{Colors.RED}Failed to log time to {old_ticket}{Colors.NC}")

        # Clear state before starting new
        state['ticket'] = None
        state['start_time'] = None
        state['accumulated'] = 0
        save_state(state)

    # Start new ticket (already validated above)
    cmd_start([new_ticket], _validated=True)

def cmd_refresh(args):
    """Force refresh Jira status cache"""
    state = load_state()

    if not state.get('ticket'):
        print(f"{Colors.YELLOW}No active timer{Colors.NC}")
        return

    ticket = state['ticket']
    print(f"{Colors.CYAN}Refreshing status for {ticket}...{Colors.NC}")
    status = get_jira_status(ticket, force=True)
    print(f"{Colors.GREEN}Status: {status}{Colors.NC}")

def cmd_move(args):
    """Move current ticket to In Progress"""
    state = load_state()

    if not state.get('ticket'):
        print(f"{Colors.YELLOW}No active timer{Colors.NC}")
        return

    ticket = state['ticket']
    print(f"{Colors.CYAN}Moving {ticket} to In Progress...{Colors.NC}")

    result = run_jira_cmd(['issue', 'move', ticket, 'In Progress'])

    if result.returncode == 0:
        get_jira_status(ticket, force=True)
        print(f"{Colors.GREEN}Moved {ticket} to In Progress{Colors.NC}")
    else:
        print(f"{Colors.RED}Failed to move ticket{Colors.NC}")

def cmd_help(args):
    """Show help"""
    print("""jt - Jira Timer

Usage: jt <command> [options]

Commands:
  start JIRA-123            Start tracking time on a ticket
  start in-progress         Pick from your In Progress tickets
  start JIRA-123 --back 30m Start with backdated time
  start JIRA-123 --at 09:30 Start from specific time
  stop                      Stop the current session
  status                    Show current timer status
  set <duration>            Set accumulated time (e.g., 30m, 1h15m, 0)
  log [comment]             Log accumulated time to Jira
  log --exact               Log without rounding
  log --remaining 2h        Also update remaining estimate
  switch JIRA-456           Stop current (optionally log), start new
  refresh                   Force refresh Jira status cache
  move                      Move current ticket to In Progress
  help                      Show this help""")

def main():
    args = sys.argv[1:]

    if not args:
        cmd_status([])
        return

    command = args[0]
    cmd_args = args[1:]

    commands = {
        'start': cmd_start,
        'stop': cmd_stop,
        'status': cmd_status,
        'set': cmd_set,
        'log': cmd_log,
        'switch': cmd_switch,
        'refresh': cmd_refresh,
        'move': cmd_move,
        'help': cmd_help,
        '--help': cmd_help,
        '-h': cmd_help,
    }

    if command in commands:
        commands[command](cmd_args)
    else:
        print(f"{Colors.RED}Unknown command: {command}{Colors.NC}")
        print("Run 'jt help' for usage")
        sys.exit(1)

if __name__ == '__main__':
    main()
