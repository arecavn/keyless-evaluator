"""FastAPI server for keyless-evaluator."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from adapter import adapt_raw_input
from evaluators import PROVIDER_MAP, get_evaluator
from models import EvaluationRequest, EvaluationRequestBody, EvaluationResponse, SearchResult


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
            results=results,
        )

        try:
            evaluator = get_evaluator(provider=provider, model=model)
            return await evaluator.evaluate(eval_request)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/health")
    async def health_check():
        """Liveness probe."""
        return {
            "status": "ok",
            "version": "0.2.0",
            "default_provider": "gemini",
            "providers": list(PROVIDER_MAP.keys()),
            "gemini_key_set": bool(os.environ.get("GEMINI_API_KEY")),
            "openai_key_set": bool(os.environ.get("OPENAI_API_KEY")),
            "anthropic_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
        }

    @app.get("/")
    async def root():
        return {"message": "Keyless Evaluator API", "docs": "/docs", "health": "/health"}

    return app


app = create_app()
