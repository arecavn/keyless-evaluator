"""LLM provider backends: OpenAI, Gemini, ChatGPT Web (anonymous)."""

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

    def _build_response(
        self,
        request: EvaluationRequest,
        scores: list[ResultScore],
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        raw_llm_response: str | None = None,
    ) -> EvaluationResponse:
        resp = EvaluationResponse(
            query=request.query,
            model=self.model,
            provider=self.provider,
            scores=scores,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        resp.ndcg = _compute_ndcg(scores)

        if raw_llm_response is not None:
            _ensure_llm_logger()
            _llm_logger.info(
                "provider=%s model=%s tokens=%s/%s query=%r\n%s\n%s\n%s",
                self.provider,
                self.model,
                prompt_tokens,
                completion_tokens,
                request.query,
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

        client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
        )

        response = await client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(request)},
            ],
            temperature=0.1,
            response_format={"type": "json_object"} if "gpt-4" in self.model else {"type": "text"},
        )

        raw = response.choices[0].message.content or ""
        scores = parse_evaluation_response(raw, request.results)

        usage = response.usage
        return self._build_response(
            request,
            scores,
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
            system_instruction=SYSTEM_PROMPT,
        )

        user_prompt = build_user_prompt(request)

        # Run in executor to avoid blocking the event loop (SDK is sync)
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
            request,
            scores,
            prompt_tokens=getattr(usage, "prompt_token_count", None) if usage else None,
            completion_tokens=getattr(usage, "candidates_token_count", None) if usage else None,
            raw_llm_response=raw,
        )


# ---------------------------------------------------------------------------
# ChatGPT Web Evaluator — anonymous, silent, no account / no API key
# ---------------------------------------------------------------------------

