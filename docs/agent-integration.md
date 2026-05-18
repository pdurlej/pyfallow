# Agent Integration

fallow-py is designed to give coding agents a deterministic static-analysis checkpoint before they claim Python work is complete. The recommended integration path is the `fallow-py-mcp` package plus an agent rule or skill that tells the model when to call it.

## Install From A Checkout

```bash
python -m pip install -e ".[dev]"
python -m pip install -e ./mcp
fallow-py-mcp --root /absolute/path/to/repo
```

The core `fallow-py` package remains stdlib-only at runtime. MCP dependencies live in the separate `fallow-py-mcp` package.
The MCP server treats `--root` as its sandbox boundary: tool calls that pass a
different `root` outside that directory are rejected. Supervisors that need a
different boundary can set `FALLOW_PY_MCP_SANDBOX_ROOT`.

## Claude Code

Copy the bundled skill into a repository-local Claude skills directory:

```bash
mkdir -p .claude/skills
cp -R examples/claude-skill/fallow-py-cleanup .claude/skills/
```

Configure the MCP server:

```json
{
  "mcpServers": {
    "pyfallow": {
      "command": "fallow-py-mcp",
      "args": ["--root", "/absolute/path/to/repo"]
    }
  }
}
```

The skill is in [`examples/claude-skill/fallow-py-cleanup/`](../examples/claude-skill/fallow-py-cleanup/). It instructs the agent to call fallow-py before commits, after multi-file Python edits, and before marking work complete. The MCP namespace is shown as `pyfallow` for 0.3.x compatibility; the package and command names are `fallow-py-mcp`.

## Cursor

Copy the Cursor mirror rule into your project:

```bash
mkdir -p .cursor/rules
cp examples/cursor-rules/fallow-py.mdc .cursor/rules/fallow-py.mdc
```

The rule is always-on for Python files and asks Cursor to use MCP when available, or fall back to the CLI:

```bash
fallow-py analyze --root . --since HEAD --format json --min-confidence medium
```

## Recommended Agent Workflow

1. Call `pyfallow.analyze_diff(since="HEAD", min_confidence="medium")` before commit, or use the branch base ref for PR cleanup.
2. Before adding uncertain imports, call `fallow_py.verify_imports(file=<path>, planned_imports=[...])`.
3. Read `analyze_diff.blocking`, `analyze_diff.decision_needed`, and `analyze_diff.auto_safe`.
4. Call `pyfallow.explain_finding` when you need remediation details.
5. Auto-fix only findings classified as `auto_safe`.
6. Show `decision_needed` findings and their trade-offs to the user.
7. Stop on `blocking` findings. Do not commit or claim completion.
8. Re-run diff analysis after edits.

Blocking findings include parse/config errors, missing runtime dependencies, circular dependencies, and architecture boundary violations.

## Tools

- `analyze_diff`: diff-aware findings for the current change, including the same classification used by `agent-fix-plan`
- `agent_context`: concise project map for planning and review
- `explain_finding`: remediation guidance and safety classification
- `safe_to_remove`: conservative removal classification by fingerprint
- `verify_imports`: pre-edit prediction for planned imports; reports hallucinated modules/symbols, missing dependencies, introduced cycles, and boundary violations

`analyze_diff` returns grouped findings using the same action policy as CLI `--format agent-fix-plan`:

- `blocking`: findings that should stop commit/ship flows unless resolved or explicitly waived
- `decision_needed`: deterministic signals that need project judgment, with explicit trade-offs
- `auto_safe`: narrow low-risk cleanup candidates

The response also keeps a flat `findings` list for backward compatibility. New integrations should consume
the grouped fields directly so agents do not reimplement classification grouping.

`safe_to_remove` is deliberately conservative. It returns:

- `classifications`: one classification per requested fingerprint
- `unrecognized`: requested fingerprints that are not present in the current analysis

Agents must treat every `unrecognized` fingerprint as stale or unknown evidence. It is not safe to
delete code from a fingerprint that cannot be matched to the current report.

`verify_imports` returns:

- `safe`: planned imports that are statically consistent with the current graph/dependency state
- `hallucinated`: missing local modules or missing symbols/exports, with similar names when available
- `missing_dependencies`: undeclared likely third-party imports
- `cycles_introduced`: local import edges that would create a cycle
- `boundary_violations`: local import edges that would violate configured architecture rules
- `decision_needed`: cases such as star imports that are too ambiguous for a safe prediction

## Release Assets

Small legacy zip bundles are checked in under `examples/` for first-release convenience:

- `examples/claude-skill/claude-skill-pyfallow-cleanup-v0.3.0.zip`
- `examples/cursor-rules/cursor-rules-pyfallow-v0.3.0.zip`

They preserve the pre-rename `pyfallow` filenames for 0.3.x alpha compatibility. New
source examples use `fallow-py-*` names.

## Limitations

Agent triggers are heuristic. Claude Code skills and Cursor rules improve the odds that a model runs fallow-py at the right time, but they cannot guarantee deterministic tool use. CI should still run `fallow-py analyze --fail-on warning --min-confidence medium` or an equivalent baseline-aware command.
