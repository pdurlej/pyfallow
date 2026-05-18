from __future__ import annotations

from pathlib import Path
from typing import Any

from fallow_py.classify import classify_finding

from .runtime import analyze_report
from .schemas import Classification


def safe_to_remove_impl(root: str | Path, fingerprints: list[str]) -> dict[str, Classification]:
    result = analyze_report(root)
    by_fingerprint = {issue["fingerprint"]: issue for issue in result["issues"]}
    return {
        fingerprint: safe_classification(fingerprint, by_fingerprint.get(fingerprint))
        for fingerprint in fingerprints
    }


def safe_classification(fingerprint: str, issue: dict[str, Any] | None) -> Classification:
    if not issue:
        return Classification(
            fingerprint=fingerprint,
            decision="decision_needed",
            rationale="Fingerprint was not found; it may be stale, mistyped, or from a different report.",
            trade_offs=[
                "Refresh analysis: safest when the fingerprint may come from an old report.",
                "Do not delete: unknown fingerprints are never auto-safe removal evidence.",
            ],
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
