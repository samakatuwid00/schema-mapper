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
