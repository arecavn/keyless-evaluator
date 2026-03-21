"""LLM provider backends: OpenAI, Gemini, ChatGPT Web (anonymous), Anthropic."""

from __future__ import annotations

import asyncio
import logging
import math
import os
from abc import ABC, abstractmethod
from datetime import datetime

from models import (
    EvaluationRequest,
    EvaluationResponse,
    ResultScore,
)
from parser import parse_evaluation_response
from presets import PRESETS
from prompts import (
    OUTPUT_FORMAT,
    SYSTEM_PROMPT,
    build_user_prompt,
)


# ---------------------------------------------------------------------------
# LLM response logger — writes raw output to logs/llm.log for tracing
# ---------------------------------------------------------------------------

_llm_logger = logging.getLogger("keyless_evaluator.llm")
_llm_logger_ready = False


def _ensure_llm_logger() -> None:
    global _llm_logger_ready
    if _llm_logger_ready:
        return
    log_dir = os.path.join(os.getcwd(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = logging.FileHandler(os.path.join(log_dir, "llm.log"), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    _llm_logger.addHandler(handler)
    _llm_logger.setLevel(logging.INFO)
    _llm_logger.propagate = False
    _llm_logger_ready = True


# ---------------------------------------------------------------------------
# nDCG helper
# ---------------------------------------------------------------------------

def _compute_ndcg(scores: list[ResultScore], k: int | None = None) -> float:
    """Compute nDCG@k for the scored result list."""
    gains = [s.score.value for s in scores]
    if k:
        gains = gains[:k]
    if not gains:
        return 0.0

    def dcg(g: list[int]) -> float:
        return sum(rel / math.log2(i + 2) for i, rel in enumerate(g))

    ideal = sorted(gains, reverse=True)
    idcg = dcg(ideal)
    return dcg(gains) / idcg if idcg > 0 else 0.0


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class BaseEvaluator(ABC):
    """Abstract LLM evaluator."""

    name: str = "base"
    model: str = ""
    provider: str = ""

    @abstractmethod
    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        ...

    def _system_prompt(self, request: EvaluationRequest) -> str:
        """Return the system prompt.

        Priority: custom prompt > prompt_preset > default SYSTEM_PROMPT.
        Custom prompts and presets get the output format spec appended.
        """
        if request.prompt:
            base = request.prompt + OUTPUT_FORMAT
        elif request.prompt_preset and request.prompt_preset in PRESETS:
            base = PRESETS[request.prompt_preset] + OUTPUT_FORMAT
        else:
            base = SYSTEM_PROMPT
        if request.response_language:
            base += f"\n\nAlways write reason_summary and reason_detail in {request.response_language}."
        return base

    def _build_response(
        self,
        request: EvaluationRequest,
        scores: list[ResultScore],
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        raw_llm_response: str | None = None,
        model_override: str | None = None,
    ) -> EvaluationResponse:
        model = model_override or self.model
        resp = EvaluationResponse(
            input=request.input,
            model=model,
            provider=self.provider,
            scores=scores,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        resp.ndcg = _compute_ndcg(scores)

        if raw_llm_response is not None:
            _ensure_llm_logger()
            _llm_logger.info(
                "provider=%s model=%s tokens=%s/%s input=%r\n%s\n%s\n%s",
                self.provider,
                model,
                prompt_tokens,
                completion_tokens,
                request.input,
                "--- RAW LLM RESPONSE ---",
                raw_llm_response,
                "--- END ---",
            )

        return resp


# ---------------------------------------------------------------------------
# OpenAI / ChatGPT API
# ---------------------------------------------------------------------------

class OpenAIEvaluator(BaseEvaluator):
    """Evaluate using OpenAI API (GPT-4o, GPT-4o-mini, etc.)."""

    provider = "openai"

    def __init__(self, model: str = "gpt-4o", api_key: str | None = None, base_url: str | None = None):
        self.model = model
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._base_url = base_url or os.environ.get("OPENAI_BASE_URL")

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError("openai package not installed. Run: uv add openai") from exc

        client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)

        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self._system_prompt(request)},
                {"role": "user", "content": build_user_prompt(request)},
            ],
            temperature=0.1,
        )

        raw = response.choices[0].message.content or ""
        scores = parse_evaluation_response(raw, request.results)

        usage = response.usage
        return self._build_response(
            request, scores,
            prompt_tokens=usage.prompt_tokens if usage else None,
            completion_tokens=usage.completion_tokens if usage else None,
            raw_llm_response=raw,
        )


