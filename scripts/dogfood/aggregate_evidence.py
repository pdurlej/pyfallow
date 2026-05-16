#!/usr/bin/env python3
"""Aggregate fallow-py dogfood evidence from Forgejo runs and report artifacts.

The script is intentionally stdlib-only so it can run from cron on rs2000
without installing the project package or extra API clients.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_FORGEJO_URL = "https://git.pdurlej.com"
REPORT_NAMES = {
    "pyfallow-report.json",
    "fallow-py-report.json",
    "fallow-report.json",
}
CLASSIFICATION_GROUPS = ("auto_safe", "review_needed", "decision_needed", "blocking", "manual_only")
UNCLASSIFIED_GROUP = "unclassified"


@dataclass(slots=True)
class RunRecord:
    repo: str
    run_id: int | str
    status: str
    workflow: str
    event: str
    started: str
    stopped: str
    html_url: str


@dataclass(slots=True)
class FindingRecord:
    repo: str
    group: str
    rule: str
    severity: str
    confidence: str
    fingerprint: str
    file: str


@dataclass(slots=True)
class EvidenceSummary:
    generated_at: str
    source_repos: list[str] = field(default_factory=list)
    runs: list[RunRecord] = field(default_factory=list)
    findings: list[FindingRecord] = field(default_factory=list)
    report_paths: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        run_statuses = Counter(run.status or "unknown" for run in self.runs)
        categories = Counter(finding.group for finding in self.findings)
        severities = Counter(finding.severity for finding in self.findings)
        confidences = Counter(finding.confidence for finding in self.findings)
        rules = Counter(finding.rule for finding in self.findings)
        repos = Counter(finding.repo for finding in self.findings)
        unclassified = categories.get(UNCLASSIFIED_GROUP, 0)
        operator_attention = (
            categories.get("blocking", 0)
            + categories.get("decision_needed", 0)
            + categories.get("review_needed", 0)
        )
        return {
            "schema": "fallow_py_dogfood_evidence.v1",
            "generated_at": self.generated_at,
            "source_repos": sorted(set(self.source_repos)),
            "run_count": len(self.runs),
            "report_count": len(self.report_paths),
            "finding_count": len(self.findings),
            "classified_finding_count": len(self.findings) - unclassified,
            "unclassified_finding_count": unclassified,
            "operator_attention_count": operator_attention,
            "warning_count": len(self.warnings),
            "run_statuses": dict(sorted(run_statuses.items())),
            "finding_categories": dict(sorted(categories.items())),
            "finding_severities": dict(sorted(severities.items())),
            "finding_confidences": dict(sorted(confidences.items())),
            "top_rules": dict(rules.most_common(15)),
            "findings_by_repo": dict(sorted(repos.items())),
            "reports": sorted(self.report_paths),
            "warnings": self.warnings,
            "runs": [asdict(run) for run in self.runs],
        }


class ForgejoClient:
    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def list_runs(
        self,
        repo: str,
        *,
        limit: int,
        page: int = 1,
        status: str | None = None,
        event: str | None = None,
    ) -> list[dict[str, Any]]:
        owner, name = split_repo(repo)
        query: dict[str, str | int] = {"limit": limit, "page": page}
        if status:
            query["status"] = status
        if event:
            query["event"] = event
        path = f"/api/v1/repos/{quote(owner)}/{quote(name)}/actions/runs"
        payload = self.get_json(path, query)
        runs = payload.get("workflow_runs", [])
        if not isinstance(runs, list):
            raise ValueError(f"Unexpected runs payload for {repo}: workflow_runs is not a list")
        return runs

    def get_json(self, path: str, query: dict[str, str | int]) -> dict[str, Any]:
        url = f"{self.base_url}{path}?{urllib.parse.urlencode(query, doseq=True)}"
        request = urllib.request.Request(url, headers=self.headers())
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Unexpected JSON payload from {url}: expected object")
        return payload

    def headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        return headers


def quote(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def split_repo(repo: str) -> tuple[str, str]:
    parts = repo.split("/", 1)
    if len(parts) != 2 or not all(parts):
        raise ValueError(f"Repository must be in owner/name form, got {repo!r}")
    return parts[0], parts[1]


def collect_runs(client: ForgejoClient, repos: list[str], *, limit: int, status: str | None, event: str | None) -> list[RunRecord]:
    records: list[RunRecord] = []
    for repo in repos:
        for run in client.list_runs(repo, limit=limit, status=status, event=event):
            records.append(
                RunRecord(
                    repo=repo,
                    run_id=run.get("id", ""),
                    status=str(run.get("status", "unknown") or "unknown"),
                    workflow=str(run.get("workflow_id", "") or ""),
                    event=str(run.get("event", "") or run.get("trigger_event", "") or ""),
                    started=str(run.get("started", "") or ""),
                    stopped=str(run.get("stopped", "") or ""),
                    html_url=str(run.get("html_url", "") or ""),
                )
            )
    return records


def collect_reports(entries: list[str], *, default_repo: str) -> tuple[list[FindingRecord], list[str], list[str]]:
    findings: list[FindingRecord] = []
    report_paths: list[str] = []
    warnings: list[str] = []
    for repo, root in parse_artifact_entries(entries, default_repo=default_repo):
        if not root.exists():
            warnings.append(f"{repo}: artifact path does not exist: {root}")
            continue
        for report_path in find_report_files(root):
            try:
                payload = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                warnings.append(f"{repo}: skipped {report_path}: {exc}")
                continue
            report_paths.append(str(report_path))
            extracted, extraction_warnings = extract_findings(payload, repo=repo, report_path=report_path)
            findings.extend(extracted)
            warnings.extend(extraction_warnings)
    return findings, report_paths, warnings


def parse_artifact_entries(entries: list[str], *, default_repo: str) -> list[tuple[str, Path]]:
    parsed: list[tuple[str, Path]] = []
    for entry in entries:
        if "=" in entry:
            repo, raw_path = entry.split("=", 1)
            split_repo(repo)
            parsed.append((repo, Path(raw_path)))
        else:
            parsed.append((default_repo, Path(entry)))
    return parsed


def find_report_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.name in REPORT_NAMES or root.suffix == ".json" else []
    return sorted(path for path in root.rglob("*.json") if path.name in REPORT_NAMES)


def extract_findings(report: dict[str, Any], *, repo: str, report_path: Path | None = None) -> tuple[list[FindingRecord], list[str]]:
    findings: list[FindingRecord] = []
    for group in CLASSIFICATION_GROUPS:
        for item in report.get(group, []) or []:
            if isinstance(item, dict):
                findings.append(finding_from_dict(item, repo=repo, group=group))
    if findings:
        return findings, []

    unclassified_count = 0
    for item in report.get("issues", []) or []:
        if isinstance(item, dict):
            group = plain_issue_classification(item)
            if group == UNCLASSIFIED_GROUP:
                unclassified_count += 1
            findings.append(finding_from_dict(item, repo=repo, group=group))
    warnings: list[str] = []
    if unclassified_count:
        source = str(report_path) if report_path else "report"
        warnings.append(
            f"{repo}: {source} has {unclassified_count} issue(s) without agent-fix-plan classification; "
            f"counted as {UNCLASSIFIED_GROUP}."
        )
    return findings, warnings


def plain_issue_classification(issue: dict[str, Any]) -> str:
    raw = str(issue.get("classification") or issue.get("decision") or issue.get("group") or "").strip()
    aliases = {
        "safe-auto": "auto_safe",
        "review-needed": "review_needed",
        "decision-needed": "decision_needed",
        "manual-only": "manual_only",
    }
    group = aliases.get(raw, raw)
    if group in CLASSIFICATION_GROUPS:
        return group
    return UNCLASSIFIED_GROUP


def finding_from_dict(item: dict[str, Any], *, repo: str, group: str) -> FindingRecord:
    return FindingRecord(
        repo=repo,
        group=group,
        rule=str(item.get("rule", "") or item.get("code", "") or "unknown"),
        severity=str(item.get("severity", "") or "unknown"),
        confidence=str(item.get("confidence", "") or "unknown"),
        fingerprint=str(item.get("fingerprint", "") or ""),
        file=str(item.get("file", "") or item.get("path", "") or ""),
    )


def render_markdown(summary: EvidenceSummary) -> str:
    data = summary.to_json()
    lines = [
        "# fallow-py dogfood evidence summary",
        "",
        f"Generated: `{data['generated_at']}`",
        "",
        "## Totals",
        "",
        f"- Source repos: {len(data['source_repos'])}",
        f"- Forgejo runs observed: {data['run_count']}",
        f"- Report artifacts parsed: {data['report_count']}",
        f"- Findings observed: {data['finding_count']}",
        "",
        "## Evidence Quality",
        "",
        f"- Classified findings: {data['classified_finding_count']}",
        f"- Unclassified findings: {data['unclassified_finding_count']}",
        f"- Operator-attention findings: {data['operator_attention_count']}",
        f"- Warnings: {data['warning_count']}",
        "",
    ]
    lines.extend(counter_section("Run Statuses", data["run_statuses"]))
    lines.extend(counter_section("Finding Categories", data["finding_categories"]))
    lines.extend(counter_section("Top Rules", data["top_rules"]))
    lines.extend(counter_section("Findings By Repo", data["findings_by_repo"]))
    if summary.warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in summary.warnings)
        lines.append("")
    if summary.runs:
        lines.extend(["## Recent Runs", ""])
        lines.append("| Repo | Status | Workflow | Event | Started | Link |")
        lines.append("|---|---:|---|---|---|---|")
        for run in summary.runs[:25]:
            link = f"[run]({run.html_url})" if run.html_url else ""
            lines.append(f"| `{run.repo}` | `{run.status}` | `{run.workflow}` | `{run.event}` | `{run.started}` | {link} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def counter_section(title: str, values: dict[str, int]) -> list[str]:
    lines = [f"## {title}", ""]
    if not values:
        lines.extend(["- none", ""])
        return lines
    for key, value in values.items():
        lines.append(f"- `{key}`: {value}")
    lines.append("")
    return lines


def write_outputs(summary: EvidenceSummary, output: Path, json_output: Path | None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(summary), encoding="utf-8")
    if json_output:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(json.dumps(summary.to_json(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--forgejo-url", default=os.environ.get("FALLOW_FORGEJO_URL", DEFAULT_FORGEJO_URL))
    parser.add_argument("--token-env", default="FALLOW_FORGEJO_TOKEN", help="Environment variable containing a Forgejo token.")
    parser.add_argument("--repo", action="append", default=[], help="Forgejo repo in owner/name form. May be repeated.")
    parser.add_argument("--runs-limit", type=int, default=25, help="Max runs to read per repo.")
    parser.add_argument("--run-status", default=None, help="Optional Forgejo run status filter, e.g. success or failure.")
    parser.add_argument("--event", default=None, help="Optional Forgejo event filter, e.g. push or pull_request.")
    parser.add_argument(
        "--artifacts-dir",
        action="append",
        default=[],
        help="Directory or JSON report to parse. Use owner/repo=PATH to attribute reports to a repo.",
    )
    parser.add_argument("--default-repo", default="local/unknown", help="Repo name for artifact paths without owner/repo= prefix.")
    parser.add_argument("--output", type=Path, default=Path("dogfood-evidence-summary.md"))
    parser.add_argument("--json-output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    warnings: list[str] = []
    runs: list[RunRecord] = []
    if args.repo:
        token = os.environ.get(args.token_env)
        client = ForgejoClient(args.forgejo_url, token)
        try:
            runs = collect_runs(client, args.repo, limit=args.runs_limit, status=args.run_status, event=args.event)
        except Exception as exc:  # noqa: BLE001 - CLI should preserve partial artifact evidence.
            warnings.append(f"Forgejo run collection failed: {exc}")
    findings, report_paths, artifact_warnings = collect_reports(args.artifacts_dir, default_repo=args.default_repo)
    warnings.extend(artifact_warnings)
    summary = EvidenceSummary(
        generated_at=generated_at,
        source_repos=[*args.repo, *[repo for repo, _ in parse_artifact_entries(args.artifacts_dir, default_repo=args.default_repo)]],
        runs=runs,
        findings=findings,
        report_paths=report_paths,
        warnings=warnings,
    )
    write_outputs(summary, args.output, args.json_output)
    if warnings:
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
    print(f"Wrote {args.output}")
    if args.json_output:
        print(f"Wrote {args.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
