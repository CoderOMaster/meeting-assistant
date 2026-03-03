# Voice AI Meeting Bot

An AI-powered bot that joins Google Meet or Zoom, listens to the conversation in real time, generates responses using GPT-4o, and speaks back using **your cloned voice** from Cartesia AI.

---

## How It Works

```
Meeting participants speak
        │
        ▼
  Browser (Chrome)  ──── audio output ────►  BlackHole 2ch (virtual loopback)
        │                                              │
        │                                             ▼
        │                                    Python captures audio
        │                                             │
        │                                             ▼
        │                                   Deepgram STT (nova-3)
        │                                             │
        │                                    final transcript
        │                                             │
        │                                             ▼
        │                                   GPT-4o generates reply
        │                                             │
        │                                             ▼
        │                                Cartesia TTS → your cloned voice
        │                                             │
        │                                             ▼
        │                              Python plays audio to BlackHole 16ch
        │                                             │
        ▼                                             │
  Browser (Chrome)  ◄─── mic input ─────────────────┘
        │
        ▼
  Other participants hear your cloned voice
```

### Component Breakdown

| Layer | Technology | Purpose |
|---|---|---|
| Meeting join | Playwright (Chrome) | Automates joining Google Meet / Zoom via browser |
| Audio capture | BlackHole 2ch + sounddevice | Captures meeting audio as a loopback virtual device |
| Speech-to-text | Deepgram `nova-3` | Streams audio and returns real-time transcripts |
| AI agent | OpenAI `gpt-4o` | Generates contextual responses with conversation memory |
| Text-to-speech | Cartesia `sonic-2` | Synthesizes replies using your cloned voice (SSE streaming) |
| Audio playback | BlackHole 16ch + sounddevice | Injects bot audio as a virtual microphone into the meeting |

---

## Prerequisites