# ---------------------------------------------------------------------------
# Google Gemini API
# ---------------------------------------------------------------------------

class GeminiEvaluator(BaseEvaluator):
    """Evaluate using Google Gemini API (free quota via Google AI Studio)."""

    provider = "gemini"

    def __init__(self, model: str = "gemini-2.0-flash", api_key: str | None = None):
        self.model = model
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        try:
            import google.generativeai as genai
        except ImportError as exc:
            raise RuntimeError(
                "google-generativeai package not installed. Run: uv add google-generativeai"
            ) from exc

        genai.configure(api_key=self._api_key)
        model = genai.GenerativeModel(
            model_name=self.model,
            system_instruction=self._system_prompt(request),
        )

        user_prompt = build_user_prompt(request)

        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: model.generate_content(
                user_prompt,
                generation_config=genai.GenerationConfig(temperature=0.1),
            ),
        )

        # For thinking models (e.g. gemini-2.5-pro, gemini-2.0-flash-thinking-exp),
        # parts with thought=True are internal reasoning — skip them, use only output parts.
        raw = ""
        try:
            parts = response.candidates[0].content.parts
            output_parts = [p.text for p in parts if not getattr(p, "thought", False) and p.text]
            raw = "\n".join(output_parts) if output_parts else (response.text or "")
        except Exception:
            raw = response.text or ""

        scores = parse_evaluation_response(raw, request.results)

        usage = getattr(response, "usage_metadata", None)
        return self._build_response(
            request, scores,
            prompt_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
            completion_tokens=getattr(usage, "candidates_token_count", None) if usage else None,
            raw_llm_response=raw,
        )


# ---------------------------------------------------------------------------
# ChatGPT Web Evaluator — anonymous, no account / no API key
# ---------------------------------------------------------------------------

_STEALTH_SCRIPT = """
// 1. Hide webdriver flag
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// 2. Patch userAgent — headless Chrome includes 'HeadlessChrome'
const _realUA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';
Object.defineProperty(navigator, 'userAgent',  {get: () => _realUA});
Object.defineProperty(navigator, 'appVersion', {get: () => _realUA.replace('Mozilla/', '')});

// 3. Realistic language + platform
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'platform',  {get: () => 'MacIntel'});

// 4. Hardware / memory hints
Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
Object.defineProperty(navigator, 'deviceMemory',        {get: () => 8});

// 5. Fake chrome runtime object — missing in headless
window.chrome = window.chrome || {};
window.chrome.runtime = window.chrome.runtime || {};
window.chrome.loadTimes = function(){};
window.chrome.csi = function(){};

// 6. Permissions API — headless returns 'denied' for notifications
const _origPerms = navigator.permissions.query.bind(navigator.permissions);
navigator.permissions.query = (p) =>
  p.name === 'notifications'
    ? Promise.resolve({state: 'default', onchange: null})
    : _origPerms(p);

// NOTE: Do NOT override navigator.plugins or window/screen dimensions.
// Doing so breaks ChatGPT's React streaming renderer (assistant text stays empty).
"""

