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
            decision="manual_only",
            rationale="Fingerprint was not found in the current analysis; treat it as stale or unknown evidence and do not remove code from it.",
            recognized=False,
        )
    if safe_auto_issue(issue):
        return Classification(fingerprint=fingerprint, decision="auto_safe", rationale="High-confidence dead-code finding without unsafe state evidence.")
    if issue.get("confidence") == "medium":
        return Classification(fingerprint=fingerprint, decision="review_needed", rationale="Medium-confidence finding requires review.")
    return Classification(fingerprint=fingerprint, decision="manual_only", rationale="Low confidence, dynamic uncertainty, public API, or non-dead-code rule.")


def safe_auto_issue(issue: dict[str, Any]) -> bool:
    if issue["rule"] not in {"unused-module", "unused-symbol"}:
        return False
    return classify_finding(issue).decision == "auto_safe"
