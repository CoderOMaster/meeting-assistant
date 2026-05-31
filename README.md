# Voice AI Meeting Bot

An AI-powered bot that joins Google Meet or Zoom on your behalf, listens to the conversation in real time, and speaks back using **your cloned voice** — no manual input required.

---

## How It Works

```
Participants speak
      │
      ▼
Chrome (Playwright)  ──── speaker ────►  Multi-Output Device
                                         ├── MacBook Speakers  (you hear them)
                                         └── BlackHole 2ch  ──► Python captures
                                                                       │
                                                                       ▼
                                                            Deepgram STT (nova-3)
                                                                       │
                                                               final transcript
                                                                       │
                                                                       ▼
                                                             GPT-4 generates reply
                                                                       │
                                                                       ▼
                                                     Cartesia TTS → cloned voice (PCM)
                                                                       │
                                                                       ▼
                                              Python → page.evaluate(__botPlay)
                                                                       │
                                                                       ▼
                                         Chrome AudioContext  ◄── injected audio
                                               │
                                               ▼ (getUserMedia override → bot's "mic")
Chrome (Playwright) ─── mic stream ──────────────────────────────────────────►
      │
      ▼
Participants hear the bot's cloned voice
```

> **No BlackHole 16ch needed for the bot's voice.**
> Audio is injected directly into Chrome's `getUserMedia` stream via JavaScript — more reliable than routing through a virtual device.

### Component breakdown

| Layer | Technology | Purpose |
|---|---|---|
| Meeting join | Playwright + Chrome | Automates Google Meet / Zoom with your signed-in profile |
| Audio capture | BlackHole 2ch + sounddevice | Captures participant audio via loopback |
| Speech-to-text | Deepgram `nova-3` | Real-time streaming transcription |
| AI agent | GPT-4.1-mini | Generates contextual replies with 20-turn memory |
| Text-to-speech | Cartesia `sonic-2` | Synthesizes speech using your cloned voice |
| Audio delivery | JavaScript AudioContext injection | Injects bot audio directly into Chrome's mic stream |
| Interruption | Interim transcript + echo detection | Bot stops mid-sentence when a human speaks |
| Mic control | Playwright click automation | Mutes mic while listening, unmutes when speaking |

---

## Prerequisites