def _get_stealth_args(headless: bool) -> list[str]:
    """
    Return Chromium launch args tuned for the current environment.

    Visible Mac Chrome: minimal clean args — WAF is suspicious of
    --disable-web-security, --use-gl=swiftshader, and GPU-disable flags on a
    machine that has a real GPU and display.

    Headless / Linux (Docker): full set of flags to avoid GPU crashes and
    Keychain decrypt failures.
    """
    import platform as _platform
    is_mac_visible = _platform.system() == "Darwin" and not headless

    args = [
        "--no-sandbox",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
        "--disable-infobars",
        "--disable-extensions",
        "--window-size=1280,800",
        "--lang=en-US,en",
    ]

    if not is_mac_visible:
        # These flags help bypass detection headlessly but look suspicious to
        # WAF when used with a real visible Chrome on Mac.
        args += [
            "--disable-features=IsolateOrigins,site-per-process",
            "--disable-web-security",
        ]

    if headless:
        # GPU flags only needed when no display / GPU is available (Docker/Linux)
        args += [
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--use-gl=swiftshader",
            # Prevent crash when a Mac-encrypted Chrome profile is loaded on Linux
            "--disable-sync",
            "--disable-background-networking",
        ]

    return args

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# JS selectors tried in order to detect the active ChatGPT model name
_MODEL_DETECTION_JS = """
() => {
    const selectors = [
        '[data-testid="model-switcher-dropdown-button"]',
        'button[aria-haspopup="menu"][id*="model"]',
        'button[aria-label*="model" i]',
        'button[aria-label*="Model" i]',
    ];
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el) {
            const text = (el.innerText || el.textContent || '').trim();
            if (text) return text;
        }
    }
    return null;
}
"""


# Serializes all chatgpt_web requests — browser automation is single-threaded
# (one page at a time). Concurrent requests would clobber each other's navigation.
_CHATGPT_LOCK = asyncio.Lock()

# CDP mode: persistent Playwright + page kept alive across requests.
# async with async_playwright() closes everything on exit, so we use .start() instead.
_cdp_pw = None          # Playwright instance (never closed)
_cdp_persistent_page = None  # reused tab between requests

_DEFAULT_PROFILE_DIR = os.path.expanduser("~/.local/share/keyless-eval/chatgpt")
_SESSION_MARKER = "keyless-eval-session"
_DEFAULT_CHATGPT_URL = "https://chatgpt.com/"


