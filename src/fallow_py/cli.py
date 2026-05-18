from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from .analysis import LIMITATIONS, analyze, filter_result
from .baseline import compare_with_baseline, create_baseline, read_baseline, write_baseline
from .config import load_config
from .doctor import doctor_payload, format_doctor_text
from .formatters import format_agent_context, format_result
from .models import CONFIDENCE_ORDER, SEVERITY_ORDER, VERSION
from .rule_explain import explain_all_rules, explain_rule, render_explanation
from .summary import summary_from_issue_dicts

TEXT_LIMITATION_FORMATS = {"text", "markdown"}


def supports_limitations_format(fmt: str) -> bool:
    return fmt in TEXT_LIMITATION_FORMATS


def main(argv: list[str] | None = None, *, prog: str = "fallow-py") -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "python":
        argv = argv[1:]
    if argv in (["--version"], ["-V"]):
        print(f"{prog} {VERSION}")
        return 0
    if not argv or argv[0].startswith("-"):
        argv = ["analyze", *argv]
    parser = build_parser(prog=prog)
    args = parser.parse_args(argv)
    _configure_logging(getattr(args, "debug", False), prog)
    try:
        if args.command == "explain":
            return _run_explain(args)
        if args.command == "baseline":
            return _run_baseline(args)
        if args.command == "doctor":
            return _run_doctor(args)
        return _run_analysis(args)
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
        if not getattr(args, "quiet", False):
            print(f"{prog} error: {exc}", file=sys.stderr)
        return 2


def build_parser(prog: str = "fallow-py") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Python static codebase intelligence.")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ["analyze", "dead-code", "deps", "graph", "cycles", "dupes", "health", "boundaries", "agent-context"]:
        child = sub.add_parser(command)
        _add_common(child, agent_context=command == "agent-context", graph=command == "graph")
        if command == "analyze":
            child.add_argument("--language", choices=["python"], default="python")
    baseline = sub.add_parser("baseline")
    baseline_sub = baseline.add_subparsers(dest="baseline_command", required=True)
    create = baseline_sub.add_parser("create")
    _add_common(create)
    compare = baseline_sub.add_parser("compare")
    _add_common(compare)
    doctor = sub.add_parser("doctor")
    _add_doctor(doctor)
    explain = sub.add_parser("explain", help="Explain a fallow-py rule id or slug.")
    explain.add_argument("rule", nargs="?", help="Rule slug such as unused-symbol, or id such as PY031.")
    explain.add_argument("--all", action="store_true", help="Show all rule explanations.")
    explain.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    explain.add_argument("--output")
    explain.add_argument("--quiet", action="store_true")
    explain.add_argument("--debug", action="store_true")
    return parser


def _add_common(parser: argparse.ArgumentParser, agent_context: bool = False, graph: bool = False) -> None:
    parser.add_argument("--root", default=".")
    parser.add_argument("--config")
    parser.add_argument(
        "--format",
        choices=["text", "json", "sarif", "markdown", "mermaid", "dot", "agent-fix-plan"],
        default="markdown" if agent_context else "text",
    )
    parser.add_argument("--output")
    tests = parser.add_mutually_exclusive_group()
    tests.add_argument("--include-tests", action="store_true")
    tests.add_argument("--exclude-tests", action="store_true")
    parser.add_argument("--changed-only", action="store_true")
    parser.add_argument("--since")
    parser.add_argument("--baseline")
    parser.add_argument("--fail-on", choices=["none", "error", "warning", "any"], default="none")
    parser.add_argument("--min-confidence", choices=["low", "medium", "high"], default="low")
    parser.add_argument("--severity-threshold", choices=["info", "warning", "error"], default="info")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--no-framework-heuristics", action="store_true")
    parser.add_argument(
        "--framework",
        choices=["auto", "django", "fastapi", "flask", "celery", "pytest", "click", "typer", "none"],
        default="auto",
    )
    parser.add_argument("--show-limitations", action="store_true")


