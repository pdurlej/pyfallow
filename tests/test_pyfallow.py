from __future__ import annotations

import json
import hashlib
import importlib.util
import os
import subprocess
import sys
import textwrap
import tomllib
import zipfile
from pathlib import Path

import pytest
import fallow_py
from fallow_py.analysis import analyze
from fallow_py.ast_index import index_file
from fallow_py.baseline import compare_with_baseline, create_baseline
from fallow_py.classify import agent_fix_plan, classify_finding
from fallow_py.config import ConfigError, load_config
from fallow_py.dependencies import parse_dependency_declarations
from fallow_py.models import RULES, VERSION
from fallow_py.predict import parse_import_spec, verify_imports
from fallow_py.rule_explain import RULE_GUIDANCE, explain_all_rules, render_explanation
from fallow_py.sarif import to_sarif


ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = 15


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


def run_cli(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "fallow_py", *args],
        text=True,
        capture_output=True,
        env=env or {**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
        timeout=TIMEOUT,
    )


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


def validate_schema(schema: dict, value) -> None:
    refs = schema.get("$defs", {})

    def resolve(ref: str) -> dict:
        assert ref.startswith("#/$defs/")
        return refs[ref.rsplit("/", 1)[-1]]

    def check(node: dict, item, path: str) -> None:
        if "$ref" in node:
            check(resolve(node["$ref"]), item, path)
            return
        if "anyOf" in node:
            errors = []
            for option in node["anyOf"]:
                try:
                    check(option, item, path)
                    return
                except AssertionError as exc:
                    errors.append(str(exc))
            raise AssertionError(f"{path} did not match anyOf: {errors}")
        if "const" in node:
            assert item == node["const"], path
        if "enum" in node:
            assert item in node["enum"], path
        expected = node.get("type")
        if expected is not None:
            allowed = expected if isinstance(expected, list) else [expected]
            assert any(_type_matches(kind, item) for kind in allowed), path
        if isinstance(item, (int, float)) and "minimum" in node:
            assert item >= node["minimum"], path
        if node.get("type") == "object" or isinstance(item, dict):
            required = set(node.get("required", []))
            assert required <= set(item), path
            properties = node.get("properties", {})
            if node.get("additionalProperties") is False:
                assert set(item) <= set(properties), path
            for key, child in properties.items():
                if key in item:
                    check(child, item[key], f"{path}.{key}")
        if node.get("type") == "array" or isinstance(item, list):
            child = node.get("items")
            if child:
                for index, element in enumerate(item):
                    check(child, element, f"{path}[{index}]")

    check(schema, value, "$")


def _type_matches(kind: str, item) -> bool:
    return (
        (kind == "object" and isinstance(item, dict))
        or (kind == "array" and isinstance(item, list))
        or (kind == "string" and isinstance(item, str))
        or (kind == "integer" and isinstance(item, int) and not isinstance(item, bool))
        or (kind == "boolean" and isinstance(item, bool))
        or (kind == "null" and item is None)
        or (kind == "number" and isinstance(item, (int, float)) and not isinstance(item, bool))
    )


def make_fixture_project(tmp_path: Path) -> Path:
    write(
        tmp_path / "pyproject.toml",
        """
        [project]
        name = "demo"
        version = "0.1.0"
        dependencies = [
          "requests>=2",
          "pillow",
          "fastapi",
          "flask",
          "celery",
          "unusedpkg",
        ]

        [project.optional-dependencies]
        math = ["numpy"]

        [project.scripts]
        demo = "pkg.cli:main"

        [tool.poetry.group.dev.dependencies]
        pytest = "*"

        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/pkg/main.py"]
        include_tests = false

        [tool.fallow_py.dupes]
        min_lines = 3
        min_tokens = 10
        mode = "mild"

        [tool.fallow_py.health]
        max_cyclomatic = 3
        max_cognitive = 4
        max_function_lines = 20
        max_file_lines = 200
        hotspot_score_threshold = 20

        [[tool.fallow_py.boundaries.rules]]
        name = "domain-no-infra"
        from = "src/pkg/domain/**"
        disallow = ["src/pkg/infra/**", "pkg.infra.*"]
        severity = "error"
        """,
    )
    write(
        tmp_path / "requirements.txt",
        """
        flask
        """,
    )
    write(
        tmp_path / "src/pkg/__init__.py",
        """
        from .used import Used
        __all__ = ["Used"]
        """,
    )
    write(
        tmp_path / "src/pkg/main.py",
        """
        import importlib
        import requests
        import missingdist
        import numpy as np
        from PIL import Image
        from .used import Used, used_function
        from . import cycle_a

        NAME = "pkg.dynamic_unknown"
        importlib.import_module("pkg.dynamic_mod")
        importlib.import_module(NAME)

        def main():
            used_function()
            return Used(), requests.__name__, Image, np.__name__, missingdist
        """,
    )
    write(
        tmp_path / "src/pkg/used.py",
        """
        # fallow: ignore[unused-module]
        from typing import TYPE_CHECKING

        if TYPE_CHECKING:
            import pandas as pd

        class Used:
            pass

        def used_function():
            return 1

        def unused_function():
            return 2

        def suppressed_unused():  # fallow: ignore[unused-symbol]
            return 3
        """,
    )
    write(
        tmp_path / "src/pkg/domain/service.py",
        """
        from pkg.infra.db import connect

        def complex_policy(value):
            total = 0
            if value > 10:
                for item in range(value):
                    if item % 2 and item > 3:
                        total += item
                    else:
                        total -= item
            elif value == 3:
                total = 3
            try:
                connect()
            except RuntimeError:
                total = -1
            return total
        """,
    )
    write(
        tmp_path / "src/pkg/infra/db.py",
        """
        def connect():
            return "ok"
        """,
    )
    write(tmp_path / "src/pkg/cycle_a.py", "from . import cycle_b\nVALUE_A = cycle_b.VALUE_B\n")
    write(tmp_path / "src/pkg/cycle_b.py", "from . import cycle_a\nVALUE_B = 1\n")
    write(tmp_path / "src/pkg/unused_mod.py", "def orphan():\n    return 'unused'\n")
    write(tmp_path / "src/pkg/dynamic_mod.py", "VALUE = 1\n")
    write(
        tmp_path / "src/pkg/api.py",
        """
        from fastapi import FastAPI

        app = FastAPI()

        @app.get("/items")
        def list_items():
            return []
        """,
    )
    write(
        tmp_path / "src/pkg/flask_app.py",
        """
        from flask import Flask

        app = Flask(__name__)

        @app.route("/")
        def home():
            return "ok"
        """,
    )
    write(
        tmp_path / "src/pkg/tasks.py",
        """
        from celery import shared_task

        @shared_task
        def work():
            return 1
        """,
    )
    duplicate = """
        def duplicate_block(value):
            total = 0
            for item in range(value):
                if item % 2:
                    total += item
                else:
                    total -= item
            return total
    """
    write(tmp_path / "src/pkg/dupe1.py", duplicate)
    write(tmp_path / "src/pkg/dupe2.py", duplicate.replace("duplicate_block", "renamed_block"))
    write(tmp_path / "src/ns_pkg/mod.py", "VALUE = 1\n")
    write(tmp_path / "src/bad.py", "def broken(:\n    pass\n")
    write(
        tmp_path / "src/tests/test_app.py",
        """
        import pytest

        @pytest.fixture
        def sample():
            return 1

        def test_sample(sample):
            assert sample == 1
        """,
    )
    return tmp_path


def analyze_fixture(root: Path) -> dict:
    return analyze(load_config(root))


def rules(result: dict) -> set[str]:
    return {issue["rule"] for issue in result["issues"]}


def issues_for(result: dict, rule: str) -> list[dict]:
    return [issue for issue in result["issues"] if issue["rule"] == rule]


def test_full_analysis_reports_required_signals(tmp_path: Path) -> None:
    root = make_fixture_project(tmp_path)
    result = analyze_fixture(root)

    assert result["tool"] == "fallow"
    assert result["language"] == "python"
    assert result["schema_version"] == "1.2"
    assert result["analysis"]["modules_analyzed"] >= 10
    assert "pkg.used" in {node["id"] for node in result["graphs"]["modules"]}
    assert "ns_pkg.mod" in {node["id"] for node in result["graphs"]["modules"]}
    assert {"fastapi", "flask", "celery", "pytest"} <= set(result["analysis"]["frameworks_detected"])

    found = rules(result)
    assert "parse-error" in found
    assert "dynamic-import" in found
    assert "circular-dependency" in found
    assert "unused-module" in found
    assert "unused-symbol" in found
    assert "missing-runtime-dependency" in found
    assert "unused-runtime-dependency" in found
    assert "optional-dependency-used-in-runtime" in found
    assert "duplicate-code" in found
    assert "high-cyclomatic-complexity" in found
    assert "high-cognitive-complexity" in found
    assert "boundary-violation" in found
    assert "stale-suppression" in found
    assert result["summary"]["duplicate_groups"] >= 1
    assert result["summary"]["boundary_violations"] == 1
    parse_errors = issues_for(result, "parse-error")
    assert parse_errors[0]["range"]["start"] == {"line": 1, "column": 12}
    assert not any(
        issue["path"] == "src/bad.py"
        and issue["rule"] in {"unused-module", "unused-symbol", "duplicate-code", "high-cyclomatic-complexity"}
        for issue in result["issues"]
    )


