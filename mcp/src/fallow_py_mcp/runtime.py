from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

from fallow_py.analysis import analyze
from fallow_py.classify import classify_finding
from fallow_py.config import load_config
from fallow_py.models import SEVERITY_ORDER

from .schemas import Finding

CACHE_TTL_SECONDS = 60
IGNORED_SIGNATURE_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "env",
    "node_modules",
    "site-packages",
    "venv",
}
REPORT_CACHE: dict[str, tuple[float, tuple[tuple[str, int, int, str], ...], dict[str, Any]]] = {}


def analyze_report(root: str | Path, since: str | None = None) -> dict[str, Any]:
    config = load_config(root)
    if since:
        config.since_ref = since
        config.changed_only_requested = True
    return analyze(config)


def cached_report(root: str | Path) -> dict[str, Any]:
    key = str(Path(root).resolve())
    now = time.monotonic()
    cached = REPORT_CACHE.get(key)
    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        signature = _report_signature(Path(key), cached[2])
        if signature == cached[1]:
            return cached[2]
    result = analyze_report(key)
    REPORT_CACHE[key] = (now, _report_signature(Path(key), result), result)
    return result


def module_graph(root: str | Path) -> dict[str, Any]:
    graph = cached_report(root)["graphs"]
    return {
        "modules": graph.get("modules", []),
        "edges": graph.get("edges", []),
        "cycles": graph.get("cycles", []),
    }


def findings(issues: list[dict[str, Any]]) -> list[Finding]:
    models: list[Finding] = []
    for issue in sorted(issues, key=issue_sort_key):
        classification = classify_finding(issue)
        payload = dict(issue)
        payload["classification"] = classification.decision
        payload["trade_offs"] = classification.trade_offs
        models.append(Finding(**payload))
    return models


def issue_sort_key(issue: dict[str, Any]) -> tuple[int, str, str, int, str]:
    return (
        -SEVERITY_ORDER[issue["severity"]],
        issue["id"],
        issue.get("path") or "",
        issue.get("range", {}).get("start", {}).get("line", 1),
        issue.get("fingerprint", ""),
    )


def _report_signature(root: Path, result: dict[str, Any]) -> tuple[tuple[str, int, int, str], ...]:
    return tuple(_path_signature(root, rel) for rel in sorted(_signature_paths(root, result)))


def _signature_paths(root: Path, result: dict[str, Any]) -> set[str]:
    paths = {
        item.get("path")
        for item in result.get("graphs", {}).get("modules", [])
        if item.get("path")
    }
    for source_root in result.get("analysis", {}).get("source_roots", []) or ["."]:
        paths.update(_scan_source_root(root, source_root))
    paths.update(result.get("analysis", {}).get("dependency_files", []))
    if result.get("config_path"):
        paths.add(result["config_path"])
    return {path for path in paths if path}


def _scan_source_root(root: Path, source_root: str) -> set[str]:
    base = root / source_root
    if not base.exists():
        return set()
    paths: set[str] = set()
    for path in base.rglob("*.py"):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if not _is_ignored_signature_path(rel):
            paths.add(rel)
    return paths


def _is_ignored_signature_path(path: str) -> bool:
    return any(part in IGNORED_SIGNATURE_DIRS or part.endswith(".egg-info") for part in path.split("/"))


def _path_signature(root: Path, rel: str) -> tuple[str, int, int, str]:
    file_path = root / rel
    try:
        stat = file_path.stat()
    except FileNotFoundError:
        return rel, -1, -1, "missing"
    return rel, stat.st_mtime_ns, stat.st_size, _file_digest(file_path)


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return "unreadable"
    return digest.hexdigest()
