from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


SOAK_DIR = Path(__file__).resolve().parent
DEFAULT_REPOS = SOAK_DIR / "repos.toml"
DEFAULT_MODELS = SOAK_DIR / "models.toml"
DEFAULT_WORKSPACE = SOAK_DIR / "workspace"
DEFAULT_RESULTS = SOAK_DIR / "results"
TIMEOUT_SECONDS = 1800
MAX_PROMPT_FINDINGS = 20
SAFE_OPENCODE_HOME_NAME = "opencode-home"
OPENCODE_EVENTS_NAME = "opencode-events.jsonl"
OPENCODE_EXPORT_NAME = "opencode-session.json"
OPENCODE_PROMPT_NAME = "opencode-prompt.md"

GLM_GUARDRAILS = [
    "Treat the model as a candidate generator, not an authority.",
    "Use fallow-py output as evidence; do not invent findings or remove code just to reduce warnings.",
    "If the evidence is ambiguous, return no_patch and explain the uncertainty.",
    "Do not touch CI, packaging, dependency declarations, auth, crypto, network, subprocess, or release files.",
    "Do not open pull requests, push branches, install dependencies, or run shell commands.",
    "Prefer one small semantic patch over cleanup, style churn, or broad refactors.",
]

SAFE_OPENCODE_PERMISSION = {
    "*": "ask",
    "read": "allow",
    "grep": "allow",
    "glob": "allow",
    "list": "allow",
    "edit": "ask",
    "write": "ask",
    "bash": "deny",
    "webfetch": "deny",
    "websearch": "deny",
    "external_directory": "deny",
}

PROJECT_OPENCODE_CONFIGS = {
    "opencode.json",
    "opencode.jsonc",
    ".opencode.json",
    ".opencode.jsonc",
}


@dataclass(frozen=True, slots=True)
class Repo:
    name: str
    url: str
    commit: str
    since: str
    category: str
    notes: str


@dataclass(frozen=True, slots=True)
class Model:
    name: str
    provider: str
    model: str
    role: str
    notes: str
    requires_env: str = ""
    base_url: str = ""
    output_limit: int = 2048
    thinking: str = "disabled"


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repos = load_repos(args.repos_config)
    models = load_models(args.models_config)
    if args.list:
        print(json.dumps(matrix_summary(repos, models), indent=2, sort_keys=True))
        return 0

    selected_repos = select_items(repos, args.repo, "repo")
    selected_models = select_items(models, args.model, "model")
    for repo in selected_repos:
        for model in selected_models:
            run_one(repo, model, args)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run or plan fallow-py multi-model soak jobs.")
    parser.add_argument("--repos-config", type=Path, default=DEFAULT_REPOS)
    parser.add_argument("--models-config", type=Path, default=DEFAULT_MODELS)
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--repo", default="all", help="Repo name from repos.toml, or all.")
    parser.add_argument("--model", default="all", help="Model name from models.toml, or all.")
    parser.add_argument("--list", action="store_true", help="Print configured repo/model matrix as JSON.")
    parser.add_argument("--dry-run", action="store_true", help="Write plan files without cloning or running tools.")
    parser.add_argument("--execute", action="store_true", help="Clone repos and run fallow-py/opencode commands.")
    parser.add_argument("--skip-opencode", action="store_true", help="Run fallow-py only, not opencode.")
    parser.add_argument(
        "--allow-host-opencode-config",
        action="store_true",
        help="Unsafe: allow opencode to use the user's normal HOME/config instead of a sterile home.",
    )
    parser.add_argument(
        "--allow-project-opencode-config",
        action="store_true",
        help="Unsafe: allow analyzed repos to provide opencode project config.",
    )
    parser.add_argument("--timeout", type=int, default=TIMEOUT_SECONDS)
    return parser


def load_repos(path: Path) -> list[Repo]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [Repo(**item) for item in data.get("repos", [])]


def load_models(path: Path) -> list[Model]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return [Model(**item) for item in data.get("models", [])]


def select_items(items: list[Any], name: str, label: str) -> list[Any]:
    if name == "all":
        return items
    selected = [item for item in items if item.name == name]
    if not selected:
        choices = ", ".join(item.name for item in items)
        raise SystemExit(f"Unknown {label} {name!r}; expected one of: {choices}")
    return selected