def test_discovery_skips_symlink_escape_files_and_roots(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    write(
        root / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src", "linked"]
        entry = ["src/app.py"]
        """,
    )
    write(root / "src/app.py", "def main():\n    return 1\n")
    write(outside / "leaked.py", "def outside_only():\n    return 1\n")
    (root / "src/leaked.py").symlink_to(outside / "leaked.py")
    (root / "linked").symlink_to(outside, target_is_directory=True)

    result = analyze_fixture(root)

    module_paths = {module["path"] for module in result["graphs"]["modules"]}
    assert module_paths == {"src/app.py"}
    assert not any(str(outside) in path for path in module_paths)
    assert not any(str(outside) in issue.get("path", "") for issue in result["issues"])


def test_import_resolution_dependency_mapping_and_type_checking(tmp_path: Path) -> None:
    root = make_fixture_project(tmp_path)
    result = analyze_fixture(root)

    edges = {(edge["from"], edge["to"]) for edge in result["graphs"]["edges"]}
    assert ("pkg.main", "pkg.used") in edges
    assert ("pkg.main", "pkg.cycle_a") in edges
    assert ("pkg.cycle_a", "pkg.cycle_b") in edges

    missing = issues_for(result, "missing-runtime-dependency") + issues_for(result, "missing-type-dependency")
    assert any(issue["evidence"]["distribution"] == "missingdist" for issue in missing)
    pandas = [issue for issue in missing if issue["evidence"]["distribution"] == "pandas"]
    assert pandas and pandas[0]["severity"] == "info" and pandas[0]["confidence"] == "low"
    assert pandas[0]["evidence"]["policy"] == "type-only"
    assert not any(issue["evidence"].get("distribution") == "pillow" for issue in missing)
    assert any(issue["evidence"]["distribution"] == "numpy" for issue in issues_for(result, "optional-dependency-used-in-runtime"))
    assert any(issue["evidence"]["distribution"] == "unusedpkg" for issue in issues_for(result, "unused-runtime-dependency"))


def test_dead_code_is_conservative_for_exports_suppressions_and_frameworks(tmp_path: Path) -> None:
    root = make_fixture_project(tmp_path)
    result = analyze_fixture(root)

    unused_symbols = {(issue["path"], issue.get("symbol")) for issue in issues_for(result, "unused-symbol")}
    assert ("src/pkg/used.py", "unused_function") in unused_symbols
    assert ("src/pkg/used.py", "suppressed_unused") not in unused_symbols
    assert ("src/pkg/api.py", "list_items") not in unused_symbols
    assert ("src/pkg/flask_app.py", "home") not in unused_symbols
    assert ("src/pkg/tasks.py", "work") not in unused_symbols
    assert ("src/pkg/__init__.py", "Used") not in unused_symbols

    unused_modules = {issue["path"] for issue in issues_for(result, "unused-module")}
    assert "src/pkg/unused_mod.py" in unused_modules
    assert "src/pkg/__init__.py" not in unused_modules


def test_config_parsers_cover_pep621_poetry_and_requirements(tmp_path: Path) -> None:
    root = make_fixture_project(tmp_path)
    declarations = parse_dependency_declarations(root)

    assert "requests" in declarations.runtime
    assert "pillow" in declarations.runtime
    assert "numpy" in declarations.optional
    assert "pytest" in declarations.dev
    assert "flask" in declarations.runtime
    assert "pkg.cli" in declarations.scripts
    assert ("pkg.cli", "main") in declarations.script_targets


def test_output_formats_baseline_and_agent_context(tmp_path: Path) -> None:
    root = make_fixture_project(tmp_path)
    baseline_path = root / ".fallow-baseline.json"
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

    json_run = run_cli(["analyze", "--root", str(root), "--format", "json"], env)
    assert json_run.returncode == 0
    payload = json.loads(json_run.stdout)
    assert "summary" in payload and "metrics" in payload and "limitations" in payload

    default_run = run_cli(["--root", str(root), "--format", "json"], env)
    assert default_run.returncode == 0
    assert json.loads(default_run.stdout)["language"] == "python"

    sarif_run = run_cli(["analyze", "--root", str(root), "--format", "sarif"], env)
    assert sarif_run.returncode == 0
    sarif = json.loads(sarif_run.stdout)
    assert sarif["version"] == "2.1.0"
    assert sarif["runs"][0]["results"]

    create_run = run_cli(
        [
            "baseline",
            "create",
            "--root",
            str(root),
            "--output",
            str(baseline_path),
            "--quiet",
        ],
        env,
    )
    assert create_run.returncode == 0
    assert baseline_path.exists()

    compare_run = run_cli(
        [
            "baseline",
            "compare",
            "--root",
            str(root),
            "--baseline",
            str(baseline_path),
            "--format",
            "json",
        ],
        env,
    )
    assert compare_run.returncode == 0
    compared = json.loads(compare_run.stdout)
    assert compared["baseline"]["new_count"] == 0
    assert compared["baseline"]["existing_count"] > 0

    context_run = run_cli(["agent-context", "--root", str(root), "--format", "markdown"], env)
    assert context_run.returncode == 0
    for heading in [
        "Project Overview",
        "Architecture Map",
        "Risk Map",
        "Dead Code Candidates",
        "Dependency Findings",
        "Suggested Agent Workflow",
        "Limitations",
    ]:
        assert heading in context_run.stdout


def test_release_metadata_version_schema_and_readme_examples() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert fallow_py.__version__ == pyproject["project"]["version"] == VERSION
    assert pyproject["project"]["name"] == "fallow-py"
    assert pyproject["project"]["version"] == "0.3.0a3"
    assert pyproject["project"]["dependencies"] == []
    assert pyproject["project"]["scripts"]["fallow-py"] == "fallow_py.cli:main"
    assert pyproject["project"]["scripts"]["pyfallow"] == "pyfallow.cli:main"

    version_run = run_cli(["--version"])
    assert version_run.returncode == 0
    assert version_run.stdout.strip() == "fallow-py 0.3.0a3"

    canonical_run = subprocess.run(
        [sys.executable, "-m", "fallow_py", "--version"],
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
        timeout=TIMEOUT,
    )
    assert canonical_run.returncode == 0
    assert canonical_run.stdout.strip() == "fallow-py 0.3.0a3"

    legacy_run = subprocess.run(
        [sys.executable, "-m", "pyfallow", "--version"],
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        check=False,
        timeout=TIMEOUT,
    )
    assert legacy_run.returncode == 0
    assert legacy_run.stdout.strip() == "pyfallow 0.3.0a3"
    assert "`pyfallow` is deprecated" in legacy_run.stderr

    for path in [
        ROOT / "schemas/pyfallow-report.schema.json",
        ROOT / "schemas/pyfallow-sarif.schema.json",
        ROOT / "schemas/pyfallow-fix-plan.schema.json",
        ROOT / "examples/outputs/demo-report.excerpt.json",
        ROOT / "examples/outputs/demo.sarif.excerpt.json",
        ROOT / "examples/outputs/soak-summary.example.json",
    ]:
        assert json.loads(path.read_text(encoding="utf-8"))

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "python -m fallow_py analyze --root examples/demo_project --format text" in readme
    assert "not currently an official fallow-rs/fallow project" in readme
    assert "Runtime dependencies are stdlib-only" in readme


def test_legacy_pyfallow_import_shim_preserves_public_api() -> None:
    for name in list(sys.modules):
        if name == "pyfallow" or name.startswith("pyfallow."):
            sys.modules.pop(name)

    with pytest.warns(DeprecationWarning, match="pyfallow"):
        legacy = importlib.import_module("pyfallow")

    assert legacy.__version__ == fallow_py.__version__
    assert legacy.analyze is fallow_py.analyze
    assert legacy.load_config is fallow_py.load_config

    legacy_analysis = importlib.import_module("pyfallow.analysis")
    canonical_analysis = importlib.import_module("fallow_py.analysis")
    assert legacy_analysis is canonical_analysis

    legacy_cli = importlib.import_module("pyfallow.cli")
    canonical_cli = importlib.import_module("fallow_py.cli")
    assert callable(legacy_cli.main)
    assert legacy_cli.main is not canonical_cli.main


def test_example_project_cli_commands_work() -> None:
    root = ROOT / "examples/demo_project"

    text_run = run_cli(["analyze", "--root", str(root), "--format", "text"])
    assert text_run.returncode == 0
    assert "PY040" in text_run.stdout
    assert "PY020" in text_run.stdout
    assert "PY070" in text_run.stdout

    json_run = run_cli(["--root", str(root), "--format", "json"])
    assert json_run.returncode == 0
    payload = json.loads(json_run.stdout)
    assert payload["language"] == "python"
    assert {"missing-runtime-dependency", "circular-dependency", "boundary-violation"} <= rules(payload)

    agent_run = run_cli(["agent-context", "--root", str(root), "--format", "markdown"])
    assert agent_run.returncode == 0
    assert "Project Overview" in agent_run.stdout


def test_self_audit_gate_is_clean_for_repository() -> None:
    result = run_cli(
        [
            "analyze",
            "--root",
            str(ROOT),
            "--fail-on",
            "warning",
            "--min-confidence",
            "medium",
        ]
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "0 issues" in result.stdout


def test_cli_exit_codes_and_focus_commands(tmp_path: Path) -> None:
    root = make_fixture_project(tmp_path)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

    no_fail = run_cli(["deps", "--root", str(root), "--format", "text"], env)
    assert no_fail.returncode == 0
    assert "PY040" in no_fail.stdout
    assert "PY060" not in no_fail.stdout

    fail_warning = run_cli(
        [
            "deps",
            "--root",
            str(root),
            "--fail-on",
            "warning",
            "--min-confidence",
            "medium",
        ],
        env,
    )
    assert fail_warning.returncode == 1

    parse_root = tmp_path / "only_bad"
    write(parse_root / "bad.py", "def broken(:\n    pass\n")
    parse_fail = run_cli(["analyze", "--root", str(parse_root), "--fail-on", "error"], env)
    assert parse_fail.returncode == 3

    changed_only = run_cli(
        [
            "analyze",
            "--root",
            str(root),
            "--changed-only",
            "--format",
            "json",
        ],
        env,
    )
    assert changed_only.returncode == 0
    payload = json.loads(changed_only.stdout)
    assert payload["analysis"]["changed_only"]["requested"] is True
    assert payload["analysis"]["changed_only"]["effective"] is False
    warning_codes = {warning["code"] for warning in payload["analysis"]["warnings"]}
    assert warning_codes == {"changed-only-not-available-non-git"}
    assert "changed-only-deprecated" not in warning_codes
    assert "--changed-only is deprecated" not in changed_only.stderr


def test_since_filters_findings_to_changed_files(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        """,
    )
    write(tmp_path / "src/app.py", "def main():\n    return 1\n")
    write(tmp_path / "src/stale.py", "def stale_unused():\n    return 1\n")
    init_git_repo(tmp_path)
    commit_all(tmp_path, "initial")
    write(tmp_path / "src/changed.py", "def changed_unused():\n    return 2\n")
    commit_all(tmp_path, "add changed file")

    result = run_cli(["analyze", "--root", str(tmp_path), "--since", "HEAD~1", "--format", "json"])

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["analysis"]["diff_scope"]["since"] == "HEAD~1"
    assert payload["analysis"]["diff_scope"]["changed_files"] == ["src/changed.py"]
    assert payload["analysis"]["diff_scope"]["changed_modules"] == ["changed"]
    assert payload["analysis"]["diff_scope"]["filtering_active"] is True
    assert payload["analysis"]["changed_only"]["effective"] is True
    assert payload["issues"]
    assert {issue["path"] for issue in payload["issues"]} == {"src/changed.py"}


def test_since_branch_ref_filters_multiple_changed_files(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        """,
    )
    write(tmp_path / "src/app.py", "def main():\n    return 1\n")
    write(tmp_path / "src/stale.py", "def stale_unused():\n    return 1\n")
    init_git_repo(tmp_path)
    commit_all(tmp_path, "initial main")
    assert run_git(tmp_path, "checkout", "-b", "feature").returncode == 0
    for index in range(5):
        write(tmp_path / f"src/changed_{index}.py", f"def changed_{index}():\n    return {index}\n")
    commit_all(tmp_path, "feature files")

    result = run_cli(["analyze", "--root", str(tmp_path), "--since", "main", "--format", "json"])

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    expected = [f"src/changed_{index}.py" for index in range(5)]
    assert payload["analysis"]["diff_scope"]["changed_files"] == expected
    assert payload["analysis"]["diff_scope"]["changed_modules"] == [f"changed_{index}" for index in range(5)]
    assert {issue["path"] for issue in payload["issues"]} == set(expected)
    assert "src/stale.py" not in {issue["path"] for issue in payload["issues"]}


def test_since_includes_uncommitted_modified_files(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        """,
    )
    write(tmp_path / "src/app.py", "def main():\n    return 1\n")
    write(tmp_path / "src/changed.py", "VALUE = 1\n")
    init_git_repo(tmp_path)
    commit_all(tmp_path, "initial")
    write(tmp_path / "src/changed.py", "def changed_unused():\n    return 2\n")

    result = run_cli(["analyze", "--root", str(tmp_path), "--since", "HEAD", "--format", "json"])

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["analysis"]["diff_scope"]["changed_files"] == ["src/changed.py"]
    assert payload["issues"]
    assert {issue["path"] for issue in payload["issues"]} == {"src/changed.py"}


def test_since_includes_untracked_python_files(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        """,
    )
    write(tmp_path / "src/app.py", "def main():\n    return 1\n")
    init_git_repo(tmp_path)
    commit_all(tmp_path, "initial")
    write(tmp_path / "src/new_unused.py", "def new_unused():\n    return 2\n")

    result = run_cli(["analyze", "--root", str(tmp_path), "--since", "HEAD", "--format", "json"])

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["analysis"]["diff_scope"]["changed_files"] == ["src/new_unused.py"]
    assert payload["analysis"]["diff_scope"]["changed_modules"] == ["new_unused"]
    assert payload["issues"]
    assert {issue["path"] for issue in payload["issues"]} == {"src/new_unused.py"}


def test_since_keeps_cycle_findings_when_one_member_changed(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        """,
    )
    write(tmp_path / "src/app.py", "import a\n\ndef main():\n    return a.VALUE\n")
    write(tmp_path / "src/a.py", "import b\nVALUE = b.VALUE\n")
    write(tmp_path / "src/b.py", "VALUE = 1\n")
    init_git_repo(tmp_path)
    commit_all(tmp_path, "initial acyclic")
    write(tmp_path / "src/b.py", "import a\nVALUE = 1\n")
    commit_all(tmp_path, "introduce cycle")

    result = run_cli(["analyze", "--root", str(tmp_path), "--since", "HEAD~1", "--format", "json"])

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["analysis"]["diff_scope"]["changed_files"] == ["src/b.py"]
    cycles = issues_for(payload, "circular-dependency")
    assert len(cycles) == 1
    assert set(cycles[0]["evidence"]["cycle_path"]) == {"a", "b"}
    assert set(cycles[0]["evidence"]["files"]) == {"src/a.py", "src/b.py"}


def test_since_keeps_boundary_findings_when_source_changed(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]

        [[tool.fallow_py.boundaries.rules]]
        name = "domain-no-infra"
        from = "src/pkg/domain/**"
        disallow = ["src/pkg/infra/**", "pkg.infra.*"]
        severity = "error"
        """,
    )
    write(tmp_path / "src/app.py", "from pkg.domain.service import service\n\ndef main():\n    return service()\n")
    write(tmp_path / "src/pkg/domain/service.py", "def service():\n    return 'ok'\n")
    write(tmp_path / "src/pkg/infra/db.py", "def connect():\n    return 'db'\n")
    init_git_repo(tmp_path)
    commit_all(tmp_path, "initial allowed")
    write(tmp_path / "src/pkg/domain/service.py", "from pkg.infra.db import connect\n\ndef service():\n    return connect()\n")
    commit_all(tmp_path, "violate boundary")

    result = run_cli(["analyze", "--root", str(tmp_path), "--since", "HEAD~1", "--format", "json"])

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["analysis"]["diff_scope"]["changed_files"] == ["src/pkg/domain/service.py"]
    violations = issues_for(payload, "boundary-violation")
    assert len(violations) == 1
    assert violations[0]["evidence"]["importer_module"] == "pkg.domain.service"
    assert violations[0]["evidence"]["imported_module"] == "pkg.infra.db"


def test_since_non_git_workspace_falls_back_with_warning(tmp_path: Path) -> None:
    root = make_fixture_project(tmp_path)

    result = run_cli(["analyze", "--root", str(root), "--since", "HEAD", "--format", "json"])

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["issues"]
    assert payload["analysis"]["changed_only"] == {
        "requested": True,
        "effective": False,
        "reason": "--since requested outside a Git workspace; full analysis was used.",
    }
    assert payload["analysis"]["diff_scope"]["filtering_active"] is False
    assert payload["analysis"]["warnings"][0]["code"] == "since-not-available-non-git"


def test_since_invalid_ref_exits_2(tmp_path: Path) -> None:
    write(tmp_path / "app.py", "def main():\n    return 1\n")
    init_git_repo(tmp_path)
    commit_all(tmp_path, "initial")

    result = run_cli(["analyze", "--root", str(tmp_path), "--since", "missing-ref"])

    assert result.returncode == 2
    assert "ref not found: missing-ref" in result.stderr


def test_changed_only_deprecation_warning_and_since_alias(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        """,
    )
    write(tmp_path / "src/app.py", "def main():\n    return 1\n")
    init_git_repo(tmp_path)
    commit_all(tmp_path, "initial")
    write(tmp_path / "src/changed.py", "def changed_unused():\n    return 2\n")
    commit_all(tmp_path, "add changed file")

    result = run_cli(["analyze", "--root", str(tmp_path), "--changed-only", "--format", "json"])

    assert result.returncode == 0, result.stdout + result.stderr
    assert "--changed-only is deprecated; use --since HEAD~1 instead." in result.stderr
    payload = json.loads(result.stdout)
    assert payload["analysis"]["diff_scope"]["since"] == "HEAD~1"
    assert payload["analysis"]["diff_scope"]["changed_files"] == ["src/changed.py"]
    warning_codes = {warning["code"] for warning in payload["analysis"]["warnings"]}
    assert "changed-only-deprecated" in warning_codes


