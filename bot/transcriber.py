"""Real-time speech-to-text via Deepgram streaming WebSocket API (SDK v6)."""

import asyncio
from typing import AsyncIterator, Callable, Awaitable

from deepgram import AsyncDeepgramClient
from deepgram.listen.v1.types import ListenV1Results


TranscriptCallback = Callable[[str, bool], Awaitable[None]]
"""Signature: async def cb(transcript: str, is_final: bool) -> None"""


class DeepgramTranscriber:
    """Streams audio bytes to Deepgram and fires a callback on each transcript.

    is_final=True  → Deepgram is confident the utterance is complete.
    is_final=False → Interim / partial result (good for low-latency display).
    """

    def __init__(self, api_key: str, language: str = "en-US"):
        self.api_key = api_key
        self.language = language

    async def stream(
        self,
        audio_source: AsyncIterator[bytes],
        on_transcript: TranscriptCallback,
    ) -> None:
        """Read audio chunks from `audio_source` and feed to Deepgram.

        `on_transcript` is awaited for every non-empty transcript event.
        Runs until `audio_source` is exhausted or the task is cancelled.
        """
        client = AsyncDeepgramClient(api_key=self.api_key)

        # All params are strings in the v6 SDK
        async with client.listen.v1.connect(
            model="nova-3",
            language=self.language,
            smart_format="true",
            interim_results="true",
            utterance_end_ms="1200",
            vad_events="true",
            endpointing="300",
            encoding="linear16",
            sample_rate="16000",
            channels="1",
        ) as connection:
            print("[transcriber] Deepgram connected — listening…")

            # Send audio in a background task so we can receive concurrently
            async def _send_loop() -> None:
                async for chunk in audio_source:
                    await connection.send_media(chunk)
                await connection.send_close_stream()

            send_task = asyncio.create_task(_send_loop())

            try:
                async for message in connection:
                    if not isinstance(message, ListenV1Results):
                        continue
                    try:
                        text = message.channel.alternatives[0].transcript.strip()
                        if not text:
                            continue
                        is_final = bool(message.is_final)
                        await on_transcript(text, is_final)
                    except Exception as exc:
                        print(f"[transcriber] handler error: {exc}")
            finally:
                send_task.cancel()
                try:
                    await send_task
                except asyncio.CancelledError:
                    pass
                print("[transcriber] Deepgram stream finished.")
