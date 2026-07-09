"""
Terminal UI helpers for interactive schema mapper.

Uses rich library for beautiful terminal output:
- Colored tables for field mappings
- Interactive prompts for user decisions
- Progress bars for backfill/worker
- Panels for section headers
"""
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn, TaskProgressColumn
from rich.prompt import Confirm, Prompt
from rich.text import Text
from rich import box

console = Console()

COLORS = {
    "success": "green",
    "error": "red",
    "warning": "yellow",
    "info": "cyan",
    "pending": "yellow",
    "accepted": "green",
    "rejected": "red",
    "cross_table": "magenta",
    "header": "bold white",
    "source": "blue",
    "target": "purple",
}


def print_header(text: str):
    console.print(Panel(text, style=COLORS["header"], box=box.DOUBLE))


def print_field_mapping_table(mappings: list[dict]):
    table = Table(box=box.ROUNDED, show_lines=True)
    table.add_column("Source Column", style=COLORS["source"])
    table.add_column("Target Column", style=COLORS["target"])
    table.add_column("Confidence", justify="right")
    table.add_column("Status")

    for m in mappings:
        status = m["status"]
        status_style = COLORS.get(status, "white")
        confidence = f"{m['confidence']:.2f}"

        icons = {
            "accepted": "accepted",
            "pending": "pending",
            "rejected": "rejected",
            "cross_table": "cross-table",
        }
        status_icon = icons.get(status, status)

        table.add_row(
            m["source_column"],
            m.get("target_column") or "-",
            confidence,
            Text(status_icon, style=status_style),
        )

    console.print(table)


def prompt_yes_no(question: str, default: bool = True) -> bool:
    suffix = " [Y/n]: " if default else " [y/N]: "
    return Confirm.ask(question + suffix, default=default)


def prompt_choice(question: str, choices: list[str]) -> str:
    return Prompt.ask(question, choices=choices)


def prompt_text(question: str, default: str = "") -> str:
    return Prompt.ask(question, default=default)


def print_progress(current: int, total: int, label: str = "Progress"):
    with Progress(
        TextColumn(f"[bold blue]{label}"),
        BarColumn(bar_width=40),
        TaskProgressColumn(),
        TextColumn("({task.completed}/{task.total})"),
        console=console,
    ) as progress:
        task = progress.add_task(label, total=total)
        progress.update(task, completed=current)


def print_deployment_summary(
    source: str, target: str, columns: list[dict], collation: str
):
    lines = [
        f"Source: {source}",
        f"Target: {target}",
        f"Columns: {len(columns)} business + 7 envelope = {len(columns) + 7} total",
        f"Collation: {collation}",
        "",
        "Column Types:",
    ]
    for col in columns:
        lines.append(f"  {col['name']}: {col['type']}")

    console.print(Panel("\n".join(lines), title="Deployment Summary", border_style="cyan"))


def print_cross_table_candidates(candidates: list[dict]):
    if not candidates:
        console.print("[dim]No cross-table candidates found.[/dim]")
        return

    lines = []
    for i, c in enumerate(candidates, 1):
        lines.append(
            f"{i}. {c['source_column']} -> {c['target_table']}.{c['target_column']} "
            f"(confidence: {c['confidence']:.2f})"
        )
        lines.append(
            f"   Source: {c['source_schema']}.{c['source_table']}.{c['source_column']}"
        )
        lines.append(
            f"   Potential target: {c['target_system']}.{c['target_table']}.{c['target_column']}"
        )
        lines.append("")

    console.print(
        Panel("\n".join(lines), title="Cross-Table Candidates", border_style="magenta")
    )


def print_final_summary(results: list[dict]):
    table = Table(box=box.HEAVY_EDGE, show_lines=True)
    table.add_column("Table", style="bold")
    table.add_column("Status")
    table.add_column("Mappings")
    table.add_column("Backfill")
    table.add_column("Worker")

    for r in results:
        status_style = "green" if r["status"] in ("deployed", "already_deployed") else "yellow"
        status_text = "already deployed" if r["status"] == "already_deployed" else r["status"]
        table.add_row(
            f"{r['source_schema']}.{r['source_table']}",
            Text(status_text, style=status_style),
            f"{r['mappings_accepted']} accepted, {r['cross_table']} cross-table",
            f"{r['backfill_count']}/{r['source_count']}",
            f"{r['worker_delivered']}/{r['backfill_count']}",
        )

    console.print(table)


def print_onboarding_status(entities: list[dict], outbox_stats: list[dict]):
    """Print onboarding status tables."""
    # Onboarding entities table
    table = Table(box=box.HEAVY_EDGE, show_lines=True, title="Onboarding Status")
    table.add_column("Source Table", style="bold", min_width=25)
    table.add_column("Target", min_width=10)
    table.add_column("Staging Table", min_width=25)
    table.add_column("Status", min_width=12)
    table.add_column("Deployed At", min_width=20)
    table.add_column("Deployed By", min_width=12)

    for e in entities:
        status = e.get("status", "unknown")
        status_style = "green" if status == "deployed" else "yellow" if status == "proposed" else "red"
        deployed_at = str(e.get("deployed_at", "-"))[:19] if e.get("deployed_at") else "-"
        deployed_by = e.get("deployed_by") or "-"

        table.add_row(
            f"{e['source_schema']}.{e['source_table']}",
            e.get("target_system", "-"),
            e.get("staging_table") or "(not deployed)",
            Text(status, style=status_style),
            deployed_at,
            deployed_by,
        )

    console.print(table)
    console.print()

    # Outbox status table
    if outbox_stats:
        outbox_table = Table(box=box.HEAVY_EDGE, show_lines=True, title="Outbox Status")
        outbox_table.add_column("Source Entity", style="bold", min_width=15)
        outbox_table.add_column("Status", min_width=12)
        outbox_table.add_column("Count", justify="right", min_width=10)
        outbox_table.add_column("Oldest", min_width=20)

        for o in outbox_stats:
            status = o.get("status", "unknown")
            status_style = "green" if status == "delivered" else "yellow" if status == "pending" else "red"
            oldest = str(o.get("oldest", "-"))[:19] if o.get("oldest") else "-"

            outbox_table.add_row(
                o.get("source_entity", "-"),
                Text(status, style=status_style),
                str(o.get("events", 0)),
                oldest,
            )

        console.print(outbox_table)
