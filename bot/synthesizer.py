"""Cartesia TTS synthesizer using your cloned voice (Cartesia SDK v3)."""

from typing import AsyncIterator

from cartesia import AsyncCartesia
from cartesia.types.sse_events import ChunkEvent


class CartesiaSynthesizer:
    """Converts text to speech using a Cartesia cloned voice.

    Uses the official Cartesia SDK which handles auth (Bearer token),
    the correct API version header, and SSE stream parsing automatically.
    """

    def __init__(self, api_key: str, voice_id: str, model_id: str = "sonic-2"):
        self.api_key = api_key
        self.voice_id = "88d827a5-9abd-497e-9364-2f0019532a60"
        self.model_id = model_id
        self.sample_rate = 22050

    def _output_format(self, container: str = "wav") -> dict:
        return {
            "container": container,
            "encoding": "pcm_f32le",
            "sample_rate": self.sample_rate,
        }

    def _voice(self) -> dict:
        return {"mode": "id", "id": self.voice_id}

    async def synthesize(self, text: str) -> bytes:
        """Return complete WAV audio bytes for `text`."""
        async with AsyncCartesia(api_key=self.api_key) as client:
            response = await client.tts.generate(
                model_id=self.model_id,
                transcript=text,
                voice=self._voice(),
                output_format=self._output_format("wav"),
            )
            return response.content

    async def synthesize_streaming(self, text: str) -> AsyncIterator[bytes]:
        """Yield raw float32 PCM chunks as they arrive via Cartesia SSE.

        Starts playing before the full audio is generated for lowest latency.
        """
        async with AsyncCartesia(api_key=self.api_key) as client:
            stream = await client.tts.generate_sse(
                model_id=self.model_id,
                transcript=text,
                voice=self._voice(),
                output_format=self._output_format("raw"),
            )
            async for event in stream:
                if isinstance(event, ChunkEvent) and event.audio:
                    yield event.audio

    async def list_voices(self) -> list[dict]:
        """Return all voices in your Cartesia account (own voices first)."""
        voices = []
        async with AsyncCartesia(api_key=self.api_key) as client:
            # is_owner=True fetches only your cloned/created voices first
            async for voice in client.voices.list(is_owner=True):
                voices.append({"id": voice.id, "name": voice.name, "owned": True})
            # Then fetch all public voices
            async for voice in client.voices.list(is_owner=False):
                if not any(v["id"] == voice.id for v in voices):
                    voices.append({"id": voice.id, "name": voice.name, "owned": False})
        return voices
