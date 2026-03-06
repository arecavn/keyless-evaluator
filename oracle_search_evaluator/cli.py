"""Typer CLI entry-point for oracle-search-evaluator."""

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

from oracle_search_evaluator.evaluators import PROVIDER_MAP, get_evaluator
from oracle_search_evaluator.models import (
    EvaluationRequest,
    SearchResult,
)
from oracle_search_evaluator.renderer import (
    render_detail_panel,
    render_result_table,
    render_summary,
)

load_dotenv()

app = typer.Typer(
    name="oracle-eval",
    help=(
        "🔮 oracle-search-evaluator — LLM-as-judge search evaluation.\n\n"
        "Score search results 0–3 (Irrelevant → Highly Relevant) using LLM-as-judge.\n"
        "Default: Oracle browser mode — uses your existing ChatGPT/Gemini login, no API key needed."
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
    query: str = typer.Option(..., "--query", "-q", help="The search query to evaluate"),
    results_file: Optional[Path] = typer.Option(
        None, "--file", "-f",
        help="Path to JSON file with search results (array of {id, title, snippet?, url?, ...})"
    ),
    provider: str = typer.Option(
        "oracle", "--provider", "-p",
        help="LLM backend: oracle (default, browser mode = no API key), openai, gemini"
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m",
        help="Model: gemini-2.0-flash (default), gpt-4o, gemini-3-pro, claude-4.5-sonnet, ..."
    ),
    engine: str = typer.Option(
        "browser", "--engine", "-e",
        help="[oracle] 'browser' = use your ChatGPT/Gemini web login (no key). 'api' = use API key."
    ),
    thinking: str = typer.Option(
        "standard", "--thinking", "-t",
        help="[oracle browser] Thinking depth: light | standard | extended | heavy"
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
    Evaluate search results for a query using an LLM oracle.

    [bold]Default (no API key needed):[/bold]
    Uses Oracle CLI in browser mode — drives your actual Gemini/ChatGPT web session.
    Make sure you're logged into [cyan]gemini.google.com[/cyan] or [cyan]chatgpt.com[/cyan] in Chrome.

    [bold]Examples:[/bold]

      # ✨ Default: Oracle browser → Gemini web (no API key!)
      oracle-eval eval -q "python async web framework" -f results.json

      # Oracle browser → ChatGPT web (no API key!)
      oracle-eval eval -q "best coffee in hanoi" -f results.json -m gpt-4o

      # Oracle browser → Gemini with extended thinking
      oracle-eval eval -q "..." -f results.json -m gemini-3-pro --thinking extended

      # Fallback: Gemini API (needs GEMINI_API_KEY)
      oracle-eval eval -q "..." -f results.json -p gemini -m gemini-2.0-flash

      # Fallback: OpenAI API (needs OPENAI_API_KEY)
      oracle-eval eval -q "..." -f results.json -p openai -m gpt-4o

      # Save full JSON output with detail panels
      oracle-eval eval -q "..." -f results.json --detail --output scored.json
    """
    # Load results
    if results_file is None:
        # Try reading from stdin
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
        else:
            err_console.print(
                "[red]Error:[/red] Pass --file or pipe JSON results via stdin.\n"
                "  Example: cat results.json | oracle-eval eval -q 'my query'"
            )
            raise typer.Exit(1)
    else:
        if not results_file.exists():
            err_console.print(
                f"[red]File not found:[/red] {results_file}\n"
            )
            # Look for any .json files nearby to suggest
            nearby = sorted(Path(".").glob("*.json"))
            if nearby:
                err_console.print(
                    f"[dim]Found nearby JSON files:[/dim] "
                    + "  ".join(str(p) for p in nearby)
                )
                err_console.print(
                    f"\n  Try: [bold]oracle-eval eval -q \"{query}\" -f {nearby[0]}[/bold]"
                )
            else:
                err_console.print(
                    "[dim]No JSON files found in current directory.[/dim]\n"
                    "  Generate an example: [bold]oracle-eval example[/bold]\n"
                    "  Then run:            [bold]oracle-eval eval -q \"your query\" -f example_results.json[/bold]"
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
        query=query,
        query_context=query_context,
        results=results,
    )

    # Build evaluator
    extra_kwargs: dict = {}
    if provider == "oracle":
        extra_kwargs["engine"] = engine
        if thinking != "standard":
            extra_kwargs["extra_args"] = ["--browser-thinking-time", thinking]

    evaluator = get_evaluator(provider, model=model, **extra_kwargs)

    # Describe the auth method to the user
    auth_note = (
        "[green]browser session[/green] (no API key)"
        if provider == "oracle" and engine == "browser"
        else "[yellow]API key[/yellow]"
    )
    console.print(
        f"\n[bold]🔮 Evaluating[/bold] [cyan]{len(results)}[/cyan] results "
        f"for query: [bold italic]\"{query}\"[/bold italic]\n"
        f"   Backend: [dim]{provider}/{evaluator.model}[/dim]  Auth: {auth_note}\n"
    )

    if provider == "oracle" and engine == "browser":
        console.print(
            "[dim]  → Make sure you're logged into gemini.google.com or chatgpt.com in Chrome.[/dim]\n"
        )

    # Run
    with console.status("[bold green]Asking the oracle...[/bold green]"):
        try:
            response = asyncio.run(evaluator.evaluate(request))
        except Exception as exc:
            err_console.print(f"[red]Evaluation failed:[/red] {exc}")
            raise typer.Exit(1)

    # Render
    render_result_table(response)
    if detail:
        render_detail_panel(response)
    render_summary(response)

    # Save output
    if output_json:
        output_json.write_text(
            json.dumps(response.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        console.print(f"\n[dim]Results saved to:[/dim] {output_json}")


# ---------------------------------------------------------------------------
# detail command — show detail for a specific result
# ---------------------------------------------------------------------------

@app.command("detail")
def cmd_detail(
    results_json: Path = typer.Argument(..., help="JSON file from a previous 'eval --output' run"),
    index: int = typer.Argument(0, help="0-based index of the result to show detail for"),
) -> None:
    """Show detailed reasoning for a specific result from a saved evaluation."""
    from oracle_search_evaluator.models import EvaluationResponse

    data = json.loads(results_json.read_text(encoding="utf-8"))
    response = EvaluationResponse(**data)

    if index >= len(response.scores):
        err_console.print(f"[red]Index {index} out of range (0–{len(response.scores)-1})[/red]")
        raise typer.Exit(1)

    render_detail_panel(response, result_index=index)


# ---------------------------------------------------------------------------
# example command — generate a sample input file
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
        "\nEvaluate using your Gemini browser session (no API key):\n"
        f"  [bold]oracle-eval eval -q \"python async web framework\" -f {output}[/bold]\n"
        "\nOr with OpenAI API key:\n"
        f"  [bold]oracle-eval eval -q \"python async web framework\" -f {output} -p openai[/bold]\n"
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
            "oracle [green](default)[/green]",
            "gemini-2.0-flash",
            "[green]None[/green] (browser mode)",
            "Drives your real Gemini/ChatGPT web session via Chrome cookies. "
            "Also accepts OPENAI/GEMINI/ANTHROPIC_API_KEY for --engine api.",
        ),
        (
            "gemini",
            "gemini-2.0-flash",
            "GEMINI_API_KEY",
            "Direct Gemini API. Free quota via Google AI Studio.",
        ),
        (
            "openai",
            "gpt-4o",
            "OPENAI_API_KEY",
            "Direct OpenAI API. Also works with gpt-4o-mini, o1, gpt-5, etc.",
        ),
    ]

    for row in rows:
        table.add_row(*row)

    console.print("\n[bold]Available Providers[/bold]\n")
    console.print(table)
    console.print(
        "\n[bold green]Browser mode (recommended, no API key):[/bold green]\n"
        "  1. Log into [cyan]gemini.google.com[/cyan] or [cyan]chatgpt.com[/cyan] in Chrome\n"
        "  2. Run: [bold]oracle-eval eval -q \"your query\" -f results.json[/bold]\n"
        "     (Oracle CLI opens Chrome automatically and uses your session)\n"
        "\n[bold yellow]Troubleshooting macOS Cookie Errors (missing __Secure-1PSID):[/bold yellow]\n"
        "  If macOS blocks cookie decryption, use ChatGPT with browser-manual-login:\n"
        "  [bold]oracle-eval eval -q \"query\" -f results.json -m gpt-4o[/bold]\n"
        "  This will open a Chrome window and reuse your active ChatGPT session.\n"
        "\n[dim]Optional API key fallback — add to .env:[/dim]\n"
        "  OPENAI_API_KEY=sk-...    # for -p openai or -p oracle -e api -m gpt-4o\n"
        "  GEMINI_API_KEY=AI...     # for -p gemini\n"
    )


# ---------------------------------------------------------------------------
# serve command
# ---------------------------------------------------------------------------

@app.command("serve")
def cmd_serve(
    host: str = typer.Option("127.0.0.1", "--host", "-h", help="API server host address"),
    port: int = typer.Option(8000, "--port", "-p", help="API server port"),
) -> None:
    """Start the FastAPI server to evaluate results over HTTP (uses ChatGPT browser mode)."""
    try:
        import uvicorn
    except ImportError as e:
        err_console.print("[red]Run `uv add uvicorn fastapi` to use this command.[/red]")
        raise typer.Exit(1) from e

    console.print(
        f"\n[bold green]Starting Oracle Web API[/bold green] on [cyan]ttp://{host}:{port}[/cyan]\n"
        "  [dim]• Endpoint: POST /v1/evaluate[/dim]\n"
        "  [dim]• Docs:     http://127.0.0.1:8000/docs[/dim]\n"
        "  [dim]• Backend:  ChatGPT Browser Automation (opens Chrome under the hood)[/dim]\n"
    )

    uvicorn.run("oracle_search_evaluator.server:app", host=host, port=port, reload=False)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