def _build_prompt_header(request: "EvaluationRequest") -> str:
    """Return a one-line header for web-provider prompts: '[TAG | YYYY-MM-DD HH:MM:SS]'."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if request.tag:
        return f"[{request.tag} | {ts}]\n\n"
    return f"[{ts}]\n\n"


def _chatgpt_profile_dir() -> str:
    return os.environ.get("CHATGPT_PROFILE_DIR", _DEFAULT_PROFILE_DIR)


def _profile_has_session(profile_dir: str) -> bool:
    """True when Chrome has written its Default/ profile dir (happens after any first session)."""
    return os.path.isdir(os.path.join(profile_dir, "Default"))


async def _fill_contenteditable(page, locator, text: str, headless: bool) -> None:
    """
    Insert text into a contenteditable element, picking the right strategy:
    - Visible mode: pbcopy/xclip + keyboard paste (reliable, handles any length)
    - Headless mode: execCommand insertText (system clipboard is inaccessible headlessly)
    """
    import subprocess, platform

    if not headless:
        if platform.system() == "Darwin":
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
            await locator.click()
            await page.keyboard.press("Meta+v")
        else:
            try:
                subprocess.run(["xclip", "-selection", "clipboard"],
                               input=text.encode("utf-8"), check=True,
                               stderr=subprocess.DEVNULL)
                await locator.click()
                await page.keyboard.press("Control+v")
            except (FileNotFoundError, subprocess.CalledProcessError):
                # xclip unavailable or no $DISPLAY — use execCommand fallback
                await locator.click()
                await page.evaluate(
                    """(text) => {
                        const el = document.activeElement;
                        el.focus();
                        document.execCommand('selectAll', false, null);
                        document.execCommand('insertText', false, text);
                    }""",
                    text,
                )
    else:
        # Headless: execCommand still works and triggers React/contenteditable state
        await locator.click()
        await page.evaluate(
            """(text) => {
                const el = document.activeElement;
                el.focus();
                document.execCommand('selectAll', false, null);
                document.execCommand('insertText', false, text);
            }""",
            text,
        )


class ChatGPTWebEvaluator(BaseEvaluator):
    """
    Evaluate using the ChatGPT web interface via Playwright.

    Supports two modes:
    - **Anonymous** (no profile): temporary dir, always visible or headless per env.
    - **Logged-in** (persistent profile): set CHATGPT_PROFILE_DIR or use the default
      (~/.local/share/keyless-eval/chatgpt). Run with CHATGPT_WEB_LOGIN=1 once to
      open a visible browser, log in manually, then close — the session is saved.
      Subsequent requests run headless automatically.

    Env vars:
      CHATGPT_PROFILE_DIR   Path to persistent Chrome profile (enables logged-in mode)
      CHATGPT_WEB_LOGIN=1   Force visible window (for first-time login)
      CHATGPT_WEB_HEADLESS  Override headless: 0=visible, 1=headless (auto if unset)
    """

    provider = "chatgpt_web"

    def __init__(self, model: str = "auto", timeout: int = 120, headless: bool | None = None):
        self.model = model
        self._timeout = timeout
        self._profile_dir = _chatgpt_profile_dir()
        force_login = os.environ.get("CHATGPT_WEB_LOGIN", "").lower() in ("1", "true", "yes")

        if headless is None:
            env_val = os.environ.get("CHATGPT_WEB_HEADLESS", "")
            if env_val:
                self._headless = env_val.lower() not in ("0", "false", "no")
            else:
                # headless only when a saved session exists and login not forced
                self._headless = _profile_has_session(self._profile_dir) and not force_login
        else:
            self._headless = headless

    async def _launch_browser(self, pw):
        os.makedirs(self._profile_dir, exist_ok=True)
        args = _get_stealth_args(self._headless)

        # Try real system Chrome installations in order (most trusted by WAF).
        # Avoid "Google Chrome for Testing" (Playwright's channel="chrome" on ARM Mac)
        # which WAF detects as a bot.
        _chrome_paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
        ]
        for _exe in _chrome_paths:
            if os.path.isfile(_exe):
                try:
                    _ensure_llm_logger()
                    _llm_logger.info("chatgpt_web: launching real Chrome at %s", _exe)
                    return await pw.chromium.launch_persistent_context(
                        self._profile_dir,
                        executable_path=_exe,
                        headless=self._headless,
                        args=args,
                        user_agent=_USER_AGENT,
                    )
                except Exception as _e:
                    _llm_logger.warning("chatgpt_web: failed to launch %s: %s", _exe, _e)

        # Fallback: channel="chrome" (may be Chrome for Testing on ARM Mac)
        try:
            return await pw.chromium.launch_persistent_context(
                self._profile_dir, channel="chrome",
                headless=self._headless,
                args=args,
                user_agent=_USER_AGENT,
            )
        except Exception:
            pass

        return await pw.chromium.launch_persistent_context(
            self._profile_dir,
            headless=self._headless,
            args=args,
            user_agent=_USER_AGENT,
        )

    async def _navigate_and_interact(self, page, chatgpt_url, is_project_url, reuse_page, full_prompt):
        """Navigate to ChatGPT and fill the prompt. Shared by CDP and launch modes."""
        _TEXTAREA_SEL = "#prompt-textarea, div[contenteditable='true'][data-virtualkeyboard-exclusion], div[contenteditable='true']"

        if reuse_page and is_project_url:
            _llm_logger.info("chatgpt_web: reused tab → navigating to project URL=%s", chatgpt_url)
            await page.goto(chatgpt_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_selector(_TEXTAREA_SEL, timeout=30_000)
            _llm_logger.info("chatgpt_web: ready at %s", page.url)
        elif reuse_page:
            _llm_logger.info("chatgpt_web: reused tab, already at home")
        else:
            await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=30000)
            _llm_logger.info("chatgpt_web: waiting for ChatGPT UI (up to 5 min, solve any WAF challenge manually)...")
            try:
                await page.wait_for_selector(_TEXTAREA_SEL, timeout=300_000)
            except Exception:
                _title = ""
                try:
                    _title = await page.title()
                except Exception:
                    pass
                raise RuntimeError(
                    "ChatGPT did not load within 5 min. "
                    + (f"Page title: {_title!r}. " if _title else "")
                    + "If a WAF challenge appeared, try solving it manually."
                )
            _llm_logger.info("chatgpt_web: ChatGPT UI ready at %s", page.url)

            for dismiss_text in ["Stay logged out", "Start now", "OK"]:
                try:
                    btn = page.get_by_text(dismiss_text, exact=True).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                except Exception:
                    pass

            if is_project_url:
                _llm_logger.info("chatgpt_web: navigating to project URL=%s", chatgpt_url)
                await page.goto(chatgpt_url, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(1000)
                _llm_logger.info("chatgpt_web: arrived at %s title=%r", page.url, await page.title())

        textarea = page.locator("#prompt-textarea").first
        try:
            await textarea.wait_for(state="visible", timeout=20000)
        except Exception:
            for sel in [
                "div[contenteditable='true'][data-virtualkeyboard-exclusion]",
                "div[contenteditable='true']",
                "textarea[placeholder]",
            ]:
                try:
                    textarea = page.locator(sel).first
                    await textarea.wait_for(state="visible", timeout=5000)
                    _llm_logger.info("chatgpt_web: found textarea via fallback %r", sel)
                    break
                except Exception:
                    pass
            else:
                _llm_logger.error("chatgpt_web: textarea not found. title=%s url=%s", await page.title(), page.url)
                raise RuntimeError("ChatGPT input field not found. Check logs/llm.log for details.")

        await _fill_contenteditable(page, textarea, full_prompt, self._headless)
        await page.wait_for_timeout(500)

        sent = False
        try:
            send_btn = page.locator("[data-testid='send-button']").first
            await send_btn.wait_for(state="visible", timeout=5000)
            await send_btn.click()
            sent = True
        except Exception:
            pass
        if not sent:
            await textarea.press("Enter")
            await page.wait_for_timeout(200)

    async def _read_response(self, page) -> tuple[str | None, str]:
        """Wait for ChatGPT to finish streaming and return (detected_model, raw_text)."""
        await page.wait_for_timeout(1500)
        await page.wait_for_function(
            """() => {
                const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
                if (!msgs.length) return false;
                return (msgs[msgs.length - 1].innerText || '').trim().length > 10;
            }""",
            timeout=self._timeout * 1000,
        )
        try:
            await page.locator("[data-testid='stop-button']").wait_for(
                state="hidden", timeout=self._timeout * 1000
            )
        except Exception:
            pass
        await page.wait_for_timeout(800)

        raw_text = await page.evaluate(
            """() => {
                const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
                return msgs.length ? (msgs[msgs.length - 1].innerText || '') : '';
            }"""
        )
        detected_model = None
        try:
            detected_model = await page.evaluate(_MODEL_DETECTION_JS)
        except Exception:
            pass
        return detected_model, raw_text

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright not installed. Run:\n"
                "  uv add playwright\n"
                "  uv run playwright install chromium"
            ) from exc

        system = self._system_prompt(request)
        full_prompt = _build_prompt_header(request) + f"{system}\n\n---\n\n{build_user_prompt(request)}"

        cdp_url = os.environ.get("CHATGPT_CDP_URL", "").strip()

        detected_model: str | None = None
        raw_text = ""

        chatgpt_url = os.environ.get("CHATGPT_URL", _DEFAULT_CHATGPT_URL)
        # Strip /project suffix — navigating to the project root directly opens a new chat
        if chatgpt_url.rstrip("/").endswith("/project"):
            chatgpt_url = chatgpt_url.rstrip("/")[: -len("/project")]
        is_project_url = "/g/g-p-" in chatgpt_url

        async with _CHATGPT_LOCK:
            global _cdp_pw, _cdp_persistent_page
            _cdp_mode = bool(cdp_url)
            _ensure_llm_logger()

            if _cdp_mode:
                # Use a persistent Playwright instance — async with closes pages on exit.
                from playwright.async_api import async_playwright as _apw
                if _cdp_pw is None:
                    _cdp_pw = await _apw().start()

                browser = await _cdp_pw.chromium.connect_over_cdp(cdp_url)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                await context.add_init_script(_STEALTH_SCRIPT)

                # Reuse persistent tab if still alive and on chatgpt.com
                _reuse_page = False
                if _cdp_persistent_page is not None:
                    try:
                        if not _cdp_persistent_page.is_closed() and "chatgpt.com" in _cdp_persistent_page.url:
                            page = _cdp_persistent_page
                            _reuse_page = True
                            _llm_logger.info("chatgpt_web: reusing persistent tab at %s", page.url)
                    except Exception:
                        _cdp_persistent_page = None

                if not _reuse_page:
                    _llm_logger.info("chatgpt_web: opening new tab via CDP to %s", cdp_url)
                    page = await context.new_page()
                    _cdp_persistent_page = page

                page.set_default_timeout(300_000)
                await page.set_viewport_size({"width": 1280, "height": 800})

                try:
                    await self._navigate_and_interact(
                        page, chatgpt_url, is_project_url, _reuse_page, full_prompt
                    )
                    detected_model, raw_text = await self._read_response(page)
                except Exception:
                    _cdp_persistent_page = None   # reset on error so next request gets fresh tab
                    raise
                # Keep tab open for reuse

            else:
                async with async_playwright() as pw:
                    context = await self._launch_browser(pw)
                    await context.add_init_script(_STEALTH_SCRIPT)
                    pages = context.pages
                    page = pages[0] if pages else await context.new_page()
                    page.set_default_timeout(300_000)
                    await page.set_viewport_size({"width": 1280, "height": 800})
                    try:
                        await self._navigate_and_interact(
                            page, chatgpt_url, is_project_url, False, full_prompt
                        )
                        detected_model, raw_text = await self._read_response(page)
                    finally:
                        await context.close()

        if not raw_text:
            raise RuntimeError("ChatGPT returned an empty response.")

        scores = parse_evaluation_response(raw_text, request.results)
        return self._build_response(
            request, scores,
            raw_llm_response=raw_text,
            model_override=detected_model,
        )


# ---------------------------------------------------------------------------
# Anthropic Claude API
# ---------------------------------------------------------------------------

class AnthropicEvaluator(BaseEvaluator):
    """Evaluate using Anthropic Claude API."""

    provider = "anthropic"

    def __init__(self, model: str = "claude-3-5-haiku-20241022", api_key: str | None = None):
        self.model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError("anthropic package not installed. Run: uv add anthropic") from exc

        client = anthropic.AsyncAnthropic(api_key=self._api_key)

        response = await client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=self._system_prompt(request),
            messages=[{"role": "user", "content": build_user_prompt(request)}],
            temperature=0.1,
        )

        raw = response.content[0].text if response.content else ""
        scores = parse_evaluation_response(raw, request.results)

        usage = response.usage
        return self._build_response(
            request, scores,
            prompt_tokens=usage.input_tokens if usage else None,
            completion_tokens=usage.output_tokens if usage else None,
            raw_llm_response=raw,
        )


# ---------------------------------------------------------------------------
# Gemini Web Evaluator — browser automation, no API key required
# ---------------------------------------------------------------------------

_GEMINI_PROFILE_DIR = os.path.expanduser("~/.local/share/keyless-eval/gemini")

_GEMINI_MODEL_JS = """
() => {
    // Try the model-selector button text
    const selectors = [
        'bard-mode-switcher button',
        '[data-test-id="bard-mode-menu-button"]',
        'model-switcher button',
        'mat-select[aria-label*="model" i]',
        '[aria-label*="Gemini" i]',
    ];
    for (const sel of selectors) {
        const el = document.querySelector(sel);
        if (el) {
            const text = (el.innerText || el.textContent || '').trim();
            if (text) return text;
        }
    }
    return null;
}
"""


class GeminiWebEvaluator(BaseEvaluator):
    """
    Evaluate using the Gemini web interface (gemini.google.com) via Playwright.

    No API key required. Login once with `keyless-eval login --provider gemini_web`,
    then all requests run headless using the saved Google session.

    Env vars:
      GEMINI_PROFILE_DIR    Path to persistent Chrome profile (default: ~/.local/share/keyless-eval/gemini)
      CHATGPT_WEB_LOGIN=1   Force visible window (for first-time login)
      CHATGPT_WEB_HEADLESS  Override headless: 0=visible, 1=headless (auto if unset)
    """

    provider = "gemini_web"

    def __init__(self, model: str = "auto", timeout: int = 300, headless: bool | None = None):
        self.model = model
        self._timeout = timeout
        profile_dir = os.environ.get("GEMINI_PROFILE_DIR", _GEMINI_PROFILE_DIR)
        self._profile_dir = profile_dir
        force_login = os.environ.get("CHATGPT_WEB_LOGIN", "").lower() in ("1", "true", "yes")

        if headless is None:
            env_val = os.environ.get("CHATGPT_WEB_HEADLESS", "")
            if env_val:
                self._headless = env_val.lower() not in ("0", "false", "no")
            else:
                self._headless = _profile_has_session(profile_dir) and not force_login
        else:
            self._headless = headless

    async def _launch_browser(self, pw):
        os.makedirs(self._profile_dir, exist_ok=True)
        args = _get_stealth_args(self._headless)
        try:
            return await pw.chromium.launch_persistent_context(
                self._profile_dir, channel="chrome",
                headless=self._headless, args=args, user_agent=_USER_AGENT,
            )
        except Exception:
            return await pw.chromium.launch_persistent_context(
                self._profile_dir,
                headless=self._headless, args=args, user_agent=_USER_AGENT,
            )

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright not installed. Run:\n"
                "  uv add playwright\n"
                "  uv run playwright install chromium"
            ) from exc

        system = self._system_prompt(request)
        full_prompt = _build_prompt_header(request) + f"{system}\n\n---\n\n{build_user_prompt(request)}"

        detected_model: str | None = None
        raw_text = ""

        async with async_playwright() as pw:
            context = await self._launch_browser(pw)
            await context.add_init_script(_STEALTH_SCRIPT)

            pages = context.pages
            page = pages[0] if pages else await context.new_page()
            page.set_default_timeout(300_000)
            await page.set_viewport_size({"width": 1280, "height": 800})

            try:
                await page.goto("https://gemini.google.com/", wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)
                _ensure_llm_logger()
                _llm_logger.info("gemini_web: page loaded, model=%s headless=%s", self.model, self._headless)

                # Model selection — map model param to Gemini web UI labels
                _MODEL_LABEL_MAP = {
                    "thinking": "Thinking",
                    "pro":      "Pro",
                    "fast":     "Fast",
                }
                desired_label = _MODEL_LABEL_MAP.get(self.model.lower() if self.model else "", None)
                if desired_label:
                    try:
                        # Open the model dropdown (the button showing the current mode)
                        dropdown = page.locator(
                            "button:has-text('Fast'), button:has-text('Thinking'), button:has-text('Pro')"
                        ).first
                        await dropdown.wait_for(state="visible", timeout=5000)
                        await dropdown.click()
                        await page.wait_for_timeout(500)
                        # Click the matching menu item
                        option = page.get_by_role("menuitem").filter(has_text=desired_label).first
                        await option.wait_for(state="visible", timeout=3000)
                        await option.click()
                        await page.wait_for_timeout(500)
                        _llm_logger.info("gemini_web: model switched to %s", desired_label)
                    except Exception as _me:
                        _llm_logger.warning("gemini_web: model selection failed for %r: %s", desired_label, _me)

                # Try model detection before sending
                try:
                    detected_model = await page.evaluate(_GEMINI_MODEL_JS)
                except Exception:
                    pass

                # Locate input field — Gemini uses a rich-textarea web component
                textarea = page.locator("rich-textarea div[contenteditable='true']").first
                try:
                    await textarea.wait_for(state="visible", timeout=10000)
                except Exception:
                    # Fallback selectors
                    for sel in ["p[data-placeholder]", "div[contenteditable='true']"]:
                        try:
                            textarea = page.locator(sel).first
                            await textarea.wait_for(state="visible", timeout=5000)
                            break
                        except Exception:
                            pass

                await _fill_contenteditable(page, textarea, full_prompt, self._headless)
                await page.wait_for_timeout(500)

                # Send: try button first, then Enter
                sent = False
                for send_sel in [
                    "button[aria-label='Send message']",
                    "button.send-button",
                    "[data-test-id='send-button']",
                    "button[mattooltip*='Send' i]",
                ]:
                    try:
                        btn = page.locator(send_sel).first
                        await btn.wait_for(state="visible", timeout=2000)
                        await btn.click()
                        sent = True
                        break
                    except Exception:
                        pass

                if not sent:
                    await textarea.press("Enter")

                # Wait for response — Gemini uses response-container or model-response
                _llm_logger.info("gemini_web: waiting for response (timeout=%ds)...", self._timeout)
                try:
                    await page.wait_for_function(
                        """() => {
                            const selectors = [
                                'response-container .markdown',
                                'model-response .response-content',
                                '.response-container p',
                                'message-content .markdown',
                            ];
                            for (const sel of selectors) {
                                const els = document.querySelectorAll(sel);
                                if (els.length) {
                                    const last = els[els.length - 1];
                                    const text = (last.innerText || '').trim();
                                    if (text.length > 10) return true;
                                }
                            }
                            return false;
                        }""",
                        timeout=self._timeout * 1000,
                    )
                except Exception as _we:
                    _llm_logger.error("gemini_web: wait_for_function timed out or failed: %s", _we)
                    raise

                # Wait for streaming to finish (send button re-appears or stop button hides)
                await page.wait_for_timeout(1000)
                for stop_sel in ["button[aria-label='Stop response']", ".stop-button"]:
                    try:
                        await page.locator(stop_sel).wait_for(
                            state="hidden", timeout=self._timeout * 1000
                        )
                        break
                    except Exception:
                        pass
                await page.wait_for_timeout(500)

                # Extract response text
                raw_text = await page.evaluate("""
                    () => {
                        const selectors = [
                            'response-container .markdown',
                            'model-response .response-content',
                            '.response-container p',
                            'message-content .markdown',
                        ];
                        for (const sel of selectors) {
                            const els = document.querySelectorAll(sel);
                            if (els.length) {
                                return els[els.length - 1].innerText || '';
                            }
                        }
                        return '';
                    }
                """)

                _llm_logger.info("gemini_web: raw_text length=%d", len(raw_text))

                # Model detection after response
                if not detected_model:
                    try:
                        detected_model = await page.evaluate(_GEMINI_MODEL_JS)
                    except Exception:
                        pass

            finally:
                await context.close()

        if not raw_text:
            _ensure_llm_logger()
            _llm_logger.error(
                "provider=gemini_web model=%s EMPTY RESPONSE input=%r detected_model=%r",
                self.model, request.input[:80], detected_model,
            )
            raise RuntimeError("Gemini returned an empty response.")

        scores = parse_evaluation_response(raw_text, request.results)
        return self._build_response(
            request, scores,
            raw_llm_response=raw_text,
            model_override=detected_model or "gemini-web",
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

PROVIDER_MAP: dict[str, type[BaseEvaluator]] = {
    "openai": OpenAIEvaluator,
    "gemini": GeminiEvaluator,
    "chatgpt_web": ChatGPTWebEvaluator,
    "gemini_web": GeminiWebEvaluator,
    "anthropic": AnthropicEvaluator,
}

_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o",
    "gemini": "gemini-2.0-flash",
    "chatgpt_web": "auto",
    "gemini_web": "auto",
    "anthropic": "claude-3-5-haiku-20241022",
}


def get_evaluator(provider: str, model: str | None = None, **kwargs) -> BaseEvaluator:
    """
    Factory: get an evaluator instance by provider name.

    Examples:
        get_evaluator("gemini")
        get_evaluator("chatgpt_web")
        get_evaluator("openai", model="gpt-4o")
        get_evaluator("anthropic", model="claude-opus-4-5")
    """
    key = provider.lower()
    cls = PROVIDER_MAP.get(key)
    if cls is None:
        raise ValueError(f"Unknown provider '{provider}'. Choose from: {', '.join(PROVIDER_MAP)}")
    return cls(model=model or _DEFAULT_MODELS[key], **kwargs)
