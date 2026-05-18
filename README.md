# fallow-py

[![CI](https://github.com/pdurlej/fallow-py/actions/workflows/ci.yml/badge.svg)](https://github.com/pdurlej/fallow-py/actions/workflows/ci.yml)

`fallow-py` is an early Python-first codebase intelligence tool for agents and reviewers.

It builds a static picture of imports, dependencies, complexity, duplication, architecture boundaries, and likely dead code without importing or executing the project under analysis. Python is dynamic, so findings carry confidence, evidence, and suggested actions instead of pretending to be runtime truth.

## Status

Current alpha: `0.3.0a3`.

Runtime dependencies are stdlib-only on Python 3.11+. Development and packaging tools are optional extras.

The dogfood evidence window is open. Phase B/C hardening work is intentionally
paused until real repository runs produce enough signal; see
[`docs/dogfood-evidence-status.md`](docs/dogfood-evidence-status.md).

## For Agents

Use fallow-py as a pre-completion checkpoint for Python edits:

```bash
python -m pip install -e ".[dev]"
python -m pip install -e ./mcp
fallow-py-mcp --root /path/to/repo
```

Then install the bundled agent instructions:

- Claude Code skill: [`examples/claude-skill/fallow-py-cleanup/`](examples/claude-skill/fallow-py-cleanup/)
- Cursor rule mirror: [`examples/cursor-rules/fallow-py.mdc`](examples/cursor-rules/fallow-py.mdc)

See [`docs/agent-integration.md`](docs/agent-integration.md) for MCP setup, trigger rules, and the blocking/review/auto-fix workflow.

## Performance

fallow-py is meant to complement ruff, vulture, deptry, mypy/pyright, and security scanners rather than replace them. The benchmark harness in [`benchmarks/comparison/`](benchmarks/comparison/) compares runtime and finding categories across a small pinned repo set.

See [`docs/performance.md`](docs/performance.md) for the current methodology, local timing table, and "best at / add fallow-py when" guidance for each tool.

## Why fallow-py?

- Built for code agents and human reviewers.
- Project-wide graph analysis, not file-local linting.
- Deterministic JSON for automation.
- SARIF 2.1.0 for code scanning consumers.
- Baselines for CI adoption with existing debt.
- Conservative Python static analysis with confidence and evidence.

## What It Checks

`fallow-py` currently reports:

- Python source discovery and module resolution
- local import graph edges and circular dependencies
- likely unused modules and top-level symbols
- declared-but-unused runtime dependencies
- missing runtime, test-only, type-only, dev-only, and optional dependency scope issues
- duplicate code blocks using normalized token windows
- cyclomatic and cognitive complexity hotspots
- configured architecture boundary violations
- parse/config errors
- stale suppressions

It also emits graph data, baseline comparisons, SARIF, compact agent-context reports, and agent fix plans.

## Quickstart

From a fresh clone:

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
python -m fallow_py analyze --root examples/demo_project --format text
```

Without installing:

```bash
PYTHONPATH=src python -m fallow_py analyze --root examples/demo_project --format text
```

Generate machine-readable output:

```bash
python -m fallow_py analyze --root examples/demo_project --format json --output /tmp/pyfallow-report.json
python -m fallow_py analyze --root examples/demo_project --format sarif --output /tmp/pyfallow.sarif
python -m fallow_py agent-context --root examples/demo_project --format markdown --output /tmp/pyfallow-agent-context.md
```

Analyze only findings relevant to files changed since a Git ref:

```bash
python -m fallow_py analyze --root . --since HEAD~1 --format text
python -m fallow_py analyze --root . --since main --format json
```

Installed console scripts:

```bash
fallow-py --format json --root .
fallow --format json --root .
fallow analyze --language python --format json --root .
fallow python --format json --root .
```

The `fallow` command is a compatibility bridge for local workflows and possible future integration. It does not mean this project is official Fallow.

## 30-Second Demo

Run the bundled demo project:

```bash
python -m fallow_py analyze --root examples/demo_project --format text
```

Abbreviated text output excerpt:

```text
PY040 error   missing-runtime-dependency   examples/demo_project/src/app/main.py:3
PY020 warning circular-dependency          examples/demo_project/src/app/cycle_a.py:1
PY070 error   boundary-violation           examples/demo_project/src/app/domain/service.py:1
```

Short checked-in excerpts live in [`examples/outputs/`](examples/outputs/).

## Agent Workflow

Use `agent-context` before broad edits:

```bash
python -m fallow_py agent-context --root . --format markdown --output /tmp/pyfallow-agent-context.md
```

Recommended workflow:

1. Inspect parse errors and config errors first.
2. Inspect missing runtime dependencies, boundary violations, and cycles before refactors.
3. Review hotspots before changing shared modules.
4. Treat high-confidence dead modules as candidates, not deletion instructions.
5. Do not auto-delete low-confidence or framework-adjacent dead code.
6. Rerun fallow-py after edits and compare new/resolved findings.

When a finding is unclear, ask fallow-py for the rule contract:

```bash
python -m fallow_py explain PY031
python -m fallow_py explain unused-symbol --format markdown
```

The same rule reference is available in [`docs/rules.md`](docs/rules.md).

## Agent Fix-Plan Format

Use `agent-fix-plan` when an AI agent needs a native cleanup plan rather than the full report:

```bash
python -m fallow_py analyze --root . --since HEAD --format agent-fix-plan
```

The plan groups findings by action policy:

- `auto_safe`: deterministic low-risk cleanup candidates; fallow-py currently emits a concrete minimal patch only for stale suppressions.
- `decision_needed`: useful structural signals that need human, agent, or product-context judgment. Items in this bucket include `trade_offs` so the operator can see why automatic action is unsafe.
- `blocking`: parse/config errors, missing runtime dependencies, enforced boundary violations, unresolved imports, and runtime import cycles.

This format is meant to work alongside ruff, mypy/pyright, tests, and human review. It is not a replacement for those tools; it gives agents a deterministic slop-prevention checklist before they claim work is done.

## Diff-Aware Analysis

Use `--since <git-ref>` when an agent or reviewer only needs findings related to a current change:

```bash
python -m fallow_py analyze --root . --since HEAD~1 --format json
```

`fallow-py` still rebuilds the full module graph, then filters findings to files changed between the ref and `HEAD`, plus staged, unstaged, and untracked Python files in the working tree:

- issues whose primary `path` is a changed Python file
- import cycles involving a changed module
- boundary violations involving a changed importer or imported module
- duplicate groups with at least one changed fragment

The JSON report includes `analysis.diff_scope` with the requested ref, resolved commit SHA, changed files, changed modules, and whether filtering was active.

`--changed-only` remains as a deprecated alias for `--since HEAD~1`; new integrations should use `--since` directly. In non-Git workspaces, `--since` emits a warning and falls back to full analysis.

## MCP Server

Sprint 1 added a separate MCP integration package so agent tools can call fallow-py directly while the core package remains stdlib-only.

Install from a checkout:

```bash
python -m pip install -e ".[dev]"
python -m pip install -e ./mcp
fallow-py-mcp --root /path/to/repo
```

Claude Code `mcp.json` example:

```json
{
  "mcpServers": {
    "pyfallow": {
      "command": "fallow-py-mcp",
      "args": ["--root", "/path/to/repo"]
    }
  }
}
```

Available tools:

- `analyze_diff`: diff-aware findings for agent cleanup loops, including the same classification used by `agent-fix-plan`
- `agent_context`: structured project map for agents
- `explain_finding`: remediation hints for a finding fingerprint
- `verify_imports`: pre-edit prediction for planned imports, including missing modules/symbols, undeclared third-party packages, cycles, and boundary violations
- `safe_to_remove`: deterministic dead-code safety classification, including explicit `unrecognized` fingerprints for stale evidence

The MCP package also exposes report and module-graph resources plus `pre-commit-check` and `pr-cleanup` prompts.

Before adding uncertain imports, agents can call:

```text
fallow_py.verify_imports(
  file="src/orders.py",
  planned_imports=["billing.compute_refund", "requests"]
)
```

The result separates safe imports from hallucinated modules/symbols, introduced cycles, boundary violations, and missing dependency declarations.

## CI Workflow

Create a baseline for existing debt:

```bash
python -m fallow_py baseline create --root . --output .fallow-baseline.json
```

Gate on new findings:

```bash
python -m fallow_py analyze --root . \
  --baseline .fallow-baseline.json \
  --fail-on warning \
  --min-confidence medium
```

Exit codes:

- `0`: no blocking issues under the active thresholds
- `1`: blocking issues found under `--fail-on`
- `2`: tool, config, or runtime error
- `3`: parse errors severe enough to invalidate analysis

The included GitHub Actions workflow gates fallow-py's own code with `--fail-on warning --min-confidence medium`.

## Add to Your CI

Drop-in examples live in [`examples/ci/`](examples/ci/). They are platform-neutral by design and use the same `agent-fix-plan` comment renderer on every platform.

- Forgejo Actions: [`examples/ci/forgejo-actions.yml`](examples/ci/forgejo-actions.yml)
- GitHub Actions: [`examples/ci/github-actions.yml`](examples/ci/github-actions.yml)
- GitLab CI: [`examples/ci/gitlab-ci.yml`](examples/ci/gitlab-ci.yml)

The templates run `fallow-py analyze --since <base> --format agent-fix-plan --fail-on warning --min-confidence medium` for PR/MR diffs, upload `pyfallow-report.json`, and post a grouped cleanup comment when a platform token is available.

See [`examples/ci/README.md`](examples/ci/README.md) for copy paths, token notes, and the shared comment format.

## Configuration

Supported config files:

- `.fallow-py.toml`
- `.fallow.toml`
- `pyproject.toml` under `[tool.fallow.python]` or `[tool.fallow_py]`

Minimal example:

```toml
[tool.fallow_py]
roots = ["src"]
entry = ["src/app/main.py"]
include_tests = false

[tool.fallow_py.dupes]
min_lines = 6
min_tokens = 40

[tool.fallow_py.health]
max_cyclomatic = 10
max_cognitive = 15

[[tool.fallow_py.boundaries.rules]]
name = "domain-no-infra"
from = "src/app/domain/**"
disallow = ["src/app/infra/**"]
severity = "error"
```

See [`examples/demo_project/.fallow-py.toml`](examples/demo_project/.fallow-py.toml) for a compact working configuration.

## Suppressions

Supported prefixes:

```python
# fallow: ignore
# fallow: ignore[unused-symbol]
# fallow: ignore[unused-module]
# fallow: ignore[missing-runtime-dependency]
# fallow: ignore[unused-runtime-dependency]
# fallow: ignore[missing-dependency]  # legacy alias for split dependency rules
# fallow: ignore[unused-dependency]   # legacy alias for split dependency rules
# fallow: ignore[duplicate-code]
# fallow: ignore[high-complexity]
# fallow: expected-unused

# fallow-py: ignore[unused-symbol]
```

Suppressions apply to the same line, symbol definition lines, or the whole file when placed near the top of the file. Stale suppressions are reported when practical.

## Output Formats

- `text`: compact human-readable diagnostics
- `json`: deterministic machine-readable report
- `agent-fix-plan`: classified JSON for agent cleanup loops
- `sarif`: SARIF 2.1.0 for code scanning consumers
- `markdown`: used by `agent-context`
- `mermaid` and `dot`: graph command output

JSON reports include summary, issues, metrics, graph data, config metadata, and limitations. `evidence` and `actions` are intentionally extensible.

## Baseline Usage

```bash
python -m fallow_py baseline create --root . --output .fallow-baseline.json
python -m fallow_py baseline compare --root . --baseline .fallow-baseline.json --format json
python -m fallow_py analyze --root . --baseline .fallow-baseline.json --fail-on warning --min-confidence medium
```

When a baseline is active, CI failure considers only new findings.

## GitHub Code Scanning / SARIF

Generate SARIF:

```bash
python -m fallow_py analyze --root . --format sarif --output pyfallow.sarif
```

SARIF includes rule metadata, levels mapped from fallow-py severity, result confidence, stable fingerprints, source-line hashes where files are available, and capped related locations for cycles and duplicate groups.

The default CI workflow does not upload SARIF. Enable code scanning intentionally after repository permissions and retention expectations are clear.

## Examples Directory

- [`examples/demo_project/`](examples/demo_project/) contains a small project with missing dependencies, an unused dependency, a cycle, a duplicate, a complexity hotspot, a boundary violation, suppressions, and public API reexports.
- [`examples/outputs/`](examples/outputs/) contains short output excerpts for README and release notes.

## False-Positive Corpus

[`benchmarks/fp-cases/`](benchmarks/fp-cases/) contains checked-in minimal projects for common
false-positive surfaces: Django management commands, FastAPI routes, package public APIs, optional
imports, type-only imports, namespace package ambiguity, Protocol classes, dataclasses, and Celery
tasks.

Each case has machine-readable expectations and a short human explanation. The corpus is not
exhaustive; if fallow-py misclassifies your project, submit the smallest reproducible case there before
changing analyzer behavior.

## Limitations

Static Python analysis is approximate. Known limits include dynamic imports, monkey patching, reflection, dependency injection containers, framework magic, plugin entry points, namespace package ambiguity, generated code, runtime path mutation, conditional imports, and public API that may be consumed outside the repository.

See [`docs/limitations.md`](docs/limitations.md) for details.

## Relationship To fallow-rs/fallow

`fallow-py` is inspired by [`fallow-rs/fallow`](https://github.com/fallow-rs/fallow), but it is not currently an official fallow-rs/fallow project and does not imply endorsement or affiliation.

This repository follows the standalone integration path: a Python package and CLI with stable JSON/SARIF output that could later be called by a broader Fallow CLI. The installed `fallow` console entry point is a compatibility bridge for future integration and local workflows.

See [`docs/fallow-integration.md`](docs/fallow-integration.md).

## Development

```bash
python -m pip install -e ".[dev]"
python -m compileall -q src tests
python -m pytest -q
python -m fallow_py analyze --root examples/demo_project --format json
python -m build
python -m twine check dist/*
```

Runtime code must remain stdlib-only and must never execute analyzed project code.

## Contributing

Contributions are welcome. Start with [`CONTRIBUTING.md`](CONTRIBUTING.md), especially the guidance on false positives, fixtures, golden outputs, and the no-runtime-execution safety rule.

## License

MIT. See [`LICENSE`](LICENSE).
