# Dogfood — fallow-py in your project's CI

**Audience:** maintainers integrating fallow-py into Python repositories, plus agents setting up such a CI pipeline.

**Status:** v1, written 2026-05-04 and kept current through the `0.3.0a3` alpha line.

**Why:** see [`docs/philosophy.md`](philosophy.md). Short version: a non-technical operator running multi-agent codebases needs a deterministic gate between agent commits and `main`.

---

## What you get

After integrating fallow-py into a Forgejo Actions workflow on a Python project:

- Every PR runs `fallow-py analyze` on the diff
- Findings are classified (`auto_safe` / `review_needed` / `blocking` / `manual_only`)
- A comment is posted on the PR with the agent-fix-plan output
- The job fails if there are `blocking` findings or warnings above threshold
- Artifacts (`pyfallow-report.json`, agent-readable feedback) are uploaded for the next agent in the chain to read

The agent that opened the PR (Codex, Claude, etc.) gets a deterministic answer to "is this commit structurally clean," independent of whether it had enough context to know.

## Prerequisites

- A Forgejo repo with Actions enabled (`has_actions: true` on the repo, runner registered)
- Python project with at least one `pyproject.toml` declaring entry points
- The repo's runner can pull container images and install Python packages from PyPI (or TestPyPI during fallow-py alpha)

## Minimal integration (3 steps)

### Step 1 — copy the workflow

Create `.forgejo/workflows/fallow-py.yml` in your project's repo. Start from the fallow-py-shipped template:

```bash
# In your project root
mkdir -p .forgejo/workflows
curl -sSL https://raw.githubusercontent.com/pdurlej/fallow-py/main/examples/ci/forgejo-actions.yml \
  -o .forgejo/workflows/fallow-py.yml
```

(Or copy by hand from `examples/ci/forgejo-actions.yml` in the fallow-py repo.)

### Step 2 — pin a fallow-py version

The shipped template installs the latest `fallow-py` from PyPI. During alpha (pre-`0.3.0` stable), pin to a specific wheel or TestPyPI version only after that artifact has been published and smoke-tested:

```yaml
# In .forgejo/workflows/fallow-py.yml, replace the install step with:

      - name: Install fallow-py (alpha pin)
        run: |
          python -m pip install --upgrade pip
          python -m pip install \
            "fallow-py==0.3.0a3"
```

If the alpha is published only to TestPyPI, add `--index-url https://test.pypi.org/simple/`
and `--extra-index-url https://pypi.org/simple/` after the TestPyPI smoke passes. Once
fallow-py lands on production PyPI as `0.3.0` stable, switch to `pip install
fallow-py~=0.3.0`.

### Step 3 — configure fallow-py for your repo

Add a `.fallow-py.toml` (or `[tool.fallow_py]` table in `pyproject.toml`) declaring:

```toml
[tool.fallow_py]
roots = ["src"]                    # source code paths
entry = ["src/yourproject/main.py", "src/yourproject/cli.py"]
# entry = roots from which reachability is computed; everything not reached
# from any entry is candidate dead code

[tool.fallow_py.boundaries]
# (optional) architecture boundary rules
# Example: domain layer cannot import from infrastructure
"src/yourproject/domain/**" = { disallow = ["src/yourproject/infrastructure/**"] }

[tool.fallow_py.suppressions]
# (optional) global suppressions; prefer line-level `# fallow: ignore[<rule>]` instead
```

Then commit, push, open a PR. The runner pulls fallow-py, runs analyze on your diff, posts the comment, fails on blocking findings.

## Reading the CI comment as operator

The fallow-py comment on a PR will look like:

```
## fallow-py analysis

**Verdict:** DO NOT COMMIT (1 blocking)

### Blocking
- src/yourproject/payments.py:42 — missing-runtime-dependency
  Runtime import uses 'stripe', but it is not declared as a runtime dependency.
  Distribution: stripe

### Auto-safe
- (none)

### Review needed
- src/yourproject/utils.py:15 — unused-symbol `legacy_helper`
  Top-level function 'legacy_helper' is not referenced by analyzed modules.
  (medium confidence — could be framework-managed)