def matrix_summary(repos: list[Repo], models: list[Model]) -> dict[str, Any]:
    return {
        "repos": [asdict(repo) for repo in repos],
        "models": [asdict(model) for model in models],
        "planned_runs": len(repos) * len(models),
    }


def run_one(repo: Repo, model: Model, args: argparse.Namespace) -> None:
    result_dir = args.results / repo.name / model.name
    repo_dir = args.workspace / repo.name
    result_dir.mkdir(parents=True, exist_ok=True)
    plan = build_plan(repo, model, repo_dir, result_dir)
    write_json(result_dir / "plan.json", plan)
    (result_dir / "prompt.md").write_text(plan["prompt"], encoding="utf-8")
    write_classification_template(result_dir / "human_classification.md", repo, model)
    if args.dry_run or not args.execute:
        return

    started = time.time()
    clone_or_checkout(repo, repo_dir, args.timeout)
    pyfallow_result = run_command(plan["commands"]["pyfallow"], result_dir / "pyfallow.stderr", args.timeout)
    opencode_result: dict[str, Any] | None = None
    if not args.skip_opencode:
        opencode_result = run_opencode(plan, result_dir, args.timeout, args)
    write_json(
        result_dir / "time.json",
        {
            "started_at": started,
            "ended_at": time.time(),
            "duration_seconds": round(time.time() - started, 3),
            "pyfallow": pyfallow_result,
            "opencode": opencode_result,
        },
    )


def build_plan(repo: Repo, model: Model, repo_dir: Path, result_dir: Path) -> dict[str, Any]:
    prompt = build_guarded_prompt(repo)
    opencode_command = [
        "opencode",
        "--pure",
        "run",
        "--dir",
        str(repo_dir),
        "--format",
        "json",
        "--model",
        model.model,
        prompt,
    ]
    if model.thinking in {"enabled", "disabled"}:
        opencode_command[1:1] = ["--log-level", "WARN"]
    return {
        "repo": asdict(repo),
        "model": asdict(model),
        "guardrails": GLM_GUARDRAILS,
        "paths": {
            "repo_dir": str(repo_dir),
            "result_dir": str(result_dir),
            "findings": str(result_dir / "findings.json"),
            "prompt": str(result_dir / "prompt.md"),
            "opencode_prompt": str(result_dir / OPENCODE_PROMPT_NAME),
            "opencode_home": str(result_dir / SAFE_OPENCODE_HOME_NAME),
            "opencode_events": str(result_dir / OPENCODE_EVENTS_NAME),
            "opencode_export": str(result_dir / OPENCODE_EXPORT_NAME),
        },
        "prompt": prompt,
        "commands": {
            "clone": ["git", "clone", "--no-checkout", repo.url, str(repo_dir)],
            "checkout": ["git", "-C", str(repo_dir), "checkout", repo.commit],
            "pyfallow": [
                sys.executable,
                "-m",
                "fallow_py",
                "analyze",
                "--root",
                str(repo_dir),
                "--since",
                repo.since,
                "--format",
                "agent-fix-plan",
                "--output",
                str(result_dir / "findings.json"),
            ],
            "opencode": opencode_command,
        },
    }


def build_guarded_prompt(repo: Repo) -> str:
    guardrail_lines = "\n".join(f"- {item}" for item in GLM_GUARDRAILS)
    return (
        f"You are reviewing a bounded fallow-py soak run for {repo.name}.\n\n"
        "Role boundary:\n"
        "- You are a candidate generator. A human/Codex reviewer decides whether anything is safe.\n"
        "- Your answer is evidence, not a public PR and not a merge recommendation.\n\n"
        "Hard guardrails:\n"
        f"{guardrail_lines}\n\n"
        "Use only local repository files and the generated fallow-py agent-fix-plan. "
        "Classify findings by auto_safe, decision_needed, and blocking only when the "
        "fallow-py output contains that evidence.\n\n"
        "Return concise Markdown with these exact headings:\n"
        "1. Findings used\n"
        "2. Candidate action\n"
        "3. Tests a supervisor should run\n"
        "4. Stop conditions\n"
    )