- **macOS** (audio capture uses BlackHole virtual drivers)
- **Python 3.11+**
- **Google Chrome** installed
- API keys for:
  - [Deepgram](https://console.deepgram.com/)
  - [Cartesia](https://play.cartesia.ai/) — with a cloned voice created
  - [OpenAI](https://platform.openai.com/api-keys)

---

## Installation

### 1. Run the setup script

```bash
chmod +x setup.sh
./setup.sh
```

This installs BlackHole 2ch/16ch virtual audio drivers, a Python venv with all dependencies, and Playwright Chrome.

### 2. Configure macOS audio routing (one time)

The bot captures participant audio via BlackHole 2ch. You need a Multi-Output Device that routes Chrome's speaker to both your ears and BlackHole 2ch simultaneously.

**Open Audio MIDI Setup** (Spotlight → "Audio MIDI Setup"):

1. Click **+** → **Create Multi-Output Device**
2. Check both:
   - `MacBook Air Speakers` (or your headphones)
   - `BlackHole 2ch`
3. Rename it `Multi-Output Device 2`

**In Google Meet (while the bot is running), set:**

| Setting | Value |
|---|---|
| Speaker | `Multi-Output Device 2` |
| Microphone | *(leave as-is — managed automatically by the bot)* |

> The mic is managed via JS injection. Chrome's `getUserMedia` is overridden before Meet loads, so it receives the bot's `AudioContext` stream directly. You do not need to select a specific mic device.

### 3. Create a dedicated Chrome profile for the bot (one time)

Google Meet blocks anonymous joins. The bot needs a Chrome profile signed into your Google account.

```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --user-data-dir="$HOME/.chrome-meeting-bot" --no-first-run
```

Sign into your Google account in the Chrome window that opens, then close it. You only need to do this once — the session persists.

### 4. Fill in `.env`

```bash
cp .env.example .env
```

```env
OPENAI_API_KEY=sk-...
DEEPGRAM_API_KEY=...
CARTESIA_API_KEY=sk_car_...
CARTESIA_VOICE_ID=<your-voice-id>

CHROME_USER_DATA_DIR=/Users/<you>/.chrome-meeting-bot
```

To find your Cartesia voice ID:

```bash
source py11/bin/activate   # or your venv name
python main.py --list-voices
```

---

## Usage

### Join a Google Meet

```bash
python main.py --url "https://meet.google.com/xxx-xxxx-xxx"
```

### Join a Zoom meeting

```bash
python main.py --url "https://zoom.us/j/1234567890"
```

### Test locally without a meeting

Verifies transcription and voice synthesis before going live:

```bash
python main.py --no-browser \
  --input-device "MacBook Air Microphone" \
  --output-device "MacBook Air Speakers" \
  --mode always
```

### Override response mode at runtime

```bash
python main.py --url "https://meet.google.com/..." --mode always
python main.py --url "https://meet.google.com/..." --mode question
python main.py --url "https://meet.google.com/..." --mode wake_word
```

### Utility commands

```bash
python main.py --list-devices   # show all audio devices
python main.py --list-voices    # list your Cartesia voices with IDs
```

---

## Configuration Reference

### API Keys

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | OpenAI API key |
| `DEEPGRAM_API_KEY` | Yes | Deepgram API key |
| `CARTESIA_API_KEY` | Yes | Cartesia API key |
| `CARTESIA_VOICE_ID` | Yes | ID of your cloned voice |
| `CARTESIA_MODEL_ID` | No | Cartesia model (default: `sonic-2`) |

### Meeting

| Variable | Default | Description |
|---|---|---|
| `MEETING_URL` | — | Google Meet or Zoom URL |
| `BOT_NAME` | `AI Assistant` | Display name shown in the meeting |
| `CHROME_USER_DATA_DIR` | — | Path to Chrome profile with Google account signed in |

### Audio

| Variable | Default | Description |
|---|---|---|
| `INPUT_DEVICE` | `BlackHole 2ch` | Device Python reads from (loopback of meeting audio) |
| `OUTPUT_DEVICE` | `BlackHole 16ch` | Fallback output device for `--no-browser` mode |
| `SILENCE_MS` | `1200` | Silence gap (ms) before utterance is treated as complete |

### Response Behaviour

| Variable | Default | Description |
|---|---|---|
| `RESPONSE_MODE` | `question` | When the bot responds — see modes below |
| `WAKE_WORD` | `hey keshav` | Trigger phrase for `wake_word` mode |

#### Response modes

| Mode | Bot responds when… |
|---|---|
| `question` | Transcript ends with `?` |
| `wake_word` | Transcript contains `WAKE_WORD` |
| `always` | Any complete utterance is detected |

### Bot Persona

| Variable | Description |
|---|---|
| `SYSTEM_PROMPT` | System message defining the bot's personality and behaviour |

---

## Project Structure

```
meeting-assistant/
├── main.py              # CLI entry point
├── config.py            # Dataclass loaded from .env
├── requirements.txt     # Python dependencies
├── setup.sh             # One-time macOS setup script
├── .env.example         # Template — copy to .env
└── bot/
    ├── audio.py         # AudioCapture (BlackHole 2ch) + AudioPlayer (--no-browser fallback)
    ├── transcriber.py   # Deepgram streaming STT
    ├── agent.py         # GPT-4.1-mini agent with rolling conversation history
    ├── synthesizer.py   # Cartesia TTS — SSE streaming at 44100 Hz
    ├── meeting.py       # Playwright browser bot + JS audio injection
    └── pipeline.py      # VoiceAIPipeline state machine
```

### Key design notes

**JavaScript audio injection** (`bot/meeting.py`)
An init script overrides `navigator.mediaDevices.getUserMedia` before Google Meet loads. When Meet requests a microphone stream it receives the bot's `AudioContext` destination instead of a real device. Python injects synthesized PCM via `window.__botPlay(base64, sampleRate, channels)`. This is more reliable than routing through BlackHole 16ch and works regardless of system audio device configuration.

**Interruption detection** (`bot/pipeline.py`)
The pipeline monitors Deepgram interim transcripts while the bot is speaking. If a human says 4+ words that don't match the bot's current response (prefix/overlap echo check), the bot stops mid-sentence, clears queued responses, and returns to listening with a 0.3 s cooldown (vs 3 s after normal speech).

**Dynamic mic muting** (`bot/meeting.py`)
The bot mutes the mic button in Meet while listening and unmutes just before injecting each response. This prevents participants from hearing silence or ambient noise between turns.

**Echo detection** (`bot/pipeline.py`)
Two-pass check: bag-of-words overlap ratio and prefix match. The prefix match catches the mic picking up the first few words of the bot's own speech in `--no-browser` test mode (real speakers into real mic).

---

## Troubleshooting

### "You can't join this video call"

The meeting link is inaccessible to your account:
- The link may be expired — create a fresh one at [meet.new](https://meet.new)
- The meeting may be restricted to a specific Google Workspace domain
- Ensure `CHROME_USER_DATA_DIR` points to a profile signed in with the invited account

### Chrome profile error: "profile already in use"

Another Chrome process is holding the profile lock. Always use `Ctrl+C` (not `Ctrl+Z`) to stop the bot. To kill a stuck process:

```bash
pkill -f "chrome-meeting-bot"
```

### Bot can't hear participants (no transcripts appearing)

- Confirm Chrome's **Speaker** in Meet is set to `Multi-Output Device 2`
- Verify `Multi-Output Device 2` includes both `MacBook Air Speakers` and `BlackHole 2ch` in Audio MIDI Setup
- Run `python main.py --list-devices` — `BlackHole 2ch` must appear as `[IN]`

### Participants can't hear the bot

- Check terminal for `[meeting] Mic unmuted` before each response — if you see `Warning: mic unmute button not found` Google Meet updated its UI and the selectors need updating in `bot/meeting.py`
- Confirm `CHROME_USER_DATA_DIR` is set and the profile directory exists (`ls $HOME/.chrome-meeting-bot`)
- Make sure Chrome's **Speaker** is `Multi-Output Device 2`, not `BlackHole 16ch` — if speaker and mic are the same virtual device, Meet's AEC will suppress all audio

### Bot responds to its own voice (echo loop)

Only happens in `--no-browser` test mode with real speakers into real mic. Use headphones to break the feedback loop, or switch to `--mode question` so the bot only responds to questions.

### Deepgram connection errors

- Verify `DEEPGRAM_API_KEY` is set in `.env`
- Requires `deepgram-sdk >= 3.7.0`: `pip show deepgram-sdk`

### Cartesia voice not found

```bash
python main.py --list-voices
```

Copy the exact ID shown into `CARTESIA_VOICE_ID` in `.env`.

---

## How to Clone Your Voice

1. Go to [play.cartesia.ai](https://play.cartesia.ai) → **Voices** → **Clone a Voice**
2. Upload 1–5 minutes of clean audio (minimal background noise)
3. Wait for processing (under a minute)
4. Copy the voice ID from the voice detail page
5. Paste it into `CARTESIA_VOICE_ID` in `.env`

---

## Limitations

- **Google Meet access** — requires a Google account with permission to join the specific meeting. Workspace-restricted meetings require the bot profile to be signed into an account in that workspace.
- **Zoom** — uses the browser web client (`/wc/join/`). Password-protected meetings are not currently handled.
- **One meeting at a time** — the bot occupies one Chrome profile. Running multiple instances requires separate profile directories and `.env` files.
- **Response latency** — typically 3–8 seconds end-to-end (LLM + TTS streaming). Latency is dominated by GPT response time.
- **macOS only** — audio capture uses BlackHole virtual drivers. Linux support would require replacing BlackHole 2ch with PulseAudio or JACK loopback.