```

**Your decision tree as operator:**

| Verdict | What to do |
|---|---|
| All green ("No findings...") | Merge if review otherwise OK |
| Only `auto_safe` findings | Tell the agent: "apply the suggested patches in your next commit" |
| `review_needed` findings | Read them. Decide: legitimate FP (suppress) or real (fix) |
| `blocking` findings | **Send the PR back to the agent.** This is the whole point — fallow-py caught what the agent missed |

**Anti-pattern:** "the CI is red but the change looks fine, let me merge anyway." Don't. The agent that opened this PR is supposed to call fallow-py before pushing — if it didn't, that's an agent integrity failure that needs to surface, not be hidden.

## Reading the artifacts as a downstream agent

After the workflow runs, three artifacts are uploaded:

- `pyfallow-report.json` — full agent-fix-plan output (structured)
- `pyfallow-comment.md` — the rendered Markdown comment
- `pyfallow-exit-code.txt` — the analyzer's exit code

If your platform has a "next-agent picks up here" pattern (e.g., Codex reading PR feedback before iterating), point it at `pyfallow-report.json`. The structure is:

```json
{
  "schema_version": "1.0",
  "tool": "fallow-py",
  "version": "0.3.0a3",
  "summary": {
    "auto_safe_count": 0,
    "review_needed_count": 1,
    "blocking_count": 1,
    "manual_only_count": 0,
    "total": 2
  },
  "auto_safe": [],
  "review_needed": [
    {
      "fingerprint": "...",
      "rule": "unused-symbol",
      "id": "PY031",
      "file": "src/yourproject/utils.py",
      "line": 15,
      "symbol": "legacy_helper",
      ...
    }
  ],
  "blocking": [...],
  "manual_only": [],
  "limitations": [...]
}
```

An agent acting on this: iterate through `auto_safe` and apply patches; iterate through `blocking` and fix the root cause; surface `review_needed` to operator.

## Identity-isolation for agents

If an agent is committing to a repo that integrates fallow-py's CI workflow, it should commit with **its own identity**, not a shared maintainer identity:

- Agent commits use an actor-specific `user.name` and `user.email`
- Agent pushes use actor-scoped credentials from your team's approved secret store
- Agent-created PRs use the same actor identity that produced the commit

This applies recursively: any new fallow-py-integrated repo inherits this convention. Fallow-py does not enforce it (out of scope), but if it's violated, audit logs will lie.

## Sister project: fallow-py-mcp

For agents using MCP transport (Claude Code, Cursor with MCP, etc.), `fallow-py-mcp` exposes the same analysis as MCP tools:

- `analyze_diff(root, since, min_confidence, max_findings)` — same as CLI agent-fix-plan but in-process
- `verify_imports(root, file, planned_imports)` — pre-edit hallucination check
- `safe_to_remove(root, fingerprints)` — agent asks "can I delete these N findings?" answer
- `agent_context(root, scope)` — full project overview for an agent starting cold
- `explain_finding(root, fingerprint)` — investigation hints + fix options for one finding

Install: `pip install fallow-py-mcp==0.1.0a3` after the corresponding alpha artifact is published and smoke-tested.

Wire into your agent's MCP config (Claude Code example):

```json
{
  "mcpServers": {
    "pyfallow": {
      "command": "fallow-py-mcp",
      "args": ["--root", "/path/to/your/project"]
    }
  }
}
```

For agents that use MCP, `verify_imports` is the highest-leverage tool: catch a hallucinated import **before** the edit lands, so you don't even need a second turn to fix it.

## Dogfood expectations (evidence-bounded window)

Operator's strategic decision (chat log 2026-05-04, refined in ADR 0008 on 2026-05-05): fallow-py does **not** push to Show HN until we have evidence from real-world dogfood. The window is evidence-bounded, not calendar-bounded:

- Fallow-py `0.3.0a3` integrated into real repositories first, starting with the operator's own working repos and expanding as appetite allows
- Operator and agents log surprising findings, FPs, missed real bugs, friction in a dogfood log (template at [`docs/dogfood-log-template.md`](dogfood-log-template.md)) in the **pyfallow** repo
- Phase B/C starts only after the evidence threshold is met: at least 100 fallow-py CI runs across integrated repos, at least 20 meaningful dogfood log entries, and the operator's qualitative read. Plans in `.codex/MASTER/PHASE-B/` and `PHASE-C/` are not deleted — they are **subjected to evidence** before execution

This is anti-AI-slop posture: don't polish from imagination, polish from logs.

## Aggregating dogfood evidence

Use [`scripts/dogfood/aggregate_evidence.py`](../scripts/dogfood/aggregate_evidence.py)
from a trusted host to turn many CI runs and report artifacts into one operator-readable
summary. The script is stdlib-only and has two inputs:

- Forgejo Actions run metadata, read through `/api/v1/repos/{owner}/{repo}/actions/runs`
- locally available fallow-py report artifacts, usually files named `pyfallow-report.json`

Example cron-friendly command:

```bash
FALLOW_FORGEJO_TOKEN="$TOKEN" \
python scripts/dogfood/aggregate_evidence.py \
  --repo pdurlej/fallow-py \
  --runs-limit 100 \
  --artifacts-dir pdurlej/fallow-py=/var/lib/fallow-py/dogfood/fallow-py \
  --output /var/lib/fallow-py/dogfood/weekly.md \
  --json-output /var/lib/fallow-py/dogfood/weekly.json