def clone_or_checkout(repo: Repo, repo_dir: Path, timeout: int) -> None:
    if not repo_dir.exists():
        require_success(
            run_command(["git", "clone", "--no-checkout", repo.url, str(repo_dir)], None, timeout),
            "git clone",
        )
    require_success(
        run_command(["git", "-C", str(repo_dir), "fetch", "--tags", "origin"], None, timeout),
        "git fetch",
    )
    require_success(
        run_command(["git", "-C", str(repo_dir), "checkout", repo.commit], None, timeout),
        "git checkout",
    )


def run_opencode(
    plan: dict[str, Any], result_dir: Path, timeout: int, args: argparse.Namespace
) -> dict[str, Any]:
    if not shutil.which("opencode"):
        return {"skipped": True, "reason": "opencode executable not found"}
    repo_dir = Path(plan["paths"]["repo_dir"])
    model = plan["model"]
    blocked_configs = project_opencode_configs(repo_dir)
    if blocked_configs and not args.allow_project_opencode_config:
        return {
            "skipped": True,
            "reason": "project opencode config present; rerun only after manual review",
            "blocked_configs": [str(path.relative_to(repo_dir)) for path in blocked_configs],
        }

    if args.allow_host_opencode_config:
        env = None
        home_path = Path.home()
        config_path = Path.home() / ".config" / "opencode" / "opencode.json"
    else:
        prepared = prepare_safe_opencode_home(result_dir, model)
        if isinstance(prepared, dict):
            return prepared
        env, home_path, config_path = prepared

    opencode_prompt = build_opencode_prompt(plan)
    (result_dir / OPENCODE_PROMPT_NAME).write_text(opencode_prompt, encoding="utf-8")
    command = [*plan["commands"]["opencode"]]
    command[-1] = opencode_prompt
    result = run_command(
        command,
        result_dir / "opencode.stderr",
        timeout,
        env=env,
    )
    events_path = result_dir / OPENCODE_EVENTS_NAME
    events_path.write_text(result.pop("stdout", ""), encoding="utf-8")
    session_id = extract_session_id(events_path)
    export_result: dict[str, Any] | None = None
    agent_text = ""
    if session_id:
        export_result = run_command(
            ["opencode", "--pure", "export", session_id],
            result_dir / "opencode-export.stderr",
            timeout,
            env=env,
        )
        export_text = export_result.pop("stdout", "")
        (result_dir / OPENCODE_EXPORT_NAME).write_text(export_text, encoding="utf-8")
        agent_text = extract_agent_text(export_text)
    (result_dir / "agent_output.md").write_text(agent_text, encoding="utf-8")
    return {
        **result,
        "session_id": session_id,
        "safe_home": str(home_path),
        "safe_config": str(config_path),
        "prompt_path": str(result_dir / OPENCODE_PROMPT_NAME),
        "events_path": str(events_path),
        "export": export_result,
    }


def build_opencode_prompt(plan: dict[str, Any]) -> str:
    findings_path = Path(plan["paths"]["findings"])
    evidence = summarize_agent_fix_plan(findings_path)
    return (
        plan["prompt"]
        + "\n\nSupervisor-supplied fallow-py evidence excerpt follows. "
        "Use this excerpt as the source of truth; do not search for a different report file.\n\n"
        "```json\n"
        + json.dumps(evidence, indent=2, sort_keys=True)
        + "\n```\n"
    )


def summarize_agent_fix_plan(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"available": False, "reason": "findings file not found", "path": str(path)}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {
            "available": False,
            "reason": f"findings file is not valid JSON: {exc}",
            "path": str(path),
        }
    buckets = ["blocking", "decision_needed", "auto_safe"]
    findings: list[dict[str, Any]] = []
    for bucket in buckets:
        for issue in data.get(bucket, []):
            findings.append(
                {
                    "bucket": bucket,
                    "fingerprint": issue.get("fingerprint"),
                    "rule": issue.get("rule"),
                    "id": issue.get("id"),
                    "file": issue.get("file") or issue.get("path"),
                    "line": issue.get("line"),
                    "symbol": issue.get("symbol"),
                    "severity": issue.get("severity"),
                    "confidence": issue.get("confidence"),
                    "message": issue.get("message"),
                    "decision": issue.get("decision"),
                    "rationale": issue.get("rationale"),
                }
            )
    return {
        "available": True,
        "summary": data.get("summary", {}),
        "limitations": data.get("limitations", []),
        "findings": findings[:MAX_PROMPT_FINDINGS],
        "total_findings": len(findings),
        "truncated": len(findings) > MAX_PROMPT_FINDINGS,
    }


