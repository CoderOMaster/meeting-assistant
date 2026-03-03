"""Orchestrates the full voice-AI pipeline:
  AudioCapture → Deepgram STT → Claude Agent → Cartesia TTS → AudioPlayer
"""

import asyncio
import io
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import soundfile as sf

from config import Config
from bot.audio import AudioCapture, AudioPlayer
from bot.transcriber import DeepgramTranscriber
from bot.agent import ClaudeAgent
from bot.synthesizer import CartesiaSynthesizer


@dataclass
class PipelineStats:
    utterances_heard: int = 0
    responses_given: int = 0
    total_response_ms: list[float] = field(default_factory=list)

    def avg_latency_ms(self) -> float:
        if not self.total_response_ms:
            return 0.0
        return sum(self.total_response_ms) / len(self.total_response_ms)


class VoiceAIPipeline:
    """End-to-end meeting voice AI pipeline.

    State machine:
      IDLE → LISTENING → THINKING → SPEAKING → LISTENING → …

    The bot ignores transcripts that arrive while it is speaking (SPEAKING state)
    to prevent self-feedback loops.
    """

    def __init__(self, config: Config):
        self.config = config

        self.capture = AudioCapture(
            device_name=config.input_device,
            sample_rate=config.sample_rate,
        )
        self.player = AudioPlayer(
            device_name=config.output_device,
            sample_rate=config.tts_sample_rate,
        )
        self.transcriber = DeepgramTranscriber(api_key=config.deepgram_key)
        self.agent = ClaudeAgent(
            api_key=config.openai_key,
            system_prompt=config.system_prompt,
        )
        self.synthesizer = CartesiaSynthesizer(
            api_key=config.cartesia_key,
            voice_id=config.cartesia_voice_id,
            model_id=config.cartesia_model_id,
        )

        self._speaking = False
        self._cooldown_until: float = 0.0   # ignore transcripts until this timestamp
        self._last_response: str = ""        # last thing the bot said (for echo detection)
        self._response_queue: asyncio.Queue[str] = asyncio.Queue()
        self._stop_event = asyncio.Event()
        self.stats = PipelineStats()

        # Seconds to stay deaf after the bot finishes speaking.
        # Increase if you hear echo loops; decrease for faster back-and-forth.
        self.POST_SPEECH_COOLDOWN = 3.0

    # ── Echo detection ─────────────────────────────────────────────────────────

    @staticmethod
    def _is_echo(transcript: str, bot_said: str, threshold: float = 0.6) -> bool:
        """Return True if `transcript` looks like an echo of what the bot just said.

        Uses a simple word-overlap ratio — robust to minor STT differences.
        """
        t_words = set(transcript.lower().split())
        b_words = set(bot_said.lower().split())
        if not t_words or not b_words:
            return False
        overlap = len(t_words & b_words) / max(len(t_words), len(b_words))
        return overlap >= threshold

    # ── Response gate ─────────────────────────────────────────────────────────

    def _should_respond(self, text: str) -> bool:
        mode = self.config.response_mode
        if mode == "always":
            return True
        if mode == "question":
            return text.strip().endswith("?")
        if mode == "wake_word":
            return self.config.wake_word.lower() in text.lower()
        return False

    # ── Transcript handler ────────────────────────────────────────────────────

    async def _on_transcript(self, text: str, is_final: bool) -> None:
        """Called by Deepgram for every transcript event."""
        prefix = "[final]" if is_final else "[interim]"
        print(f"  {prefix} {text}")

        if not is_final:
            return

        # Never react while the bot is speaking
        if self._speaking:
            return

        # Stay deaf during post-speech cooldown (mic echo dying down)
        if time.monotonic() < self._cooldown_until:
            print(f"  [cooldown] ignored: {text}")
            return

        # Echo filter: skip if the transcript is clearly the bot's own voice
        if self._last_response and self._is_echo(text, self._last_response):
            print(f"  [echo] ignored: {text}")
            return

        self.stats.utterances_heard += 1

        if not self._should_respond(text):
            return

        # Enqueue for the speaker loop (non-blocking)
        await self._response_queue.put(text)

    # ── Speaker loop ──────────────────────────────────────────────────────────

    async def _speaker_loop(self) -> None:
        """Dequeues transcripts, generates responses, and plays them."""
        while not self._stop_event.is_set():
            try:
                utterance = await asyncio.wait_for(
                    self._response_queue.get(), timeout=1.0
                )
            except asyncio.TimeoutError:
                continue

            t0 = time.monotonic()
            self._speaking = True
            print(f"\n[agent] Thinking about: '{utterance}'")

            try:
                response_text = await self.agent.respond(utterance)
                self._last_response = response_text
                print(f"[agent] Response: {response_text}")

                # Synthesize with streaming for low latency
                audio_chunks: list[bytes] = []
                async for chunk in self.synthesizer.synthesize_streaming(response_text):
                    audio_chunks.append(chunk)

                if audio_chunks:
                    raw_pcm = b"".join(audio_chunks)
                    await self._play_raw_pcm(raw_pcm)

                elapsed_ms = (time.monotonic() - t0) * 1000
                self.stats.responses_given += 1
                self.stats.total_response_ms.append(elapsed_ms)
                print(f"[pipeline] Responded in {elapsed_ms:.0f}ms\n")

            except Exception as exc:
                print(f"[pipeline] Error during response: {exc}")
            finally:
                self._speaking = False
                # Mute the pipeline for a short window so the mic echo of
                # the bot's voice doesn't trigger another response
                self._cooldown_until = time.monotonic() + self.POST_SPEECH_COOLDOWN

    async def _play_raw_pcm(self, pcm_bytes: bytes) -> None:
        """Convert raw float32 PCM from Cartesia into WAV and play."""
        audio = np.frombuffer(pcm_bytes, dtype=np.float32)
        buf = io.BytesIO()
        sf.write(buf, audio, self.synthesizer.sample_rate, format="WAV", subtype="FLOAT")
        buf.seek(0)
        await self.player.play_wav(buf.read())

    # ── Main run ──────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Start the pipeline. Returns when stop() is called or on error."""
        print(f"\n[pipeline] Starting — mode={self.config.response_mode}")
        if self.config.response_mode == "wake_word":
            print(f"[pipeline] Wake word: '{self.config.wake_word}'")

        audio_stream = self.capture.stream()

        # Run transcription and speaker tasks concurrently
        await asyncio.gather(
            self.transcriber.stream(audio_stream, self._on_transcript),
            self._speaker_loop(),
        )

    async def stop(self) -> None:
        self._stop_event.set()

    def print_stats(self) -> None:
        print(
            f"\n[stats] Heard {self.stats.utterances_heard} utterances, "
            f"gave {self.stats.responses_given} responses, "
            f"avg latency {self.stats.avg_latency_ms():.0f}ms"
        )