```

If artifact download is handled by a separate rs2000 job, point `--artifacts-dir`
at the extracted artifact tree. The aggregator does not need maintainer credentials
unless it is reading private Forgejo run metadata.

Prefer `--format agent-fix-plan` reports for dogfood artifacts. Plain JSON reports
do not carry action-policy buckets, so the aggregator counts those findings as
`unclassified` unless each issue already includes an explicit classification field.

## When fallow-py is wrong

If you're confident fallow-py flagged something incorrectly:

1. Add `# fallow: ignore[<rule>]` on the line, with a comment explaining why
2. Open an issue in the fallow-py issue tracker with:
   - Link to the suppressed line
   - Reasoning why it's a false positive
   - The rule code (e.g. `PY031`)
   - The fingerprint (from `fallow-py analyze --format json`)
3. The fallow-py Phase B/C planning will treat it as input for framework heuristic improvements

If you're confident fallow-py **missed** a real structural problem:

1. Same — open an issue, but with the reverse: "this committed code has structural issue X, fallow-py didn't flag it, expected behavior?"
2. Phase B already has tickets for known gaps (SQLAlchemy declarative_base, async generators, descriptors with `__set_name__`). Check `.codex/MASTER/PHASE-B/` first.

## Using low-cost coding models safely

The dogfood thesis includes cheap or mid-tier models using fallow-py feedback, but those models are not allowed to become unsupervised maintainers. For GLM-5.1 on Z.ai Coding Plan and similar models, use this containment pattern:

1. Run the model in a sterile local environment: temporary `HOME`, `opencode --pure`, `share: disabled`, no MCP servers, no global plugins.
2. Deny shell, web fetch/search, and external-directory access for the first pass. A supervisor runs fallow-py and tests, then feeds back bounded excerpts.
3. Treat model output as a candidate patch. Codex/human review decides whether it is safe to apply.
4. Require a behavioral reproducer before public PRs. A fallow-py warning reduction alone is not evidence that the bug is fixed.
5. Keep the PR small and disclose AI assistance if the upstream project allows it.

Stop immediately if the model asks to inspect secrets, home directories, shell history, cloud config, browser state, package credentials, CI secrets, or anything outside the checked-out public repository. Also stop if it tries to touch CI, packaging, dependency locks, auth, crypto, subprocess invocation, release automation, or network behavior without an issue explicitly asking for that surface.

For repeatable soak runs, prefer [`benchmarks/soak/run.py`](../benchmarks/soak/run.py). It writes a sterile OpenCode config per run and records artifacts under `benchmarks/soak/results/`.

## References

- [`docs/philosophy.md`](philosophy.md) — why fallow-py exists in this shape
- [`docs/limitations.md`](limitations.md) — what fallow-py does NOT catch (Phase C ticket)
- Full rule reference — Phase C ticket; not yet present as a live docs page
- [`examples/ci/forgejo-actions.yml`](../examples/ci/forgejo-actions.yml) — the workflow template
- [`examples/ci/README.md`](../examples/ci/README.md) — multi-platform CI guide (Forgejo, GitHub, GitLab)
- Local `AGENTS.md` — identity-isolation and review expectations for this repository
- a dogfood log (template at [`docs/dogfood-log-template.md`](dogfood-log-template.md)) — log template for evidence collection

---

*Maintained by Claude Opus 4.7 under operator direction. Updates flow through normal PR review.*
