"""LLM provider backends: OpenAI, Gemini, ChatGPT Web (anonymous), Anthropic."""

from __future__ import annotations

import asyncio
import logging
import math
import os
from abc import ABC, abstractmethod

from models import (
    EvaluationRequest,
    EvaluationResponse,
    ResultScore,
)
from parser import parse_evaluation_response
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
        """Return the system prompt. Custom prompts get the output format spec appended."""
        if request.prompt:
            return request.prompt + OUTPUT_FORMAT
        return SYSTEM_PROMPT

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
            response_format={"type": "json_object"} if "gpt-4" in self.model else {"type": "text"},
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

_STEALTH_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--disable-infobars",
    "--disable-extensions",
    "--window-size=1280,800",
    "--start-maximized",
    "--disable-features=IsolateOrigins,site-per-process",
    "--disable-web-security",
    "--lang=en-US,en",
]

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


_DEFAULT_PROFILE_DIR = os.path.expanduser("~/.local/share/keyless-eval/chatgpt")
_SESSION_MARKER = "keyless-eval-session"


def _chatgpt_profile_dir() -> str:
    return os.environ.get("CHATGPT_PROFILE_DIR", _DEFAULT_PROFILE_DIR)


def _profile_has_session(profile_dir: str) -> bool:
    """True only when the user has explicitly logged in (sentinel file present)."""
    return os.path.isfile(os.path.join(profile_dir, _SESSION_MARKER))


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

        args = _STEALTH_ARGS if self._headless else [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--window-size=1280,800",
        ]

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
        full_prompt = f"{system}\n\n---\n\n{build_user_prompt(request)}"

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
                await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=30000)

                try:
                    title = await page.title()
                    if "just a moment" in title.lower() or "cloudflare" in title.lower():
                        raise RuntimeError(
                            "Cloudflare bot-detection triggered. "
                            "Set CHATGPT_WEB_HEADLESS=0 to use visible browser mode."
                        )
                except RuntimeError:
                    raise
                except Exception:
                    pass

                for dismiss_text in ["Stay logged out", "Start now", "OK"]:
                    try:
                        btn = page.get_by_text(dismiss_text, exact=True).first
                        if await btn.is_visible(timeout=2000):
                            await btn.click()
                    except Exception:
                        pass

                # Try to detect the active model before sending (UI is stable at this point)
                try:
                    detected_model = await page.evaluate(_MODEL_DETECTION_JS)
                except Exception:
                    pass

                textarea = page.locator("#prompt-textarea").first
                await textarea.wait_for(state="visible", timeout=15000)
                await textarea.click()

                # Write full prompt to system clipboard then paste — avoids dropped
                # characters and React state sync issues with contenteditable.
                import subprocess, platform
                if platform.system() == "Darwin":
                    subprocess.run(["pbcopy"], input=full_prompt.encode("utf-8"), check=True)
                    await page.keyboard.press("Meta+v")
                else:
                    # Linux fallback: xclip or xdotool
                    try:
                        subprocess.run(["xclip", "-selection", "clipboard"],
                                       input=full_prompt.encode("utf-8"), check=True)
                    except FileNotFoundError:
                        subprocess.run(["xdotool", "type", "--clearmodifiers", "--", full_prompt],
                                       check=True)
                        await page.wait_for_timeout(300)
                    else:
                        await page.keyboard.press("Control+v")

                await page.wait_for_timeout(500)

                # Wait briefly for React to process the typed text before sending
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
                    # Try keyboard Enter (works when textarea is focused)
                    await textarea.press("Enter")
                    await page.wait_for_timeout(200)

                await page.wait_for_timeout(1500)
                try:
                    title = await page.title()
                    if "just a moment" in title.lower() or "cloudflare" in title.lower():
                        raise RuntimeError(
                            "Cloudflare bot-detection triggered. "
                            "Set CHATGPT_WEB_HEADLESS=0 to use visible browser mode."
                        )
                except RuntimeError:
                    raise
                except Exception:
                    pass

                await page.wait_for_function(
                    """() => {
                        const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
                        if (!msgs.length) return false;
                        const last = msgs[msgs.length - 1];
                        const text = last.innerText ? last.innerText.trim() : '';
                        return text.length > 10;
                    }""",
                    timeout=self._timeout * 1000,
                )

                try:
                    await page.locator("[data-testid='stop-button']").wait_for(
                        state="hidden", timeout=60000
                    )
                except Exception:
                    pass

                await page.wait_for_timeout(800)

                raw_text = await page.evaluate(
                    """() => {
                        const msgs = document.querySelectorAll('[data-message-author-role="assistant"]');
                        if (!msgs.length) return '';
                        return msgs[msgs.length - 1].innerText || '';
                    }"""
                )

                # Re-try model detection after response (model switcher may have updated)
                if not detected_model:
                    try:
                        detected_model = await page.evaluate(_MODEL_DETECTION_JS)
                    except Exception:
                        pass

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

    def __init__(self, model: str = "auto", timeout: int = 120, headless: bool | None = None):
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
        args = _STEALTH_ARGS if self._headless else [
            "--no-sandbox",
            "--disable-blink-features=AutomationControlled",
            "--disable-dev-shm-usage",
            "--window-size=1280,800",
        ]
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
        full_prompt = f"{system}\n\n---\n\n{build_user_prompt(request)}"

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

                await textarea.click()

                # Write prompt to clipboard and paste
                import subprocess, platform
                if platform.system() == "Darwin":
                    subprocess.run(["pbcopy"], input=full_prompt.encode("utf-8"), check=True)
                    await page.keyboard.press("Meta+v")
                else:
                    try:
                        subprocess.run(["xclip", "-selection", "clipboard"],
                                       input=full_prompt.encode("utf-8"), check=True)
                    except FileNotFoundError:
                        await textarea.press_sequentially(full_prompt, delay=0)
                    else:
                        await page.keyboard.press("Control+v")

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

                # Wait for streaming to finish (send button re-appears or stop button hides)
                await page.wait_for_timeout(1000)
                for stop_sel in ["button[aria-label='Stop response']", ".stop-button"]:
                    try:
                        await page.locator(stop_sel).wait_for(state="hidden", timeout=30000)
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

                # Model detection after response
                if not detected_model:
                    try:
                        detected_model = await page.evaluate(_GEMINI_MODEL_JS)
                    except Exception:
                        pass

            finally:
                await context.close()

        if not raw_text:
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
