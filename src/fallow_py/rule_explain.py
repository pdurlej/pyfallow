from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .models import RULES


SEVERITY_MEANINGS = {
    "error": "Stops normal commit or release flow unless fixed or explicitly waived.",
    "warning": "Useful structural signal; review before changing behavior.",
    "info": "Context or uncertainty signal; do not treat it as proof by itself.",
}

PRECISION_MEANINGS = {
    "very-high": "Usually deterministic for static inputs.",
    "high": "Strong static signal, still not runtime proof.",
    "medium": "Useful context with common dynamic-Python caveats.",
}

ACTION_POLICY_NOTE = (
    "Do not infer edit safety from the rule name alone. Use the finding's "
    "`--format agent-fix-plan` classification, confidence, evidence, and local tests."
)


@dataclass(frozen=True, slots=True)
class RuleGuidance:
    summary: str
    why_it_matters: str
    false_positive_notes: tuple[str, ...]
    agent_action: str


RULE_GUIDANCE: dict[str, RuleGuidance] = {
    "parse-error": RuleGuidance(
        "A Python file could not be parsed.",
        "Other findings for that file are incomplete until syntax is fixed.",
        ("Generated or version-specific syntax may be parsed with the wrong Python version.",),
        "Fix syntax first, then rerun fallow-py before acting on downstream findings.",
    ),
    "config-error": RuleGuidance(
        "fallow-py configuration is invalid or inconsistent.",
        "Bad configuration can invalidate every later analyzer decision.",
        ("A config path may be correct on CI but missing in the local checkout.",),
        "Fix the config or run with the intended `--config` path; do not suppress this.",
    ),
    "unresolved-import": RuleGuidance(
        "A local import could not be resolved to an analyzed module.",
        "Agents often create broken imports while moving code; this catches that before commit.",
        ("Optional imports, platform-specific modules, and generated modules may be intentionally unresolved.",),
        "Check the import target, source roots, package layout, and generated-code story before editing.",
    ),
    "dynamic-import": RuleGuidance(
        "An import target is dynamic and cannot be fully resolved statically.",
        "Dynamic imports are where dead-code and dependency analysis become less certain.",
        ("Plugin systems and framework loaders intentionally use dynamic import patterns.",),
        "Treat nearby unused-code findings as less certain and look for runtime/plugin registration.",
    ),
    "production-imports-test-code": RuleGuidance(
        "Production code imports a module that looks like test code.",
        "This can accidentally ship test helpers, fixtures, or heavyweight test dependencies.",
        ("Some projects intentionally share test utilities with examples or local tooling.",),
        "Verify whether the importer is truly runtime code; move shared helpers out of tests if needed.",
    ),
    "circular-dependency": RuleGuidance(
        "Local modules form an import cycle.",
        "Runtime cycles can produce partially initialized modules and fragile import ordering.",
        ("Cycles that only involve `TYPE_CHECKING` imports may be harmless after review.",),
        "Break runtime cycles with dependency inversion, local imports, or type-only imports when appropriate.",
    ),
    "unused-module": RuleGuidance(
        "A module is not reachable from configured or inferred entrypoints.",
        "Unreachable modules are often abandoned code, but Python frameworks can hide entrypoints.",
        ("Framework discovery, plugin loading, scripts, and public API modules may be missed.",),
        "Before deleting, verify entrypoints, packaging exports, dynamic imports, and tests.",
    ),
    "unused-symbol": RuleGuidance(
        "A top-level function, class, or assignment is not referenced by analyzed code.",
        "Unused symbols are common agent leftovers and review noise.",
        ("Public APIs, framework hooks, decorators, dynamic lookups, and docs examples can be real usage.",),
        "Use evidence flags before deleting; prefer `safe_to_remove` or targeted tests for removals.",
    ),
    "stale-suppression": RuleGuidance(
        "A fallow-py suppression comment no longer matches a current finding.",
        "Stale suppressions hide nothing useful and make future findings harder to trust.",
        ("A suppression may be waiting for a branch or generated file not present in this checkout.",),
        "Remove the stale suppression comment when the local report confirms it is unused.",
    ),
    "missing-runtime-dependency": RuleGuidance(
        "Runtime code imports a third-party package that is not declared as a runtime dependency.",
        "This is a common 'works on my machine' failure for agents and CI.",
        ("Import-name to distribution-name mapping can be ambiguous for some packages.",),
        "Declare the dependency, remove the import, or guard it as optional with explicit behavior.",
    ),
    "missing-type-dependency": RuleGuidance(
        "Type-checking-only code imports an undeclared third-party package.",
        "Type-only imports can still break type checking or editor workflows.",
        ("Projects may intentionally omit optional type stubs from runtime install profiles.",),
        "Add the package to a type/dev dependency group or guard the type usage more explicitly.",
    ),
    "missing-test-dependency": RuleGuidance(
        "Test code imports an undeclared third-party package.",
        "Tests may fail in clean CI even when runtime installation works.",
        ("Some CI images preinstall common test tools outside project metadata.",),
        "Declare the dependency in the test/dev group or remove the import.",
    ),
    "dev-dependency-used-in-runtime": RuleGuidance(
        "Runtime code imports a package declared only for development.",
        "A production install can fail even though local developer machines pass.",
        ("Some projects intentionally ship with a combined dev/runtime environment.",),
        "Move the dependency to runtime dependencies or move the import out of runtime code.",
    ),
    "optional-dependency-used-in-runtime": RuleGuidance(
        "Runtime code imports an optional dependency without an obvious guard.",
        "Optional extras should not be required by the default runtime path.",
        ("A higher-level entrypoint may guarantee the extra is installed.",),
        "Guard the import, document the extra, or move the dependency to the required runtime set.",
    ),
    "runtime-dependency-used-only-in-tests": RuleGuidance(
        "A runtime dependency appears to be imported only from tests.",
        "It may be safe to demote the dependency and shrink production installs.",
        ("Dynamic runtime imports or generated code may use the dependency invisibly.",),
        "Review package metadata and runtime entrypoints before demoting.",
    ),
    "runtime-dependency-used-only-for-types": RuleGuidance(
        "A runtime dependency appears to be used only for type-checking imports.",
        "It may not need to be installed in production.",
        ("Some packages expose runtime side effects even when referenced in type-only code.",),
        "Consider moving it to type/dev dependencies after verifying runtime behavior.",
    ),
    "unused-runtime-dependency": RuleGuidance(
        "A declared runtime dependency was not imported by analyzed Python code.",
        "Unused dependencies increase install time, attack surface, and maintenance load.",
        ("CLI plugins, package extras, subprocess usage, and non-Python assets may require it.",),
        "Verify metadata, dynamic loading, and external integrations before removing.",
    ),
    "duplicate-code": RuleGuidance(
        "Similar token windows appear in multiple locations.",
        "Duplication can make agent edits inconsistent across copies.",
        ("Generated files and intentionally parallel tests can duplicate code on purpose.",),
        "Refactor only when shared behavior is real; otherwise leave or exclude generated paths.",
    ),
    "high-cyclomatic-complexity": RuleGuidance(
        "A function has many independent control-flow branches.",
        "Complex branch structure is harder for agents and reviewers to modify safely.",
        ("State machines and parsers can be complex for valid reasons.",),
        "Prefer characterization tests before refactoring; do not block release on this alone.",
    ),
    "high-cognitive-complexity": RuleGuidance(
        "A function is structurally hard to follow by a lightweight cognitive approximation.",
        "Nested control flow increases the chance of plausible but wrong agent edits.",
        ("The metric is approximate and may over-penalize clear defensive code.",),
        "Use it as refactor context, not proof that code is wrong.",
    ),
    "large-function": RuleGuidance(
        "A function exceeds the configured line threshold.",
        "Large functions tend to hide multiple responsibilities and weak test seams.",
        ("Generated functions and declarative tables can be large without being risky.",),
        "Split only when behavior boundaries are clear and tests cover the movement.",
    ),
    "large-file": RuleGuidance(
        "A file exceeds the configured line threshold.",
        "Large files slow agent orientation and increase accidental edit scope.",
        ("Schema fixtures, generated files, and test data can be large intentionally.",),
        "Use as navigation context; do not refactor solely to satisfy the threshold.",
    ),
    "boundary-violation": RuleGuidance(
        "A configured architecture boundary was crossed.",
        "Boundary rules encode local design constraints that generic linters do not know.",
        ("The boundary config may be too broad or a migration may temporarily cross layers.",),
        "Fix the import direction, adjust the boundary config, or record a deliberate exception.",
    ),
    "framework-entrypoint-detected": RuleGuidance(
        "fallow-py detected a framework-managed entrypoint or hook.",
        "Framework hooks explain why static reachability may not see a runtime caller.",
        ("Heuristics may recognize a framework pattern that is not active in this project.",),
        "Use this as confidence context for nearby dead-code findings, not as an edit request.",
    ),
    "risky-hotspot": RuleGuidance(
        "A file combines multiple risk signals into one hotspot score.",
        "Files with overlapping complexity, dependency, and duplication signals deserve extra review.",
        ("A file can score high because it is central and well-tested, not because it is wrong.",),
        "Route agent edits through narrower tests and review; do not rewrite the file wholesale.",
    ),
}


