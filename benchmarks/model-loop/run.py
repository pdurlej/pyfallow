from __future__ import annotations

import argparse
import json
import statistics
import sys
import tomllib
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BENCH_DIR = Path(__file__).resolve().parent
DEFAULT_TASKS = BENCH_DIR / "tasks.toml"
DEFAULT_MODELS = BENCH_DIR / "models.toml"
DEFAULT_PROMPTS = BENCH_DIR / "prompt_variants.toml"
DEFAULT_RECORDS = BENCH_DIR / "examples/run-records"
SCHEMA = "fallow_py_model_loop_run.v1"
CONDITIONS = ("model_only", "model_plus_fallow")
DECISIONS = (
    "opened_pr",
    "safe_no_pr",
    "rejected_bad_patch",
    "tooling_failure",
    "aborted_policy",
    "aborted_containment",
)
INTERVENTION_LABELS = (
    "model_only_patch",
    "model_patch_codex_review_only",
    "model_patch_human_mechanical_edit",
    "codex_or_human_authored_fix",
)


@dataclass(frozen=True, slots=True)
class Task:
    name: str
    repo: str
    repo_url: str
    repo_commit: str
    worktree_path: str
    bug_reference: str
    summary: str
    pre_test_command: str
    post_test_command: str
    risk_level: str
    public_pr_allowed: bool
    notes: str = ""


@dataclass(frozen=True, slots=True)
class Model:
    name: str
    provider: str
    provider_model: str
    effort: str
    cost_tier: str
    notes: str = ""