def project_opencode_configs(repo_dir: Path) -> list[Path]:
    return sorted(path for path in repo_dir.iterdir() if path.name in PROJECT_OPENCODE_CONFIGS)


def prepare_safe_opencode_home(
    result_dir: Path, model: dict[str, Any]
) -> tuple[dict[str, str], Path, Path] | dict[str, Any]:
    required_env = model.get("requires_env") or ""
    if required_env and not os.environ.get(required_env):
        return {
            "skipped": True,
            "reason": f"required environment variable {required_env} is not set",
        }
    home_path = result_dir / SAFE_OPENCODE_HOME_NAME
    config_dir = home_path / ".config" / "opencode"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "opencode.json"
    config = safe_opencode_config(model)
    config_path.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    env = {
        "HOME": str(home_path),
        "PATH": os.environ.get("PATH", ""),
        "NO_COLOR": "1",
    }
    if required_env:
        env[required_env] = os.environ[required_env]
    return env, home_path, config_path


def safe_opencode_config(model: dict[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = {
        "$schema": "https://opencode.ai/config.json",
        "share": "disabled",
        "model": model["model"],
        "permission": SAFE_OPENCODE_PERMISSION,
        "mcp": {},
    }
    provider_id = model["model"].split("/", 1)[0]
    if provider_id == "zai-coding":
        config["provider"] = {
            "zai-coding": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Z.ai Coding Plan clean",
                "options": {
                    "baseURL": model.get("base_url") or "https://api.z.ai/api/coding/paas/v4",
                    "apiKey": "{env:Z_AI_API_KEY}",
                },
                "models": {
                    "glm-5.1": {
                        "name": "GLM-5.1 Coding Plan",
                        "limit": {
                            "context": 200000,
                            "output": int(model.get("output_limit") or 2048),
                        },
                    }
                },
            }
        }
    return config


def extract_session_id(events_path: Path) -> str:
    for line in events_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        session_id = event.get("sessionID")
        if session_id:
            return str(session_id)
    return ""


def extract_agent_text(export_text: str) -> str:
    json_start = export_text.find("{")
    if json_start < 0:
        return ""
    try:
        data = json.loads(export_text[json_start:])
    except json.JSONDecodeError:
        return ""
    parts: list[str] = []
    for message in data.get("messages", []):
        if message.get("info", {}).get("role") != "assistant":
            continue
        for part in message.get("parts", []):
            if part.get("type") == "text" and part.get("text"):
                parts.append(part["text"])
    return "\n\n".join(parts)


def run_command(
    command: list[str],
    stderr_path: Path | None,
    timeout: int,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    started = time.time()
    result = subprocess.run(
        command,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
        env=env,
    )
    if stderr_path:
        stderr_path.write_text(redact(result.stderr), encoding="utf-8")
    return {
        "command": command,
        "returncode": result.returncode,
        "duration_seconds": round(time.time() - started, 3),
        "stdout": redact(result.stdout),
        "stderr": redact(result.stderr),
    }


def redact(text: str) -> str:
    redacted = text
    for key in ("Z_AI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        value = os.environ.get(key)
        if value:
            redacted = redacted.replace(value, "***REDACTED***")
    return redacted


def require_success(result: dict[str, Any], label: str) -> None:
    if result["returncode"] != 0:
        raise SystemExit(f"{label} failed with exit code {result['returncode']}")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_classification_template(path: Path, repo: Repo, model: Model) -> None:
    if path.exists():
        return
    path.write_text(
        "\n".join(
            [
                f"# Human Classification: {repo.name} / {model.name}",
                "",
                "| fingerprint | rule | verdict | notes |",
                "| --- | --- | --- | --- |",
                "| TBD | TBD | true-positive / false-positive / disputed | TBD |",
                "",
                "Use `findings.json` as the source of truth. Do not classify model-invented findings.",
                "",
            ]
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
