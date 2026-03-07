"""Rich terminal renderer for evaluation results."""

from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import print as rprint

from models import EvaluationResponse, RelevanceScore

console = Console()

# Score badge styles
_SCORE_STYLE: dict[int, str] = {
    0: "bold red",
    1: "bold yellow",
    2: "bold cyan",
    3: "bold green",
}


def render_result_table(resp: EvaluationResponse) -> None:
    """Print a summary table of all scored results."""
    table = Table(
        title=f"[bold]Search Evaluation Results[/bold]",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold white on #2b2b2b",
        expand=True,
        padding=(0, 1),
    )

    table.add_column("#", justify="right", width=3, style="dim")
    table.add_column("ID", justify="left", max_width=20)
    table.add_column("Title", justify="left", ratio=3)
    table.add_column("Score", justify="center", width=12)
    table.add_column("Summary", justify="left", ratio=4)

    for i, score in enumerate(resp.scores, 1):
        s = score.score
        score_cell = Text(f"{s.emoji} {s.value} · {s.label}", style=_SCORE_STYLE[s.value])
        table.add_row(
            str(i),
            str(score.result_id),
            score.title[:70],
            score_cell,
            score.reason_summary,
        )

    console.print(table)


def render_detail_panel(resp: EvaluationResponse, result_index: int | None = None) -> None:
    """Print detailed reasoning for results (or a single result if index given)."""
    items = resp.scores
    if result_index is not None:
        items = [resp.scores[result_index]]

    for score in items:
        s = score.score
        header = Text()
        header.append(f"{s.emoji} ", style=_SCORE_STYLE[s.value])
        header.append(f"[{s.value}/3] ", style="bold white")
        header.append(score.title, style="bold")

        body = Text()
        body.append("Summary: ", style="dim")
        body.append(score.reason_summary + "\n\n", style="italic")
        body.append("Detail:\n", style="dim")
        body.append(score.reason_detail)

        console.print(
            Panel(
                body,
                title=header,
                border_style=_SCORE_STYLE[s.value].split()[-1],  # color part
                padding=(1, 2),
            )
        )


def render_summary(resp: EvaluationResponse) -> None:
    """Print a one-line stats summary."""
    dist = {v: 0 for v in range(4)}
    for s in resp.scores:
        dist[s.score.value] += 1

    score_dist = "  ".join(
        f"[{_SCORE_STYLE[v]}]{RelevanceScore(v).emoji} {v}: {dist[v]}[/]"
        for v in range(3, -1, -1)
    )

    ndcg_str = f"nDCG: [bold]{resp.ndcg:.4f}[/bold]  " if resp.ndcg is not None else ""
    avg_str = f"Avg: [bold]{resp.average_score:.2f}/3[/bold]  "
    model_str = f"Model: [dim]{resp.provider}/{resp.model}[/dim]"
    tokens_str = ""
    if resp.prompt_tokens or resp.completion_tokens:
        tokens_str = f"  Tokens: [dim]{resp.prompt_tokens}↑ {resp.completion_tokens}↓[/dim]"

    console.print(
        Panel(
            f"{score_dist}\n\n{ndcg_str}{avg_str}{model_str}{tokens_str}",
            title=f"[bold]Input:[/bold] {resp.input}",
            border_style="#5865f2",
            padding=(0, 2),
        )
    )
