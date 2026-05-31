#!/usr/bin/env python3
"""Voice AI Meeting Bot — entry point.
Usage:
  python main.py --url "https://meet.google.com/xxx-xxxx-xxx"
  python main.py --no-browser          # mic-only mode (no meeting join)
  python main.py --list-devices        # print available audio devices
  python main.py --list-voices         # print Cartesia voices
"""

import argparse
import asyncio
import signal
import sys

from config import Config
from bot.audio import list_devices
from bot.meeting import MeetingBot
from bot.pipeline import VoiceAIPipeline


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Voice AI Meeting Bot")
    p.add_argument("--url", help="Meeting URL (Google Meet or Zoom)")
    p.add_argument(
        "--no-browser",
        action="store_true",
        help="Skip joining a meeting; listen from system microphone only",
    )
    p.add_argument(
        "--list-devices",
        action="store_true",
        help="Print available audio devices and exit",
    )
    p.add_argument(
        "--list-voices",
        action="store_true",
        help="Print your Cartesia voices and exit",
    )
    p.add_argument(
        "--mode",
        choices=["always", "question", "wake_word"],
        help="Override RESPONSE_MODE from .env",
    )
    p.add_argument("--headless", action="store_true", help="Run browser in headless mode")
    p.add_argument(
        "--input-device",
        help="Override INPUT_DEVICE — partial name match (e.g. 'MacBook Air Microphone'). "
             "Use --list-devices to see options.",
    )
    p.add_argument(
        "--output-device",
        help="Override OUTPUT_DEVICE — partial name match (e.g. 'BlackHole 16ch'). "
             "Use --list-devices to see options.",
    )
    return p.parse_args()


async def list_cartesia_voices(config: Config) -> None:
    from bot.synthesizer import CartesiaSynthesizer
    synth = CartesiaSynthesizer(config.cartesia_key, config.cartesia_voice_id)
    voices = await synth.list_voices()
    owned = [v for v in voices if v.get("owned")]
    print(f"\n── Your cloned / owned voices ({len(owned)}) ──")
    print("-" * 60)
    for v in owned:
        marker = "  ← ACTIVE (set in .env)" if v["id"] == config.cartesia_voice_id else ""
        print(f"  {v['id']}  {v.get('name', 'unnamed')}{marker}")
    if not owned:
        print("  (none — clone your voice at https://play.cartesia.ai/voices)")
    print("-" * 60)
    print(f"\nCopy the ID above into CARTESIA_VOICE_ID in your .env file.")


async def run(args: argparse.Namespace) -> None:
    config = Config()

    if args.mode:
        config.response_mode = args.mode
    if args.url:
        config.meeting_url = args.url
    if args.input_device:
        config.input_device = args.input_device
    if args.output_device:
        config.output_device = args.output_device

    meeting_bot: MeetingBot | None = None
    pipeline = VoiceAIPipeline(config)

    # ── Graceful shutdown ─────────────────────────────────────────────────────
    loop = asyncio.get_running_loop()

    async def _shutdown(sig_name: str) -> None:
        print(f"\n[main] Received {sig_name} — shutting down…")
        await pipeline.stop()
        if meeting_bot:
            await meeting_bot.close()
        pipeline.print_stats()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(
            sig,
            lambda s=sig: asyncio.create_task(_shutdown(s.name)),
        )

    # ── Join meeting ──────────────────────────────────────────────────────────
    if not args.no_browser:
        if not config.meeting_url:
            print(
                "[main] No meeting URL provided. "
                "Set MEETING_URL in .env or pass --url. "
                "Use --no-browser for mic-only mode."
            )
            sys.exit(1)

        meeting_bot = MeetingBot(
            meeting_url=config.meeting_url,
            bot_name=config.bot_name,
            mic_device=config.output_device,
            headless=args.headless,
            user_data_dir=config.chrome_user_data_dir,
        )
        print(f"[main] Joining: {config.meeting_url}")
        await meeting_bot.launch()

        # JS audio injection: bot voice goes directly into Chrome's mic stream.
        # This bypasses BlackHole 16ch entirely — no virtual device needed.
        pipeline.meeting_play = meeting_bot.play_audio
        pipeline.meeting_stop = meeting_bot.stop_audio

        # Still mute/unmute the Meet mic button so the bot is silent
        # while listening and only transmits when it actually speaks.
        pipeline.on_speak_start = meeting_bot.unmute_mic
        pipeline.on_speak_end = meeting_bot.mute_mic

        # Give the meeting a moment to stabilise audio
        await asyncio.sleep(3)

    print("[main] Pipeline running. Press Ctrl+C to stop.\n")
    try:
        await pipeline.run()
    except asyncio.CancelledError:
        pass
    finally:
        pipeline.print_stats()
        if meeting_bot:
            await meeting_bot.close()


def main() -> None:
    args = parse_args()

    if args.list_devices:
        list_devices()
        return

    if args.list_voices:
        config = Config()
        asyncio.run(list_cartesia_voices(config))
        return

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