def _add_doctor(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", default=".")
    parser.add_argument("--config")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--output")
    tests = parser.add_mutually_exclusive_group()
    tests.add_argument("--include-tests", action="store_true")
    tests.add_argument("--exclude-tests", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--debug", action="store_true")


def _run_analysis(args: argparse.Namespace) -> int:
    config = load_config(args.root, args.config)
    _apply_cli_config(config, args)
    result = analyze(config)
    _print_cli_warnings(args, result)
    _log_analysis_warnings(args, result)
    baseline = None
    if getattr(args, "baseline", None):
        baseline = read_baseline(args.baseline)
        comparison = compare_with_baseline(_issues_as_objects(result["issues"]), baseline)
        _mark_baseline_status(result, comparison)
        result["baseline"] = comparison
    filtered = filter_result(result, args.min_confidence, args.severity_threshold)
    output = _format_for_command(filtered, args.command, args.format)
    output = _with_limitations(output, args.format, args.show_limitations)
    _write_or_print(output, args.output)
    exit_result = (
        _focused_result(filtered, args.command)
        if args.command in {"cycles", "dupes", "deps", "dead-code", "health", "boundaries"}
        else filtered
    )
    return _exit_code(exit_result, args.fail_on, baseline_active=baseline is not None)


def _run_baseline(args: argparse.Namespace) -> int:
    config = load_config(args.root, args.config)
    _apply_cli_config(config, args)
    result = analyze(config)
    _print_cli_warnings(args, result)
    _log_analysis_warnings(args, result)
    if args.baseline_command == "create":
        baseline = create_baseline(result)
        output_path = args.output or config.baseline.path
        write_baseline(output_path, baseline)
        if not args.quiet:
            print(f"Wrote baseline with {baseline['summary']['total_issues']} issues to {output_path}")
        return 0
    baseline_path = args.baseline or config.baseline.path
    baseline = read_baseline(baseline_path)
    comparison = compare_with_baseline(_issues_as_objects(result["issues"]), baseline)
    _mark_baseline_status(result, comparison)
    result["baseline"] = comparison
    filtered = filter_result(result, args.min_confidence, args.severity_threshold)
    output = format_result(filtered, args.format, "baseline")
    output = _with_limitations(output, args.format, args.show_limitations)
    _write_or_print(output, args.output)
    return _exit_code(filtered, args.fail_on, baseline_active=True)


def _run_doctor(args: argparse.Namespace) -> int:
    config = load_config(args.root, args.config)
    _apply_doctor_config(config, args)
    result = analyze(config)
    _log_analysis_warnings(args, result)
    payload = doctor_payload(config, result, args.root)
    output = (
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
        if args.format == "json"
        else format_doctor_text(payload)
    )
    _write_or_print(output, args.output)
    return 0


def _run_explain(args: argparse.Namespace) -> int:
    if args.all:
        value = explain_all_rules()
    elif args.rule:
        value = explain_rule(args.rule)
    else:
        raise ValueError("explain requires a rule id/slug or --all")
    _write_or_print(render_explanation(value, args.format), args.output)
    return 0


def _configure_logging(debug: bool, prog: str = "fallow-py") -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.WARNING,
        format=f"{prog} %(levelname)s: %(message)s",
    )


def _log_analysis_warnings(args: argparse.Namespace, result: dict[str, Any]) -> None:
    if not args.debug:
        return
    for warning in result.get("analysis", {}).get("warnings", []):
        logging.debug("analysis warning: %s", warning)


def _print_cli_warnings(args: argparse.Namespace, result: dict[str, Any]) -> None:
    if args.quiet:
        return
    for warning in result.get("analysis", {}).get("warnings", []):
        if warning.get("code") in {"changed-only-deprecated", "changed-only-not-available-non-git"}:
            print(warning["message"], file=sys.stderr)


def _with_limitations(output: str, fmt: str, show_limitations: bool) -> str:
    if not show_limitations or not supports_limitations_format(fmt):
        return output
    heading = "## Limitations" if fmt == "markdown" else "Limitations:"
    lines = [output.rstrip(), "", heading]
    if fmt == "markdown":
        lines.extend(f"- {item}" for item in LIMITATIONS)
    else:
        lines.extend(f"- {item}" for item in LIMITATIONS)
    return "\n".join(lines) + "\n"


def _apply_cli_config(config, args: argparse.Namespace) -> None:
    if args.include_tests:
        config.include_tests = True
    if args.exclude_tests:
        config.include_tests = False
    if args.no_framework_heuristics or args.framework == "none":
        config.framework_heuristics = False
    elif args.framework != "auto":
        config.frameworks = [args.framework]
    if args.since and args.changed_only:
        raise ValueError("--since and --changed-only cannot be used together")
    if args.since:
        config.changed_only_requested = True
        config.since_ref = args.since
    elif args.changed_only:
        config.changed_only_requested = True
        config.changed_only_alias = True
        config.since_ref = "HEAD~1"


def _apply_doctor_config(config, args: argparse.Namespace) -> None:
    if args.include_tests:
        config.include_tests = True
    if args.exclude_tests:
        config.include_tests = False


def _format_for_command(result: dict[str, Any], command: str, fmt: str) -> str:
    if command == "agent-context":
        if fmt == "agent-fix-plan":
            return format_result(result, fmt, command)
        return format_agent_context(result, "json" if fmt == "json" else "markdown")
    if command == "graph" and fmt in {"mermaid", "dot"}:
        return _graph_format(result, fmt)
    if command in {"cycles", "dupes", "deps", "dead-code", "health", "boundaries"}:
        focused = _focused_result(result, command)
        if fmt == "json":
            return json.dumps(focused, indent=2, sort_keys=True) + "\n"
        return format_result(focused, fmt, command)
    return format_result(result, fmt, command)


def _focused_result(result: dict[str, Any], command: str) -> dict[str, Any]:
    rules = {
        "cycles": {"circular-dependency"},
        "dupes": {"duplicate-code"},
        "deps": {
            "missing-runtime-dependency",
            "missing-type-dependency",
            "missing-test-dependency",
            "dev-dependency-used-in-runtime",
            "optional-dependency-used-in-runtime",
            "runtime-dependency-used-only-in-tests",
            "runtime-dependency-used-only-for-types",
            "unused-runtime-dependency",
        },
        "dead-code": {"unused-module", "unused-symbol", "stale-suppression"},
        "health": {
            "high-cyclomatic-complexity",
            "high-cognitive-complexity",
            "large-function",
            "large-file",
            "risky-hotspot",
        },
        "boundaries": {"boundary-violation"},
    }[command]
    clone = dict(result)
    clone["issues"] = [issue for issue in result["issues"] if issue["rule"] in rules]
    clone["summary"] = summary_from_issue_dicts(
        clone["issues"],
        result["summary"].get("duplicate_groups", 0) if command == "dupes" else 0,
    )
    return clone


def _graph_format(result: dict[str, Any], fmt: str) -> str:
    edges = result["graphs"]["edges"]
    if fmt == "dot":
        lines = ["digraph pyfallow {"]
        for edge in edges:
            lines.append(f'  "{edge["from"]}" -> "{edge["to"]}";')
        lines.append("}")
        return "\n".join(lines) + "\n"
    lines = ["graph TD"]
    if not edges:
        lines.append("  empty[No local import edges]")
    for edge in edges:
        lines.append(f'  {edge["from"].replace(".", "_")}["{edge["from"]}"] --> {edge["to"].replace(".", "_")}["{edge["to"]}"]')
    return "\n".join(lines) + "\n"


def _write_or_print(output: str, path: str | None) -> None:
    if path:
        Path(path).write_text(output, encoding="utf-8")
    else:
        print(output, end="")


def _exit_code(result: dict[str, Any], fail_on: str, baseline_active: bool) -> int:
    if result["summary"].get("parse_errors", 0) and result["analysis"].get("modules_analyzed", 0) == result["summary"]["parse_errors"]:
        return 3
    if fail_on == "none":
        return 0
    issues = result["issues"]
    if baseline_active:
        issues = [issue for issue in issues if issue.get("baseline_status") == "new"]
    if fail_on == "any":
        return 1 if issues else 0
    threshold = {"error": "error", "warning": "warning"}[fail_on]
    return 1 if any(SEVERITY_ORDER[issue["severity"]] >= SEVERITY_ORDER[threshold] for issue in issues) else 0


def _issues_as_objects(issue_dicts: list[dict[str, Any]]):
    class IssueLike:
        def __init__(self, data: dict[str, Any]) -> None:
            self.fingerprint = data["fingerprint"]

    return [IssueLike(item) for item in issue_dicts]


def _mark_baseline_status(result: dict[str, Any], comparison: dict[str, Any]) -> None:
    new = set(comparison["new"])
    existing = set(comparison["existing"])
    for issue in result["issues"]:
        if issue["fingerprint"] in new:
            issue["baseline_status"] = "new"
        elif issue["fingerprint"] in existing:
            issue["baseline_status"] = "existing"


if __name__ == "__main__":
    raise SystemExit(main())
