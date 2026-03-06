"""LLM provider backends: OpenAI, Gemini, and Oracle CLI."""

from __future__ import annotations

import asyncio
import json
import math
import os
import subprocess
import tempfile
from abc import ABC, abstractmethod

from oracle_search_evaluator.models import (
    EvaluationRequest,
    EvaluationResponse,
    ResultScore,
)
from oracle_search_evaluator.parser import parse_evaluation_response
from oracle_search_evaluator.prompts import (
    SYSTEM_PROMPT,
    build_oracle_cli_prompt,
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
# OpenAI / ChatGPT
# ---------------------------------------------------------------------------

class OpenAIEvaluator(BaseEvaluator):
    """Evaluate using OpenAI API (GPT-4o, GPT-5, etc.)."""

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
# Google Gemini
# ---------------------------------------------------------------------------

class GeminiEvaluator(BaseEvaluator):
    """Evaluate using Google Gemini API."""

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

        # Run in executor to avoid blocking event loop (SDK is sync)
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
# Oracle CLI (browser or API mode — kept for advanced users with Chrome cookies)
# ---------------------------------------------------------------------------

class OracleEvaluator(BaseEvaluator):
    """Evaluate using the Oracle CLI tool (steipete/oracle). Requires Chrome cookies."""

    provider = "oracle"

    def __init__(
        self,
        model: str = "gemini-2.0-flash",
        engine: str = "browser",
        extra_args: list[str] | None = None,
        timeout: int = 300,
    ):
        self.model = model
        self.engine = engine
        self._extra_args = extra_args or []
        self._timeout = timeout

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        prompt = build_oracle_cli_prompt(request)
        import uuid as _uuid

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(prompt)
            tmp_path = tmp.name

        cmd = [
            "npx", "--cache", "/tmp/oracle-eval-npm-cache", "-y", "@steipete/oracle",
            "--model", self.model,
            "--engine", self.engine,
            "--file", tmp_path,
            "-p", (
                f"Evaluate these search results. "
                f"Return ONLY a JSON array. "
                f"Run ID: {_uuid.uuid4()}"
            ),
            "--wait",
            "--no-background",
            "--browser-manual-login",
        ] + self._extra_args

        env = os.environ.copy()
        safe_home = "/tmp/oracle-eval-home"
        os.makedirs(safe_home, exist_ok=True)
        env["ORACLE_HOME_DIR"] = safe_home

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=self._timeout, env=env),
        )

        raw = result.stdout.strip()
        if result.returncode != 0:
            raise RuntimeError(
                f"Oracle CLI failed (exit {result.returncode}):\n"
                f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
            )
        scores = parse_evaluation_response(raw, request.results)
        return self._build_response(request, scores)


# ---------------------------------------------------------------------------
# ChatGPT Web Evaluator — anonymous, no account/API key/browser required!
# ---------------------------------------------------------------------------

class ChatGPTWebEvaluator(BaseEvaluator):
    """
    Evaluate using ChatGPT's public anonymous web API.
    Uses the same backend endpoint that chatgpt.com calls internally —
    no account, no API key, no browser, no cookies required.
    Works 100% headlessly.
    """

    provider = "chatgpt_web"

    _SENTINEL_URL = "https://chatgpt.com/backend-anon/sentinel/chat-requirements"
    _COMPLETION_URL = "https://chatgpt.com/backend-anon/conversation"

    def __init__(self, model: str = "auto", timeout: int = 120):
        self.model = model
        self._timeout = timeout

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResponse:
        try:
            from playwright.async_api import async_playwright, TimeoutError as PWTimeout
        except ImportError as exc:
            raise RuntimeError(
                "playwright not installed. Run:\n"
                "  uv add playwright\n"
                "  uv run playwright install chromium"
            ) from exc

        import uuid as _uuid

        full_prompt = f"{SYSTEM_PROMPT}\n\n---\n\n{build_user_prompt(request)}"

        async with async_playwright() as pw:
            # Use real Chrome (not headless Chromium) to pass Cloudflare bot detection.
            # headless Chromium triggers "Just a moment..." Cloudflare challenge.
            try:
                browser = await pw.chromium.launch(
                    channel="chrome",   # Use user's real Chrome installation
                    headless=False,     # Must be visible — headless is detected by Cloudflare
                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
                )
            except Exception:
                # Fallback: use bundled Chromium with stealth args if Chrome not found
                browser = await pw.chromium.launch(
                    headless=False,
                    args=[
                        "--no-sandbox",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                    ],
                )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            page = await context.new_page()

            # Mask automation flags so Cloudflare can't detect Playwright
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            try:
                # Navigate to ChatGPT (no login required for anonymous chats)
                await page.goto("https://chatgpt.com/", wait_until="domcontentloaded", timeout=30000)

                # Dismiss any modals (login prompt, cookie notices, etc.)
                for dismiss_text in ["Stay logged out", "Start now", "OK"]:
                    try:
                        btn = page.get_by_text(dismiss_text, exact=True).first
                        if await btn.is_visible(timeout=2000):
                            await btn.click()
                    except Exception:
                        pass

                # Type prompt into the input
                textarea = page.locator("#prompt-textarea").first
                await textarea.wait_for(state="visible", timeout=15000)
                await textarea.click()
                await textarea.fill(full_prompt)

                # Click send
                await page.locator("[data-testid='send-button']").click()

                # Wait until assistant response has actual text content (not just the empty loading dot)
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

                # Wait for the stop button to disappear (confirms generation is fully done)
                try:
                    await page.locator("[data-testid='stop-button']").wait_for(
                        state="hidden", timeout=30000
                    )
                except Exception:
                    pass

                # Small settle wait
                await page.wait_for_timeout(1000)

                # Extract final response text via JavaScript (works regardless of CSS classes)
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
# Factory
# ---------------------------------------------------------------------------

PROVIDER_MAP: dict[str, type[BaseEvaluator]] = {
    "openai": OpenAIEvaluator,
    "gemini": GeminiEvaluator,
    "oracle": OracleEvaluator,
    "chatgpt_web": ChatGPTWebEvaluator,
}


def get_evaluator(
    provider: str,
    model: str | None = None,
    **kwargs,
) -> BaseEvaluator:
    """
    Factory: get an evaluator instance by provider name.

    Examples:
        get_evaluator("openai", model="gpt-4o")
        get_evaluator("gemini", model="gemini-2.0-flash")
        get_evaluator("oracle", model="gemini-3-pro", engine="browser")
    """
    cls = PROVIDER_MAP.get(provider.lower())
    if cls is None:
        raise ValueError(
            f"Unknown provider '{provider}'. Choose from: {', '.join(PROVIDER_MAP)}"
        )

    # Default models per provider
    defaults: dict[str, str] = {
        "openai": "gpt-4o",
        "gemini": "gemini-2.0-flash",
        "oracle": "gemini-2.0-flash",
        "chatgpt_web": "auto",
    }

    return cls(model=model or defaults[provider.lower()], **kwargs)