- **macOS** (audio routing uses BlackHole virtual drivers)
- **Python 3.11+**
- **Google Chrome** installed
- API keys for:
  - [OpenAI](https://platform.openai.com/api-keys)
  - [Deepgram](https://console.deepgram.com/)
  - [Cartesia](https://play.cartesia.ai/) — with a cloned voice already created

---

## Installation

### 1. Run the setup script

```bash
chmod +x setup.sh
./setup.sh
```

This installs:
- BlackHole 2ch and 16ch virtual audio drivers (via Homebrew)
- Python virtual environment with all dependencies
- Playwright + Chrome browser

### 2. Configure macOS audio routing (required — one time only)

The bot uses two BlackHole virtual devices to route audio without feedback loops.

**Step 1 — Open Audio MIDI Setup**
```
Spotlight → "Audio MIDI Setup"
```

**Step 2 — Create a Multi-Output Device**
- Click `+` at the bottom left → **Create Multi-Output Device**
- Check both:
  - `Built-in Output` (your speakers/headphones — so you can monitor)
  - `BlackHole 2ch`
- Rename it `Meeting Monitor`

**Step 3 — Set system audio output**
```
System Settings → Sound → Output → Meeting Monitor
```
> This routes all system audio (including meeting participants) to both your ears and BlackHole 2ch, where Python can capture it.

**Step 4 — Configure your meeting app**

| Setting | Value |
|---|---|
| Microphone | BlackHole 16ch |
| Speaker | Meeting Monitor |

> The bot speaks through BlackHole 16ch. Meeting participants hear your cloned voice as if it came from the microphone.

### 3. Fill in your `.env`

```bash
cp .env.example .env
```

Edit `.env`:

```env
OPENAI_API_KEY=sk-...
DEEPGRAM_API_KEY=...
CARTESIA_API_KEY=sk_car_...
CARTESIA_VOICE_ID=<your-cloned-voice-id>
```

To find your Cartesia voice ID:
```bash
source .venv/bin/activate
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

### Test without joining a meeting (system mic only)

```bash
python main.py --no-browser
```

### Override the response mode at runtime

```bash
python main.py --url "https://meet.google.com/..." --mode always
python main.py --url "https://meet.google.com/..." --mode question
python main.py --url "https://meet.google.com/..." --mode wake_word
```

### Run the browser headless (no visible window)

```bash
python main.py --url "https://meet.google.com/..." --headless
```

### Utility commands

```bash
python main.py --list-devices   # show all audio devices (find BlackHole names)
python main.py --list-voices    # list your Cartesia voices with their IDs
```

---

## Configuration Reference

All settings live in `.env`. The table below covers every option.

### API Keys

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | Yes | OpenAI API key |
| `DEEPGRAM_API_KEY` | Yes | Deepgram API key |
| `CARTESIA_API_KEY` | Yes | Cartesia API key |
| `CARTESIA_VOICE_ID` | Yes | ID of your cloned voice in Cartesia |
| `CARTESIA_MODEL_ID` | No | Cartesia model (default: `sonic-2`) |

### Meeting

| Variable | Default | Description |
|---|---|---|
| `MEETING_URL` | — | Google Meet or Zoom URL to join |
| `BOT_NAME` | `AI Assistant` | Display name shown in the meeting |

### Audio Devices

| Variable | Default | Description |
|---|---|---|
| `INPUT_DEVICE` | `BlackHole 2ch` | Device Python reads from (loopback of meeting audio) |
| `OUTPUT_DEVICE` | `BlackHole 16ch` | Device Python writes to (becomes the bot's microphone) |
| `SILENCE_MS` | `1200` | Milliseconds of silence before an utterance is considered complete |

### Response Behaviour

| Variable | Default | Description |
|---|---|---|
| `RESPONSE_MODE` | `question` | When to respond — see modes below |
| `WAKE_WORD` | `hey assistant` | Trigger phrase when mode is `wake_word` |

#### Response Modes

| Mode | Bot responds when… |
|---|---|
| `question` | The transcript ends with `?` |
| `wake_word` | The transcript contains `WAKE_WORD` |
| `always` | Any complete utterance is detected |

### Bot Persona

| Variable | Default | Description |
|---|---|---|
| `SYSTEM_PROMPT` | See `.env.example` | System prompt sent to GPT-4o on every request |

---

## Project Structure

```
voice-ai-project/
├── main.py              # CLI entry point — parses args, starts everything
├── config.py            # Dataclass loaded from .env
├── requirements.txt     # Python dependencies
├── setup.sh             # One-time macOS setup script
├── .env.example         # Template — copy to .env and fill in keys
└── bot/
    ├── audio.py         # AudioCapture (BlackHole 2ch) + AudioPlayer (BlackHole 16ch)
    ├── transcriber.py   # Deepgram asyncwebsocket streaming STT
    ├── agent.py         # GPT-4o agent with rolling conversation history
    ├── synthesizer.py   # Cartesia TTS — both batch and SSE streaming
    ├── meeting.py       # Playwright browser bot for Google Meet + Zoom
    └── pipeline.py      # VoiceAIPipeline — state machine orchestrating all components
```

### Key Files in Detail

**[bot/pipeline.py](bot/pipeline.py)** — The core state machine:
```
IDLE → LISTENING → THINKING → SPEAKING → LISTENING → …
```
Transcripts are ignored while the bot is speaking, preventing audio feedback loops. Responses are queued so the bot never interrupts itself mid-sentence.

**[bot/meeting.py](bot/meeting.py)** — Playwright automation that:
- Detects whether the URL is Google Meet or Zoom
- Navigates to the meeting and dismisses sign-in prompts
- Enters the bot's display name as a guest
- Mutes the browser's own mic/camera (the bot uses the virtual device instead)
- Clicks join and waits for the meeting UI to load

**[bot/transcriber.py](bot/transcriber.py)** — Deepgram streaming with:
- `nova-3` model for highest accuracy
- `interim_results=True` — prints partial transcripts as people speak
- `endpointing=300` — triggers a final transcript after 300ms of silence
- VAD events to detect speech start/end

**[bot/synthesizer.py](bot/synthesizer.py)** — Cartesia TTS with:
- `synthesize()` — blocking, returns full WAV bytes
- `synthesize_streaming()` — SSE stream, yields PCM chunks for lowest latency playback

**[bot/agent.py](bot/agent.py)** — GPT-4o chat completions with:
- Persistent `_history` list (last 20 turns) so it remembers the conversation
- Thread-safe `asyncio.Lock` preventing concurrent requests
- `add_context()` helper to inject meeting notes mid-session

---

## Troubleshooting

### Bot can't hear the meeting

- Confirm system output is set to `Meeting Monitor` (not Built-in Output)
- Run `python main.py --list-devices` — verify `BlackHole 2ch` appears as an input device
- Check `INPUT_DEVICE` in `.env` matches the exact device name from `--list-devices`

### Meeting participants can't hear the bot

- Confirm the meeting app's microphone is set to `BlackHole 16ch`
- Run `python main.py --list-devices` — verify `BlackHole 16ch` appears as an output device
- Check `OUTPUT_DEVICE` in `.env` matches the exact device name

### Bot joins but `--list-devices` shows no BlackHole devices

BlackHole needs a system restart after installation to register the audio drivers:
```bash
sudo reboot
```

### Deepgram connection errors

- Verify `DEEPGRAM_API_KEY` is correct
- Deepgram's asyncwebsocket API requires `deepgram-sdk>=3.7.0` — check with `pip show deepgram-sdk`

### Cartesia voice not found

- Run `python main.py --list-voices` to see all voices in your account
- Copy the exact ID into `CARTESIA_VOICE_ID` in `.env`
- Ensure your Cartesia account has the voice cloning feature enabled

### Google Meet asks for sign-in

Google Meet sometimes requires a Google account. Options:
1. Sign into Chrome manually before running the bot — Playwright will use the existing session
2. Use a Google account configured in Chrome's default profile
3. Ask the meeting host to allow external participants

### Zoom opens the desktop app instead of browser

The bot navigates to `https://zoom.us/wc/join/<id>` (web client URL) directly, bypassing the app redirect. If it still redirects, add `?prefer=1` to the Zoom URL in `.env`.

---

## How to Clone Your Voice in Cartesia

1. Go to [play.cartesia.ai](https://play.cartesia.ai)
2. Navigate to **Voices** → **Clone a Voice**
3. Upload 1–5 minutes of clean audio of yourself speaking
4. Wait for processing (usually under a minute)
5. Copy the voice ID from the voice detail page
6. Paste it into `CARTESIA_VOICE_ID` in `.env`

---

## Limitations

- **Google Meet guest join** only works for meetings that allow external participants without a Google account. If the host has restricted access, you'll need to be signed in.
- **Zoom** uses the browser web client, which has fewer features than the desktop app. Password-protected meetings are not currently handled.
- **Audio feedback** is mitigated by ignoring transcripts during bot speech, but in very echo-heavy environments you may want to use headphones during testing.
- **Response latency** depends on GPT-4o + Cartesia API speed, typically 1–3 seconds end-to-end.
