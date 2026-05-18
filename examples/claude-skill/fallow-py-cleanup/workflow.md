# fallow-py-cleanup Workflow

Use this workflow for Python changes when the fallow-py MCP server is available.

## Step 1: Detect The Trigger

Activate this skill when:

- the user asks to commit, merge, finish, ship, or mark work done
- you edited 3 or more Python files in one task
- you are about to say the task is complete
- you are about to run `git commit` or `git push`

## Step 2: Run Diff-Aware Analysis

Prefer the CLI fix-plan format when shell access is available:

```bash
fallow-py analyze --root . --since HEAD --format agent-fix-plan
```

If using MCP tools directly, call:

```text
pyfallow.analyze_diff(
  root=<workspace_root>,
  since="HEAD",
  min_confidence="medium",
  max_findings=50
)
```

`since="HEAD"` focuses on staged, unstaged, and untracked working-tree edits before commit. If the repository has a long-lived branch base, use `since="main"` or the branch base ref for PR cleanup.

## Step 3: Classify Findings

With `--format agent-fix-plan`, pyfallow returns `auto_safe`, `decision_needed`, and `blocking` groups directly. With MCP `analyze_diff`, use each finding's `classification`; call `explain_finding` when you need remediation details:

```text
pyfallow.explain_finding(root=<workspace_root>, fingerprint=<fingerprint>)
```

Use the returned `classification`:

- `auto_safe`: low-risk cleanup where the explanation provides a specific minimal action
- `decision_needed`: plausible issue, but static uncertainty or product intent matters; include the trade-offs when escalating
- `blocking`: must be resolved or explicitly waived before commit

## Step 4: Auto-Fix Safe Findings

For `auto_safe` findings:

1. Apply the smallest patch that resolves the finding.
2. Avoid opportunistic refactors.
3. Keep behavior unchanged.
4. Summarize what was auto-fixed after the patch.

Do not ask the user for permission for each `auto_safe` finding. The point of the classification is to allow small mechanical cleanup.

## Step 5: Surface Review-Needed Findings

Show review-needed findings like this:

```text
Review needed (3):
- src/api.py:42 unused-symbol format_response (medium confidence)
  -> likely forgotten wire-up. Search for callers or remove after review.
- src/billing.py:15 duplicate-code (medium confidence)
  -> matches src/checkout.py:88. Extract a shared helper or accept intentional duplication.
- pyproject.toml optional-dependency-used-in-runtime numpy
  -> move to runtime dependencies or guard the import.
```

Wait for user direction before editing review-needed findings.

## Step 6: Block On Blocking Findings

If `blocking` is not empty:

1. Do not mark the task as complete.
2. Do not commit or push.
3. Show the blocking findings and the most direct remediation.
4. Offer to fix the first blocker when the fix is clear.

Explicit rule: DO NOT mark the task complete if `blocking != []`.

Example:

```text
Blocking findings:
- src/orders.py:12 missing-runtime-dependency nonexistent_pkg
  -> imported package is not declared. Add the dependency or correct the import.
- src/checkout.py circular-dependency checkout -> orders -> checkout
  -> extract the shared type or policy into a lower-level module.
```

## Step 7: Verify

After fixes, call `pyfallow.analyze_diff` again with the same `since` ref. Continue only when there are no new blocking findings.

If the project has an explicit baseline, prefer the CLI for baseline-aware gating until MCP baseline arguments are added:

```bash
fallow-py analyze --root . --since HEAD --baseline .fallow-baseline.json --fail-on warning --min-confidence medium
```

## Pre-Edit Usage

Before adding imports you are not certain about, call:

```text
pyfallow.verify_imports(
  root=<workspace_root>,
  file="src/orders.py",
  planned_imports=["billing.compute_refund"]
)
```

Treat `hallucinated`, `cycles_introduced`, `boundary_violations`, and `missing_dependencies` as blockers before editing. Imports listed under `safe` are statically consistent with the current project graph and declarations, but tests and type checking still need to run after the edit.

## Anti-Patterns

- Skipping pyfallow because tests pass.
- Treating `decision_needed` as safe to auto-fix.
- Hiding `blocking` findings in a TODO.
- Making broad refactors to silence one finding.
- Re-running until findings disappear through unrelated edits.