def explain_rule(query: str) -> dict[str, Any]:
    rule = resolve_rule(query)
    return rule_explanation(rule)


def explain_all_rules() -> list[dict[str, Any]]:
    return [rule_explanation(rule) for rule in sorted(RULES, key=lambda item: RULES[item]["id"])]


def resolve_rule(query: str) -> str:
    normalized = query.strip().lower()
    if normalized in RULES:
        return normalized
    for rule, meta in RULES.items():
        if normalized == meta["id"].lower():
            return rule
    raise ValueError(f"unknown fallow-py rule: {query}")


def rule_explanation(rule: str) -> dict[str, Any]:
    meta = RULES[rule]
    guidance = RULE_GUIDANCE[rule]
    severity = meta["default_severity"]
    precision = meta["precision"]
    return {
        "id": meta["id"],
        "rule": rule,
        "category": meta["category"],
        "default_severity": severity,
        "precision": precision,
        "summary": guidance.summary,
        "why_it_matters": guidance.why_it_matters,
        "false_positive_notes": list(guidance.false_positive_notes),
        "agent_action": guidance.agent_action,
        "severity_meaning": SEVERITY_MEANINGS[severity],
        "precision_meaning": PRECISION_MEANINGS[precision],
        "action_policy": ACTION_POLICY_NOTE,
    }


