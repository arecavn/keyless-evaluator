"""Typer CLI entry-point for keyless-evaluator."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from dotenv import load_dotenv
from rich.console import Console

from evaluators import PROVIDER_MAP, get_evaluator
from models import (
    EvaluationRequest,
    SearchResult,
)
from renderer import (
    render_detail_panel,
    render_result_table,
    render_summary,
)

load_dotenv()

app = typer.Typer(
    name="keyless-eval",
    help=(
        "🔑 keyless-evaluator — LLM-as-judge search evaluation.\n\n"
        "Score search results 0–3 (Irrelevant → Highly Relevant) using LLM-as-judge.\n"
        "Default: gemini — free 1500 req/day with GEMINI_API_KEY from aistudio.google.com.\n"
        "No-key option: chatgpt_web — uses anonymous ChatGPT web session via browser."
    ),
    rich_markup_mode="rich",
)

console = Console()
err_console = Console(stderr=True)


# ---------------------------------------------------------------------------
# eval command
# ---------------------------------------------------------------------------

@app.command("eval")
def cmd_eval(
    input_text: str = typer.Option(..., "--input", "-i", "--query", "-q", help="The search query or evaluation input"),
    results_file: Optional[Path] = typer.Option(
        None, "--file", "-f",
        help="Path to JSON file with search results (array of {id, title, snippet?, url?, ...})"
    ),
    provider: str = typer.Option(
        "gemini", "--provider", "-p",
        help="LLM backend: gemini (default, free), chatgpt_web (no key), openai, anthropic"
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m",
        help="Model override: gemini-2.0-flash (default), gpt-4o, claude-3-5-haiku-20241022, ..."
    ),
    detail: bool = typer.Option(
        False, "--detail", "-d",
        help="Show detailed reasoning panels for every result"
    ),
    output_json: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Write full evaluation JSON to this file"
    ),
    query_context: Optional[str] = typer.Option(
        None, "--context", "-c",
        help="Additional context about the query intent"
    ),
) -> None:
    """
    Evaluate search results for an input using an LLM judge.

    [bold]Quick start (free, no credit card):[/bold]
    Set GEMINI_API_KEY from [cyan]aistudio.google.com[/cyan] (1500 free req/day), then:
      keyless-eval eval -q "python async web framework" -f results.json

    [bold]No account / no key:[/bold]
    Use ChatGPT anonymous browser mode:
      keyless-eval eval -q "your query" -f results.json -p chatgpt_web

    [bold]Examples:[/bold]

      # Default: Gemini API (free quota, needs GEMINI_API_KEY)
      keyless-eval eval -q "python async web framework" -f results.json

      # Anonymous ChatGPT web (no account/key!)
      keyless-eval eval -q "best coffee in hanoi" -f results.json -p chatgpt_web

      # OpenAI API
      keyless-eval eval -q "..." -f results.json -p openai -m gpt-4o

      # Anthropic Claude API
      keyless-eval eval -q "..." -f results.json -p anthropic -m claude-opus-4-5

      # Save full JSON output with detail panels
      keyless-eval eval -q "..." -f results.json --detail --output scored.json
    """
    # Load results
    if results_file is None:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
        else:
            err_console.print(
                "[red]Error:[/red] Pass --file or pipe JSON results via stdin.\n"
                "  Example: cat results.json | keyless-eval eval -q 'my query'"
            )
            raise typer.Exit(1)
    else:
        if not results_file.exists():
            err_console.print(
                f"[red]File not found:[/red] {results_file}\n"
            )
            nearby = sorted(Path(".").glob("*.json"))
            if nearby:
                err_console.print(
                    f"[dim]Found nearby JSON files:[/dim] "
                    + "  ".join(str(p) for p in nearby)
                )
                err_console.print(
                    f"\n  Try: [bold]keyless-eval eval -q \"{input_text}\" -f {nearby[0]}[/bold]"
                )
            else:
                err_console.print(
                    "[dim]No JSON files found in current directory.[/dim]\n"
                    "  Generate an example: [bold]keyless-eval example[/bold]\n"
                    "  Then run:            [bold]keyless-eval eval -q \"your input\" -f example_results.json[/bold]"
                )
            raise typer.Exit(1)
        raw = results_file.read_text(encoding="utf-8")

    try:
        raw_results = json.loads(raw)
    except json.JSONDecodeError as e:
        err_console.print(f"[red]Invalid JSON in results file:[/red] {e}")
        raise typer.Exit(1)

    if isinstance(raw_results, dict) and "results" in raw_results:
        raw_results = raw_results["results"]

    if not isinstance(raw_results, list):
        err_console.print("[red]Results must be a JSON array.[/red]")
        raise typer.Exit(1)

    results = [SearchResult(**r) if isinstance(r, dict) else r for r in raw_results]
    request = EvaluationRequest(
        input=input_text,
        query_context=query_context,
        results=results,
    )

    evaluator = get_evaluator(provider, model=model)

    auth_note = (
        "[dim]anonymous browser[/dim] (no key needed)"
        if provider == "chatgpt_web"
        else "[yellow]API key[/yellow]"
    )
    console.print(
        f"\n[bold]🔮 Evaluating[/bold] [cyan]{len(results)}[/cyan] results "
        f"for input: [bold italic]'{input_text}'[/bold italic]\n"
        f"   Backend: [dim]{provider}/{evaluator.model}[/dim]  Auth: {auth_note}\n"
    )

    with console.status("[bold green]Evaluating...[/bold green]"):
        try:
            response = asyncio.run(evaluator.evaluate(request))
        except Exception as exc:
            err_console.print(f"[red]Evaluation failed:[/red] {exc}")
            raise typer.Exit(1)

    render_result_table(response)
    if detail:
        render_detail_panel(response)
    render_summary(response)

    if output_json:
        output_json.write_text(
            json.dumps(response.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        console.print(f"\n[dim]Results saved to:[/dim] {output_json}")


# ---------------------------------------------------------------------------
# detail command
# ---------------------------------------------------------------------------

@app.command("detail")
def cmd_detail(
    results_json: Path = typer.Argument(..., help="JSON file from a previous 'eval --output' run"),
    index: int = typer.Argument(0, help="0-based index of the result to show detail for"),
) -> None:
    """Show detailed reasoning for a specific result from a saved evaluation."""
    from models import EvaluationResponse

    data = json.loads(results_json.read_text(encoding="utf-8"))
    response = EvaluationResponse(**data)

    if index >= len(response.scores):
        err_console.print(f"[red]Index {index} out of range (0–{len(response.scores)-1})[/red]")
        raise typer.Exit(1)

    render_detail_panel(response, result_index=index)


# ---------------------------------------------------------------------------
# example command
# ---------------------------------------------------------------------------

@app.command("example")
def cmd_example(
    output: Path = typer.Option(Path("example_results.json"), "--output", "-o"),
) -> None:
    """Generate an example results.json to get started quickly."""
    example = {
        "results": [
            {
                "id": "result_001",
                "title": "FastAPI — Modern, fast web framework for building APIs",
                "snippet": "FastAPI is a modern, fast (high-performance), web framework for building APIs with Python 3.8+ based on standard Python type hints.",
                "url": "https://fastapi.tiangolo.com/",
                "metadata": {"category": "web-framework", "language": "Python"}
            },
            {
                "id": "result_002",
                "title": "Django REST Framework",
                "snippet": "Django REST framework is a powerful and flexible toolkit for building Web APIs in Django.",
                "url": "https://www.django-rest-framework.org/",
                "metadata": {"category": "web-framework", "language": "Python"}
            },
            {
                "id": "result_003",
                "title": "Node.js Express Framework",
                "snippet": "Express is a minimal and flexible Node.js web application framework.",
                "url": "https://expressjs.com/",
                "metadata": {"category": "web-framework", "language": "JavaScript"}
            },
            {
                "id": "result_004",
                "title": "Best Python Chili Recipe",
                "snippet": "A delicious and hearty chili recipe using fresh ingredients.",
                "url": "https://cooking.example.com/chili",
                "metadata": {"category": "food"}
            },
        ]
    }
    output.write_text(json.dumps(example, indent=2, ensure_ascii=False), encoding="utf-8")
    console.print(f"[green]Example results written to:[/green] {output}")
    console.print(
        "\nEvaluate with Gemini free quota (set GEMINI_API_KEY first):\n"
        f"  [bold]keyless-eval eval -q \"python async web framework\" -f {output}[/bold]\n"
        "\nOr with no account/key (anonymous ChatGPT browser):\n"
        f"  [bold]keyless-eval eval -q \"python async web framework\" -f {output} -p chatgpt_web[/bold]\n"
    )


# ---------------------------------------------------------------------------
# providers command
# ---------------------------------------------------------------------------

@app.command("providers")
def cmd_providers() -> None:
    """List available LLM providers and their required environment variables."""
    from rich.table import Table
    from rich import box

    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
    table.add_column("Provider", style="cyan bold")
    table.add_column("Default Model")
    table.add_column("Env Variable(s)")
    table.add_column("Notes")

    rows = [
        (
            "gemini [green](default)[/green]",
            "gemini-2.0-flash",
            "GEMINI_API_KEY",
            "Free 1500 req/day via Google AI Studio. Best default choice.",
        ),
        (
            "chatgpt_web",
            "auto",
            "[green]None[/green] (anonymous)",
            "Anonymous ChatGPT web via Playwright. No account or key needed.",
        ),
        (
            "openai",
            "gpt-4o",
            "OPENAI_API_KEY",
            "Direct OpenAI API. Works with gpt-4o, gpt-4o-mini, etc.",
        ),
        (
            "anthropic",
            "claude-3-5-haiku-20241022",
            "ANTHROPIC_API_KEY",
            "Anthropic Claude API. Great reasoning quality.",
        ),
    ]

    for row in rows:
        table.add_row(*row)

    console.print("\n[bold]Available Providers[/bold]\n")
    console.print(table)
    console.print(
        "\n[bold green]Free & easy (recommended):[/bold green]\n"
        "  1. Get a free GEMINI_API_KEY at [cyan]aistudio.google.com[/cyan]\n"
        "  2. Add to .env: [bold]GEMINI_API_KEY=AI...[/bold]\n"
        "  3. Run: [bold]keyless-eval eval -q \"your input\" -f results.json[/bold]\n"
        "\n[bold yellow]No account, no key — ChatGPT anonymous browser:[/bold yellow]\n"
        "  [bold]keyless-eval eval -q \"input_text\" -f results.json -p chatgpt_web[/bold]\n"
        "  (Opens a Chrome window automatically)\n"
        "\n[dim]Optional — add to .env:[/dim]\n"
        "  OPENAI_API_KEY=sk-...    # for -p openai\n"
        "  ANTHROPIC_API_KEY=sk-ant-...  # for -p anthropic\n"
    )


# ---------------------------------------------------------------------------
# serve command
# ---------------------------------------------------------------------------

@app.command("serve")
def cmd_serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="API server host address"),
    port: int = typer.Option(8000, "--port", "-p", help="API server port"),
) -> None:
    """Start the FastAPI HTTP server (for integrating as a microservice)."""
    try:
        import uvicorn
    except ImportError as e:
        err_console.print("[red]Run `uv add uvicorn fastapi` to use this command.[/red]")
        raise typer.Exit(1) from e

    console.print(
        f"\n[bold green]Starting Keyless Evaluator API[/bold green] on [cyan]http://{host}:{port}[/cyan]\n"
        "  [dim]• Endpoint: POST /v1/evaluate[/dim]\n"
        "  [dim]• Docs:     http://127.0.0.1:8000/docs[/dim]\n"
        "  [dim]• Health:   http://127.0.0.1:8000/health[/dim]\n"
    )

    uvicorn.run("server:app", host=host, port=port, reload=False)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