def test_since_zero_changes_returns_clean_report(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        """,
    )
    write(tmp_path / "src/app.py", "def main():\n    return 1\n")
    write(tmp_path / "src/stale.py", "def stale_unused():\n    return 1\n")
    init_git_repo(tmp_path)
    commit_all(tmp_path, "initial")

    result = run_cli(["analyze", "--root", str(tmp_path), "--since", "HEAD", "--format", "json"])

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["analysis"]["diff_scope"]["changed_files"] == []
    assert payload["analysis"]["diff_scope"]["filtering_active"] is True
    assert payload["analysis"]["changed_only"]["effective"] is True
    assert payload["summary"]["total_issues"] == 0
    assert payload["summary"]["duplicate_groups"] == 0
    assert payload["issues"] == []


def test_since_renamed_file_uses_new_path(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        """,
    )
    write(tmp_path / "src/app.py", "def main():\n    return 1\n")
    write(tmp_path / "src/old.py", "def old_unused():\n    return 1\n")
    init_git_repo(tmp_path)
    commit_all(tmp_path, "initial")
    assert run_git(tmp_path, "mv", "src/old.py", "src/new.py").returncode == 0
    commit_all(tmp_path, "rename file")

    result = run_cli(["analyze", "--root", str(tmp_path), "--since", "HEAD~1", "--format", "json"])

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["analysis"]["diff_scope"]["changed_files"] == ["src/new.py"]
    assert payload["issues"]
    assert {issue["path"] for issue in payload["issues"]} == {"src/new.py"}


def test_inferred_entrypoints_management_commands_and_no_boundary_violation(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]

        [[tool.fallow_py.boundaries.rules]]
        name = "domain-no-infra"
        from = "src/pkg/domain/**"
        disallow = ["src/pkg/infra/**"]
        severity = "error"
        """,
    )
    write(tmp_path / "src/app/main.py", "from pkg.domain.model import Model\n\ndef main():\n    return Model()\n")
    write(tmp_path / "src/pkg/domain/model.py", "class Model:\n    pass\n")
    write(tmp_path / "src/pkg/infra/db.py", "def connect():\n    return None\n")
    write(tmp_path / "src/pkg/management/commands/cleanup.py", "def handle():\n    return None\n")

    result = analyze_fixture(tmp_path)

    assert any(entry["module"] == "app.main" and entry["reason"] == "conventional-name" for entry in result["analysis"]["entrypoints"])
    assert "src/pkg/domain/model.py" not in {issue["path"] for issue in issues_for(result, "unused-module")}
    assert "src/pkg/management/commands/cleanup.py" not in {issue["path"] for issue in issues_for(result, "unused-module")}
    assert result["summary"]["boundary_violations"] == 0


def test_include_tests_keeps_pytest_fixtures_conservative(tmp_path: Path) -> None:
    root = make_fixture_project(tmp_path)
    config = load_config(root)
    config.include_tests = True
    result = analyze(config)

    unused_symbols = {(issue["path"], issue.get("symbol")) for issue in issues_for(result, "unused-symbol")}
    assert ("src/tests/test_app.py", "sample") not in unused_symbols


def test_visit_if_test_condition_records_name_references(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        """,
    )
    write(
        tmp_path / "src/app.py",
        """
        ALLOWED = {"a", "b"}
        FALLBACK = "x"
        DEFAULT = 1

        def main(x):
            if x in ALLOWED:
                return FALLBACK
            elif DEFAULT and x:
                return DEFAULT
            return None
        """,
    )

    result = analyze_fixture(tmp_path)
    unused = {(issue["module"], issue.get("symbol")) for issue in issues_for(result, "unused-symbol")}
    assert ("app", "ALLOWED") not in unused
    assert ("app", "FALLBACK") not in unused
    assert ("app", "DEFAULT") not in unused


