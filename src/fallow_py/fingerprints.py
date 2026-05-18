from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import Issue, stable_data


def issue_fingerprint(issue: Issue) -> str:
    payload = {
        "rule": issue.rule,
        "path": issue.path,
        "symbol": issue.symbol,
        "module": issue.module,
        "target": issue.evidence.get("distribution")
        or issue.evidence.get("imported_module")
        or issue.evidence.get("normalized_hash")
        or _canonical_cycle_path(issue.evidence.get("cycle_path"))
        or issue.message.split(".", 1)[0],
    }
    raw = json.dumps(stable_data(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _canonical_cycle_path(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    if not value:
        return None
    nodes = value[:-1] if len(value) > 1 and value[0] == value[-1] else value
    if not nodes:
        return None
    rotations = [nodes[index:] + nodes[:index] for index in range(len(nodes))]
    canonical = min(rotations)
    return canonical + [canonical[0]]


def assign_fingerprints(issues: list[Issue]) -> None:
    for issue in issues:
        if not issue.fingerprint:
            issue.fingerprint = issue_fingerprint(issue)