@dataclass(frozen=True, slots=True)
class PromptVariant:
    name: str
    condition: str
    description: str


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "list":
        tasks = load_tasks(args.tasks_config)
        models = load_models(args.models_config)
        prompts = load_prompt_variants(args.prompts_config)
        print(json.dumps(matrix_summary(tasks, models, prompts), indent=2, sort_keys=True))
        return 0
    if args.command == "plan":
        plan = build_plan_from_args(args)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        write_json(args.output_dir / "plan.json", plan)
        (args.output_dir / "prompt.md").write_text(plan["prompt"], encoding="utf-8")
        return 0
    if args.command == "record-template":
        record = build_record_template_from_args(args)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.output, record)
        return 0
    if args.command == "validate":
        records = load_run_records(args.records)
        errors = validate_records(records)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        print(f"validated {len(records)} run record(s)")
        return 0
    if args.command == "aggregate":
        records = load_run_records(args.records)
        errors = validate_records(records)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        summary = aggregate_records(records)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render_markdown(summary), encoding="utf-8")
        if args.json_output:
            args.json_output.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.json_output, summary)
        return 0
    parser.error("missing command")
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare and aggregate fallow-py model-loop benchmark evidence.")
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser("list", help="Print configured task/model/prompt matrix.")
    add_config_args(list_parser)

    plan_parser = subparsers.add_parser("plan", help="Write a deterministic plan and prompt for one attempt.")
    add_config_args(plan_parser)
    add_selection_args(plan_parser)
    plan_parser.add_argument("--output-dir", type=Path, required=True)

    template_parser = subparsers.add_parser("record-template", help="Write a blank run-record JSON template.")
    add_config_args(template_parser)
    add_selection_args(template_parser)
    template_parser.add_argument("--output", type=Path, required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate run-record JSON files.")
    validate_parser.add_argument("records", type=Path, nargs="+")

    aggregate_parser = subparsers.add_parser("aggregate", help="Aggregate run-record JSON files.")
    aggregate_parser.add_argument("--records", type=Path, nargs="+", default=[DEFAULT_RECORDS])
    aggregate_parser.add_argument("--output", type=Path, required=True)
    aggregate_parser.add_argument("--json-output", type=Path)
    return parser


def add_config_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tasks-config", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--models-config", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--prompts-config", type=Path, default=DEFAULT_PROMPTS)


def add_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--condition", choices=CONDITIONS, required=True)


def load_tasks(path: Path) -> list[Task]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [Task(**item) for item in data.get("tasks", [])]


def load_models(path: Path) -> list[Model]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [Model(**item) for item in data.get("models", [])]


def load_prompt_variants(path: Path) -> list[PromptVariant]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [PromptVariant(**item) for item in data.get("prompt_variants", [])]


def select_named(items: list[Any], name: str, label: str) -> Any:
    for item in items:
        if item.name == name:
            return item
    choices = ", ".join(item.name for item in items)
    raise SystemExit(f"Unknown {label} {name!r}; expected one of: {choices}")


def matrix_summary(tasks: list[Task], models: list[Model], prompts: list[PromptVariant]) -> dict[str, Any]:
    conditions = sorted({prompt.condition for prompt in prompts})
    return {
        "schema": "fallow_py_model_loop_matrix.v1",
        "tasks": [asdict(task) for task in tasks],
        "models": [asdict(model) for model in models],
        "prompt_variants": [asdict(prompt) for prompt in prompts],
        "conditions": conditions,
        "planned_attempts": len(tasks) * len(models) * len(prompts),
        "public_pr_automation": "disabled",
    }


def build_plan_from_args(args: argparse.Namespace) -> dict[str, Any]:
    task, model, prompt = select_configured(args)
    return build_plan(task, model, prompt, args.condition)


def build_record_template_from_args(args: argparse.Namespace) -> dict[str, Any]:
    task, model, prompt = select_configured(args)
    plan = build_plan(task, model, prompt, args.condition)
    return blank_run_record(plan)


def select_configured(args: argparse.Namespace) -> tuple[Task, Model, PromptVariant]:
    task = select_named(load_tasks(args.tasks_config), args.task, "task")
    model = select_named(load_models(args.models_config), args.model, "model")
    prompt = select_named(load_prompt_variants(args.prompts_config), args.prompt, "prompt")
    if prompt.condition != args.condition:
        raise SystemExit(
            f"Prompt {prompt.name!r} is for condition {prompt.condition!r}, not {args.condition!r}."
        )
    return task, model, prompt


def build_plan(task: Task, model: Model, prompt: PromptVariant, condition: str) -> dict[str, Any]:
    return {
        "schema": "fallow_py_model_loop_plan.v1",
        "task": asdict(task),
        "model": asdict(model),
        "condition": condition,
        "prompt_variant": asdict(prompt),
        "public_pr_automation": False,
        "secret_policy": "Do not include provider keys, shell env, credentials, or private paths in prompts or records.",
        "evidence_policy": [
            "Every selected task counts in the denominator.",
            "Record failures and rejected patches.",
            "Separate model-authored patches from Codex/human-authored fixes.",
            "Do not claim a quality win from fallow-py warning reduction alone.",
        ],
        "commands": {
            "pre_test": task.pre_test_command,
            "post_test": task.post_test_command,
            "fallow_py": f"python -m fallow_py analyze --root {task.worktree_path} --format agent-fix-plan",
        },
        "prompt": render_prompt(task, model, prompt, condition),
    }


def render_prompt(task: Task, model: Model, prompt: PromptVariant, condition: str) -> str:
    fallow_clause = (
        "Use the supplied fallow-py output as deterministic evidence. Do not edit code only to reduce warnings."
        if condition == "model_plus_fallow"
        else "Do not assume fallow-py feedback is available in this baseline condition."
    )
    return (
        f"You are producing a candidate patch for an internal fallow-py model-loop benchmark.\n\n"
        f"Task: {task.name}\n"
        f"Repository: {task.repo} at {task.repo_commit}\n"
        f"Worktree path: {task.worktree_path}\n"
        f"Bug/reference: {task.bug_reference}\n"
        f"Summary: {task.summary}\n"
        f"Model alias: {model.name} ({model.provider}/{model.provider_model}, effort={model.effort})\n"
        f"Condition: {condition}\n"
        f"Prompt variant: {prompt.name} - {prompt.description}\n\n"
        "Rules:\n"
        "- Produce a small candidate patch or explicitly return no_patch.\n"
        "- Do not touch CI, packaging, auth, crypto, release automation, or unrelated files.\n"
        "- Do not open a PR, push a branch, install dependencies, or access secrets.\n"
        "- A passing test is required but not sufficient; explain why behavior is correct.\n"
        f"- {fallow_clause}\n\n"
        "Evidence to capture after the attempt:\n"
        "- token/cost estimate\n"
        "- iteration count\n"
        "- fallow-py before/after summary if enabled\n"
        "- tests before/after\n"
        "- intervention label\n"
        "- final decision\n"
    )


def blank_run_record(plan: dict[str, Any]) -> dict[str, Any]:
    task = plan["task"]
    model = plan["model"]
    return {
        "schema": SCHEMA,
        "attempt_id": f"{task['name']}-{model['name']}-{plan['condition']}-TODO",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "task": {
            "name": task["name"],
            "repo": task["repo"],
            "repo_url": task["repo_url"],
            "repo_commit": task["repo_commit"],
            "bug_reference": task["bug_reference"],
            "summary": task["summary"],
        },
        "model": {
            "name": model["name"],
            "provider": model["provider"],
            "provider_model": model["provider_model"],
            "effort": model["effort"],
            "temperature": 0,
        },
        "condition": plan["condition"],
        "prompt_variant": plan["prompt_variant"]["name"],
        "fallow_py": {
            "enabled": plan["condition"] == "model_plus_fallow",
            "version": "",
            "before_command": plan["commands"]["fallow_py"] if plan["condition"] == "model_plus_fallow" else "",
            "before_summary": {},
            "after_command": plan["commands"]["fallow_py"] if plan["condition"] == "model_plus_fallow" else "",
            "after_summary": {},
        },
        "metrics": {
            "input_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": 0.0,
            "iteration_count": 0,
            "wall_seconds": 0,
        },
        "tests": {
            "pre": {"command": plan["commands"]["pre_test"], "outcome": ""},
            "post": {"command": plan["commands"]["post_test"], "outcome": ""},
        },
        "patch": {"diff_path": "", "changed_files": [], "changed_lines": 0},
        "intervention_label": "",
        "decision": "",
        "review": {"codex_notes": "", "human_notes": ""},
    }


def load_run_records(paths: list[Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in paths:
        if path.is_dir():
            files = sorted(path.rglob("*.json"))
        else:
            files = [path]
        for file in files:
            records.append(json.loads(file.read_text(encoding="utf-8")))
    return records


def validate_records(records: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    seen_attempts: set[str] = set()
    required_top = {
        "schema",
        "attempt_id",
        "created_at",
        "task",
        "model",
        "condition",
        "prompt_variant",
        "fallow_py",
        "metrics",
        "tests",
        "patch",
        "intervention_label",
        "decision",
        "review",
    }
    for index, record in enumerate(records, start=1):
        prefix = f"record {index}"
        missing = sorted(required_top - set(record))
        if missing:
            errors.append(f"{prefix}: missing top-level keys: {', '.join(missing)}")
            continue
        if record["schema"] != SCHEMA:
            errors.append(f"{prefix}: unsupported schema {record['schema']!r}")
        attempt_id = str(record["attempt_id"])
        if attempt_id in seen_attempts:
            errors.append(f"{prefix}: duplicate attempt_id {attempt_id!r}")
        seen_attempts.add(attempt_id)
        if record["condition"] not in CONDITIONS:
            errors.append(f"{prefix}: invalid condition {record['condition']!r}")
        if record["decision"] not in DECISIONS:
            errors.append(f"{prefix}: invalid decision {record['decision']!r}")
        if record["intervention_label"] not in INTERVENTION_LABELS:
            errors.append(f"{prefix}: invalid intervention_label {record['intervention_label']!r}")
        if not isinstance(record.get("metrics"), dict):
            errors.append(f"{prefix}: metrics must be an object")
            continue
        for numeric_key in ("input_tokens", "output_tokens", "estimated_cost_usd", "iteration_count"):
            value = record["metrics"].get(numeric_key)
            if not isinstance(value, int | float) or value < 0:
                errors.append(f"{prefix}: metrics.{numeric_key} must be a non-negative number")
        tests = record.get("tests", {})
        if not isinstance(tests, dict) or "pre" not in tests or "post" not in tests:
            errors.append(f"{prefix}: tests.pre and tests.post are required")
        fallow = record.get("fallow_py", {})
        if record["condition"] == "model_plus_fallow" and not fallow.get("enabled"):
            errors.append(f"{prefix}: model_plus_fallow requires fallow_py.enabled=true")
    return errors


def aggregate_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_decision = Counter(record["decision"] for record in records)
    by_condition = Counter(record["condition"] for record in records)
    by_model = Counter(record["model"]["name"] for record in records)
    by_intervention = Counter(record["intervention_label"] for record in records)
    by_task = Counter(record["task"]["name"] for record in records)
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[(record["condition"], record["model"]["name"], record["prompt_variant"])].append(record)
    group_rows = [aggregate_group(condition, model, prompt, items) for (condition, model, prompt), items in groups.items()]
    group_rows.sort(key=lambda row: (row["condition"], row["model"], row["prompt_variant"]))
    return {
        "schema": "fallow_py_model_loop_summary.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(records),
        "attempt_denominator": len(records),
        "by_decision": dict(sorted(by_decision.items())),
        "by_condition": dict(sorted(by_condition.items())),
        "by_model": dict(sorted(by_model.items())),
        "by_intervention": dict(sorted(by_intervention.items())),
        "by_task": dict(sorted(by_task.items())),
        "totals": {
            "input_tokens": sum_metric(records, "input_tokens"),
            "output_tokens": sum_metric(records, "output_tokens"),
            "estimated_cost_usd": round(sum_metric(records, "estimated_cost_usd"), 6),
            "iterations": sum_metric(records, "iteration_count"),
        },
        "groups": group_rows,
        "claim_status": "internal_evidence_only",
    }


def aggregate_group(condition: str, model: str, prompt: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    decisions = Counter(record["decision"] for record in records)
    iterations = [float(record["metrics"].get("iteration_count", 0)) for record in records]
    return {
        "condition": condition,
        "model": model,
        "prompt_variant": prompt,
        "runs": len(records),
        "opened_pr": decisions.get("opened_pr", 0),
        "safe_no_pr": decisions.get("safe_no_pr", 0),
        "rejected_bad_patch": decisions.get("rejected_bad_patch", 0),
        "tooling_failure": decisions.get("tooling_failure", 0),
        "aborted": decisions.get("aborted_policy", 0) + decisions.get("aborted_containment", 0),
        "input_tokens": sum_metric(records, "input_tokens"),
        "output_tokens": sum_metric(records, "output_tokens"),
        "estimated_cost_usd": round(sum_metric(records, "estimated_cost_usd"), 6),
        "median_iterations": round(statistics.median(iterations), 3) if iterations else 0,
    }


def sum_metric(records: list[dict[str, Any]], key: str) -> float:
    return sum(float(record.get("metrics", {}).get(key, 0)) for record in records)


def render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# fallow-py model-loop benchmark summary",
        "",
        f"Generated: `{summary['generated_at']}`",
        "",
        "## Owner Action Board",
        "",
        "### Needs owner now",
        "",
        "- none; this summary is internal evidence plumbing unless a run record says `opened_pr`.",
        "",
        "### Default path unless owner objects",
        "",
        "- DEFAULT: keep collecting denominator-backed records before making public claims.",
        "",
        "### Agent follow-up, no owner attention now",
        "",
        "- TASK: inspect rejected patches and tooling failures before celebrating wins.",
        "",
        "### Blocked / waiting on precondition",
        "",
        "- BLOCKED: public model-quality claims until enough real records exist.",
        "",
        "## Totals",
        "",
        f"- Run records: {summary['record_count']}",
        f"- Attempt denominator: {summary['attempt_denominator']}",
        f"- Input tokens: {int(summary['totals']['input_tokens'])}",
        f"- Output tokens: {int(summary['totals']['output_tokens'])}",
        f"- Estimated cost USD: {summary['totals']['estimated_cost_usd']}",
        f"- Total iterations: {int(summary['totals']['iterations'])}",
        "",
    ]
    lines.extend(counter_section("Decisions", summary["by_decision"]))
    lines.extend(counter_section("Conditions", summary["by_condition"]))
    lines.extend(counter_section("Models", summary["by_model"]))
    lines.extend(group_table(summary["groups"]))
    lines.extend(
        [
            "## Claim Status",
            "",
            "`internal_evidence_only` - this file is useful for triage, not a public benchmark claim.",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def counter_section(title: str, values: dict[str, int]) -> list[str]:
    lines = [f"## {title}", ""]
    if not values:
        return [*lines, "- none", ""]
    for key, value in sorted(values.items()):
        lines.append(f"- `{key}`: {value}")
    lines.append("")
    return lines


def group_table(groups: list[dict[str, Any]]) -> list[str]:
    lines = [
        "## Comparison Groups",
        "",
        "| Condition | Model | Prompt | Runs | Opened PR | Safe no-PR | Rejected | Tooling failure | Input tokens | Output tokens | Est. cost | Median iterations |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in groups:
        lines.append(
            "| {condition} | {model} | {prompt_variant} | {runs} | {opened_pr} | {safe_no_pr} | "
            "{rejected_bad_patch} | {tooling_failure} | {input_tokens:.0f} | {output_tokens:.0f} | "
            "{estimated_cost_usd:.6f} | {median_iterations:.3f} |".format(**row)
        )
    lines.append("")
    return lines


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())

