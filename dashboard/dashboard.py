"""
Live Terminal Dashboard — Part E Bonus
Purplle Tech Challenge 2026

Shows real-time store metrics updating as events flow in.
Uses the `rich` library for a professional terminal UI.

Usage:
    python dashboard/dashboard.py --store ST1008 --api http://localhost:8000
"""

import time
import argparse
import urllib.request
import json
from datetime import datetime
from rich.console import Console
from rich.table   import Table
from rich.panel   import Panel
from rich.columns import Columns
from rich.text    import Text
from rich.live    import Live
from rich.layout  import Layout
from rich.align   import Align


console = Console()


def fetch(url: str) -> dict:
    """Fetch JSON from API. Returns empty dict on any error."""
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return {}


def build_metrics_panel(metrics: dict) -> Panel:
    """Build the top metrics panel."""
    if not metrics:
        return Panel("[red]API unavailable[/red]", title="Metrics")

    visitors   = metrics.get("unique_visitors", 0)
    conversion = metrics.get("conversion", {})
    abandon    = metrics.get("abandonment", {})
    queue      = metrics.get("queue_depth", {})

    conv_rate  = conversion.get("rate", 0.0)
    conv_pct   = f"{conv_rate*100:.1f}%"
    conv_color = "green" if conv_rate > 0.3 else "yellow" if conv_rate > 0.1 else "red"

    aband_rate  = abandon.get("rate", 0.0)
    aband_color = "green" if aband_rate < 0.2 else "yellow" if aband_rate < 0.4 else "red"

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="bold cyan", width=28)
    table.add_column("Value",  width=20)

    table.add_row("Unique Visitors Today",
                  f"[bold white]{visitors}[/bold white]")
    table.add_row("Conversion Rate",
                  f"[bold {conv_color}]{conv_pct}[/bold {conv_color}]")
    table.add_row("Converted / Total",
                  f"{conversion.get('converted_count',0)} / "
                  f"{conversion.get('total_visitors',0)}")
    table.add_row("Queue Abandonment Rate",
                  f"[{aband_color}]{aband_rate*100:.1f}%[/{aband_color}]")
    table.add_row("Abandon / Join",
                  f"{abandon.get('abandon_count',0)} / "
                  f"{abandon.get('join_count',0)}")

    if queue:
        for zone, depth in queue.items():
            color = "red" if depth >= 10 else "yellow" if depth >= 5 else "green"
            table.add_row(f"Queue Depth [{zone}]",
                          f"[{color}]{depth}[/{color}]")

    return Panel(table, title="[bold]Store Metrics[/bold]",
                 border_style="blue")


def build_funnel_panel(funnel: dict) -> Panel:
    """Build the conversion funnel panel."""
    if not funnel:
        return Panel("[red]No funnel data[/red]", title="Funnel")

    stages  = funnel.get("stages", [])
    overall = funnel.get("overall_conversion_pct", 0.0)

    table = Table(show_header=True, box=None, padding=(0, 1))
    table.add_column("Stage",    style="bold cyan", width=22)
    table.add_column("Count",    justify="right",   width=8)
    table.add_column("Drop-off", justify="right",   width=10)
    table.add_column("Bar",                         width=20)

    max_count = max((s.get("count", 0) for s in stages), default=1) or 1

    for stage in stages:
        count   = stage.get("count", 0)
        dropoff = stage.get("dropoff_pct", 0.0)
        label   = stage.get("label", stage.get("stage", ""))
        bar_len = int((count / max_count) * 18)
        bar     = "█" * bar_len + "░" * (18 - bar_len)
        d_color = "red" if dropoff > 40 else "yellow" if dropoff > 20 else "green"

        table.add_row(
            label,
            str(count),
            f"[{d_color}]{dropoff:.1f}%[/{d_color}]" if dropoff > 0 else "—",
            f"[cyan]{bar}[/cyan]",
        )

    return Panel(
        table,
        title=f"[bold]Conversion Funnel[/bold] "
              f"[dim](overall: {overall:.1f}%)[/dim]",
        border_style="blue",
    )


def build_heatmap_panel(heatmap: dict) -> Panel:
    """Build the zone heatmap panel."""
    if not heatmap or not heatmap.get("zones"):
        return Panel("[dim]No zone data yet[/dim]", title="Zone Heatmap")

    zones      = heatmap.get("zones", {})
    confidence = heatmap.get("data_confidence", "OK")
    conf_color = "yellow" if confidence == "LOW" else "green"

    table = Table(show_header=True, box=None, padding=(0, 1))
    table.add_column("Zone",      style="bold cyan", width=18)
    table.add_column("Visits",    justify="right",   width=8)
    table.add_column("Avg Dwell", justify="right",   width=12)
    table.add_column("Heat",                         width=22)

    sorted_zones = sorted(
        zones.items(),
        key=lambda x: x[1].get("normalised_score", 0),
        reverse=True,
    )

    for zone_id, data in sorted_zones:
        score   = data.get("normalised_score", 0)
        visits  = data.get("visit_count", 0)
        dwell_s = data.get("avg_dwell_ms", 0) / 1000
        bar_len = int(score / 5)
        bar     = "█" * bar_len + "░" * (20 - bar_len)
        heat_color = (
            "red"    if score >= 80 else
            "yellow" if score >= 50 else
            "green"
        )
        table.add_row(
            zone_id,
            str(visits),
            f"{dwell_s:.0f}s",
            f"[{heat_color}]{bar}[/{heat_color}]",
        )

    return Panel(
        table,
        title=f"[bold]Zone Heatmap[/bold] "
              f"[{conf_color}][confidence: {confidence}][/{conf_color}]",
        border_style="blue",
    )


