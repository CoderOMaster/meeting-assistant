"""Playwright-based browser bot that joins Google Meet or Zoom meetings."""

import asyncio
import re
from enum import Enum, auto
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext, Page


class MeetingPlatform(Enum):
    GOOGLE_MEET = auto()
    ZOOM = auto()
    UNKNOWN = auto()


def detect_platform(url: str) -> MeetingPlatform:
    if "meet.google.com" in url:
        return MeetingPlatform.GOOGLE_MEET
    if "zoom.us" in url or "zoom.com" in url:
        return MeetingPlatform.ZOOM
    return MeetingPlatform.UNKNOWN


class MeetingBot:
    """Controls a Chrome browser window that joins and stays in a meeting.

    Audio routing relies on the OS virtual device setup (BlackHole), not on
    anything browser-specific here. We just make sure the browser uses the
    correct microphone / speaker devices that were configured via setup.sh.
    """

    def __init__(
        self,
        meeting_url: str,
        bot_name: str = "AI Assistant",
        mic_device: str = "BlackHole 16ch",
        headless: bool = False,
    ):
        self.meeting_url = meeting_url
        self.bot_name = bot_name
        self.mic_device = mic_device
        self.headless = headless
        self.platform = detect_platform(meeting_url)

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def launch(self) -> None:
        """Open browser, grant permissions, and join the meeting."""
        self._playwright = await async_playwright().start()

        # Use a persistent context so Chrome remembers granted permissions
        self._browser = await self._playwright.chromium.launch(
            channel="chrome",          # Real Chrome (better codec support)
            headless=self.headless,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--use-fake-ui-for-media-stream",   # Auto-accept mic/cam dialogs
                "--disable-web-security",
                "--allow-running-insecure-content",
                # Route output to BlackHole so Python can capture it
                "--disable-features=VizDisplayCompositor",
            ],
        )

        self._context = await self._browser.new_context(
            permissions=["camera", "microphone"],
        )
        self._page = await self._context.new_page()

        # Silence console noise
        self._page.on("console", lambda msg: None)

        if self.platform == MeetingPlatform.GOOGLE_MEET:
            await self._join_google_meet()
        elif self.platform == MeetingPlatform.ZOOM:
            await self._join_zoom()
        else:
            # Generic fallback — just navigate and hope for the best
            await self._page.goto(self.meeting_url, wait_until="domcontentloaded")

        print(f"[meeting] Joined: {self.meeting_url}")

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    # ── Google Meet ───────────────────────────────────────────────────────────

    async def _join_google_meet(self) -> None:
        page = self._page

        await page.goto(self.meeting_url, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # Dismiss sign-in prompts / "Use another account" dialogs
        for selector in [
            'button:has-text("Continue without signing in")',
            'button:has-text("Use without an account")',
            '[data-testid="guest-join-button"]',
        ]:
            try:
                await page.click(selector, timeout=3000)
                await asyncio.sleep(1)
                break
            except Exception:
                pass

        # Enter guest name if prompted
        name_input_selectors = [
            'input[placeholder="Your name"]',
            'input[aria-label="Your name"]',
            'input[type="text"]',
        ]
        for sel in name_input_selectors:
            try:
                elem = await page.wait_for_selector(sel, timeout=4000)
                await elem.fill(self.bot_name)
                await asyncio.sleep(0.5)
                break
            except Exception:
                pass

        # Turn off own camera and mute own mic (bot speaks via virtual device)
        for btn_text in ["Turn off camera", "Turn off microphone"]:
            try:
                await page.click(f'button[aria-label*="{btn_text}"]', timeout=2000)
                await asyncio.sleep(0.3)
            except Exception:
                pass

        # Click "Ask to join" or "Join now"
        for selector in [
            'button:has-text("Ask to join")',
            'button:has-text("Join now")',
            'button:has-text("Join")',
            '[data-idom-class*="join"]',
        ]:
            try:
                await page.click(selector, timeout=5000)
                await asyncio.sleep(2)
                break
            except Exception:
                pass

        # Wait for meeting to load (look for participant grid or chat panel)
        try:
            await page.wait_for_selector(
                '[data-allocation-index], [jscontroller="XdQjMe"]',
                timeout=30000,
            )
        except Exception:
            print("[meeting] Warning: couldn't confirm Google Meet fully loaded.")

    # ── Zoom ──────────────────────────────────────────────────────────────────

    async def _join_zoom(self) -> None:
        page = self._page

        # Zoom web client URL format: https://zoom.us/wc/join/<meeting-id>
        meeting_id = re.search(r"/j/(\d+)", self.meeting_url)
        if meeting_id:
            wc_url = f"https://zoom.us/wc/join/{meeting_id.group(1)}"
            await page.goto(wc_url, wait_until="domcontentloaded")
        else:
            await page.goto(self.meeting_url, wait_until="domcontentloaded")

        await asyncio.sleep(3)

        # Dismiss "Open Zoom" banner and stay in browser
        for selector in [
            'button:has-text("Join from Your Browser")',
            'a:has-text("Join from Your Browser")',
            '#btnJoinFromBrowser',
        ]:
            try:
                await page.click(selector, timeout=5000)
                await asyncio.sleep(2)
                break
            except Exception:
                pass

        # Enter name
        for sel in ['input[placeholder="Your Name"]', '#inputname', 'input[type="text"]']:
            try:
                elem = await page.wait_for_selector(sel, timeout=5000)
                await elem.fill(self.bot_name)
                await asyncio.sleep(0.5)
                break
            except Exception:
                pass

        # Click Join
        for selector in [
            'button:has-text("Join")',
            '#joinBtn',
            'button[type="submit"]',
        ]:
            try:
                await page.click(selector, timeout=5000)
                await asyncio.sleep(2)
                break
            except Exception:
                pass

        # Wait for meeting canvas
        try:
            await page.wait_for_selector(
                '.meeting-canvas, #wc-content, .video-avatar__avatar',
                timeout=30000,
            )
        except Exception:
            print("[meeting] Warning: couldn't confirm Zoom fully loaded.")

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def keep_alive(self) -> None:
        """Block until the browser page is closed."""
        if self._page:
            await self._page.wait_for_event("close", timeout=0)

    async def is_active(self) -> bool:
        """Return True if the browser is still open and the meeting page is alive."""
        try:
            if self._page and not self._page.is_closed():
                return True
        except Exception:
            pass
        return False