def test_reexports_and_from_package_submodule_alias_usage(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/main.py"]

        [tool.fallow_py.dead_code]
        confidence_for_init_exports = "high"
        """,
    )
    write(
        tmp_path / "src/pkg/__init__.py",
        """
        from .model import Exported
        __all__ = ["Exported"]
        """,
    )
    write(
        tmp_path / "src/pkg/model.py",
        """
        class Exported:
            pass

        class Unused:
            pass
        """,
    )
    write(
        tmp_path / "src/pkg/submodule.py",
        """
        class Thing:
            pass

        class Other:
            pass
        """,
    )
    write(
        tmp_path / "src/main.py",
        """
        from pkg import Exported
        from pkg import submodule

        def main():
            return Exported(), submodule.Thing()
        """,
    )

    result = analyze_fixture(tmp_path)
    unused = {(issue["module"], issue.get("symbol")) for issue in issues_for(result, "unused-symbol")}
    assert ("pkg.model", "Exported") not in unused
    assert ("pkg.submodule", "Thing") not in unused
    assert ("pkg.model", "Unused") in unused
    assert ("pkg.submodule", "Other") in unused
    exports = result["graphs"]["exports"]
    assert any(item["name"] == "Exported" and item["origin_module"] == "pkg.model" for item in exports)


def test_export_mutations_aliases_and_star_exports(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/main.py"]

        [tool.fallow_py.dead_code]
        confidence_for_init_exports = "high"
        """,
    )
    write(
        tmp_path / "src/pkg/__init__.py",
        """
        from .model import Original as PublicAlias
        from .extra import *
        __all__ = ["PublicAlias"]
        __all__ += ["Extra"]
        __all__.append("Appended")
        __all__.extend(["Extended"])
        """,
    )
    write(
        tmp_path / "src/pkg/model.py",
        """
        class Original:
            pass

        class Appended:
            pass

        class Extended:
            pass
        """,
    )
    write(
        tmp_path / "src/pkg/extra.py",
        """
        __all__ = ["Extra"]

        class Extra:
            pass
        """,
    )
    write(
        tmp_path / "src/main.py",
        """
        from pkg import PublicAlias, Extra

        def main():
            return PublicAlias(), Extra()
        """,
    )

    result = analyze_fixture(tmp_path)
    exports = result["graphs"]["exports"]
    assert any(item["name"] == "PublicAlias" and item["origin_symbol"] == "Original" and item["source"] == "direct-reexport" for item in exports)
    assert any(item["name"] == "Extra" and item["source"] == "star-reexport" and item["confidence"] == "high" for item in exports)
    assert any(item["name"] == "Appended" and item["source"] == "__all__-mutation" for item in exports)
    assert any(item["name"] == "Extended" and item["source"] == "__all__-mutation" for item in exports)
    model_node = next(module for module in result["graphs"]["modules"] if module["id"] == "pkg.model")
    original = next(symbol for symbol in model_node["symbols"] if symbol["name"] == "Original")
    appended = next(symbol for symbol in model_node["symbols"] if symbol["name"] == "Appended")
    assert original["state"]["public_api"] is True and original["state"]["referenced"] is True
    assert original["state"]["public_api_confidence"] == "high"
    assert appended["state"]["public_api"] is False and appended["state"]["referenced"] is False


def test_unknown_star_exports_lower_but_do_not_suppress_unused_symbol(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/main.py"]
        """,
    )
    write(tmp_path / "src/pkg/__init__.py", "from .unknown import *\n")
    write(tmp_path / "src/pkg/unknown.py", "class MaybePublic:\n    pass\n")
    write(tmp_path / "src/main.py", "import pkg\n\ndef main():\n    return pkg\n")

    result = analyze_fixture(tmp_path)
    unused = [issue for issue in issues_for(result, "unused-symbol") if issue["symbol"] == "MaybePublic"]
    assert unused and unused[0]["confidence"] == "low"
    node = next(module for module in result["graphs"]["modules"] if module["id"] == "pkg.unknown")
    symbol = next(item for item in node["symbols"] if item["name"] == "MaybePublic")
    assert symbol["state"]["public_api"] is True
    assert symbol["state"]["public_api_confidence"] == "low"
    assert symbol["state"]["dynamic_uncertain"] is True


def test_all_concat_getattr_and_namespace_ambiguity_are_reported(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src", "alt"]
        entry = ["src/main.py"]
        """,
    )
    write(
        tmp_path / "src/pkg/__init__.py",
        """
        from .model import A, B
        __all__ = ["A"] + ["B"]

        def __getattr__(name):
            raise AttributeError(name)
        """,
    )
    write(tmp_path / "src/pkg/model.py", "class A:\n    pass\n\nclass B:\n    pass\n")
    write(tmp_path / "src/pkg/amb.py", "VALUE = 'src'\n")
    write(tmp_path / "alt/pkg/amb.py", "VALUE = 'alt'\n")
    write(tmp_path / "src/main.py", "from pkg import A\n\ndef main():\n    return A()\n")

    result = analyze_fixture(tmp_path)
    exports = {(item["module"], item["name"]) for item in result["graphs"]["exports"]}
    assert ("pkg", "A") in exports
    assert ("pkg", "B") in exports
    package = next(module for module in result["graphs"]["modules"] if module["id"] == "pkg")
    assert package["state"]["dynamic_uncertain"] is True
    assert any(item["module"] == "pkg.amb" for item in result["analysis"]["module_ambiguities"])


def test_explicit_source_roots_preserve_configured_order(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["zsrc", "asrc"]
        entry = ["zsrc/app.py"]
        """,
    )
    write(tmp_path / "zsrc/app.py", "def main():\n    return 1\n")
    write(tmp_path / "asrc/helper.py", "def helper():\n    return 1\n")

    result = analyze_fixture(tmp_path)

    assert result["analysis"]["source_roots"] == ["zsrc", "asrc"]


def test_inferred_source_roots_prefer_specific_children_before_repo_root(tmp_path: Path) -> None:
    write(tmp_path / "app.py", "def root_main():\n    return 1\n")
    write(tmp_path / "src/pkg/app.py", "def package_main():\n    return 1\n")

    result = analyze_fixture(tmp_path)

    assert result["analysis"]["source_roots"][:2] == ["src", "."]


def test_include_tests_false_does_not_leak_test_references_to_production(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        include_tests = false

        [tool.fallow_py.dependencies]
        include_dev = true
        """,
    )
    write(tmp_path / "src/app.py", "def main():\n    return 'ok'\n")
    write(tmp_path / "src/prod.py", "class OnlyTestsUse:\n    pass\n")
    write(tmp_path / "src/tests/test_prod.py", "from prod import OnlyTestsUse\n\ndef test_ref():\n    assert OnlyTestsUse\n")

    result = analyze_fixture(tmp_path)
    unused = {(issue["module"], issue.get("symbol")) for issue in issues_for(result, "unused-symbol")}
    assert ("prod", "OnlyTestsUse") in unused
    edges = {(edge["from"], edge["to"]) for edge in result["graphs"]["edges"]}
    assert ("tests.test_prod", "prod") not in edges
    prod_node = next(module for module in result["graphs"]["modules"] if module["id"] == "prod")
    symbol = next(item for item in prod_node["symbols"] if item["name"] == "OnlyTestsUse")
    assert symbol["state"]["referenced"] is False
    assert symbol["state"]["referenced_by"] == {"production": 0, "tests": 1, "type_only": 0}


def test_production_importing_test_code_is_reported_without_graph_edge(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        include_tests = false

        [tool.fallow_py.dependencies]
        include_dev = true
        """,
    )
    write(tmp_path / "src/app.py", "from tests.helpers import helper\n\ndef main():\n    return helper()\n")
    write(tmp_path / "src/tests/helpers.py", "def helper():\n    return 1\n")

    result = analyze_fixture(tmp_path)
    assert any(issue["rule"] == "production-imports-test-code" for issue in result["issues"])
    edges = {(edge["from"], edge["to"]) for edge in result["graphs"]["edges"]}
    assert ("app", "tests.helpers") not in edges


def test_test_duplicates_and_complexity_are_skipped_when_tests_excluded(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        include_tests = false

        [tool.fallow_py.dupes]
        min_lines = 3
        min_tokens = 8

        [tool.fallow_py.health]
        max_cyclomatic = 2
        max_cognitive = 2
        """,
    )
    write(tmp_path / "src/app.py", "def main():\n    return 1\n")
    block = """
        def test_complex(value):
            total = 0
            if value:
                for item in range(value):
                    if item % 2:
                        total += item
            return total
    """
    write(tmp_path / "src/tests/test_one.py", block)
    write(tmp_path / "src/tests/test_two.py", block.replace("test_complex", "test_other"))

    result = analyze_fixture(tmp_path)
    assert not issues_for(result, "duplicate-code")
    assert not [
        issue
        for issue in result["issues"]
        if issue["rule"] in {"high-cyclomatic-complexity", "high-cognitive-complexity"}
    ]

    config = load_config(tmp_path)
    config.include_tests = True
    included = analyze(config)
    assert issues_for(included, "duplicate-code")
    assert any(
        issue["path"].startswith("src/tests/")
        for issue in included["issues"]
        if issue["rule"] in {"high-cyclomatic-complexity", "high-cognitive-complexity"}
    )


def test_packaging_script_target_symbol_is_used(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [project.scripts]
        demo = "pkg.cli:main"

        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/other.py"]
        """,
    )
    write(tmp_path / "src/pkg/__init__.py", "__all__ = []\n")
    write(
        tmp_path / "src/pkg/cli.py",
        """
        def main():
            return 0

        def helper():
            return 1
        """,
    )
    write(tmp_path / "src/other.py", "def main():\n    return 2\n")

    result = analyze_fixture(tmp_path)
    unused = {(issue["module"], issue.get("symbol")) for issue in issues_for(result, "unused-symbol")}
    assert ("pkg.cli", "main") not in unused
    assert ("pkg.cli", "helper") in unused


def test_configured_entry_symbols_are_entrypoint_managed(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]

        [tool.fallow_py.dead_code]
        entry_symbols = ["serve"]
        """,
    )
    write(
        tmp_path / "src/app.py",
        """
        def serve():
            return 1

        def helper():
            return 2
        """,
    )

    result = analyze_fixture(tmp_path)
    unused = {(issue["module"], issue.get("symbol")) for issue in issues_for(result, "unused-symbol")}
    assert ("app", "serve") not in unused
    assert ("app", "helper") in unused


