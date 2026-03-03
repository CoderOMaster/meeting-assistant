#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup.sh — One-time setup for the Voice AI Meeting Bot on macOS
# Run: chmod +x setup.sh && ./setup.sh
# ─────────────────────────────────────────────────────────────────────────────
set -e

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${GREEN}[setup]${NC} $*"; }
warn()    { echo -e "${YELLOW}[setup]${NC} $*"; }
section() { echo -e "\n${BOLD}── $* ──${NC}"; }

# ── 1. Homebrew ───────────────────────────────────────────────────────────────
section "Homebrew"
if ! command -v brew &>/dev/null; then
  info "Installing Homebrew…"
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
else
  info "Homebrew already installed."
fi

# ── 2. Python 3.11+ ───────────────────────────────────────────────────────────
section "Python"
if ! command -v python3 &>/dev/null || [[ $(python3 -c "import sys; print(sys.version_info >= (3,11))") != "True" ]]; then
  info "Installing Python 3.11 via Homebrew…"
  brew install python@3.11
else
  info "Python $(python3 --version) already available."
fi

# ── 3. BlackHole virtual audio ────────────────────────────────────────────────
section "BlackHole Virtual Audio Devices"
info "Installing BlackHole 2ch and 16ch (virtual audio drivers)…"
brew install --cask blackhole-2ch 2>/dev/null && info "BlackHole 2ch installed." || warn "BlackHole 2ch already installed or failed."
brew install --cask blackhole-16ch 2>/dev/null && info "BlackHole 16ch installed." || warn "BlackHole 16ch already installed or failed."

echo ""
warn "IMPORTANT — Manual audio routing steps (do this in macOS settings):"
echo ""
echo "  Step 1: Open 'Audio MIDI Setup' (Spotlight → 'Audio MIDI Setup')"
echo ""
echo "  Step 2: Create a Multi-Output Device:"
echo "    • Click '+' → 'Create Multi-Output Device'"
echo "    • Check: Built-in Output (your real speakers/headphones)"
echo "    • Check: BlackHole 2ch"
echo "    • Name it 'Meeting Monitor'"
echo ""
echo "  Step 3: Set System Output to 'Meeting Monitor':"
echo "    • System Preferences → Sound → Output → 'Meeting Monitor'"
echo "    (This routes meeting audio to both your ears AND BlackHole 2ch)"
echo ""
echo "  Step 4: Configure your meeting app:"
echo "    Google Meet / Zoom → Settings → Audio:"
echo "    • Microphone: BlackHole 16ch   ← bot speaks through this"
echo "    • Speaker:    Meeting Monitor  ← you can hear the meeting"
echo ""
echo "  The bot captures audio from BlackHole 2ch (INPUT_DEVICE in .env)"
echo "  The bot speaks through BlackHole 16ch (OUTPUT_DEVICE in .env)"
echo ""

# ── 4. Python virtual environment ─────────────────────────────────────────────
section "Python Environment"
if [ ! -d ".venv" ]; then
  info "Creating virtual environment…"
  python3 -m venv .venv
fi
source .venv/bin/activate
info "Installing Python dependencies…"
pip install --upgrade pip -q
pip install -r requirements.txt

# ── 5. Playwright browsers ────────────────────────────────────────────────────
section "Playwright"
info "Installing Playwright browsers (Chromium + Chrome)…"
playwright install chromium
# Try to install real Chrome (better WebRTC support)
playwright install chrome 2>/dev/null || warn "Chrome install optional — Chromium will be used."

# ── 6. .env file ──────────────────────────────────────────────────────────────
section ".env Configuration"
if [ ! -f ".env" ]; then
  cp .env.example .env
  warn ".env created from .env.example — fill in your API keys before running!"
else
  info ".env already exists."
fi

# ── Done ──────────────────────────────────────────────────────────────────────
section "Setup Complete"
echo ""
echo "  Next steps:"
echo "  1. Complete the audio routing steps above (Audio MIDI Setup)"
echo "  2. Edit .env with your API keys and CARTESIA_VOICE_ID"
echo "  3. Activate venv:  source .venv/bin/activate"
echo "  4. List devices:   python main.py --list-devices"
echo "  5. List voices:    python main.py --list-voices"
echo "  6. Run the bot:"
echo "       python3 main.py --url 'https://meet.google.com/xxx-xxxx-xxx'"
echo "       python3 main.py --url 'https://zoom.us/j/1234567890'"
echo "       python3 main.py --no-browser   # test with system mic"
echo ""
