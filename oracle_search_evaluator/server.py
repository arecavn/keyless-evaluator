"""FastAPI server for oracle-search-evaluator.

Provides a HTTP API to evaluate search results using configurable LLM backends.
Default: Gemini Flash (free, 1500 req/day, no credit card) via GEMINI_API_KEY.
Optional: ChatGPT web anonymous mode via ?provider=chatgpt_web (no account needed).
"""

from fastapi import FastAPI, HTTPException, Query
from contextlib import asynccontextmanager
from typing import Annotated

from oracle_search_evaluator.evaluators import get_evaluator
from oracle_search_evaluator.models import EvaluationRequest, EvaluationResponse


def create_app() -> FastAPI:
    """Factory to create the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import subprocess, sys
        # playwright install is a no-op if the browser is already present
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
        )
        yield

    app = FastAPI(
        title="Oracle Search Evaluator API",
        description=(
            "Rank search results 0-3 with reason/summary using LLMs.\n\n"
            "**Default backend**: Gemini Flash (free — set `GEMINI_API_KEY` from [Google AI Studio](https://aistudio.google.com/apikey)).\n\n"
            "**Anonymous backend**: Pass `?provider=chatgpt_web` to use ChatGPT without an account."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.post("/v1/evaluate", response_model=EvaluationResponse)
    async def evaluate_results(
        request: EvaluationRequest,
        provider: Annotated[str, Query(description="LLM backend: gemini | chatgpt_web | openai | oracle")] = "gemini",
        model: Annotated[str | None, Query(description="Model name (optional, uses provider default)")] = None,
    ) -> EvaluationResponse:
        """
        Evaluate a query and a list of search hits.
        Returns a 0-3 relevance score with reasons for each item.

        **Providers:**
        - `gemini` (default) – Requires `GEMINI_API_KEY` env var (free from aistudio.google.com)
        - `chatgpt_web` – No account/key needed; uses ChatGPT anonymous web API
        - `openai` – Requires `OPENAI_API_KEY`
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

    @app.get("/health")
    async def health_check():
        import os
        return {
            "status": "ok",
            "default_provider": "gemini",
            "gemini_key_set": bool(os.environ.get("GEMINI_API_KEY")),
        }

    return app

app = create_app()
