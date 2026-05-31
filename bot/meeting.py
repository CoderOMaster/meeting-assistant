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

    When `user_data_dir` is provided the bot reuses an existing Chrome profile
    (cookies, Google sign-in, etc.) via Playwright's persistent context.
    This is required for Google Meet, which blocks anonymous/guest joins.

    Audio routing relies on the OS virtual device setup (BlackHole).
    """

    def __init__(
        self,
        meeting_url: str,
        bot_name: str = "AI Assistant",
        mic_device: str = "BlackHole 16ch",
        headless: bool = False,
        user_data_dir: str = "",
    ):
        self.meeting_url = meeting_url
        self.bot_name = bot_name
        self.mic_device = mic_device
        self.headless = headless
        self.user_data_dir = user_data_dir
        self.platform = detect_platform(meeting_url)

        self._playwright = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    # Used for anonymous/guest joins only — fake media avoids permission dialogs
    _GUEST_ARGS = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--use-fake-ui-for-media-stream",
        "--disable-features=VizDisplayCompositor",
    ]

    # With a real profile we must NOT inject fake media — it would replace
    # BlackHole with a silent fake stream.  Permissions are already stored in
    # the profile so no extra flags are needed.
    # --disable-blink-features=AutomationControlled removes the flag that
    # Google Meet (and other sites) check to detect Playwright automation.
    _PROFILE_ARGS = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-blink-features=AutomationControlled",
        "--disable-features=VizDisplayCompositor",
    ]

    # JS injected before any page script runs.
    # 1. Removes Playwright automation fingerprints.
    # 2. Overrides getUserMedia so the bot's AudioContext becomes the mic
    #    stream — this means Chrome captures whatever Python injects via
    #    window.__botPlay() instead of a real mic device.
    #    BlackHole 16ch is no longer needed for the bot's voice.
    _STEALTH_SCRIPT = """
    (function () {
        // ── Anti-detection ──────────────────────────────────────────────────
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

        // ── Audio injection ──────────────────────────────────────────────────
        var _ctx = null, _dest = null, _src = null;

        function _getCtx() {
            if (!_ctx || _ctx.state === 'closed') {
                _ctx = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: 44100 });
                _dest = _ctx.createMediaStreamDestination();
            }
            if (_ctx.state === 'suspended') _ctx.resume().catch(function(){});
            return _ctx;
        }

        // Replace getUserMedia so Meet receives our AudioContext stream as mic
        var _orig = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
        navigator.mediaDevices.getUserMedia = function (constraints) {
            if (constraints && constraints.audio) {
                _getCtx();
                return Promise.resolve(_dest.stream);
            }
            return _orig(constraints);
        };

        // Called from Python to play raw float32-LE PCM into the meeting
        window.__botPlay = function (b64, sr, ch) {
            try {
                var ctx = _getCtx();
                var bin = atob(b64);
                var raw = new Uint8Array(bin.length);
                for (var i = 0; i < bin.length; i++) raw[i] = bin.charCodeAt(i);
                var f32 = new Float32Array(raw.buffer);
                var frames = Math.floor(f32.length / ch);
                var buf = ctx.createBuffer(ch, frames, sr);
                for (var c = 0; c < ch; c++) {
                    var ch_data = buf.getChannelData(c);
                    for (var i = 0; i < frames; i++) ch_data[i] = f32[i * ch + c];
                }
                if (_src) { try { _src.stop(); } catch(e) {} }
                _src = ctx.createBufferSource();
                _src.buffer = buf;
                _src.connect(_dest);
                _src.start(0);
                _src.onended = function () { _src = null; };
                return frames / sr;
            } catch (e) {
                console.error('[bot] __botPlay error:', e);
                return 0;
            }
        };

        // Called to interrupt mid-sentence
        window.__botStop = function () {
            if (_src) { try { _src.stop(); } catch(e) {} _src = null; }
        };
    })();
    """

    async def launch(self) -> None:
        """Open browser, grant permissions, and join the meeting."""
        self._playwright = await async_playwright().start()

        if self.user_data_dir:
            import os as _os
            if not _os.path.isdir(self.user_data_dir):
                raise RuntimeError(
                    f"Chrome profile directory not found: {self.user_data_dir}\n"
                    "Create it by running Chrome once with that path and signing in:\n"
                    f"  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome "
                    f"--user-data-dir=\"{self.user_data_dir}\" --no-first-run\n"
                    "Sign into Google, then close Chrome and re-run the bot."
                )
            print(f"[meeting] Using Chrome profile: {self.user_data_dir}")
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=self.user_data_dir,
                channel="chrome",
                headless=self.headless,
                args=self._PROFILE_ARGS,
                permissions=["camera", "microphone"],
            )
            await self._context.add_init_script(self._STEALTH_SCRIPT)
            pages = self._context.pages
            self._page = pages[0] if pages else await self._context.new_page()
        else:
            print("[meeting] No CHROME_USER_DATA_DIR set — joining as guest")
            self._browser = await self._playwright.chromium.launch(
                channel="chrome",
                headless=self.headless,
                args=self._GUEST_ARGS,
            )
            self._context = await self._browser.new_context(
                permissions=["camera", "microphone"],
            )
            await self._context.add_init_script(self._STEALTH_SCRIPT)
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
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    # ── Google Meet ───────────────────────────────────────────────────────────

    async def _join_google_meet(self) -> None:
        page = self._page

        await page.goto(self.meeting_url, wait_until="domcontentloaded")
        await asyncio.sleep(3)

        # Fail fast if Meet explicitly blocks this account/link
        if await page.locator("text=You can't join this video call").is_visible():
            raise RuntimeError(
                "Google Meet blocked the join: 'You can't join this video call'.\n"
                "  - The meeting link may be expired or already ended.\n"
                "  - The meeting may be restricted to a specific Google Workspace.\n"
                "  - Try creating a fresh meeting at https://meet.new and using that URL."
            )

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

    # ── Mic control ───────────────────────────────────────────────────────────

    async def set_mic_muted(self, muted: bool) -> None:
        """Mute or unmute the microphone in the meeting UI.

        Google Meet's mic button says "Turn off microphone" when the mic is ON
        and "Turn on microphone" when it is OFF — so we look for the opposite
        label of the state we want to reach.
        """
        if not self._page or self._page.is_closed():
            return
        try:
            if muted:
                selectors = [
                    '[aria-label="Turn off microphone"]',
                    '[data-is-muted="false"][aria-label*="microphone" i]',
                ]
            else:
                selectors = [
                    '[aria-label="Turn on microphone"]',
                    '[data-is-muted="true"][aria-label*="microphone" i]',
                ]
            for sel in selectors:
                btn = self._page.locator(sel).first
                if await btn.is_visible(timeout=400):
                    await btn.click()
                    action = "muted" if muted else "unmuted"
                    print(f"[meeting] Mic {action} (selector: {sel})")
                    return
            print(f"[meeting] Warning: mic {'mute' if muted else 'unmute'} button not found — mic may already be in target state")
        except Exception as exc:
            print(f"[meeting] Mic toggle error: {exc}")

    async def mute_mic(self) -> None:
        await self.set_mic_muted(True)

    async def unmute_mic(self) -> None:
        await self.set_mic_muted(False)
        # Give Chrome's audio pipeline time to open the capture gate before
        # audio starts streaming — without this the first ~300ms gets dropped.
        await asyncio.sleep(0.4)

    async def play_audio(
        self, pcm_bytes: bytes, sample_rate: int = 44100, channels: int = 1
    ) -> float:
        """Inject raw float32-LE PCM directly into the meeting mic stream.

        Returns the audio duration in seconds so the caller can wait.
        Requires the _STEALTH_SCRIPT getUserMedia override to be active.
        """
        if not self._page or self._page.is_closed():
            return 0.0
        import base64
        b64 = base64.b64encode(pcm_bytes).decode()
        try:
            duration = await self._page.evaluate(
                "([b64, sr, ch]) => window.__botPlay(b64, sr, ch)",
                [b64, sample_rate, channels],
            )
            return float(duration or 0)
        except Exception as exc:
            print(f"[meeting] Audio inject error: {exc}")
            return 0.0

    async def stop_audio(self) -> None:
        """Stop any currently playing bot audio (for interruptions)."""
        if not self._page or self._page.is_closed():
            return
        try:
            await self._page.evaluate("() => window.__botStop()")
        except Exception:
            pass

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
