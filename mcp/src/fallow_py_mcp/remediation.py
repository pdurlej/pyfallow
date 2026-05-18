from __future__ import annotations

from pathlib import Path

from fallow_py.classify import classify_finding

from .advice import fix_options, investigation_hints, safety_notes

from .runtime import analyze_report
from .schemas import Finding, Remediation


def explain_finding_impl(root: str | Path, fingerprint: str) -> Remediation:
    result = analyze_report(root)
    issue = next((item for item in result["issues"] if item["fingerprint"] == fingerprint), None)
    if not issue:
        raise ValueError(f"finding not found: {fingerprint}")
    classification = classify_finding(issue)
    finding_payload = dict(issue)
    finding_payload["classification"] = classification.decision
    finding_payload["trade_offs"] = classification.trade_offs
    related = [
        item["fingerprint"]
        for item in result["issues"]
        if item["fingerprint"] != fingerprint and item.get("path") == issue.get("path")
    ][:10]
    return Remediation(
        finding=Finding(**finding_payload),
        classification=classification.decision,
        one_liner=f"{issue['id']} {issue['rule']}: {issue['message']}",
        trade_offs=classification.trade_offs,
        investigation_hints=investigation_hints(issue),
        fix_options=fix_options(issue),
        safety_notes=safety_notes(issue),
        related_findings=related,
    )