def render_explanation(value: dict[str, Any] | list[dict[str, Any]], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(value, indent=2, sort_keys=True) + "\n"
    if isinstance(value, list):
        return _render_rule_list(value, fmt)
    return _render_single_rule(value, fmt)


def _render_rule_list(rules: list[dict[str, Any]], fmt: str) -> str:
    if fmt == "markdown":
        lines = ["# fallow-py Rules", ""]
        for item in rules:
            lines.extend(_markdown_rule(item, heading_level=2))
        return "\n".join(lines).rstrip() + "\n"
    lines = ["fallow-py rules", ""]
    for item in rules:
        lines.append(
            f"{item['id']} {item['rule']} [{item['category']}, {item['default_severity']}, {item['precision']}]"
        )
        lines.append(f"  {item['summary']}")
    return "\n".join(lines).rstrip() + "\n"


def _render_single_rule(item: dict[str, Any], fmt: str) -> str:
    if fmt == "markdown":
        return "\n".join(_markdown_rule(item, heading_level=1)).rstrip() + "\n"
    lines = [
        f"{item['id']} {item['rule']}",
        f"Category: {item['category']}",
        f"Default severity: {item['default_severity']} - {item['severity_meaning']}",
        f"Precision: {item['precision']} - {item['precision_meaning']}",
        "",
        f"Summary: {item['summary']}",
        f"Why it matters: {item['why_it_matters']}",
        "Common false-positive surfaces:",
        *[f"- {note}" for note in item["false_positive_notes"]],
        f"Agent action: {item['agent_action']}",
        f"Action policy: {item['action_policy']}",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _markdown_rule(item: dict[str, Any], *, heading_level: int) -> list[str]:
    heading = "#" * heading_level
    return [
        f"{heading} {item['id']} {item['rule']}",
        "",
        f"- Category: `{item['category']}`",
        f"- Default severity: `{item['default_severity']}` - {item['severity_meaning']}",
        f"- Precision: `{item['precision']}` - {item['precision_meaning']}",
        f"- Summary: {item['summary']}",
        f"- Why it matters: {item['why_it_matters']}",
        "- Common false-positive surfaces:",
        *[f"  - {note}" for note in item["false_positive_notes"]],
        f"- Agent action: {item['agent_action']}",
        f"- Action policy: {item['action_policy']}",
        "",
    ]
