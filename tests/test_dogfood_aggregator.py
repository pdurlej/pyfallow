from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/dogfood/aggregate_evidence.py"


def load_aggregator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("aggregate_evidence", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload) + "\n", encoding="utf-8")


def test_aggregates_agent_fix_plan_reports(tmp_path: Path) -> None:
    aggregator = load_aggregator()
    report_dir = tmp_path / "artifacts"
    write_json(
        report_dir / "run-1/pyfallow-report.json",
        {
            "schema_version": "1.0",
            "blocking": [
                {
                    "rule": "missing-runtime-dependency",
                    "severity": "error",
                    "confidence": "high",
                    "fingerprint": "abc",
                    "file": "src/app.py",
                }
            ],
            "review_needed": [
                {
                    "rule": "unused-symbol",
                    "severity": "warning",
                    "confidence": "medium",
                    "fingerprint": "def",
                    "file": "src/legacy.py",
                }
            ],
        },
    )

    findings, reports, warnings = aggregator.collect_reports([f"owner/repo={report_dir}"], default_repo="local/unknown")

    assert warnings == []
    assert reports == [str(report_dir / "run-1/pyfallow-report.json")]
    assert [(finding.repo, finding.group, finding.rule) for finding in findings] == [
        ("owner/repo", "review_needed", "unused-symbol"),
        ("owner/repo", "blocking", "missing-runtime-dependency"),
    ]

    summary = aggregator.EvidenceSummary(
        generated_at="2026-05-16T00:00:00+00:00",
        source_repos=["owner/repo"],
        findings=findings,
        report_paths=reports,
    )

    data = summary.to_json()
    assert data["schema"] == "fallow_py_dogfood_evidence.v1"
    assert data["finding_count"] == 2
    assert data["finding_categories"] == {"blocking": 1, "review_needed": 1}
    assert data["top_rules"] == {"unused-symbol": 1, "missing-runtime-dependency": 1}

    markdown = aggregator.render_markdown(summary)
    assert "# fallow-py dogfood evidence summary" in markdown
    assert "`missing-runtime-dependency`: 1" in markdown


def test_aggregates_plain_json_reports_as_unclassified_without_policy_guessing(tmp_path: Path) -> None:
    aggregator = load_aggregator()
    report_path = tmp_path / "pyfallow-report.json"
    write_json(
        report_path,
        {
            "issues": [
                {
                    "rule": "boundary-violation",
                    "severity": "error",
                    "confidence": "high",
                    "fingerprint": "1",
                    "path": "src/domain/service.py",
                },
                {
                    "rule": "dynamic-import",
                    "severity": "info",
                    "confidence": "low",
                    "fingerprint": "2",
                    "path": "src/plugin.py",
                },
            ]
        },
    )

    findings, reports, warnings = aggregator.collect_reports([str(report_path)], default_repo="owner/repo")

    assert len(warnings) == 1
    assert "counted as unclassified" in warnings[0]
    assert reports == [str(report_path)]
    assert [(finding.group, finding.rule) for finding in findings] == [
        ("unclassified", "boundary-violation"),
        ("unclassified", "dynamic-import"),
    ]


def test_plain_json_reports_can_use_explicit_classification(tmp_path: Path) -> None:
    aggregator = load_aggregator()
    report_path = tmp_path / "pyfallow-report.json"
    write_json(
        report_path,
        {
            "issues": [
                {
                    "rule": "dynamic-import",
                    "severity": "info",
                    "confidence": "low",
                    "classification": "review_needed",
                    "fingerprint": "2",
                    "path": "src/plugin.py",
                },
                {
                    "rule": "unused-symbol",
                    "severity": "warning",
                    "confidence": "medium",
                    "decision": "decision-needed",
                    "fingerprint": "3",
                    "path": "src/legacy.py",
                },
            ]
        },
    )

    findings, reports, warnings = aggregator.collect_reports([str(report_path)], default_repo="owner/repo")

    assert warnings == []
    assert reports == [str(report_path)]
    assert [(finding.group, finding.rule) for finding in findings] == [
        ("review_needed", "dynamic-import"),
        ("decision_needed", "unused-symbol"),
    ]


def test_collect_runs_normalizes_forgejo_payload() -> None:
    aggregator = load_aggregator()

    class FakeClient:
        def list_runs(self, repo: str, *, limit: int, status: str | None, event: str | None):
            assert repo == "owner/repo"
            assert limit == 3
            assert status == "success"
            assert event == "push"
            return [
                {
                    "id": 123,
                    "status": "success",
                    "workflow_id": "ci.yml",
                    "event": "push",
                    "started": "2026-05-16T00:00:00+02:00",
                    "stopped": "2026-05-16T00:01:00+02:00",
                    "html_url": "https://git.example/actions/runs/1",
                }
            ]

    runs = aggregator.collect_runs(FakeClient(), ["owner/repo"], limit=3, status="success", event="push")

    assert len(runs) == 1
    assert runs[0].repo == "owner/repo"
    assert runs[0].run_id == 123
    assert runs[0].status == "success"
    assert runs[0].html_url.endswith("/1")


def test_cli_writes_markdown_and_json_outputs(tmp_path: Path) -> None:
    aggregator = load_aggregator()
    report_dir = tmp_path / "artifacts"
    output = tmp_path / "summary.md"
    json_output = tmp_path / "summary.json"
    write_json(
        report_dir / "pyfallow-report.json",
        {
            "blocking": [
                {
                    "rule": "circular-dependency",
                    "severity": "warning",
                    "confidence": "high",
                    "fingerprint": "cycle",
                    "file": "src/a.py",
                }
            ]
        },
    )

    exit_code = aggregator.main(
        [
            "--artifacts-dir",
            f"owner/repo={report_dir}",
            "--output",
            str(output),
            "--json-output",
            str(json_output),
        ]
    )

    assert exit_code == 0
    assert "`circular-dependency`: 1" in output.read_text(encoding="utf-8")
    payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert payload["finding_count"] == 1
    assert payload["classified_finding_count"] == 1
    assert payload["unclassified_finding_count"] == 0
    assert payload["operator_attention_count"] == 1
    assert payload["reports"] == [str(report_dir / "pyfallow-report.json")]
