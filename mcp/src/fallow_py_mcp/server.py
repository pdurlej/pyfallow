from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

from . import VERSION
from .schemas import AgentContext, AnalysisResult, Classification, Remediation, VerifyResult
from .runtime import cached_report, module_graph
from .tools import (
    agent_context_impl,
    analyze_diff_impl,
    explain_finding_impl,
    safe_to_remove_impl,
    verify_imports_impl,
)

SERVER_INSTRUCTIONS = (
    "Use fallow-py tools before committing or showing Python code changes. "
    "Treat high-confidence findings as actionable and low-confidence findings as review context."
)
SANDBOX_ENV = "FALLOW_PY_MCP_SANDBOX_ROOT"
LEGACY_SANDBOX_ENV = "PYFALLOW_MCP_SANDBOX_ROOT"


def build_server(default_root: str | Path | None = None) -> FastMCP:
    server = FastMCP("pyfallow", version=VERSION, instructions=SERVER_INSTRUCTIONS)
    sandbox_root = _sandbox_root(default_root)

    def root_or_default(root: str | Path | None = None) -> str:
        return str(_validated_root(root or default_root or ".", sandbox_root))

    @server.tool
    def analyze_diff(
        root: str | None = None,
        since: str = "HEAD~1",
        min_confidence: str = "medium",
        max_findings: int = 50,
    ) -> AnalysisResult:
        return analyze_diff_impl(root_or_default(root), since, min_confidence, max_findings)

    @server.tool
    def agent_context(root: str | None = None, scope: str = "diff", max_findings: int = 20) -> AgentContext:
        return agent_context_impl(root_or_default(root), scope, max_findings)

    @server.tool
    def explain_finding(root: str | None = None, fingerprint: str = "") -> Remediation:
        return explain_finding_impl(root_or_default(root), fingerprint)

    @server.tool
    def verify_imports(root: str | None = None, file: str = "", planned_imports: list[str] | None = None) -> VerifyResult:
        return verify_imports_impl(root_or_default(root), file, planned_imports or [])

    @server.tool
    def safe_to_remove(root: str | None = None, fingerprints: list[str] | None = None) -> dict[str, Classification]:
        return safe_to_remove_impl(root_or_default(root), fingerprints or [])

    @server.resource("pyfallow://report/current/{root}")
    def current_report(root: str) -> dict[str, Any]:
        return cached_report(root_or_default(root))

    @server.resource("pyfallow://module-graph/{root}")
    def current_module_graph(root: str) -> dict[str, Any]:
        return module_graph(root_or_default(root))

    @server.prompt("pre-commit-check")
    def pre_commit_check() -> str:
        return (
            "Before committing Python changes, call pyfallow.analyze_diff(since='HEAD~1'). "
            "Auto-fix only findings classified as auto_safe, show decision_needed findings to the user, "
            "and block the commit when blocking findings remain."
        )

    @server.prompt("pr-cleanup")
    def pr_cleanup() -> str:
        return (
            "Before pushing a PR branch, call pyfallow.analyze_diff(since='main') and "
            "pyfallow.agent_context(scope='diff'). Auto-fix safe findings, inspect decision_needed "
            "findings for false positives, and summarize remaining risks for the user."
        )

    return server


def _sandbox_root(default_root: str | Path | None) -> Path | None:
    configured = os.environ.get(SANDBOX_ENV) or os.environ.get(LEGACY_SANDBOX_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    if default_root is not None:
        return Path(default_root).expanduser().resolve()
    return None


def _validated_root(root: str | Path, sandbox_root: Path | None) -> Path:
    resolved = Path(root).expanduser().resolve()
    if _is_protected_root(resolved):
        raise ValueError(f"Refusing to analyze unsafe MCP root: {resolved}")
    if sandbox_root is not None and not _is_inside(resolved, sandbox_root):
        raise ValueError(
            f"Requested root {resolved} is outside the MCP sandbox {sandbox_root}."
        )
    return resolved


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _is_protected_root(path: Path) -> bool:
    protected = {
        Path("/").resolve(),
        Path.home().resolve(),
        Path("/bin").resolve(),
        Path("/etc").resolve(),
        Path("/sbin").resolve(),
        Path("/usr").resolve(),
    }
    for raw in ("/Library", "/System", "/private/etc"):
        candidate = Path(raw)
        if candidate.exists():
            protected.add(candidate.resolve())
    return path in protected


def main(argv: list[str] | None = None, *, prog: str = "fallow-py-mcp") -> int:
    parser = argparse.ArgumentParser(prog=prog, description="MCP server for fallow-py.")
    parser.add_argument("--root", default=".", help="Default analysis root for tools/resources.")
    parser.add_argument("--version", action="version", version=f"{prog} {VERSION}")
    args = parser.parse_args(argv)
    build_server(args.root).run()
    return 0