# Full stealth init script injected on every page before any JS runs.
# Patches the most common fingerprinting vectors used by Cloudflare and
# ChatGPT's bot-detection layer to distinguish headless from real browsers.
_STEALTH_SCRIPT = """
// 1. Hide webdriver flag — the most obvious headless tell
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

// 2. Patch userAgent — headless Chrome includes 'HeadlessChrome', Cloudflare checks this
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
// The PluginArray prototype mutation breaks ChatGPT's React streaming renderer,
// causing the assistant response container to appear in the DOM with empty text.
// Window dimension overrides similarly interfere with React's layout calculations.
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
    "--disable-web-security",       # avoids some iframe fingerprinting
    "--lang=en-US,en",
]

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class ChatGPTWebEvaluator(BaseEvaluator):
    """
    Evaluate using ChatGPT's public anonymous web interface via Playwright.

    **Headless by default with full stealth** — runs Chrome in headless mode
    while masking all common bot-detection fingerprints used by Cloudflare/ChatGPT:
    - navigator.userAgent patched to remove "HeadlessChrome"
    - navigator.webdriver hidden
    - Realistic plugins, hardwareConcurrency, deviceMemory, screen dimensions
    - chrome.runtime, chrome.loadTimes, chrome.csi present
    - Permissions API returns 'default' for notifications

    Set CHATGPT_WEB_HEADLESS=0 to force a visible Chrome window instead.
    """

    provider = "chatgpt_web"

    def __init__(
        self,
        model: str = "auto",
        timeout: int = 120,
        headless: bool | None = None,
    ):
        self.model = model
        self._timeout = timeout
        # Default True (headless + stealth). Set CHATGPT_WEB_HEADLESS=0 for visible window.
        if headless is None:
            env_val = os.environ.get("CHATGPT_WEB_HEADLESS", "1")
            self._headless = env_val.lower() not in ("0", "false", "no")
        else:
            self._headless = headless

    async def _launch_browser(self, pw):
        """
        Try browsers in order: Chrome headless → Chromium headless → visible fallback.
        Returns a connected browser instance.
        """
        import tempfile
        import uuid

        # Use a unique dir per request so concurrent calls never share state.
        # Also avoids stale lock files from previous crashed sessions.
        user_data_dir = os.path.join(tempfile.gettempdir(), f"pw-keval-{uuid.uuid4().hex[:8]}")
        os.makedirs(user_data_dir, exist_ok=True)
        self._user_data_dir = user_data_dir  # store for cleanup in evaluate()
        
        errors = []

        if self._headless:
            # Headless mode: try real Chrome → bundled Chromium → visible fallback
            # user_agent is set here so HTTP headers also show the patched UA (not just JS)
            try:
                browser = await pw.chromium.launch_persistent_context(
                    user_data_dir,
                    channel="chrome",
                    headless=True,
                    args=_STEALTH_ARGS,
                    user_agent=_USER_AGENT,
                )
                return browser
            except Exception as e:
                errors.append(f"Chrome headless: {e}")

            try:
                browser = await pw.chromium.launch_persistent_context(
                    user_data_dir,
                    headless=True,
                    args=_STEALTH_ARGS,
                    user_agent=_USER_AGENT,
                )
                return browser
            except Exception as e:
                errors.append(f"Chromium headless: {e}")

        # Visible mode (CHATGPT_WEB_HEADLESS=0): real Chrome → bundled Chromium
        try:
            browser = await pw.chromium.launch_persistent_context(
                user_data_dir,
                channel="chrome",
                headless=False,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                user_agent=_USER_AGENT,
            )
            return browser
        except Exception as e:
            errors.append(f"Chrome visible: {e}")

        browser = await pw.chromium.launch_persistent_context(
            user_data_dir,
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage"],
            user_agent=_USER_AGENT,
        )
        return browser

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright not installed. Run:\n"
                "  uv add playwright\n"
                "  uv run playwright install chromium"
            ) from exc

        full_prompt = f"{SYSTEM_PROMPT}\n\n---\n\n{build_user_prompt(request)}"

        async with async_playwright() as pw:
            context = await self._launch_browser(pw)
            
            # Since we used launch_persistent_context, we cannot call new_context again.
            # We apply the stealth script and limits to all pages in this context.
            await context.add_init_script(_STEALTH_SCRIPT)

            # In persistent contexts, there's always an initial page
            pages = context.pages
            if pages:
                page = pages[0]
            else:
                page = await context.new_page()

            # Hard timeout: close the browser if no response within 5 minutes (300s).
            # This ensures the visible Chrome window always closes even if something hangs.
            page.set_default_timeout(300_000)
            await page.set_viewport_size({"width": 1280, "height": 800})
            
            raw_text = ""
            try:
                await page.goto(
                    "https://chatgpt.com/",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )

                # If Cloudflare challenge detected immediately, raise
                title = await page.title()
                if "just a moment" in title.lower() or "cloudflare" in title.lower():
                    raise RuntimeError(
                        "Cloudflare bot-detection triggered. "
                        "Set CHATGPT_WEB_HEADLESS=0 to use visible browser mode."
                    )

                # Dismiss modals (login prompt, cookie banner, etc.)
                for dismiss_text in ["Stay logged out", "Start now", "OK"]:
                    try:
                        btn = page.get_by_text(dismiss_text, exact=True).first
                        if await btn.is_visible(timeout=2000):
                            await btn.click()
                    except Exception:
                        pass

                textarea = page.locator("#prompt-textarea").first
                await textarea.wait_for(state="visible", timeout=15000)
                await textarea.click()

                # Use execCommand('insertText') — the only safe way to insert multi-line
                # text into ChatGPT's contenteditable without \n triggering "send".
                # keyboard.type() maps \n → Enter which sends the message prematurely.
                await page.evaluate(
                    """(text) => {
                        const el = document.querySelector('#prompt-textarea');
                        el.focus();
                        document.execCommand('selectAll', false, null);
                        document.execCommand('insertText', false, text);
                    }""",
                    full_prompt,
                )

                # Try send button first; fall back to Enter key
                try:
                    send_btn = page.locator("[data-testid='send-button']").first
                    await send_btn.wait_for(state="visible", timeout=5000)
                    await send_btn.click()
                except Exception:
                    await page.keyboard.press("Enter")

                # Check for bot detection after sending (Cloudflare may appear post-load)
                await page.wait_for_timeout(1500)
                title = await page.title()
                if "just a moment" in title.lower() or "cloudflare" in title.lower():
                    raise RuntimeError(
                        "Cloudflare bot-detection triggered. "
                        "Set CHATGPT_WEB_HEADLESS=0 to use visible browser mode."
                    )

                # Wait until assistant response has actual content
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

                # Wait for generation to finish (stop button disappears)
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

            finally:
                await context.close()
                # Clean up per-request user_data_dir (avoids stale lock files)
                import shutil
                try:
                    shutil.rmtree(getattr(self, "_user_data_dir", ""), ignore_errors=True)
                except Exception:
                    pass

        if not raw_text:
            raise RuntimeError("ChatGPT returned an empty response.")

        scores = parse_evaluation_response(raw_text, request.results)
        return self._build_response(request, scores, raw_llm_response=raw_text)


# ---------------------------------------------------------------------------
# Anthropic Claude API
# ---------------------------------------------------------------------------

class AnthropicEvaluator(BaseEvaluator):
    """Evaluate using Anthropic Claude API (claude-3-5-haiku, claude-opus-4, etc.)."""

    provider = "anthropic"

    def __init__(self, model: str = "claude-3-5-haiku-20241022", api_key: str | None = None):
        self.model = model
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError(
                "anthropic package not installed. Run: uv add anthropic"
            ) from exc

        client = anthropic.AsyncAnthropic(api_key=self._api_key)

        user_prompt = build_user_prompt(request)

        response = await client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            temperature=0.1,
        )

        raw = response.content[0].text if response.content else ""
        scores = parse_evaluation_response(raw, request.results)

        usage = response.usage
        return self._build_response(
            request,
            scores,
            prompt_tokens=usage.input_tokens if usage else None,
            completion_tokens=usage.output_tokens if usage else None,
            raw_llm_response=raw,
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

PROVIDER_MAP: dict[str, type[BaseEvaluator]] = {
    "openai": OpenAIEvaluator,
    "gemini": GeminiEvaluator,
    "chatgpt_web": ChatGPTWebEvaluator,
    "anthropic": AnthropicEvaluator,
}

# Default model per provider
_DEFAULT_MODELS: dict[str, str] = {
    "openai": "gpt-4o",
    "gemini": "gemini-2.0-flash",
    "chatgpt_web": "auto",
    "anthropic": "claude-3-5-haiku-20241022",
}


def get_evaluator(
    provider: str,
    model: str | None = None,
    **kwargs,
) -> BaseEvaluator:
    """
    Factory: get an evaluator instance by provider name.

    Examples:
        get_evaluator("gemini")                           # free, just set GEMINI_API_KEY
        get_evaluator("chatgpt_web")                      # no account / no key needed
        get_evaluator("openai", model="gpt-4o")
        get_evaluator("anthropic", model="claude-opus-4-5")
    """
    key = provider.lower()
    cls = PROVIDER_MAP.get(key)
    if cls is None:
        raise ValueError(
            f"Unknown provider '{provider}'. Choose from: {', '.join(PROVIDER_MAP)}"
        )

    return cls(model=model or _DEFAULT_MODELS[key], **kwargs)
