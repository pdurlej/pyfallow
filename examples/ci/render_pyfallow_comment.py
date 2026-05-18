#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


GROUPS = [
    ("blocking", "Blocking"),
    ("decision_needed", "Decision needed"),
    ("auto_safe", "Auto-fixable"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a fallow-py agent-fix-plan as Markdown.")
    parser.add_argument("report", type=Path, help="Path to fallow-py agent-fix-plan JSON.")
    args = parser.parse_args()
    plan = json.loads(args.report.read_text(encoding="utf-8"))
    print(render_comment(plan))
    return 0


def render_comment(plan: dict[str, Any]) -> str:
    counts = {name: len(plan.get(name, [])) for name, _ in GROUPS}
    total = sum(counts.values())
    lines = ["## fallow-py analysis", ""]
    if total == 0:
        lines.extend(
            [
                "**No findings matched this change.**",
                "",
                "The full machine-readable report is attached as `pyfallow-report.json`.",
            ]
        )
        return "\n".join(lines) + "\n"

    lines.append(
        f"**{total} findings on this change** "
        f"({counts['auto_safe']} auto-fixable, "
        f"{counts['decision_needed']} decision needed, "
        f"{counts['blocking']} blocking):"
    )
    lines.append("")

    for name, title in GROUPS:
        items = plan.get(name, [])
        if not items:
            continue
        lines.append(f"### {title} ({len(items)})")
        for item in items[:20]:
            lines.append(format_item(item))
        if len(items) > 20:
            lines.append(f"- ... {len(items) - 20} more in `pyfallow-report.json`")
        lines.append("")

    lines.append("[View full report](pyfallow-report.json)")
    return "\n".join(lines).rstrip() + "\n"


def format_item(item: dict[str, Any]) -> str:
    location = item.get("path") or "unknown"
    start = item.get("range", {}).get("start", {})
    if start.get("line"):
        location = f"{location}:{start['line']}"
    rule = item.get("rule", "unknown-rule")
    confidence = item.get("confidence", "unknown")
    symbol = f" `{item['symbol']}`" if item.get("symbol") else ""
    message = str(item.get("message") or "No message.").rstrip(".")
    return f"- `{location}` - `{rule}`{symbol} ({confidence}) - {message}"


if __name__ == "__main__":
    raise SystemExit(main())
