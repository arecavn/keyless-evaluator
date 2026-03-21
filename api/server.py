"""FastAPI server for keyless-evaluator."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import traceback
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from adapter import adapt_raw_input
from cache import cache_get, cache_set
from evaluators import PROVIDER_MAP, _DEFAULT_MODELS, _compute_ndcg, get_evaluator
from models import EvaluationRequest, EvaluationRequestBody, EvaluationResponse, SearchResult


# ---------------------------------------------------------------------------
# Server error logger — writes to logs/server.log
# ---------------------------------------------------------------------------

_server_logger = logging.getLogger("keyless_evaluator.server")
_server_logger_ready = False


def _ensure_server_logger() -> None:
    global _server_logger_ready
    if _server_logger_ready:
        return
    log_dir = os.path.join(os.getcwd(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    handler = logging.FileHandler(os.path.join(log_dir, "server.log"), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    _server_logger.addHandler(handler)
    _server_logger.setLevel(logging.DEBUG)
    _server_logger.propagate = False
    _server_logger_ready = True


def create_app() -> FastAPI:

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import subprocess, sys
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=False,
        )
        yield

    _raw_origins = os.environ.get("ALLOWED_ORIGINS", "")
    allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()] or ["*"]

    app = FastAPI(
        title="Keyless Evaluator API",
        description=(
            "Score search results 0–3 with reason/summary using any LLM.\n\n"
            "**Single endpoint** — `POST /v1/evaluate` accepts:\n"
            "- `output` as a **plain string** (single document to score)\n"
            "- `output` as a **JSON object/array** (raw search API response, auto-adapted)\n\n"
            "**Custom prompt** — pass `prompt` to replace the built-in scoring rubric with your own "
            "(job search, candidate matching, product search, etc.).\n\n"
            "**Default backend**: Gemini Flash (free — set `GEMINI_API_KEY` from "
            "[Google AI Studio](https://aistudio.google.com/apikey)).\n\n"
            "**Anonymous backend**: `?provider=chatgpt_web` — no account or key needed; "
            "returns the actual ChatGPT model name in the response."
        ),
        version="0.2.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization"],
    )

    @app.post("/v1/evaluate", response_model=EvaluationResponse)
    async def evaluate(
        body: EvaluationRequestBody,
        provider: Annotated[
            str,
            Query(description=f"LLM backend: {' | '.join(PROVIDER_MAP)}")
        ] = "gemini",
        model: Annotated[
            str | None,
            Query(description="Model name override (optional, uses provider default)")
        ] = None,
    ) -> EvaluationResponse:
        """
        Evaluate ``input`` against ``output`` using an LLM judge.

        **``output`` can be:**
        - A **string** — treated as a single document/passage to score
        - A **JSON object/array** — raw response from any search API, fields are auto-detected

        **``prompt``** replaces the built-in TREC 0–3 rubric when provided.
        Write your own scoring criteria (job match, candidate ranking, product relevance, etc.).

        **Providers:**
        - `gemini` (default) — free 1500 req/day, set `GEMINI_API_KEY`
        - `chatgpt_web` — no account/key, returns detected model name
        - `openai` — set `OPENAI_API_KEY`
        - `anthropic` — set `ANTHROPIC_API_KEY`
        """
        # Convert output (str or JSON) → list[SearchResult]
        if isinstance(body.output, str):
            results = [
                SearchResult(
                    id="1",
                    title=body.input[:200],
                    snippet=body.output,
                )
            ]
        else:
            try:
                results = adapt_raw_input(
                    raw=body.output,
                    mapping=body.mapping,
                    max_results=body.max_results,
                )
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e))

        if not results:
            raise HTTPException(status_code=400, detail="No results to evaluate.")

        eval_request = EvaluationRequest(
            input=body.input,
            prompt=body.prompt,
            prompt_preset=body.prompt_preset,
            results=results,
            response_language=body.response_language,
            tag=body.tag,
        )

        try:
            evaluator = get_evaluator(provider=provider, model=model)
            resolved_model = model or _DEFAULT_MODELS.get(provider, "")

            # ── Cache lookup ──────────────────────────────────────────────
            cached = cache_get(eval_request, provider, resolved_model)
            if cached is not None:
                resp, cache_key = cached
                _ensure_server_logger()
                _server_logger.info("cache HIT key=%s", cache_key)
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    content=resp.model_dump(),
                    headers={"X-Cache": "HIT", "X-Cache-Key": cache_key},
                )

            if body.batch_size is None or body.batch_size >= len(results):
                resp = await evaluator.evaluate(eval_request)
                cache_set(eval_request, provider, resolved_model, resp)
                return resp

            # Batched evaluation: split results into chunks, merge scores
            chunks = [
                results[i : i + body.batch_size]
                for i in range(0, len(results), body.batch_size)
            ]
            all_scores = []
            total_prompt_tokens = 0
            total_completion_tokens = 0
            last_resp = None

            for i, chunk in enumerate(chunks):
                if body.sleep is not None:
                    delay = random.uniform(body.sleep, body.sleep * 2.5) + max(0.0, random.gauss(0, 1.0))
                    await asyncio.sleep(delay)

                chunk_req = EvaluationRequest(
                    input=eval_request.input,
                    prompt=eval_request.prompt,
                    prompt_preset=eval_request.prompt_preset,
                    query_context=eval_request.query_context,
                    results=chunk,
                    response_language=eval_request.response_language,
                    tag=eval_request.tag,
                )
                resp = await evaluator.evaluate(chunk_req)
                all_scores.extend(resp.scores)
                if resp.prompt_tokens is not None:
                    total_prompt_tokens += resp.prompt_tokens
                if resp.completion_tokens is not None:
                    total_completion_tokens += resp.completion_tokens
                last_resp = resp

            merged = EvaluationResponse(
                input=eval_request.input,
                model=last_resp.model,
                provider=last_resp.provider,
                scores=all_scores,
                prompt_tokens=total_prompt_tokens or None,
                completion_tokens=total_completion_tokens or None,
            )
            merged.ndcg = _compute_ndcg(all_scores)
            cache_set(eval_request, provider, resolved_model, merged)
            return merged

        except ValueError as e:
            _ensure_server_logger()
            _server_logger.warning("400 ValueError provider=%s model=%s input=%r: %s", provider, model, body.input[:80], e)
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            _ensure_server_logger()
            _server_logger.error(
                "500 %s provider=%s model=%s input=%r\n%s",
                type(e).__name__, provider, model, body.input[:80],
                traceback.format_exc(),
            )
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/health")
    async def health_check():
        """Liveness probe."""
        from presets import PRESETS
        return {
            "status": "ok",
            "version": "0.2.0",
            "default_provider": "gemini",
            "providers": list(PROVIDER_MAP.keys()),
            "prompt_presets": list(PRESETS.keys()),
            "gemini_key_set": bool(os.environ.get("GEMINI_API_KEY")),
            "openai_key_set": bool(os.environ.get("OPENAI_API_KEY")),
            "anthropic_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
        }

    @app.get("/")
    async def root():
        return {"message": "Keyless Evaluator API", "docs": "/docs", "health": "/health"}

    return app


app = create_app()
