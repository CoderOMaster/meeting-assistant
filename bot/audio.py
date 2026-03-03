"""Audio capture (from virtual loopback) and playback (into virtual mic)."""

import asyncio
import io
import queue
import threading
from typing import AsyncIterator, Optional

import numpy as np
import sounddevice as sd
import soundfile as sf


def list_devices() -> None:
    """Print all available audio devices — useful for finding BlackHole names."""
    print("\nAvailable audio devices:")
    print("-" * 60)
    for i, dev in enumerate(sd.query_devices()):
        tag = ""
        if dev["max_input_channels"] > 0:
            tag += " [IN]"
        if dev["max_output_channels"] > 0:
            tag += " [OUT]"
        print(f"  {i:2d}: {dev['name']}{tag}")
    print("-" * 60)


def find_device(name: str, kind: str) -> Optional[int]:
    """Return device index by partial name match, or None for the system default.

    kind: 'input' | 'output'
    Passing name='' or name='default' always returns None (sounddevice default).
    """
    if not name or name.lower() == "default":
        return None
    for i, dev in enumerate(sd.query_devices()):
        if name.lower() in dev["name"].lower():
            if kind == "input" and dev["max_input_channels"] > 0:
                return i
            if kind == "output" and dev["max_output_channels"] > 0:
                return i
    # Device not found — fall back to system default with a warning
    default_dev = sd.query_devices(kind=kind)
    print(
        f"[audio] Warning: device '{name}' not found as {kind}. "
        f"Falling back to system default: '{default_dev['name']}'. "
        f"Run `python main.py --list-devices` to see all options."
    )
    return None


class AudioCapture:
    """Captures audio from a virtual loopback device (BlackHole 2ch).

    The macOS Multi-Output Device routes the meeting audio to BlackHole 2ch,
    which this class reads and forwards as raw PCM int16 chunks.
    """

    def __init__(self, device_name: str, sample_rate: int = 16000, chunk_ms: int = 100):
        self.device_name = device_name
        self.sample_rate = sample_rate
        self.blocksize = int(sample_rate * chunk_ms / 1000)
        self._device_idx: Optional[int] = None
        self._resolved: bool = False
        self._q: queue.Queue = queue.Queue()
        self._stream: Optional[sd.InputStream] = None

    def _resolve_device(self) -> None:
        if not self._resolved:
            self._device_idx = find_device(self.device_name, "input")
            self._resolved = True

    def _sd_callback(self, indata: np.ndarray, frames: int, time, status) -> None:
        if status:
            print(f"[audio capture] {status}")
        # Convert float32 → int16 for Deepgram
        pcm = (indata[:, 0] * 32767).astype(np.int16)
        self._q.put_nowait(pcm.tobytes())

    async def stream(self) -> AsyncIterator[bytes]:
        """Async generator that yields raw PCM int16 audio chunks."""
        self._resolve_device()
        loop = asyncio.get_event_loop()

        # Use an asyncio queue so we don't block the event loop
        async_q: asyncio.Queue[bytes] = asyncio.Queue()

        def _enqueue(data: bytes) -> None:
            loop.call_soon_threadsafe(async_q.put_nowait, data)

        def _sd_cb(indata, frames, time, status):
            if status:
                print(f"[audio capture] {status}")
            pcm = (indata[:, 0] * 32767).astype(np.int16)
            _enqueue(pcm.tobytes())

        with sd.InputStream(
            device=self._device_idx,
            channels=1,
            samplerate=self.sample_rate,
            dtype="float32",
            blocksize=self.blocksize,
            callback=_sd_cb,
        ):
            print(f"[audio] Capturing from: '{self.device_name}'")
            while True:
                chunk = await async_q.get()
                yield chunk


class AudioPlayer:
    """Plays synthesized audio into the virtual microphone device (BlackHole 16ch).

    The meeting software is configured to use BlackHole 16ch as its microphone,
    so anything played here is heard by other participants as the bot's voice.
    """

    def __init__(self, device_name: str, sample_rate: int = 22050):
        self.device_name = device_name
        self.sample_rate = sample_rate
        self._device_idx: Optional[int] = None
        self._resolved: bool = False
        self._lock = asyncio.Lock()

    def _resolve_device(self) -> None:
        if not self._resolved:
            self._device_idx = find_device(self.device_name, "output")
            self._resolved = True

    async def play_wav(self, wav_bytes: bytes) -> None:
        """Play WAV audio bytes through the virtual mic device."""
        self._resolve_device()
        async with self._lock:  # serialise playback
            await asyncio.to_thread(self._blocking_play, wav_bytes)

    def _blocking_play(self, wav_bytes: bytes) -> None:
        buf = io.BytesIO(wav_bytes)
        data, sr = sf.read(buf, dtype="float32")
        if data.ndim == 1:
            data = data[:, np.newaxis]
        sd.play(data, samplerate=sr, device=self._device_idx)
        sd.wait()

    async def play_pcm_f32(self, pcm_bytes: bytes, sample_rate: int) -> None:
        """Play raw float32 PCM audio bytes."""
        self._resolve_device()
        async with self._lock:
            audio = np.frombuffer(pcm_bytes, dtype=np.float32)
            await asyncio.to_thread(
                sd.play, audio, samplerate=sample_rate, device=self._device_idx
            )
            await asyncio.to_thread(sd.wait)

    @property
    def is_playing(self) -> bool:
        return self._lock.locked()
