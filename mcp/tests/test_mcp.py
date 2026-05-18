from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import subprocess
import sys
import textwrap
import time
import tomllib
from pathlib import Path
from urllib.parse import quote

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from fallow_py_mcp.runtime import REPORT_CACHE, cached_report
from fallow_py_mcp.server import build_server
from fallow_py_mcp.tools import verify_imports_impl

TIMEOUT = 15
ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


def run_git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
        timeout=TIMEOUT,
    )


def init_git_repo(root: Path) -> None:
    assert run_git(root, "init").returncode == 0
    assert run_git(root, "branch", "-M", "main").returncode == 0
    assert run_git(root, "config", "user.email", "pyfallow@example.invalid").returncode == 0
    assert run_git(root, "config", "user.name", "pyfallow tests").returncode == 0


def commit_all(root: Path, message: str) -> None:
    assert run_git(root, "add", "-A").returncode == 0
    result = run_git(root, "commit", "-m", message)
    assert result.returncode == 0, result.stdout + result.stderr


def make_repo(root: Path) -> Path:
    write(
        root / "pyproject.toml",
        """
        [project]
        dependencies = ["unusedpkg"]

        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        """,
    )
    write(
        root / "src/pkg/__init__.py",
        """
        from .public import PublicThing
        __all__ = ["PublicThing"]
        """,
    )
    write(root / "src/pkg/public.py", "class PublicThing:\n    pass\n")
    write(root / "src/app.py", "from pkg import PublicThing\n\ndef main():\n    return PublicThing()\n")
    init_git_repo(root)
    commit_all(root, "initial")
    write(root / "src/changed.py", "def changed_unused():\n    return 1\n")
    commit_all(root, "changed")
    return root


def make_cycle_repo(root: Path) -> Path:
    write(
        root / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        """,
    )
    write(root / "src/app.py", "import a\n\ndef main():\n    return a.VALUE\n")
    write(root / "src/a.py", "VALUE = 1\n")
    init_git_repo(root)
    commit_all(root, "initial")
    write(root / "src/a.py", "import b\nVALUE = b.VALUE\n")
    write(root / "src/b.py", "import a\nVALUE = 1\n")
    commit_all(root, "introduce cycle")
    return root


def make_grouped_repo(root: Path) -> Path:
    write(
        root / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]

        [[tool.fallow_py.boundaries.rules]]
        name = "domain-no-infra"
        from = "src/domain/**"
        disallow = ["src/infra/**", "infra.*"]
        severity = "error"
        """,
    )
    write(
        root / "src/app.py",
        """
        import a
        from domain.service import run

        def main():
            return a.VALUE, run()
        """,
    )
    write(root / "src/a.py", "VALUE = 1\n")
    write(root / "src/domain/service.py", "def run():\n    return 'ok'\n")
    write(root / "src/infra/db.py", "def connect():\n    return 'ok'\n")
    init_git_repo(root)
    commit_all(root, "initial")
    write(root / "src/a.py", "import b\nVALUE = b.VALUE\n")
    write(root / "src/b.py", "import a\nVALUE = 1\n")
    write(root / "src/domain/service.py", "from infra.db import connect\n\ndef run():\n    return connect()\n")
    write(root / "src/unused.py", "def orphan():\n    return 1\n")
    commit_all(root, "introduce graph findings")
    return root


async def call_tool(name: str, arguments: dict) -> dict:
    async with Client(build_server()) as client:
        result = await client.call_tool(name, arguments)
        assert not result.is_error
        data = normalize(result.data)
        assert isinstance(data, dict)
        return data


def normalize(value):
    if hasattr(value, "model_dump"):
        return normalize(value.model_dump(mode="json"))
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return normalize(dataclasses.asdict(value))
    if hasattr(value, "__dict__") and value.__class__.__module__.startswith("fastmcp."):
        return normalize(vars(value))
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