def build_anomalies_panel(anomalies: dict) -> Panel:
    """Build the anomalies panel."""
    items = anomalies.get("anomalies", []) if anomalies else []

    if not items:
        return Panel(
            "[green]✓ No active anomalies[/green]",
            title="Anomalies",
            border_style="green",
        )

    table = Table(show_header=True, box=None, padding=(0, 1))
    table.add_column("Severity", width=10)
    table.add_column("Type",     width=24)
    table.add_column("Action",   width=45)

    severity_colors = {
        "CRITICAL": "bold red",
        "WARN":     "yellow",
        "INFO":     "cyan",
    }

    for a in items:
        sev    = a.get("severity", "INFO")
        color  = severity_colors.get(sev, "white")
        atype  = a.get("anomaly_type", "")
        action = a.get("suggested_action", "")[:80]

        table.add_row(
            f"[{color}]{sev}[/{color}]",
            atype,
            f"[dim]{action}[/dim]",
        )

    border = "red" if any(
        a.get("severity") == "CRITICAL" for a in items
    ) else "yellow"

    return Panel(
        table,
        title=f"[bold]Anomalies[/bold] [dim]({len(items)} active)[/dim]",
        border_style=border,
    )


def build_header(store_id: str, health: dict) -> Panel:
    """Build the top header bar."""
    status     = health.get("status", "UNKNOWN") if health else "UNKNOWN"
    color      = "green" if status == "OK" else "red"
    store_info = health.get("stores", {}).get(store_id, {})
    lag        = store_info.get("lag_seconds")
    lag_str    = f"{lag}s lag" if lag is not None else "no data"
    now        = datetime.now().strftime("%H:%M:%S")

    text = Text(justify="center")
    text.append("🏪 Purplle Store Intelligence  ", style="bold white")
    text.append(f"│  Store: {store_id}  ", style="cyan")
    text.append(f"│  API: [{color}]{status}[/{color}]  ")
    text.append(f"│  Feed: {lag_str}  ", style="dim")
    text.append(f"│  {now}", style="dim")

    return Panel(Align.center(text), border_style=color)


def run_dashboard(store_id: str, api_base: str, refresh: int = 5):
    """Main dashboard loop."""
    console.clear()

    with Live(console=console, refresh_per_second=1, screen=True) as live:
        while True:
            metrics   = fetch(f"{api_base}/stores/{store_id}/metrics")
            funnel    = fetch(f"{api_base}/stores/{store_id}/funnel")
            heatmap   = fetch(f"{api_base}/stores/{store_id}/heatmap")
            anomalies = fetch(f"{api_base}/stores/{store_id}/anomalies")
            health    = fetch(f"{api_base}/health")

            layout = Layout()
            layout.split_column(
                Layout(name="header", size=3),
                Layout(name="top",    size=16),
                Layout(name="bottom", size=16),
            )
            layout["top"].split_row(
                Layout(name="metrics", ratio=1),
                Layout(name="funnel",  ratio=1),
            )
            layout["bottom"].split_row(
                Layout(name="heatmap",   ratio=1),
                Layout(name="anomalies", ratio=1),
            )

            layout["header"].update(build_header(store_id, health))
            layout["metrics"].update(build_metrics_panel(metrics))
            layout["funnel"].update(build_funnel_panel(funnel))
            layout["heatmap"].update(build_heatmap_panel(heatmap))
            layout["anomalies"].update(build_anomalies_panel(anomalies))

            live.update(layout)
            time.sleep(refresh)


def main():
    parser = argparse.ArgumentParser(
        description="Purplle Store Intelligence — Live Dashboard"
    )
    parser.add_argument(
        "--store",
        default = "ST1008",
        help    = "Store ID to monitor",
    )
    parser.add_argument(
        "--api",
        default = "http://localhost:8000",
        help    = "API base URL",
    )
    parser.add_argument(
        "--refresh",
        type    = int,
        default = 5,
        help    = "Refresh interval in seconds",
    )
    args = parser.parse_args()
    run_dashboard(args.store, args.api, args.refresh)


if __name__ == "__main__":
    main()