def test_dependency_policy_defaults(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [project]
        dependencies = ["runtimeonly"]

        [tool.poetry.group.dev.dependencies]
        devonly = "*"

        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        include_tests = false

        [tool.fallow_py.dependencies]
        include_dev = true
        """,
    )
    write(
        tmp_path / "src/app.py",
        """
        from typing import TYPE_CHECKING
        import devonly

        if TYPE_CHECKING:
            import typeonly

        def main():
            return devonly.__name__
        """,
    )
    write(tmp_path / "src/tests/test_deps.py", "import testonly\nimport runtimeonly\n")

    result = analyze_fixture(tmp_path)
    missing = (
        issues_for(result, "dev-dependency-used-in-runtime")
        + issues_for(result, "missing-type-dependency")
        + issues_for(result, "missing-test-dependency")
    )
    assert any(issue["evidence"]["distribution"] == "devonly" and issue["evidence"]["policy"] == "dev-declared-runtime-use" for issue in missing)
    typeonly = [issue for issue in issues_for(result, "missing-type-dependency") if issue["evidence"]["distribution"] == "typeonly"]
    assert typeonly and typeonly[0]["severity"] == "info" and typeonly[0]["confidence"] == "low"
    assert not any(issue["evidence"].get("distribution") == "testonly" for issue in missing)

    unused = issues_for(result, "runtime-dependency-used-only-in-tests")
    assert any(issue["evidence"]["distribution"] == "runtimeonly" and issue["evidence"]["policy"] == "test-only" for issue in unused)


def test_dependency_include_optional_and_dev_knobs_are_observable(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [project.optional-dependencies]
        speedups = ["orjson"]

        [tool.poetry.group.dev.dependencies]
        devonly = "*"

        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]

        [tool.fallow_py.dependencies]
        include_optional = false
        include_dev = false
        """,
    )
    write(
        tmp_path / "src/app.py",
        """
        import devonly
        import orjson

        def main():
            return devonly.__name__, orjson.__name__
        """,
    )

    result = analyze_fixture(tmp_path)
    missing = {issue["evidence"]["distribution"] for issue in issues_for(result, "missing-runtime-dependency")}
    assert {"devonly", "orjson"} <= missing
    assert not issues_for(result, "dev-dependency-used-in-runtime")
    assert not issues_for(result, "optional-dependency-used-in-runtime")

    config = load_config(tmp_path)
    config.dependencies.include_optional = True
    config.dependencies.include_dev = True
    included = analyze(config)
    assert any(issue["evidence"]["distribution"] == "devonly" for issue in issues_for(included, "dev-dependency-used-in-runtime"))
    assert any(issue["evidence"]["distribution"] == "orjson" for issue in issues_for(included, "optional-dependency-used-in-runtime"))


def test_guarded_optional_import_does_not_count_as_runtime_violation(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [project.optional-dependencies]
        speedups = ["orjson"]

        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        """,
    )
    write(
        tmp_path / "src/app.py",
        """
        try:
            import orjson
        except ImportError:
            orjson = None

        def main():
            return orjson
        """,
    )

    result = analyze_fixture(tmp_path)
    assert not issues_for(result, "optional-dependency-used-in-runtime")
    assert not issues_for(result, "missing-runtime-dependency")


def test_tuple_import_error_guard_marks_imports_guarded(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [project.optional-dependencies]
        speedups = ["orjson"]

        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        """,
    )
    write(
        tmp_path / "src/app.py",
        """
        try:
            import orjson
        except (ImportError, ModuleNotFoundError):
            orjson = None

        def main():
            return orjson
        """,
    )

    indexed = index_file(tmp_path / "src/app.py", tmp_path, "app", "src", False)
    imports = [record for record in indexed.imports if record.raw_module == "orjson"]
    assert imports and all(record.guarded for record in imports)

    result = analyze_fixture(tmp_path)
    assert not issues_for(result, "optional-dependency-used-in-runtime")
    assert not issues_for(result, "missing-runtime-dependency")


def test_namespace_protocol_dunder_and_init_export_knobs_are_observable(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        namespace_packages = false

        [tool.fallow_py.dead_code]
        ignore_protocol_methods = false
        ignore_dunder_methods = false
        confidence_for_init_exports = "medium"
        """,
    )
    write(
        tmp_path / "src/app.py",
        """
        import pkg

        def main():
            return pkg
        """,
    )
    write(tmp_path / "src/ns/mod.py", "VALUE = 1\n")
    write(tmp_path / "src/pkg/__init__.py", "from .model import Public\n__all__ = ['Public']\n")
    write(
        tmp_path / "src/pkg/model.py",
        """
        from typing import Protocol

        class Public:
            pass

        class Service(Protocol):
            pass

        __magic__ = 1
        """,
    )

    result = analyze_fixture(tmp_path)
    modules = {node["id"] for node in result["graphs"]["modules"]}
    assert "ns.mod" not in modules
    unused = {(issue["module"], issue.get("symbol")) for issue in issues_for(result, "unused-symbol")}
    assert ("pkg.model", "Public") not in unused
    assert ("pkg.model", "Service") in unused
    assert ("pkg.model", "__magic__") in unused
    public = next(
        symbol
        for node in result["graphs"]["modules"]
        if node["id"] == "pkg.model"
        for symbol in node["symbols"]
        if symbol["name"] == "Public"
    )
    assert public["state"]["public_api_confidence"] == "medium"


def test_cli_debug_and_show_limitations_flags_are_observable(tmp_path: Path) -> None:
    write(tmp_path / "app.py", "def main():\n    return 1\n")

    debug_run = run_cli(["analyze", "--root", str(tmp_path), "--changed-only", "--debug", "--format", "json"])
    assert debug_run.returncode == 0
    assert "fallow-py DEBUG: analysis warning:" in debug_run.stderr
    assert "changed-only-not-available-non-git" in debug_run.stderr
    assert "--changed-only is deprecated" not in debug_run.stderr

    limitations_run = run_cli(["analyze", "--root", str(tmp_path), "--format", "text", "--show-limitations"])
    assert limitations_run.returncode == 0
    assert "Limitations:" in limitations_run.stdout
    assert "Dynamic imports" in limitations_run.stdout


def test_cli_explain_rule_by_id_slug_and_all() -> None:
    json_run = run_cli(["explain", "PY031", "--format", "json"])
    assert json_run.returncode == 0, json_run.stdout + json_run.stderr
    payload = json.loads(json_run.stdout)
    assert payload["rule"] == "unused-symbol"
    assert payload["id"] == "PY031"
    assert payload["why_it_matters"]
    assert payload["false_positive_notes"]
    assert payload["agent_action"]
    assert "agent-fix-plan" in payload["action_policy"]

    text_run = run_cli(["explain", "unused-symbol"])
    assert text_run.returncode == 0, text_run.stdout + text_run.stderr
    assert "PY031 unused-symbol" in text_run.stdout
    assert "Common false-positive surfaces:" in text_run.stdout

    all_run = run_cli(["explain", "--all", "--format", "markdown"])
    assert all_run.returncode == 0, all_run.stdout + all_run.stderr
    assert "# fallow-py Rules" in all_run.stdout
    assert "## PY000 parse-error" in all_run.stdout
    assert "## PY090 risky-hotspot" in all_run.stdout

    missing_run = run_cli(["explain", "PY999"])
    assert missing_run.returncode == 2
    assert "unknown fallow-py rule" in missing_run.stderr


def test_rule_explanations_cover_rules_and_docs_do_not_drift() -> None:
    assert set(RULE_GUIDANCE) == set(RULES)
    rendered = render_explanation(explain_all_rules(), "markdown")
    assert (ROOT / "docs/rules.md").read_text(encoding="utf-8") == rendered


def test_nested_function_complexity_does_not_inflate_parent(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]

        [tool.fallow_py.health]
        max_cyclomatic = 3
        max_cognitive = 4
        """,
    )
    write(
        tmp_path / "src/app.py",
        """
        def main():
            def nested(value):
                if value:
                    for item in range(value):
                        if item % 2:
                            return item
                return 0
            return nested(3)
        """,
    )

    result = analyze_fixture(tmp_path)
    complexity = [
        issue
        for issue in result["issues"]
        if issue["rule"] in {"high-cyclomatic-complexity", "high-cognitive-complexity"}
    ]
    assert not any(issue["symbol"] == "main" for issue in complexity)
    assert any(issue["symbol"] == "nested" for issue in complexity)


