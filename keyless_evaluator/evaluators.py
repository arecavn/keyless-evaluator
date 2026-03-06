"""LLM provider backends: OpenAI, Gemini, ChatGPT Web (anonymous)."""

from __future__ import annotations

import asyncio
import math
import os
from abc import ABC, abstractmethod

from keyless_evaluator.models import (
    EvaluationRequest,
    EvaluationResponse,
    ResultScore,
)
from keyless_evaluator.parser import parse_evaluation_response
from keyless_evaluator.prompts import (
    SYSTEM_PROMPT,
    build_user_prompt,
)


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

// 2. Realistic language + platform
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
Object.defineProperty(navigator, 'platform', {get: () => 'MacIntel'});

// 3. Fake plugin list — headless has 0 plugins
Object.defineProperty(navigator, 'plugins', {
  get: () => {
    const arr = [
      {name:'Chrome PDF Plugin', filename:'internal-pdf-viewer'},
      {name:'Chrome PDF Viewer', filename:'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
      {name:'Native Client', filename:'internal-nacl-plugin'},
    ];
    arr.__proto__ = PluginArray.prototype;
    return arr;
  }
});

// 4. Fake chrome runtime object — missing in headless
window.chrome = window.chrome || {};
window.chrome.runtime = window.chrome.runtime || {};

// 5. Permissions API — headless returns 'denied' for notifications, real browser returns 'default'
const _origPerms = navigator.permissions.query.bind(navigator.permissions);
navigator.permissions.query = (p) =>
  p.name === 'notifications'
    ? Promise.resolve({state: 'default', onchange: null})
    : _origPerms(p);

// 6. Remove Automation-related CSS media feature
Object.defineProperty(window, 'outerWidth',  {get: () => 1280});
Object.defineProperty(window, 'outerHeight', {get: () => 800});
Object.defineProperty(window, 'innerWidth',  {get: () => 1280});
Object.defineProperty(window, 'innerHeight', {get: () => 800});
Object.defineProperty(screen, 'width',       {get: () => 1280});
Object.defineProperty(screen, 'height',      {get: () => 800});
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

    **Silent by default** — runs Chrome in headless mode using a 3-tier strategy
    to bypass Cloudflare and ChatGPT bot detection without showing any window:

    Tier 1 (default): Real Chrome install + headless=True + full stealth patches.
      Chrome's "new headless" mode (since v112) shares the same rendering engine
      as headed mode, making it the hardest to fingerprint.

    Tier 2: Bundled Playwright Chromium + headless=True + stealth patches.
      Works when Chrome is not installed (e.g. CI, Docker).

    Tier 3 (fallback): headless=False — visible window. Only used when both
      headless tiers are detected and blocked by Cloudflare.

    The stealth script patches: navigator.webdriver, navigator.plugins,
    navigator.languages, chrome.runtime, permissions API, screen/window dimensions.

    No account, no API key, no cookies required. Completely anonymous.
    Set env var CHATGPT_WEB_HEADLESS=0 to force visible mode.
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
        # Respect env override; default True (silent)
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
        errors = []

        # Tier 1: real Chrome + new headless (hardest to detect)
        if self._headless:
            try:
                browser = await pw.chromium.launch(
                    channel="chrome",
                    headless=True,
                    args=_STEALTH_ARGS,
                )
                return browser
            except Exception as e:
                errors.append(f"Chrome headless: {e}")

        # Tier 2: bundled Chromium + headless (works in Docker/CI)
        if self._headless:
            try:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=_STEALTH_ARGS,
                )
                return browser
            except Exception as e:
                errors.append(f"Chromium headless: {e}")

        # Tier 3: visible window fallback (always works but shows UI)
        try:
            browser = await pw.chromium.launch(
                channel="chrome",
                headless=False,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            return browser
        except Exception:
            pass

        browser = await pw.chromium.launch(
            headless=False,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage"],
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
            browser = await self._launch_browser(pw)
            context = await browser.new_context(
                user_agent=_USER_AGENT,
                viewport={"width": 1280, "height": 800},
                locale="en-US",
                timezone_id="America/New_York",
                color_scheme="light",
                java_script_enabled=True,
            )
            # Inject stealth patches before any page script runs
            await context.add_init_script(_STEALTH_SCRIPT)

            page = await context.new_page()
            raw_text = ""
            try:
                await page.goto(
                    "https://chatgpt.com/",
                    wait_until="domcontentloaded",
                    timeout=30000,
                )

                # Dismiss modals (login prompt, cookie banner, etc.)
                for dismiss_text in ["Stay logged out", "Start now", "OK"]:
                    try:
                        btn = page.get_by_text(dismiss_text, exact=True).first
                        if await btn.is_visible(timeout=2000):
                            await btn.click()
                    except Exception:
                        pass

                # If Cloudflare challenge detected, raise immediately
                title = await page.title()
                if "just a moment" in title.lower() or "cloudflare" in title.lower():
                    raise RuntimeError(
                        "Cloudflare bot-detection triggered in headless mode. "
                        "Set CHATGPT_WEB_HEADLESS=0 to use visible browser mode."
                    )

                textarea = page.locator("#prompt-textarea").first
                await textarea.wait_for(state="visible", timeout=15000)
                await textarea.click()
                await textarea.fill(full_prompt)

                await page.locator("[data-testid='send-button']").click()

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
                        state="hidden", timeout=30000
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
                await browser.close()

        if not raw_text:
            raise RuntimeError("ChatGPT returned an empty response.")

        scores = parse_evaluation_response(raw_text, request.results)
        return self._build_response(request, scores)


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
