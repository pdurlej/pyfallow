from __future__ import annotations

import textwrap
from pathlib import Path

from fallow_py.analysis import analyze
from fallow_py.config import load_config
from fallow_py.fingerprints import issue_fingerprint
from fallow_py.models import Issue


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).strip() + "\n", encoding="utf-8")


def test_cycle_fingerprint_is_stable_across_rotated_cycle_paths() -> None:
    first = Issue(
        rule="circular-dependency",
        severity="warning",
        confidence="high",
        path="src/a.py",
        module="pkg.a",
        message="Import cycle detected: pkg.a -> pkg.b -> pkg.c -> pkg.a",
        evidence={"cycle_path": ["pkg.a", "pkg.b", "pkg.c", "pkg.a"]},
    )
    rotated = Issue(
        rule="circular-dependency",
        severity="warning",
        confidence="high",
        path="src/a.py",
        module="pkg.a",
        message="Import cycle detected: pkg.b -> pkg.c -> pkg.a -> pkg.b",
        evidence={"cycle_path": ["pkg.b", "pkg.c", "pkg.a", "pkg.b"]},
    )

    assert issue_fingerprint(first) == issue_fingerprint(rotated)


def test_analysis_fingerprints_are_stable_for_cycles_and_dead_code(tmp_path: Path) -> None:
    write(
        tmp_path / "pyproject.toml",
        """
        [tool.fallow_py]
        roots = ["src"]
        entry = ["src/app.py"]
        """,
    )
    write(tmp_path / "src/unused.py", "def orphan():\n    return 1\n")
    write(tmp_path / "src/b.py", "import a\nVALUE = 1\n")
    write(tmp_path / "src/app.py", "import a\n\ndef main():\n    return a.VALUE\n")
    write(tmp_path / "src/a.py", "import b\nVALUE = b.VALUE\n")

    first = _fingerprint_snapshot(tmp_path)
    second = _fingerprint_snapshot(tmp_path)

    assert first == second
    assert any(rule == "circular-dependency" for rule, *_ in first)
    assert any(rule == "unused-module" for rule, *_ in first)


def _fingerprint_snapshot(root: Path) -> list[tuple[str, str | None, str | None, str | None, str]]:
    result = analyze(load_config(root))
    return sorted(
        (
            issue["rule"],
            issue.get("path"),
            issue.get("module"),
            issue.get("symbol"),
            issue["fingerprint"],
        )
        for issue in result["issues"]
        if issue["rule"] in {"circular-dependency", "unused-module", "unused-symbol"}
    )
