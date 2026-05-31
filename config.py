"""Central configuration loaded from environment variables."""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    # API keys
    openai_key: str = field(default_factory=lambda: _require("OPENAI_API_KEY"))
    deepgram_key: str = field(default_factory=lambda: _require("DEEPGRAM_API_KEY"))
    cartesia_key: str = field(default_factory=lambda: _require("CARTESIA_API_KEY"))
    cartesia_voice_id: str = field(default_factory=lambda: _require("CARTESIA_VOICE_ID"))
    cartesia_model_id: str = field(default_factory=lambda: os.getenv("CARTESIA_MODEL_ID", "sonic-2"))

    # Meeting
    meeting_url: str = field(default_factory=lambda: os.getenv("MEETING_URL", ""))
    bot_name: str = field(default_factory=lambda: os.getenv("BOT_NAME", "AI Assistant"))
    chrome_user_data_dir: str = field(
        default_factory=lambda: os.path.expanduser(os.getenv("CHROME_USER_DATA_DIR", ""))
    )

    # Audio devices
    input_device: str = field(default_factory=lambda: os.getenv("INPUT_DEVICE", "BlackHole 2ch"))
    output_device: str = field(default_factory=lambda: os.getenv("OUTPUT_DEVICE", "BlackHole 16ch"))
    sample_rate: int = 16000
    tts_sample_rate: int = 44100

    # Response behaviour
    response_mode: str = field(default_factory=lambda: os.getenv("RESPONSE_MODE", "question"))
    wake_word: str = field(default_factory=lambda: os.getenv("WAKE_WORD", "hey assistant"))
    silence_ms: int = field(default_factory=lambda: int(os.getenv("SILENCE_MS", "1200")))

    # AI persona
    system_prompt: str = field(default_factory=lambda: os.getenv(
        "SYSTEM_PROMPT",
        "You are a helpful AI assistant in a meeting. "
        "Give concise, accurate answers in 1-3 sentences unless more detail is needed."
    ))


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise EnvironmentError(
            f"Required environment variable '{key}' is not set. "
            f"Copy .env.example to .env and fill in your values."
        )
    return val
