# jira-timer Oh My Zsh plugin
# Displays active Jira timer in RPROMPT

# State file location
JT_STATE_FILE="$HOME/.jira-timer.json"

# Cache for status check (avoid reading file too often)
_jt_last_check=0
_jt_cached_output=""

# Get timer info for prompt
jira_timer_prompt_info() {
    # Only check every 1 second to avoid performance issues
    local now=$(date +%s)
    if (( now - _jt_last_check < 1 )) && [[ -n "$_jt_cached_output" ]]; then
        echo "$_jt_cached_output"
        return
    fi
    _jt_last_check=$now

    # Check if state file exists
    if [[ ! -f "$JT_STATE_FILE" ]]; then
        _jt_cached_output=""
        echo ""
        return
    fi

    # Read state using python (fast enough for prompt)
    local info=$(python3 -c "
import json
import time

try:
    with open('$JT_STATE_FILE', 'r') as f:
        state = json.load(f)

    ticket = state.get('ticket')
    if not ticket:
        print('')
        exit()

    start_time = state.get('start_time')
    accumulated = state.get('accumulated', 0)
    paused = state.get('paused', False)

    # Calculate total time
    if start_time and start_time != 'None':
        elapsed = int(time.time()) - int(start_time)
        total = accumulated + elapsed
        running = True
    else:
        total = accumulated
        running = False

    # Format duration
    hours = total // 3600
    minutes = (total % 3600) // 60
    seconds = total % 60

    if hours > 0:
        duration = f'{hours:d}:{minutes:02d}:{seconds:02d}'
    else:
        duration = f'{minutes:d}:{seconds:02d}'

    # Get cached status
    status_cache = state.get('status_cache', {})
    jira_status = status_cache.get(ticket, {}).get('status', '')

    # Determine if in progress
    in_progress = jira_status.lower() in ['in progress', 'in development', 'doing', 'working', 'active']

    # Output format: running|paused|warning, ticket, duration, jira_status
    if running:
        if in_progress or not jira_status:
            print(f'running|{ticket}|{duration}|')
        else:
            print(f'warning|{ticket}|{duration}|{jira_status}')
    else:
        print(f'paused|{ticket}|{duration}|')

except Exception as e:
    print('')
" 2>/dev/null)

    if [[ -z "$info" ]]; then
        _jt_cached_output=""
        echo ""
        return
    fi

    # Parse output
    local state=$(echo "$info" | cut -d'|' -f1)
    local ticket=$(echo "$info" | cut -d'|' -f2)
    local duration=$(echo "$info" | cut -d'|' -f3)
    local jira_status=$(echo "$info" | cut -d'|' -f4)

    local output=""

    case "$state" in
        running)
            # Green timer icon and text
            output="%{$fg_bold[green]%}⏱ ${ticket} ${duration}%{$reset_color%}"
            ;;
        paused)
            # Yellow - paused with accumulated time
            output="%{$fg_bold[yellow]%}⏸ ${ticket} ${duration}%{$reset_color%}"
            ;;
        warning)
            # Red/Yellow warning - wrong Jira status
            output="%{$fg_bold[yellow]%}⚠ ${ticket}%{$reset_color%} %{$fg[red]%}(${jira_status})%{$reset_color%} %{$fg_bold[yellow]%}${duration}%{$reset_color%}"
            ;;
    esac

    _jt_cached_output="$output"
    echo "$output"
}

# Add to RPROMPT if not already set
if [[ -z "$RPROMPT" ]]; then
    RPROMPT='$(jira_timer_prompt_info)'
else
    # Prepend to existing RPROMPT
    RPROMPT='$(jira_timer_prompt_info) '"$RPROMPT"
fi

# Ensure prompt is refreshed frequently for timer updates
# This uses TMOUT which triggers TRAPALRM
TMOUT=1
TRAPALRM() {
    # Only refresh if we have a running timer
    if [[ -f "$JT_STATE_FILE" ]]; then
        local has_timer=$(python3 -c "
import json
try:
    with open('$JT_STATE_FILE', 'r') as f:
        state = json.load(f)
    if state.get('ticket') and state.get('start_time') and state.get('start_time') != 'None':
        print('yes')
except:
    pass
" 2>/dev/null)
        if [[ "$has_timer" == "yes" ]]; then
            zle reset-prompt 2>/dev/null || true
        fi
    fi
}
