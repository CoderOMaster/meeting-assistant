"""Orchestrates the full voice-AI pipeline:
  AudioCapture → Deepgram STT → Claude Agent → Cartesia TTS → AudioPlayer
"""

import asyncio
import io
import time
from dataclasses import dataclass, field
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
    interruptions: int = 0
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
    to prevent self-feedback loops, with the exception of interruption detection:
    if a human speaks >= INTERRUPT_MIN_WORDS words while the bot is talking the
    bot stops mid-sentence and returns to LISTENING immediately.
    """

    # Minimum words in a human utterance to count as an interruption.
    # Kept at 4 so that short acoustic-echo fragments ("yes a question")
    # don't fire before the echo-detection filter has enough context.
    INTERRUPT_MIN_WORDS = 4
    # Don't accept interruptions for the first N seconds after bot starts speaking.
    # 1.0 s lets the first sentence get out before we listen for real interrupts.
    INTERRUPT_GRACE_SECONDS = 1.0

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
        self._speech_started_at: float = 0.0
        self._cooldown_until: float = 0.0
        self._last_response: str = ""
        self._response_queue: asyncio.Queue[str] = asyncio.Queue()
        self._interrupt_event: asyncio.Event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self.stats = PipelineStats()

        # Optional async callbacks wired up by main.py when a meeting bot is active.
        # on_speak_start  → unmute mic in the meeting UI before TTS plays
        # on_speak_end    → re-mute mic after TTS finishes (or is interrupted)
        # meeting_play    → async (pcm_bytes, sr, ch) → float: JS audio injection
        # meeting_stop    → async (): stop JS audio mid-sentence (interruptions)
        self.on_speak_start = None
        self.on_speak_end = None
        self.meeting_play = None
        self.meeting_stop = None

        # Seconds to stay deaf after the bot finishes speaking normally.
        # Reduced automatically to 0.3 s when the bot was interrupted.
        self.POST_SPEECH_COOLDOWN = 3.0

    # ── Echo detection ─────────────────────────────────────────────────────────

    @staticmethod
    def _is_echo(transcript: str, bot_said: str, threshold: float = 0.6) -> bool:
        # Strip punctuation and lowercase for robust comparison
        import re
        def _words(s: str) -> list:
            return re.sub(r"[^\w\s]", "", s.lower()).split()

        t_words = _words(transcript)
        b_words = _words(bot_said)
        if not t_words or not b_words:
            return False

        # 1. Bag-of-words overlap (catches full-sentence echo and paraphrases)
        t_set, b_set = set(t_words), set(b_words)
        overlap = len(t_set & b_set) / max(len(t_set), len(b_set))
        if overlap >= threshold:
            return True

        # 2. Prefix match: transcript == first N words of what the bot said.
        #    Catches the mic picking up the bot's voice mid-sentence
        #    (e.g. "A question" matching "A question mark is used at...").
        prefix_matches = sum(a == b for a, b in zip(t_words, b_words))
        if prefix_matches / max(len(t_words), 1) >= threshold:
            return True

        return False

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

        # ── Interruption detection ────────────────────────────────────────────
        # Fires on interim transcripts too (low latency) so the bot stops fast.
        if self._speaking:
            time_into_speech = time.monotonic() - self._speech_started_at
            if (
                time_into_speech > self.INTERRUPT_GRACE_SECONDS
                and len(text.split()) >= self.INTERRUPT_MIN_WORDS
                and not self._is_echo(text, self._last_response)
                and not self._interrupt_event.is_set()
            ):
                print(f"  [interrupt] Human spoke over bot: '{text}'")
                self._interrupt_event.set()
            # Don't further process transcripts while bot is speaking;
            # the final version of this utterance will arrive after _speaking=False.
            return

        if not is_final:
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
            self._speech_started_at = time.monotonic()
            self._interrupt_event.clear()
            interrupted = False

            print(f"\n[agent] Thinking about: '{utterance}'")

            try:
                response_text = await self.agent.respond(utterance)
                self._last_response = response_text
                print(f"[agent] Response: {response_text}")

                # Collect TTS chunks — abort early if interrupted during synthesis
                audio_chunks: list[bytes] = []
                async for chunk in self.synthesizer.synthesize_streaming(response_text):
                    if self._interrupt_event.is_set():
                        print("[pipeline] Interrupt during TTS synthesis — aborting")
                        interrupted = True
                        break
                    audio_chunks.append(chunk)

                if audio_chunks and not interrupted:
                    # Unmute meeting mic so participants hear the bot speak
                    if self.on_speak_start:
                        await self.on_speak_start()
                    interrupted = await self._play_raw_pcm_interruptible(
                        b"".join(audio_chunks)
                    )

                if interrupted:
                    self.stats.interruptions += 1
                    print("[pipeline] Interrupted — flushing stale response queue")
                    while not self._response_queue.empty():
                        try:
                            self._response_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                else:
                    elapsed_ms = (time.monotonic() - t0) * 1000
                    self.stats.responses_given += 1
                    self.stats.total_response_ms.append(elapsed_ms)
                    print(f"[pipeline] Responded in {elapsed_ms:.0f}ms\n")

            except Exception as exc:
                print(f"[pipeline] Error during response: {exc}")
            finally:
                self._speaking = False
                # Re-mute mic after speaking (whether completed or interrupted)
                if self.on_speak_end:
                    await self.on_speak_end()
                cooldown = 0.3 if interrupted else self.POST_SPEECH_COOLDOWN
                self._cooldown_until = time.monotonic() + cooldown

    async def _play_raw_pcm_interruptible(self, pcm_bytes: bytes) -> bool:
        """Play Cartesia float32 PCM with interruption support.

        In meeting mode: injects audio directly into Chrome's mic stream via JS.
        In no-browser mode: plays through the configured audio output device.
        Returns True if interrupted, False if played to completion.
        """
        if self.meeting_play:
            # JavaScript injection — audio goes straight into the browser's
            # getUserMedia stream so it bypasses virtual audio device issues.
            duration = await self.meeting_play(
                pcm_bytes, self.synthesizer.sample_rate, 1
            )
            if duration <= 0:
                return False
            # Poll every 50 ms for interruption while the browser plays
            deadline = time.monotonic() + duration + 0.2
            while time.monotonic() < deadline:
                if self._interrupt_event.is_set():
                    if self.meeting_stop:
                        await self.meeting_stop()
                    return True
                await asyncio.sleep(0.05)
            return False

        # Fallback: AudioPlayer → BlackHole / system speakers (--no-browser)
        audio = np.frombuffer(pcm_bytes, dtype=np.float32)
        buf = io.BytesIO()
        sf.write(buf, audio, self.synthesizer.sample_rate, format="WAV", subtype="FLOAT")
        buf.seek(0)
        return await self.player.play_wav_interruptible(buf.read(), self._interrupt_event)

    # ── Main run ──────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Start the pipeline. Returns when stop() is called or on error."""
        print(f"\n[pipeline] Starting — mode={self.config.response_mode}")
        if self.config.response_mode == "wake_word":
            print(f"[pipeline] Wake word: '{self.config.wake_word}'")

        audio_stream = self.capture.stream()

        await asyncio.gather(
            self.transcriber.stream(audio_stream, self._on_transcript),
            self._speaker_loop(),
        )

    async def stop(self) -> None:
        self._stop_event.set()

    def print_stats(self) -> None:
        print(
            f"\n[stats] Heard {self.stats.utterances_heard} utterances, "
            f"gave {self.stats.responses_given} responses "
            f"({self.stats.interruptions} interrupted), "
            f"avg latency {self.stats.avg_latency_ms():.0f}ms"
        )
