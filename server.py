"""FastAPI server for keyless-evaluator.

Provides a REST API to evaluate search results using configurable LLM backends.
Default: Gemini Flash (free, 1500 req/day) via GEMINI_API_KEY.
Also supports OpenAI, Anthropic, and anonymous ChatGPT Web.

Security notes:
- In production, set ALLOWED_ORIGINS in env (comma-separated) for CORS.
- Rate limiting is the caller's responsibility (reverse proxy / Vercel Edge).
- API keys are read from environment, never echoed in responses.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from adapter import adapt_raw_input
from evaluators import PROVIDER_MAP, get_evaluator
from models import EvaluationRequest, EvaluationResponse, RawEvaluationRequest


def create_app() -> FastAPI:
    """Factory to create the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # playwright install is a no-op if the browser is already present.
        # Only needed for the chatgpt_web provider.
        import subprocess, sys
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=False,  # don't fail startup if playwright isn't in use
        )
        yield

    # CORS — allow configurable origins for production deployments
    _raw_origins = os.environ.get("ALLOWED_ORIGINS", "")
    allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()] or ["*"]

    app = FastAPI(
        title="Keyless Evaluator API",
        description=(
            "Rank search results 0-3 with reason/summary using LLMs.\n\n"
            "**Default backend**: Gemini Flash (free — set `GEMINI_API_KEY` from "
            "[Google AI Studio](https://aistudio.google.com/apikey)).\n\n"
            "**Anonymous backend**: Pass `?provider=chatgpt_web` for no-account ChatGPT.\n\n"
            "**Anthropic**: Pass `?provider=anthropic` with `ANTHROPIC_API_KEY`."
        ),
        version="0.1.0",
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
    async def evaluate_results(
        request: EvaluationRequest,
        provider: Annotated[
            str,
            Query(description=f"LLM backend: {' | '.join(PROVIDER_MAP)}")
        ] = "gemini",
        model: Annotated[
            str | None,
            Query(description="Model name (optional, uses provider default)")
        ] = None,
    ) -> EvaluationResponse:
        """
        Evaluate a query and a list of search hits.
        Returns a 0-3 relevance score with reasons for each item.

        **Providers:**
        - `gemini` (default) – Requires `GEMINI_API_KEY` (free from aistudio.google.com)
        - `chatgpt_web` – No account/key needed; uses ChatGPT anonymous web session
        - `openai` – Requires `OPENAI_API_KEY`
        - `anthropic` – Requires `ANTHROPIC_API_KEY`
        """
        if not request.results:
            raise HTTPException(status_code=400, detail="No search results provided.")

        try:
            evaluator = get_evaluator(provider=provider, model=model)
            return await evaluator.evaluate(request)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/v1/evaluate/raw", response_model=EvaluationResponse)
    async def evaluate_raw(
        request: RawEvaluationRequest,
        provider: Annotated[
            str,
            Query(description=f"LLM backend: {' | '.join(PROVIDER_MAP)}")
        ] = "gemini",
        model: Annotated[
            str | None,
            Query(description="Model name (optional, uses provider default)")
        ] = None,
    ) -> EvaluationResponse:
        """
        Evaluate search results from **any search API response** — no reformatting needed.

        Paste your search API's JSON response body directly into ``raw``.
        The adapter extracts results using ``mapping`` (all fields optional with smart defaults).

        **Minimal example** (auto-detect everything):
        ```json
        {
          "query": "remote jobs",
          "raw": { ...your search API response... }
        }
        ```

        **With explicit mapping** (for non-standard field names):
        ```json
        {
          "query": "remote jobs",
          "raw": { ...your search API response... },
          "mapping": {
            "data_path": "data",
            "id_field": "id",
            "title_field": "jobTitle",
            "snippet_field": "jobDescription",
            "metadata_fields": ["company", "salary", "location", "employmentTypeEn"]
          }
        }
        ```

        **Providers:** same as `/v1/evaluate` — gemini (default), openai, anthropic, chatgpt_web.
        """
        try:
            results = adapt_raw_input(
                raw=request.raw,
                mapping=request.mapping,
                max_results=request.max_results,
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

        eval_request = EvaluationRequest(
            query=request.query,
            query_context=request.query_context,
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
        """Liveness probe — safe to call without auth."""
        return {
            "status": "ok",
            "default_provider": "gemini",
            "providers": list(PROVIDER_MAP.keys()),
            "gemini_key_set": bool(os.environ.get("GEMINI_API_KEY")),
            "openai_key_set": bool(os.environ.get("OPENAI_API_KEY")),
            "anthropic_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
        }

    @app.get("/")
    async def root():
        """Redirect hint — OpenAPI docs are at /docs."""
        return {"message": "Keyless Evaluator API", "docs": "/docs", "health": "/health"}

    return app


app = create_app()