def test_mcp_server_lists_expected_capabilities(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with Client(build_server(default_root=tmp_path)) as client:
            tools = {tool.name for tool in await client.list_tools()}
            assert tools == {
                "analyze_diff",
                "agent_context",
                "explain_finding",
                "verify_imports",
                "safe_to_remove",
            }
            templates = {template.uriTemplate for template in await client.list_resource_templates()}
            assert "pyfallow://report/current/{root}" in templates
            assert "pyfallow://module-graph/{root}" in templates
            prompts = {prompt.name for prompt in await client.list_prompts()}
            assert {"pre-commit-check", "pr-cleanup"} <= prompts

    asyncio.run(scenario())


def test_mcp_rejects_roots_outside_default_sandbox(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    write(workspace / "pyproject.toml", "[tool.fallow_py]\nroots = ['src']\n")
    write(workspace / "src/app.py", "def main():\n    return 1\n")
    write(outside / "pyproject.toml", "[tool.fallow_py]\nroots = ['src']\n")
    write(outside / "src/app.py", "def main():\n    return 1\n")

    async def scenario() -> None:
        async with Client(build_server(default_root=workspace)) as client:
            with pytest.raises(ToolError, match="outside the MCP sandbox"):
                await client.call_tool("analyze_diff", {"root": str(outside), "since": "HEAD~1"})

    asyncio.run(scenario())


def test_analyze_diff_returns_structured_diff_result(tmp_path: Path) -> None:
    root = make_repo(tmp_path)

    result = asyncio.run(
        call_tool(
            "analyze_diff",
            {"root": str(root), "since": "HEAD~1", "min_confidence": "medium", "max_findings": 10},
        )
    )

    assert result["summary"]["total_issues"] >= 1
    assert result["diff_scope"]["changed_files"] == ["src/changed.py"]
    assert result["diff_scope"]["changed_modules"] == ["changed"]
    assert result["findings"]
    assert result["truncated"] is False


def test_mcp_classifies_runtime_cycles_like_agent_fix_plan(tmp_path: Path) -> None:
    root = make_cycle_repo(tmp_path)

    result = asyncio.run(
        call_tool(
            "analyze_diff",
            {"root": str(root), "since": "HEAD~1", "min_confidence": "medium", "max_findings": 10},
        )
    )

    cycle = next(item for item in result["findings"] if item["rule"] == "circular-dependency")
    assert cycle["classification"] == "blocking"

    remediation = asyncio.run(call_tool("explain_finding", {"root": str(root), "fingerprint": cycle["fingerprint"]}))
    assert remediation["classification"] == "blocking"


def test_analyze_diff_returns_grouped_classifications(tmp_path: Path) -> None:
    root = make_grouped_repo(tmp_path)

    result = asyncio.run(
        call_tool(
            "analyze_diff",
            {"root": str(root), "since": "HEAD~1", "min_confidence": "medium", "max_findings": 20},
        )
    )

    for group in ["auto_safe", "review_needed", "blocking", "manual_only"]:
        assert isinstance(result[group], list)
    assert {item["rule"] for item in result["blocking"]} == {"boundary-violation", "circular-dependency"}
    assert any(item["rule"] == "unused-module" for item in result["review_needed"])
    grouped = [
        item
        for group in ["auto_safe", "review_needed", "blocking", "manual_only"]
        for item in result[group]
    ]
    assert len(grouped) == len(result["findings"])
    assert [item["fingerprint"] for item in grouped] == [item["fingerprint"] for item in result["findings"]]


def test_analyze_diff_grouped_truncation_keeps_flat_compatibility(tmp_path: Path) -> None:
    root = make_grouped_repo(tmp_path)

    result = asyncio.run(
        call_tool(
            "analyze_diff",
            {"root": str(root), "since": "HEAD~1", "min_confidence": "medium", "max_findings": 1},
        )
    )

    grouped_count = sum(len(result[group]) for group in ["auto_safe", "review_needed", "blocking", "manual_only"])
    assert result["truncated"] is True
    assert grouped_count == 1
    assert len(result["findings"]) == 1
    assert [item["fingerprint"] for group in ["auto_safe", "review_needed", "blocking", "manual_only"] for item in result[group]] == [
        item["fingerprint"] for item in result["findings"]
    ]


def test_agent_context_includes_public_api_and_risk_sections(tmp_path: Path) -> None:
    root = make_repo(tmp_path)

    result = asyncio.run(
        call_tool("agent_context", {"root": str(root), "scope": "full", "max_findings": 10})
    )

    assert result["project_overview"]["modules_count"] >= 3
    assert any(item["name"] == "PublicThing" for item in result["public_api"])
    assert "cycles" in result["architecture_map"]
    assert "dead_code_candidates" in result
    assert "dependency_findings" in result
    assert result["limitations"]


def test_explain_finding_produces_remediation(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    findings = asyncio.run(
        call_tool("analyze_diff", {"root": str(root), "since": "HEAD~1", "max_findings": 10})
    )
    fingerprint = findings["findings"][0]["fingerprint"]

    result = asyncio.run(call_tool("explain_finding", {"root": str(root), "fingerprint": fingerprint}))

    assert result["finding"]["fingerprint"] == fingerprint
    assert result["classification"] in {"auto_safe", "review_needed", "blocking"}
    assert result["one_liner"]
    assert result["fix_options"]
    assert result["safety_notes"]


def test_verify_imports_returns_pre_edit_prediction(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    result = asyncio.run(
        call_tool(
            "verify_imports",
            {
                "root": str(root),
                "file": "src/app.py",
                "planned_imports": ["pkg.PublicThing", "missing_local_symbol", "os"],
            },
        )
    )

    assert result["status"] == "issues_found"
    assert {item["raw"] for item in result["safe"]} == {"pkg.PublicThing", "os"}
    assert result["hallucinated"][0]["raw"] == "missing_local_symbol"
    assert "module 'missing_local_symbol' not found" in result["hallucinated"][0]["reason"]
    assert result["planned_imports"] == ["pkg.PublicThing", "missing_local_symbol", "os"]


def test_verify_imports_uses_cached_report_for_second_call(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    first = normalize(verify_imports_impl(root, "src/app.py", ["os"]))
    start = time.perf_counter()
    second = normalize(verify_imports_impl(root, "src/app.py", ["json"]))

    assert first["status"] == "ok"
    assert second["status"] == "ok"
    assert time.perf_counter() - start < 0.2


def test_verify_imports_cache_detects_new_python_module(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    first = asyncio.run(
        call_tool("verify_imports", {"root": str(root), "file": "src/app.py", "planned_imports": ["new_module"]})
    )
    write(root / "src/new_module.py", "VALUE = 1\n")
    second = asyncio.run(
        call_tool("verify_imports", {"root": str(root), "file": "src/app.py", "planned_imports": ["new_module"]})
    )

    assert first["hallucinated"][0]["raw"] == "new_module"
    assert {item["raw"] for item in second["safe"]} == {"new_module"}


def test_cached_report_invalidates_same_size_source_content_change(tmp_path: Path) -> None:
    root = tmp_path
    write(
        root / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        """,
    )
    source = root / "src/app.py"
    source.parent.mkdir(parents=True)
    before = "def main():\n    return 1\n"
    after = "def main(:\n    return 1\n "
    assert len(before) == len(after)
    source.write_text(before, encoding="utf-8")

    REPORT_CACHE.clear()
    first = cached_report(root)
    original_stat = source.stat()
    assert "parse-error" not in {issue["rule"] for issue in first["issues"]}

    source.write_text(after, encoding="utf-8")
    os.utime(source, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    second = cached_report(root)

    assert second is not first
    assert "parse-error" in {issue["rule"] for issue in second["issues"]}


def test_cached_report_invalidates_same_size_config_content_change(tmp_path: Path) -> None:
    root = tmp_path
    write(root / "src/app.py", "def main():\n    return 1\n")
    config = root / ".fallow-py.toml"
    before = textwrap.dedent(
        """
        roots = ["src"]
        entry = ["src/app.py"]

        [dupes]
        mode = "mild"
        """
    ).strip() + "\n"
    after = before.replace('"mild"', '"wild"')
    assert len(before) == len(after)
    config.write_text(before, encoding="utf-8")

    REPORT_CACHE.clear()
    first = cached_report(root)
    original_stat = config.stat()
    assert "config-error" not in {issue["rule"] for issue in first["issues"]}

    config.write_text(after, encoding="utf-8")
    os.utime(config, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    second = cached_report(root)

    assert second is not first
    assert "config-error" in {issue["rule"] for issue in second["issues"]}


def test_safe_to_remove_classifies_unknown_fingerprints_deterministically(tmp_path: Path) -> None:
    fingerprints = [f"missing-{index}" for index in range(10)]

    result = asyncio.run(call_tool("safe_to_remove", {"root": str(tmp_path), "fingerprints": fingerprints}))

    assert result["unrecognized"] == fingerprints
    assert sorted(result["classifications"]) == fingerprints
    assert {item["decision"] for item in result["classifications"].values()} == {"manual_only"}
    assert {item["recognized"] for item in result["classifications"].values()} == {False}
    assert all(item["decision"] != "auto_safe" for item in result["classifications"].values())
    assert all("not found" in item["rationale"] for item in result["classifications"].values())


def test_safe_to_remove_separates_unrecognized_from_recognized(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        """,
    )
    write(tmp_path / "src/app.py", "def main():\n    return 1\n")
    write(tmp_path / "src/unused.py", "def orphan():\n    return 1\n")
    REPORT_CACHE.clear()
    report = cached_report(tmp_path)
    recognized = next(issue["fingerprint"] for issue in report["issues"] if issue["rule"] == "unused-module")

    result = asyncio.run(
        call_tool("safe_to_remove", {"root": str(tmp_path), "fingerprints": [recognized, "missing-fp"]})
    )

    assert result["unrecognized"] == ["missing-fp"]
    assert set(result["classifications"]) == {recognized, "missing-fp"}
    assert result["classifications"][recognized]["recognized"] is True
    assert result["classifications"]["missing-fp"]["recognized"] is False


def test_resources_return_report_and_module_graph(tmp_path: Path) -> None:
    root = make_repo(tmp_path)
    encoded = quote(str(root), safe="")

    async def scenario() -> None:
        async with Client(build_server()) as client:
            report = await client.read_resource(f"pyfallow://report/current/{encoded}")
            report_payload = json.loads(report[0].text)
            assert "summary" in report_payload
            graph = await client.read_resource(f"pyfallow://module-graph/{encoded}")
            graph_payload = json.loads(graph[0].text)
            assert "modules" in graph_payload
            assert "edges" in graph_payload

    asyncio.run(scenario())


def test_console_entrypoint_help() -> None:
    for module in ["fallow_py_mcp", "pyfallow_mcp"]:
        result = subprocess.run(
            [sys.executable, "-m", module, "--help"],
            text=True,
            capture_output=True,
            check=False,
            timeout=TIMEOUT,
        )

        assert result.returncode == 0
        assert "--root" in result.stdout
        if module == "pyfallow_mcp":
            assert "`pyfallow-mcp` is deprecated" in result.stderr


def test_mcp_release_metadata_uses_fallow_py_distribution() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["name"] == "fallow-py-mcp"
    assert pyproject["project"]["version"] == "0.1.0a3"
    assert "fallow-py>=0.3.0a3" in pyproject["project"]["dependencies"]
    assert pyproject["project"]["scripts"]["fallow-py-mcp"] == "fallow_py_mcp.server:main"
    assert pyproject["project"]["scripts"]["pyfallow-mcp"] == "pyfallow_mcp.server:main"


def test_mcp_self_audit_has_no_distribution_drift() -> None:
    from fallow_py.analysis import analyze
    from fallow_py.config import load_config

    result = analyze(load_config(ROOT))
    dependency_issues = [
        issue
        for issue in result["issues"]
        if issue["rule"] in {"missing-runtime-dependency", "unused-runtime-dependency"}
    ]

    assert not [
        issue
        for issue in dependency_issues
        if issue["rule"] == "missing-runtime-dependency"
        and issue.get("evidence", {}).get("distribution") == "fallow-py"
    ]
    assert not [
        issue
        for issue in dependency_issues
        if issue["rule"] == "unused-runtime-dependency"
        and issue.get("evidence", {}).get("distribution") == "pyfallow"
    ]