def test_config_validation_emits_config_error(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]

        [tool.fallow_py.dupes]
        mode = "wild"
        min_tokens = 0

        [tool.fallow_py.health]
        max_cognitive = 0

        [[tool.fallow_py.boundaries.rules]]
        name = "bad"
        from = []
        disallow = []
        severity = "fatal"
        """,
    )
    write(tmp_path / "src/app.py", "VALUE = 1\n")

    result = analyze_fixture(tmp_path)
    config_errors = issues_for(result, "config-error")
    assert len(config_errors) >= 4
    assert result["summary"]["config_errors"] == len(config_errors)
    config = load_config(tmp_path)
    assert config.dupes.min_tokens == 40
    assert config.dupes.mode == "mild"
    assert config.health.max_cognitive == 15


@pytest.mark.parametrize(
    ("config_text", "field"),
    [
        (
            """
            [tool.fallow_py]
            roots = "src"
            """,
            "roots",
        ),
        (
            """
            [tool.fallow_py]
            entry = [123]
            """,
            "entry",
        ),
        (
            """
            [tool.fallow_py]
            include_tests = "yes"
            """,
            "include_tests",
        ),
        (
            """
            [tool.fallow_py.dupes]
            min_tokens = "40"
            """,
            "dupes.min_tokens",
        ),
        (
            """
            [tool.fallow_py.dependencies]
            include_optional = "true"
            """,
            "dependencies.include_optional",
        ),
        (
            """
            [tool.fallow_py.dependencies.import_map]
            PIL = 123
            """,
            "dependencies.import_map.PIL",
        ),
    ],
)
def test_config_type_validation_rejects_malformed_toml_values(
    tmp_path: Path, config_text: str, field: str
) -> None:
    write(tmp_path / "pyproject.toml", config_text)

    with pytest.raises(ConfigError, match=field):
        load_config(tmp_path)


def test_canonical_config_names_and_legacy_fallbacks(tmp_path: Path) -> None:
    write(
        tmp_path / ".fallow-py.toml",
        """
        roots = ["pkg"]
        entry = ["pkg/app.py"]
        include_tests = true
        """,
    )
    config = load_config(tmp_path)
    assert config.config_path == tmp_path / ".fallow-py.toml"
    assert config.roots == ["pkg"]
    assert config.entry == ["pkg/app.py"]
    assert config.include_tests is True

    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/main.py"]
        """,
    )
    (tmp_path / ".fallow-py.toml").unlink()
    config = load_config(tmp_path)
    assert config.roots == ["src"]
    assert config.entry == ["src/main.py"]

    write(
        tmp_path / ".pyfallow.toml",
        """
        roots = ["legacy"]
        entry = ["legacy/main.py"]
        """,
    )
    (tmp_path / "pyproject.toml").unlink()
    with pytest.warns(DeprecationWarning, match=".pyfallow.toml"):
        legacy_config = load_config(tmp_path)
    assert legacy_config.roots == ["legacy"]

    write(
        tmp_path / "pyproject.toml",
        """
        [tool.pyfallow]
        roots = ["legacy_tool"]
        """,
    )
    (tmp_path / ".pyfallow.toml").unlink()
    with pytest.warns(DeprecationWarning, match=r"\[tool\.pyfallow\]"):
        legacy_tool_config = load_config(tmp_path)
    assert legacy_tool_config.roots == ["legacy_tool"]


def test_sarif_has_fingerprints_properties_and_related_locations(tmp_path: Path, monkeypatch) -> None:
    root = make_fixture_project(tmp_path)
    result = analyze_fixture(root)
    monkeypatch.chdir(root)
    sarif = to_sarif(result)
    sarif_results = sarif["runs"][0]["results"]
    rules = sarif["runs"][0]["tool"]["driver"]["rules"]

    assert all("primaryLocationLineHash" in item["partialFingerprints"] for item in sarif_results)
    assert all("precision" in item["properties"] and "problem.severity" in item["properties"] for item in rules)
    assert any(item["properties"]["problem.severity"] == "recommendation" for item in rules)
    assert sarif["runs"][0]["automationDetails"]["id"] == "pyfallow/python/"
    assert all("endLine" in item["locations"][0]["physicalLocation"]["region"] for item in sarif_results)
    cycle = next(item for item in sarif_results if item["ruleId"] == "PY020")
    assert cycle["relatedLocations"]
    duplicate = next(item for item in sarif_results if item["ruleId"] == "PY050")
    assert duplicate["relatedLocations"]
    capped = to_sarif(result, max_related_locations=1)
    assert all(len(item.get("relatedLocations", [])) <= 1 for item in capped["runs"][0]["results"])
    missing = next(item for item in result["issues"] if item["rule"] == "missing-runtime-dependency")
    expected_line = (root / missing["path"]).read_text(encoding="utf-8").splitlines()[missing["range"]["start"]["line"] - 1]
    expected_hash = hashlib.sha1(" ".join(expected_line.strip().split()).encode("utf-8")).hexdigest()
    sarif_missing = next(item for item in sarif_results if item["partialFingerprints"]["pyfallowFingerprint"] == missing["fingerprint"])
    assert sarif_missing["partialFingerprints"]["primaryLocationLineHash"] == expected_hash


def test_sarif_schema_and_golden_fixture_contract() -> None:
    result = analyze_fixture(ROOT / "tests/fixtures/demo_project")
    sarif = to_sarif(result)
    schema = json.loads((ROOT / "schemas/pyfallow-sarif.schema.json").read_text(encoding="utf-8"))
    validate_schema(schema, sarif)
    subset = {
        "version": sarif["version"],
        "automation_id": sarif["runs"][0]["automationDetails"]["id"],
        "rule_ids": sorted(rule["id"] for rule in sarif["runs"][0]["tool"]["driver"]["rules"]),
        "result_rule_ids": sorted({item["ruleId"] for item in sarif["runs"][0]["results"]}),
        "has_related": any(
            item.get("relatedLocations")
            for item in sarif["runs"][0]["results"]
            if item["ruleId"] in {"PY020", "PY050"}
        ),
    }
    golden = json.loads((ROOT / "tests/golden/demo_project_sarif_golden.json").read_text(encoding="utf-8"))
    assert subset == golden


def test_baseline_helpers_classify_existing_new_and_resolved(tmp_path: Path) -> None:
    root = make_fixture_project(tmp_path)
    result = analyze_fixture(root)
    baseline = create_baseline(result)
    comparison = compare_with_baseline([], baseline)
    assert comparison["resolved_count"] == len(baseline["issues"])

    comparison = compare_with_baseline(
        [type("IssueLike", (), {"fingerprint": result["issues"][0]["fingerprint"]})()],
        baseline,
    )
    assert comparison["existing_count"] == 1
    assert comparison["resolved_count"] == len(baseline["issues"]) - 1


def test_json_schema_and_golden_fixture_contract() -> None:
    schema = json.loads((ROOT / "schemas/pyfallow-report.schema.json").read_text(encoding="utf-8"))
    result = analyze_fixture(ROOT / "tests/fixtures/demo_project")

    validate_schema(schema, result)
    assert set(schema["required"]) <= set(result)
    assert set(schema["properties"]["analysis"]["required"]) <= set(result["analysis"])
    assert set(schema["properties"]["summary"]["required"]) <= set(result["summary"])
    assert set(schema["properties"]["graphs"]["required"]) <= set(result["graphs"])
    assert all(issue["severity"] in {"info", "warning", "error"} for issue in result["issues"])
    assert all(issue["confidence"] in {"low", "medium", "high"} for issue in result["issues"])

    actual = {
        "summary": result["summary"],
        "rules": sorted({issue["rule"] for issue in result["issues"]}),
        "edges": [[edge["from"], edge["to"]] for edge in result["graphs"]["edges"]],
    }
    golden = json.loads((ROOT / "tests/golden/demo_project_report_golden.json").read_text(encoding="utf-8"))
    assert actual == golden


def test_agent_integration_examples_are_packaged() -> None:
    skill_root = ROOT / "examples/claude-skill/fallow-py-cleanup"
    cursor_rule = ROOT / "examples/cursor-rules/fallow-py.mdc"
    agent_doc = ROOT / "docs/agent-integration.md"

    for path in [
        skill_root / "SKILL.md",
        skill_root / "workflow.md",
        skill_root / "README.md",
        cursor_rule,
        agent_doc,
    ]:
        assert path.exists(), path

    skill_text = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    workflow_text = (skill_root / "workflow.md").read_text(encoding="utf-8")
    cursor_text = cursor_rule.read_text(encoding="utf-8")

    assert "pyfallow.analyze_diff" in skill_text
    assert "fallow-py analyze" in skill_text
    assert "blocking" in skill_text
    assert "DO NOT mark the task complete" in workflow_text
    assert "missing_dependencies" in workflow_text
    assert "alwaysApply: true" in cursor_text
    assert "fallow-py analyze" in cursor_text

    archives = {
        ROOT / "examples/claude-skill/claude-skill-pyfallow-cleanup-v0.3.0.zip": {
            "pyfallow-cleanup/SKILL.md",
            "pyfallow-cleanup/workflow.md",
            "pyfallow-cleanup/README.md",
        },
        ROOT / "examples/cursor-rules/cursor-rules-pyfallow-v0.3.0.zip": {
            "pyfallow.mdc",
        },
    }
    for archive, expected in archives.items():
        assert archive.exists(), archive
        with zipfile.ZipFile(archive) as bundle:
            assert expected <= set(bundle.namelist())


def test_ci_templates_are_packaged_and_platform_neutral() -> None:
    ci_root = ROOT / "examples/ci"
    templates = {
        "forgejo": ci_root / "forgejo-actions.yml",
        "github": ci_root / "github-actions.yml",
        "gitlab": ci_root / "gitlab-ci.yml",
    }
    readme = (ci_root / "README.md").read_text(encoding="utf-8")

    assert readme.index("## Forgejo Actions") < readme.index("## GitHub Actions")
    assert readme.index("## GitHub Actions") < readme.index("## GitLab CI")
    assert "complementary to ruff, mypy, vulture, CodeQL" in readme

    for path in templates.values():
        text = path.read_text(encoding="utf-8")
        assert "--format agent-fix-plan" in text
        assert "--since" in text
        assert "--fail-on warning" in text
        assert "--min-confidence medium" in text
        assert "render_pyfallow_comment.py" in text
        assert "pyfallow-report.json" in text

    forgejo_text = templates["forgejo"].read_text(encoding="utf-8")
    # ADR 0011: Forgejo template uses Forgejo-native action URLs +
    # explicit ubuntu-22.04 pin (parity with the shared Forgejo-native
    # convention). ADR 0003's `runs-on: ubuntu-latest` was correct
    # conceptually for unblocking Phase A but partially superseded.
    assert "runs-on: ubuntu-22.04" in forgejo_text
    assert "container:" not in forgejo_text
    assert "https://data.forgejo.org/actions/setup-python@v5" in forgejo_text
    assert "https://data.forgejo.org/actions/checkout@v4" in forgejo_text
    # GitHub template still uses canonical `ubuntu-latest` + `actions/...`
    # URLs since it runs on github.com infrastructure, no rate-limit concern.
    assert "runs-on: ubuntu-latest" in templates["github"].read_text(encoding="utf-8")
    assert "image: python:3.12" in templates["gitlab"].read_text(encoding="utf-8")
    assert "reports:" not in templates["gitlab"].read_text(encoding="utf-8")

    archive = ci_root / "ci-templates-v0.3.0.zip"
    assert archive.exists(), archive
    with zipfile.ZipFile(archive) as bundle:
        assert {
            "README.md",
            "forgejo-actions.yml",
            "github-actions.yml",
            "gitlab-ci.yml",
            "render_pyfallow_comment.py",
        } <= set(bundle.namelist())


