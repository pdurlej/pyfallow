from __future__ import annotations

from pathlib import Path
from typing import Any

from fallow_py.classify import classify_finding

from .runtime import analyze_report
from .schemas import Classification, SafeToRemoveResult


def safe_to_remove_impl(root: str | Path, fingerprints: list[str]) -> SafeToRemoveResult:
    result = analyze_report(root)
    by_fingerprint = {issue["fingerprint"]: issue for issue in result["issues"]}
    classifications: dict[str, Classification] = {}
    unrecognized: list[str] = []
    seen_unrecognized: set[str] = set()
    for fingerprint in fingerprints:
        issue = by_fingerprint.get(fingerprint)
        classifications[fingerprint] = safe_classification(fingerprint, issue)
        if issue is None and fingerprint not in seen_unrecognized:
            unrecognized.append(fingerprint)
            seen_unrecognized.add(fingerprint)
    return SafeToRemoveResult(classifications=classifications, unrecognized=unrecognized)


def safe_classification(fingerprint: str, issue: dict[str, Any] | None) -> Classification:
    if not issue:
        return Classification(
            fingerprint=fingerprint,
            decision="decision_needed",
            rationale="Fingerprint was not found in the current analysis; treat it as stale or unknown evidence and do not remove code from it.",
            trade_offs=[
                "Refresh analysis: safest when the fingerprint may come from an old report.",
                "Do not delete: unknown fingerprints are never auto-safe removal evidence.",
            ],
            recognized=False,
        )
    if safe_auto_issue(issue):
        classification = classify_finding(issue)
        return Classification(
            fingerprint=fingerprint,
            decision="auto_safe",
            rationale="High-confidence dead-code finding without unsafe state evidence.",
            trade_offs=classification.trade_offs,
        )
    classification = classify_finding(issue)
    return Classification(
        fingerprint=fingerprint,
        decision=classification.decision,
        rationale="Finding is not auto-safe for removal; use the classifier decision and trade-offs before editing.",
        trade_offs=classification.trade_offs,
    )


def safe_auto_issue(issue: dict[str, Any]) -> bool:
    if issue["rule"] not in {"unused-module", "unused-symbol"}:
        return False
    return classify_finding(issue).decision == "auto_safe"
