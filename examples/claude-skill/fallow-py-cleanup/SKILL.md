---
name: fallow-py-cleanup
description: |
  Static analysis layer for Python repos. Run BEFORE marking a task complete and
  BEFORE creating commits. Detects likely dead code, missing or hallucinated
  imports, circular dependencies, architecture boundary violations, complexity
  hotspots, duplicated logic, and dependency declaration drift. Auto-fix only
  safe findings, surface review-needed findings, and BLOCK commits when blocking
  findings remain.
trigger:
  - User says: "commit", "merge", "finish", "done", "ready to ship", or "looks good"
  - After 3 or more Python file edits in one task
  - Before responding that a Python code task is complete
  - Before any git commit or git push shell command
tools:
  - pyfallow.analyze_diff
  - pyfallow.agent_context
  - pyfallow.explain_finding
  - pyfallow.safe_to_remove
  - pyfallow.verify_imports
---

# Workflow

See [workflow.md](workflow.md) for the full workflow and examples.

# Quick Reference

When triggered:

1. Prefer `fallow-py analyze --since HEAD --format agent-fix-plan` before commit, or call `pyfallow.analyze_diff(since="HEAD", min_confidence="medium")` through MCP.
2. Before adding uncertain imports, call `pyfallow.verify_imports(file=<path>, planned_imports=[...])`.
3. Use the returned `auto_safe`, `decision_needed`, and `blocking` groups.
4. For each `auto_safe` finding, call `pyfallow.explain_finding(fingerprint=<fingerprint>)` and apply the minimal safe patch when one is available.
5. For each `decision_needed` finding, show the user the path, rule, confidence, one-line remediation, and trade-offs. Wait for direction.
6. If any `blocking` finding remains, stop. Do not claim the task is complete. Do not commit or push.
7. Re-run `pyfallow.analyze_diff` after edits and verify there are no new medium-or-higher confidence blockers.

# Blocking Rules

Treat these as blocking by default:

- `parse-error`
- `config-error`
- `missing-runtime-dependency`
- `circular-dependency`
- `boundary-violation`

# What This Prevents

- Hallucinated imports and undeclared runtime packages
- Dead helpers from forgotten wire-up
- Reimplemented logic that duplicates nearby modules
- Circular imports introduced by incomplete context
- Layer violations against configured architecture rules
- Complexity spikes in changed functions

# Notes

`pyfallow.verify_imports` predicts whether planned imports are safe before editing. The MCP namespace remains `pyfallow` in 0.3.x for compatibility; the installed package and CLI are `fallow-py` / `fallow-py-mcp`. Treat hallucinated imports, introduced cycles, enforced boundary violations, and missing runtime dependencies as blockers unless the user explicitly accepts the risk.
