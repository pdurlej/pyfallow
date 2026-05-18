from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .models import VERSION
from .paths import relpath


def doctor_payload(config, result: dict[str, Any], root_arg: str) -> dict[str, Any]:
    analysis = result["analysis"]
    entrypoints = analysis.get("entrypoints", [])
    files_analyzed = analysis.get("files_analyzed", 0)
    git = _git_state(config.root)
    notes = _notes(config, files_analyzed, entrypoints, git)
    return {
        "schema": "fallow_py_doctor.v1",
        "tool": "fallow-py",
        "version": VERSION,
        "status": "ready" if files_analyzed and entrypoints else "needs_attention",
        "root": str(config.root),
        "config": {
            "found": config.config_path is not None,
            "path": _display_path(config.config_path, config.root) if config.config_path else None,
            "include_tests": config.include_tests,
            "framework_heuristics": config.framework_heuristics,
            "boundary_rules_configured": bool(config.boundary_rules),
        },
        "analysis": {
            "source_roots": analysis.get("source_roots", []),
            "files_analyzed": files_analyzed,
            "modules_analyzed": analysis.get("modules_analyzed", 0),
            "entrypoints": entrypoints,
            "frameworks_detected": analysis.get("frameworks_detected", []),
            "dependency_files": analysis.get("dependency_files", []),
            "module_ambiguities": len(analysis.get("module_ambiguities", [])),
        },
        "git": git,
        "next_commands": _next_commands(root_arg, git["available"]),
        "notes": notes,
    }


def format_doctor_text(payload: dict[str, Any]) -> str:
    analysis = payload["analysis"]
    config = payload["config"]
    git = payload["git"]
    lines = [
        "fallow-py doctor",
        f"Status: {payload['status']}",
        f"Root: {payload['root']}",
        f"Config: {config['path'] if config['found'] else 'none (using defaults)'}",
        f"Source roots: {_join_or_none(analysis['source_roots'])}",
        f"Python files: {analysis['files_analyzed']}",
        f"Modules: {analysis['modules_analyzed']}",
        f"Include tests: {str(config['include_tests']).lower()}",
        f"Entrypoints: {_format_entrypoints(analysis['entrypoints'])}",
        f"Frameworks: {_join_or_none(analysis['frameworks_detected'])}",
        f"Dependency files: {_join_or_none(analysis['dependency_files'])}",
        f"Boundary rules: {'configured' if config['boundary_rules_configured'] else 'none'}",
        f"Git diff: {'available' if git['available'] else 'unavailable - ' + git['reason']}",
        "",
        "Next commands:",
    ]
    lines.extend(f"- {command}" for command in payload["next_commands"])
    if payload["notes"]:
        lines.extend(["", "Notes:"])
        lines.extend(f"- {note}" for note in payload["notes"])
    return "\n".join(lines) + "\n"


def _git_state(root: Path) -> dict[str, Any]:
    result = _run_git(root, "rev-parse", "--is-inside-work-tree")
    if result is None:
        return {"available": False, "reason": "git executable was not available"}
    if result.returncode != 0 or result.stdout.strip() != "true":
        return {"available": False, "reason": "root is not inside a Git worktree"}
    top = _run_git(root, "rev-parse", "--show-toplevel")
    return {
        "available": True,
        "worktree_root": top.stdout.strip() if top and top.returncode == 0 else None,
        "since_suggestion": "HEAD~1",
    }


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            text=True,
            capture_output=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _notes(config, files_analyzed: int, entrypoints: list[dict[str, Any]], git: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    if config.config_path is None:
        notes.append("No fallow-py config file found; defaults are in use. Add .fallow-py.toml for stable CI behavior.")
    if not files_analyzed:
        notes.append("No Python files were discovered. Check roots, ignore patterns, and repository layout.")
    if files_analyzed and not entrypoints:
        notes.append("No entrypoints were detected. Configure entry = [...] to improve dead-code reachability.")
    if not git["available"]:
        notes.append(f"Git diff analysis is unavailable: {git['reason']}. CI should run in a full checkout with history.")
    return notes


def _next_commands(root_arg: str, git_available: bool) -> list[str]:
    commands = [f"fallow-py analyze --root {root_arg} --format agent-fix-plan"]
    if git_available:
        commands.append(f"fallow-py analyze --root {root_arg} --since HEAD~1 --format agent-fix-plan")
    commands.append("copy examples/ci/forgejo-actions.yml to .forgejo/workflows/fallow-py.yml")
    return commands


def _format_entrypoints(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "none"
    return ", ".join(f"{entry['module']} ({entry['reason']}, {entry['confidence']})" for entry in entries)


def _join_or_none(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def _display_path(path: Path | None, root: Path) -> str | None:
    if path is None:
        return None
    return relpath(path, root)
