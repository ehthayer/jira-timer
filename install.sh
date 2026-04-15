#!/bin/bash
# jt - Jira Timer Installer
# Installs jt and its components with prerequisite checking

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}  jt - Jira Timer Installer${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Track what needs to be done
PREREQS_MET=true

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

#
# Prerequisite Checks
#

echo -e "${CYAN}Checking prerequisites...${NC}"
echo ""

# Check: Homebrew
echo -n "  Homebrew: "
if command -v brew &> /dev/null; then
    echo -e "${GREEN}installed${NC}"
else
    echo -e "${RED}not found${NC}"
    echo -e "    ${YELLOW}Install from: https://brew.sh${NC}"
    PREREQS_MET=false
fi

# Check: Python 3
echo -n "  Python 3: "
if command -v python3 &> /dev/null; then
    echo -e "${GREEN}$(python3 --version)${NC}"
else
    echo -e "${RED}not found${NC}"
    echo -e "    ${YELLOW}Install with: brew install python3${NC}"
    PREREQS_MET=false
fi

# Check: jira-cli
echo -n "  jira-cli: "
if command -v jira &> /dev/null; then
    echo -e "${GREEN}installed${NC}"
else
    echo -e "${YELLOW}not found${NC}"
    echo -e "    Install with: ${CYAN}brew install jira-cli${NC}"
    PREREQS_MET=false
fi

# Check: JIRA_API_TOKEN
echo -n "  JIRA_API_TOKEN: "
if [[ -n "$JIRA_API_TOKEN" ]]; then
    echo -e "${GREEN}set${NC}"
elif grep -q "JIRA_API_TOKEN" ~/.zshrc 2>/dev/null; then
    echo -e "${GREEN}found in ~/.zshrc${NC}"
else
    echo -e "${YELLOW}not set${NC}"
    echo -e "    1. Create token at: ${CYAN}https://id.atlassian.com/manage-profile/security/api-tokens${NC}"
    echo -e "    2. Add to ~/.zshrc:"
    echo -e "       ${CYAN}echo 'export JIRA_API_TOKEN=\"your-token\"' >> ~/.zshrc${NC}"
    PREREQS_MET=false
fi

# Check: jira-cli configured
echo -n "  jira-cli config: "
if [[ -f "$HOME/.config/.jira/.config.yml" ]] || [[ -f "$HOME/.jira/.config.yml" ]]; then
    echo -e "${GREEN}configured${NC}"
else
    echo -e "${YELLOW}not configured${NC}"
    echo -e "    Run: ${CYAN}jira init${NC}"
    PREREQS_MET=false
fi

# Check: Oh My Zsh
echo -n "  Oh My Zsh: "
if [[ -d "$HOME/.oh-my-zsh" ]]; then
    echo -e "${GREEN}installed${NC}"
else
    echo -e "${YELLOW}not found${NC}"
    echo -e "    Install from: ${CYAN}https://ohmyz.sh${NC}"
    echo -e "    (Prompt integration requires Oh My Zsh)"
fi

# Check: loguru
echo -n "  loguru: "
if python3 -c "import loguru" 2>/dev/null; then
    echo -e "${GREEN}installed${NC}"
else
    echo -e "${YELLOW}not found${NC}"
    echo -e "    Install with: ${CYAN}pip3 install loguru${NC}"
    PREREQS_MET=false
fi

# Check: zsh
echo -n "  Default shell: "
if [[ "$SHELL" == *"zsh"* ]]; then
    echo -e "${GREEN}zsh${NC}"
else
    echo -e "${YELLOW}$SHELL (not zsh)${NC}"
    echo -e "    ${YELLOW}Prompt integration works best with zsh${NC}"
fi

echo ""

#
# Stop if prerequisites not met
#

if [[ "$PREREQS_MET" == "false" ]]; then
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}  Please install missing prerequisites and run again.${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    exit 1
fi

#
# Install Components
#

echo -e "${CYAN}Installing components...${NC}"
echo ""

# Create directories
mkdir -p "$HOME/.local/bin"
mkdir -p "$HOME/.oh-my-zsh/custom/plugins/jira-timer"
mkdir -p "$HOME/Library/LaunchAgents"

# Install jt CLI
echo -n "  Installing jt CLI: "
cp "$SCRIPT_DIR/bin/jt" "$HOME/.local/bin/jt"
chmod +x "$HOME/.local/bin/jt"
echo -e "${GREEN}~/.local/bin/jt${NC}"

# Install idle monitor
echo -n "  Installing idle monitor: "
cp "$SCRIPT_DIR/bin/jt-idle-monitor" "$HOME/.local/bin/jt-idle-monitor"
chmod +x "$HOME/.local/bin/jt-idle-monitor"
echo -e "${GREEN}~/.local/bin/jt-idle-monitor${NC}"

# Install Oh My Zsh plugin
echo -n "  Installing zsh plugin: "
cp "$SCRIPT_DIR/plugins/jira-timer.plugin.zsh" "$HOME/.oh-my-zsh/custom/plugins/jira-timer/jira-timer.plugin.zsh"
echo -e "${GREEN}~/.oh-my-zsh/custom/plugins/jira-timer/${NC}"

# Install launchd plist (substitute username)
echo -n "  Installing launchd agent: "
sed "s|/Users/eht|$HOME|g" "$SCRIPT_DIR/launchd/com.jira-timer.idle-monitor.plist" > "$HOME/Library/LaunchAgents/com.jira-timer.idle-monitor.plist"
echo -e "${GREEN}~/Library/LaunchAgents/${NC}"

# Load launchd agent
echo -n "  Loading idle monitor agent: "
launchctl unload "$HOME/Library/LaunchAgents/com.jira-timer.idle-monitor.plist" 2>/dev/null || true
launchctl load "$HOME/Library/LaunchAgents/com.jira-timer.idle-monitor.plist"
echo -e "${GREEN}loaded${NC}"

echo ""

#
# Update .zshrc
#

echo -n "  Updating ~/.zshrc plugins: "
if grep -q "jira-timer" "$HOME/.zshrc" 2>/dev/null; then
    echo -e "${GREEN}already configured${NC}"
else
    # Add jira-timer to plugins list
    if grep -q "^plugins=(" "$HOME/.zshrc"; then
        # Add to existing plugins line
        sed -i.bak 's/^plugins=(\(.*\))/plugins=(\1 jira-timer)/' "$HOME/.zshrc"
        rm -f "$HOME/.zshrc.bak"
        echo -e "${GREEN}added to plugins${NC}"
    else
        echo -e "${YELLOW}manual setup needed${NC}"
        echo -e "    Add ${CYAN}jira-timer${NC} to your plugins in ~/.zshrc"
    fi
fi

# Check PATH
echo -n "  Checking PATH: "
if echo "$PATH" | grep -q "$HOME/.local/bin"; then
    echo -e "${GREEN}~/.local/bin in PATH${NC}"
elif grep -q '\.local/bin' "$HOME/.zshrc" 2>/dev/null; then
    echo -e "${GREEN}configured in ~/.zshrc${NC}"
else
    echo -e "${YELLOW}adding to ~/.zshrc${NC}"
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zshrc"
fi

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Installation complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo -e "  ${CYAN}Next steps:${NC}"
echo -e "    1. Open a new terminal (or run: source ~/.zshrc)"
echo -e "    2. Test with: jt help"
echo -e "    3. Start tracking: jt start JIRA-123"
echo ""
echo -e "  ${CYAN}Your prompt will show the active timer:${NC}"
echo -e "    ➜ mydir ${GREEN}⏱ JIRA-123 1:23:45${NC}"
echo ""