def test_ci_comment_renderer_groups_agent_fix_plan(tmp_path: Path) -> None:
    report = tmp_path / "pyfallow-report.json"
    report.write_text(
        json.dumps(
            {
                "blocking": [
                    {
                        "path": "src/orders.py",
                        "range": {"start": {"line": 12}},
                        "rule": "missing-runtime-dependency",
                        "confidence": "high",
                        "message": "Imported third-party package is not declared.",
                    }
                ],
                "review_needed": [
                    {
                        "path": "src/billing.py",
                        "range": {"start": {"line": 88}},
                        "rule": "unused-symbol",
                        "symbol": "format_amount",
                        "confidence": "medium",
                        "message": "Function defined but not referenced.",
                    }
                ],
                "auto_safe": [],
                "manual_only": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(ROOT / "examples/ci/render_pyfallow_comment.py"), str(report)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=TIMEOUT,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "## fallow-py analysis" in result.stdout
    assert "**2 findings on this change**" in result.stdout
    assert "### Blocking (1)" in result.stdout
    assert "`src/orders.py:12` - `missing-runtime-dependency` (high)" in result.stdout
    assert "### Review needed (1)" in result.stdout
    assert "`src/billing.py:88` - `unused-symbol` `format_amount` (medium)" in result.stdout


def minimal_issue(
    rule: str,
    confidence: str = "high",
    severity: str | None = None,
    evidence: dict | None = None,
) -> dict:
    return {
        "id": RULES[rule]["id"],
        "rule": rule,
        "category": RULES[rule]["category"],
        "severity": severity or RULES[rule]["default_severity"],
        "confidence": confidence,
        "path": "src/example.py",
        "range": {"start": {"line": 7, "column": 1}, "end": {"line": 7, "column": 1}},
        "symbol": "example_symbol" if rule == "unused-symbol" else None,
        "module": "example",
        "message": f"{rule} message",
        "evidence": evidence or {},
        "actions": [],
        "fingerprint": f"fp-{rule}-{confidence}",
    }


def test_agent_fix_plan_classifies_every_rule_deterministically() -> None:
    expected = {
        "parse-error": "blocking",
        "config-error": "blocking",
        "unresolved-import": "blocking",
        "dynamic-import": "review_needed",
        "production-imports-test-code": "review_needed",
        "circular-dependency": "blocking",
        "unused-module": "review_needed",
        "unused-symbol": "auto_safe",
        "stale-suppression": "auto_safe",
        "missing-runtime-dependency": "blocking",
        "missing-type-dependency": "review_needed",
        "missing-test-dependency": "review_needed",
        "dev-dependency-used-in-runtime": "blocking",
        "optional-dependency-used-in-runtime": "review_needed",
        "runtime-dependency-used-only-in-tests": "review_needed",
        "runtime-dependency-used-only-for-types": "review_needed",
        "unused-runtime-dependency": "review_needed",
        "duplicate-code": "review_needed",
        "high-cyclomatic-complexity": "review_needed",
        "high-cognitive-complexity": "review_needed",
        "large-function": "review_needed",
        "large-file": "review_needed",
        "boundary-violation": "review_needed",
        "framework-entrypoint-detected": "manual_only",
        "risky-hotspot": "review_needed",
    }

    assert set(expected) == set(RULES)
    for rule, decision in expected.items():
        assert classify_finding(minimal_issue(rule)).decision == decision

    assert classify_finding(minimal_issue("missing-runtime-dependency", "medium")).decision == "blocking"
    assert classify_finding(minimal_issue("unused-symbol", "medium")).decision == "review_needed"
    assert classify_finding(minimal_issue("unused-symbol", "low")).decision == "manual_only"
    assert classify_finding(minimal_issue("unused-module", "low")).decision == "manual_only"
    assert (
        classify_finding(
            minimal_issue("circular-dependency", evidence={"type_checking_imports_contributed": True})
        ).decision
        == "review_needed"
    )
    assert classify_finding(minimal_issue("boundary-violation", severity="error")).decision == "blocking"


def test_unused_symbol_framework_managed_is_review_not_auto() -> None:
    issue = minimal_issue(
        "unused-symbol",
        "high",
        evidence={"state": {"framework_managed": True, "entrypoint_managed": False}},
    )

    result = classify_finding(issue)

    assert result.decision == "review_needed"
    assert "framework" in result.rationale


def test_stale_suppression_minimal_patch_applies_cleanly(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        """,
    )
    write(
        tmp_path / "src/app.py",
        """
        # fallow: ignore[unused-symbol]

        def main():
            return 1
        """,
    )

    result = analyze_fixture(tmp_path)
    plan = agent_fix_plan(result)
    stale = plan["auto_safe"][0]
    patch = stale["minimal_patch"]

    assert stale["rule"] == "stale-suppression"
    assert patch["type"] == "delete_line"
    path = tmp_path / patch["file"]
    lines = path.read_text(encoding="utf-8").splitlines()
    del lines[patch["line"] - 1]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert "fallow: ignore" not in path.read_text(encoding="utf-8")


def test_agent_fix_plan_format_matches_schema_and_golden(tmp_path: Path) -> None:
    result = analyze_fixture(ROOT / "tests/fixtures/demo_project")
    plan = agent_fix_plan(result)
    schema = json.loads((ROOT / "schemas/pyfallow-fix-plan.schema.json").read_text(encoding="utf-8"))
    validate_schema(schema, plan)

    assert plan["schema_version"] == "1.0"
    assert plan["summary"]["blocking_count"] >= 1
    missing = next(item for item in plan["blocking"] if item["rule"] == "missing-runtime-dependency")
    assert [option["type"] for option in missing["fix_options"]] == [
        "declare",
        "remove_import",
        "guard",
        "rename",
    ]

    golden = json.loads((ROOT / "tests/golden/demo_project_fix_plan_golden.json").read_text(encoding="utf-8"))
    actual = {
        "summary": plan["summary"],
        "blocking_rules": sorted({item["rule"] for item in plan["blocking"]}),
        "review_needed_rules": sorted({item["rule"] for item in plan["review_needed"]}),
        "auto_safe_rules": sorted({item["rule"] for item in plan["auto_safe"]}),
    }
    assert actual == golden


def test_agent_fix_plan_cli_with_since_includes_diff_scope(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        """,
    )
    write(tmp_path / "src/app.py", "def main():\n    return 1\n")
    init_git_repo(tmp_path)
    commit_all(tmp_path, "initial")
    write(tmp_path / "src/changed.py", "def changed_unused():\n    return 2\n")

    result = run_cli(
        ["analyze", "--root", str(tmp_path), "--since", "HEAD", "--format", "agent-fix-plan"]
    )

    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads(result.stdout)
    assert plan["diff_scope"]["changed_files"] == ["src/changed.py"]
    assert plan["summary"]["review_needed_count"] == 2
    assert {item["rule"] for item in plan["review_needed"]} == {"unused-module", "unused-symbol"}

    default_result = run_cli(
        ["--root", str(tmp_path), "--since", "HEAD", "--format", "agent-fix-plan"]
    )
    assert default_result.returncode == 0, default_result.stdout + default_result.stderr
    assert json.loads(default_result.stdout)["diff_scope"]["changed_files"] == ["src/changed.py"]


def make_predict_project(tmp_path: Path) -> Path:
    write(
        tmp_path / "pyproject.toml",
        """
        [project]
        dependencies = []

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
    write(tmp_path / "src/app.py", "def main():\n    return 1\n")
    write(tmp_path / "src/billing.py", "def compute_total_with_refunds():\n    return 1\n")
    write(tmp_path / "src/cart.py", "def cart_total():\n    return 1\n")
    write(tmp_path / "src/checkout.py", "import cart\n\ndef checkout():\n    return cart.cart_total()\n")
    write(tmp_path / "src/domain/service.py", "def service():\n    return 'ok'\n")
    write(tmp_path / "src/infra/db.py", "def connect():\n    return 'db'\n")
    write(
        tmp_path / "src/pkg/__init__.py",
        """
        from .public import PublicThing
        __all__ = ["PublicThing"]
        """,
    )
    write(tmp_path / "src/pkg/public.py", "class PublicThing:\n    pass\n")
    return tmp_path


def test_parse_import_spec_supports_agent_import_forms() -> None:
    specs = {
        "os": ("os", None),
        "requests as http": ("requests", "http"),
        "billing.compute_refund": ("billing.compute_refund", None),
        "billing.compute_refund as refund": ("billing.compute_refund", "refund"),
        ".sibling.PublicThing": (".sibling.PublicThing", None),
    }

    for raw, expected in specs.items():
        parsed = parse_import_spec(raw)
        assert (parsed.name, parsed.alias) == expected


def test_verify_imports_detects_hallucinated_module_and_symbol(tmp_path: Path) -> None:
    root = make_predict_project(tmp_path)
    config = load_config(root)

    result = verify_imports(
        config,
        Path("src/app.py"),
        ["nonexistent_module", "billing.compute_refund"],
    )

    reasons = {item.raw: item.reason for item in result.hallucinated}
    assert "module 'nonexistent_module' not found" in reasons["nonexistent_module"]
    assert reasons["billing.compute_refund"] == "module 'billing' has no symbol 'compute_refund'"
    symbol = next(item for item in result.hallucinated if item.raw == "billing.compute_refund")
    assert "compute_total_with_refunds" in symbol.similar


def test_verify_imports_predicts_cycles_boundaries_and_missing_dependencies(tmp_path: Path) -> None:
    root = make_predict_project(tmp_path)
    config = load_config(root)

    result = verify_imports(
        config,
        Path("src/cart.py"),
        ["checkout", "requests", "os"],
    )

    assert result.status == "issues_found"
    assert result.cycles_introduced[0].cycle_path == ["cart", "checkout", "cart"]
    assert result.missing_dependencies[0].distribution == "requests"
    assert {item.raw for item in result.safe} == {"os"}

    boundary = verify_imports(config, Path("src/domain/service.py"), ["infra.db"])
    assert boundary.boundary_violations
    assert boundary.boundary_violations[0].rule == "domain-no-infra"


def test_verify_imports_handles_reexports_relative_imports_and_new_files(tmp_path: Path) -> None:
    root = make_predict_project(tmp_path)
    config = load_config(root)

    result = verify_imports(
        config,
        Path("src/pkg/new_feature.py"),
        [".public.PublicThing", "pkg.PublicThing"],
    )

    assert result.status == "ok"
    assert {item.raw for item in result.safe} == {".public.PublicThing", "pkg.PublicThing"}
    assert not result.hallucinated


def test_verify_imports_rejects_target_file_outside_root(tmp_path: Path) -> None:
    root = make_predict_project(tmp_path)
    config = load_config(root)

    try:
        verify_imports(config, Path("../outside.py"), ["os"])
    except ValueError as exc:
        assert "outside analysis root" in str(exc)
    else:
        raise AssertionError("verify_imports accepted a target outside the analysis root")


def test_soak_matrix_is_reproducible_and_pinned() -> None:
    repos = tomllib.loads((ROOT / "benchmarks/soak/repos.toml").read_text(encoding="utf-8"))[
        "repos"
    ]
    models = tomllib.loads((ROOT / "benchmarks/soak/models.toml").read_text(encoding="utf-8"))[
        "models"
    ]

    assert len(repos) == 10
    assert len(models) == 5
    assert {repo["name"] for repo in repos} >= {"requests", "fastapi", "autogpt"}
    assert {model["name"] for model in models} == {
        "glm-5-1",
        "claude-opus",
        "gpt-5",
        "qwen-35b",
        "qwen-9b",
    }
    for repo in repos:
        assert repo["url"].startswith("https://github.com/")
        assert len(repo["commit"]) == 40
        assert all(character in "0123456789abcdef" for character in repo["commit"])
        assert repo["since"] == "HEAD~5"
    glm = next(model for model in models if model["name"] == "glm-5-1")
    assert glm["model"] == "zai-coding/glm-5.1"
    assert glm["requires_env"] == "Z_AI_API_KEY"
    assert glm["base_url"] == "https://api.z.ai/api/coding/paas/v4"

    result = subprocess.run(
        [sys.executable, "benchmarks/soak/run.py", "--list"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=TIMEOUT,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert summary["planned_runs"] == 50
    assert len(summary["repos"]) == 10
    assert len(summary["models"]) == 5


def test_soak_dry_run_writes_plan_without_cloning(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    results = tmp_path / "results"

    result = subprocess.run(
        [
            sys.executable,
            "benchmarks/soak/run.py",
            "--repo",
            "requests",
            "--model",
            "qwen-9b",
            "--dry-run",
            "--workspace",
            str(workspace),
            "--results",
            str(results),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=TIMEOUT,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    result_dir = results / "requests/qwen-9b"
    plan = json.loads((result_dir / "plan.json").read_text(encoding="utf-8"))
    assert plan["repo"]["name"] == "requests"
    assert plan["model"]["name"] == "qwen-9b"
    assert plan["commands"]["pyfallow"][1:4] == ["-m", "fallow_py", "analyze"]
    assert "--since" in plan["commands"]["pyfallow"]
    assert "agent-fix-plan" in plan["commands"]["pyfallow"]
    assert plan["commands"]["opencode"][:4] == ["opencode", "--log-level", "WARN", "--pure"]
    assert "run" in plan["commands"]["opencode"]
    assert "--dir" in plan["commands"]["opencode"]
    assert "--format" in plan["commands"]["opencode"]
    assert plan["guardrails"]
    assert "Do not open pull requests" in "\n".join(plan["guardrails"])
    assert "candidate generator" in (result_dir / "prompt.md").read_text(encoding="utf-8")
    assert (result_dir / "human_classification.md").exists()
    assert not (workspace / "requests").exists()


def test_soak_glm_plan_uses_coding_endpoint_and_sterile_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    results = tmp_path / "results"

    result = subprocess.run(
        [
            sys.executable,
            "benchmarks/soak/run.py",
            "--repo",
            "requests",
            "--model",
            "glm-5-1",
            "--dry-run",
            "--workspace",
            str(workspace),
            "--results",
            str(results),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=TIMEOUT,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    result_dir = results / "requests/glm-5-1"
    plan = json.loads((result_dir / "plan.json").read_text(encoding="utf-8"))
    assert plan["model"]["model"] == "zai-coding/glm-5.1"
    assert plan["model"]["requires_env"] == "Z_AI_API_KEY"
    assert plan["paths"]["opencode_home"].endswith("opencode-home")
    assert plan["paths"]["opencode_events"].endswith("opencode-events.jsonl")
    assert "api/coding/paas/v4" in plan["model"]["base_url"]
    assert "Do not touch CI, packaging" in "\n".join(plan["guardrails"])


def test_soak_prompt_summarizes_fallow_py_evidence_without_full_context(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("soak_run", ROOT / "benchmarks/soak/run.py")
    assert spec and spec.loader
    soak_run = importlib.util.module_from_spec(spec)
    sys.modules["soak_run"] = soak_run
    spec.loader.exec_module(soak_run)

    findings = {
        "summary": {"blocking_count": 1, "review_needed_count": 1},
        "blocking": [
            {
                "fingerprint": "abc",
                "rule": "missing-runtime-dependency",
                "id": "PY040",
                "file": "src/app.py",
                "line": 3,
                "message": "Runtime import uses missing dependency.",
                "evidence": {"large": "not copied into prompt"},
            }
        ],
        "review_needed": [
            {
                "fingerprint": "def",
                "rule": "unused-symbol",
                "file": "src/util.py",
                "symbol": "helper",
                "confidence": "medium",
                "message": "Potential unused helper.",
            }
        ],
        "auto_safe": [],
        "manual_only": [],
        "limitations": ["Dynamic imports are approximate."],
    }
    path = tmp_path / "findings.json"
    path.write_text(json.dumps(findings), encoding="utf-8")

    summary = soak_run.summarize_agent_fix_plan(path)

    assert summary["available"] is True
    assert summary["total_findings"] == 2
    assert summary["findings"][0]["bucket"] == "blocking"
    assert summary["findings"][0]["rule"] == "missing-runtime-dependency"
    assert "evidence" not in summary["findings"][0]
    assert summary["limitations"] == ["Dynamic imports are approximate."]

    config = soak_run.safe_opencode_config(
        {
            "model": "zai-coding/glm-5.1",
            "base_url": "https://api.z.ai/api/coding/paas/v4",
            "output_limit": 2048,
        }
    )
    assert config["share"] == "disabled"
    assert config["mcp"] == {}
    assert config["permission"]["bash"] == "deny"
    assert config["permission"]["webfetch"] == "deny"
    assert config["permission"]["external_directory"] == "deny"
    assert config["provider"]["zai-coding"]["options"]["baseURL"].endswith("/api/coding/paas/v4")


def test_comparison_benchmark_matrix_and_docs_are_complementary() -> None:
    repos = tomllib.loads((ROOT / "benchmarks/comparison/repos.toml").read_text(encoding="utf-8"))[
        "repos"
    ]
    tools = tomllib.loads((ROOT / "benchmarks/comparison/tools.toml").read_text(encoding="utf-8"))[
        "tools"
    ]
    soak_repos = tomllib.loads((ROOT / "benchmarks/soak/repos.toml").read_text(encoding="utf-8"))[
        "repos"
    ]

    assert [repo["name"] for repo in repos] == ["requests", "fastapi", "flask", "pydantic", "httpx"]
    assert {repo["name"] for repo in repos} <= {repo["name"] for repo in soak_repos}
    assert {tool["name"] for tool in tools} == {"ruff", "vulture", "deptry", "pyfallow"}
    assert any(tool["package"] == "ruff==0.15.12" for tool in tools)
    assert any(tool["package"] == "vulture==2.16" for tool in tools)
    assert any(tool["package"] == "deptry==0.25.1" for tool in tools)

    result = subprocess.run(
        [sys.executable, "benchmarks/comparison/run.py", "--list"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=TIMEOUT,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert summary["planned_runs"] == 20
    assert summary["timed_runs_per_pair"] == 5

    performance = (ROOT / "docs/performance.md").read_text(encoding="utf-8")
    assert "not a ranking" in performance
    assert "ruff 0.15.12" in performance
    assert "vulture 2.16" in performance
    assert "deptry 0.25.1" in performance
    assert "Add fallow-py when" in performance
    assert "| requests | 0.021s | 0.179s | 0.121s | 0.245s |" in performance
    assert "**Use alongside:** ruff" in performance


def test_comparison_benchmark_dry_run_writes_plan_without_cloning(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    results = tmp_path / "results"
    venvs = tmp_path / "venvs"

    result = subprocess.run(
        [
            sys.executable,
            "benchmarks/comparison/run.py",
            "--repo",
            "requests",
            "--tool",
            "pyfallow",
            "--dry-run",
            "--workspace",
            str(workspace),
            "--results",
            str(results),
            "--venvs",
            str(venvs),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=TIMEOUT,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads((results / "requests/pyfallow.plan.json").read_text(encoding="utf-8"))
    assert plan["repo"]["name"] == "requests"
    assert plan["tool"]["name"] == "pyfallow"
    assert plan["command"][1:4] == ["-m", "fallow_py", "analyze"]
    assert "--format" in plan["command"]
    assert "json" in plan["command"]
    assert not (workspace / "requests").exists()


def test_analysis_profile_benchmark_lists_cases_and_phases() -> None:
    result = subprocess.run(
        [sys.executable, "benchmarks/analysis-profile/run.py", "--list"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=TIMEOUT,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert {case["name"] for case in summary["cases"]} == {"demo-project", "generated"}
    assert "file_indexing" in summary["phases"]
    assert "format_serialization" in summary["phases"]


def test_analysis_profile_benchmark_profiles_generated_fixture(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    output = tmp_path / "profile.json"

    result = subprocess.run(
        [
            sys.executable,
            "benchmarks/analysis-profile/run.py",
            "--case",
            "generated",
            "--generated-modules",
            "12",
            "--runs",
            "1",
            "--workspace",
            str(workspace),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=TIMEOUT,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(output.read_text(encoding="utf-8"))
    case = payload["cases"][0]
    assert case["case"] == "generated"
    assert case["repo_metrics"]["files_analyzed"] == 14
    assert case["median_total_seconds"] > 0
    phases = {row["phase"] for row in case["median_phases"]}
    assert {
        "source_discovery",
        "file_indexing",
        "module_resolution",
        "dependency_analysis",
        "graph_analysis",
        "duplicate_detection",
        "format_serialization",
    } <= phases

    readme = (ROOT / "benchmarks/analysis-profile/README.md").read_text(encoding="utf-8")
    performance = (ROOT / "docs/performance.md").read_text(encoding="utf-8")
    assert "Do not implement parallel AST indexing yet" in readme
    assert "Analyzer Internals Profile" in performance
    assert "do not add parallel AST indexing on this evidence alone" in performance
