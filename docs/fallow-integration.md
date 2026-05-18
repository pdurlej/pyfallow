# Future Fallow Integration

`fallow-py` is inspired by `fallow-rs/fallow`, but it is not currently an official fallow-rs/fallow project and does not imply endorsement or affiliation.

The current repository is a standalone Python package and CLI. This was the safest integration path because the repository started empty and had no existing Rust, Node, or shared Fallow core to extend.

## Compatibility Surface

The core package installs three console scripts during the alpha migration:

- `fallow-py`
- `fallow`
- `pyfallow` (legacy alias)

The `fallow` entry point is a compatibility bridge for local workflows and possible future integration. It accepts patterns such as:

```bash
fallow doctor --root .
fallow --format json --root .
fallow analyze --language python --format json --root .
fallow python --format json --root .
fallow python agent-context --format markdown --root .
```

The separate `fallow-py-mcp` package exposes the same analyzer through MCP for agent runtimes. It is intentionally packaged outside the stdlib-only core:

```bash
python -m pip install fallow-py fallow-py-mcp
fallow-py-mcp --root /path/to/repo
```

## Subprocess Backend Contract

A future upstream Fallow CLI could invoke fallow-py as a subprocess:

```bash
python -m fallow_py analyze --language python --format json --root <repo>
```

Expected output:

- stdout contains the requested report format unless `--output` is used
- stderr contains tool/runtime errors
- exit codes follow fallow-py CLI semantics
- JSON report uses `schema_version`

For first-run checks, an upstream wrapper can call:

```bash
python -m fallow_py doctor --root <repo> --format json
```

The doctor command is read-only. It reports discovered config, source roots,
entrypoints, Git diff availability, and suggested next commands without treating
normal analyzer findings as failures.

## Input Contract

Minimum inputs:

- analysis root
- config path or discoverable config
- output format
- optional baseline path
- thresholds such as `--fail-on`, `--min-confidence`, and `--severity-threshold`

The backend must not require network access and must not execute analyzed project code.

## Output Contract

The JSON report is the integration contract. Consumers should read:

- `summary`
- `issues`
- `graphs`
- `metrics`
- `analysis.entrypoints`
- `analysis.frameworks_detected`
- `limitations`

Issue `fingerprint` is intended for baselines and regression gating.

The doctor JSON uses `schema = "fallow_py_doctor.v1"` and is intentionally a
preflight summary, not the analyzer report contract. Consumers should use it to
decide whether a repository is ready for `analyze`, not as a finding source.

MCP tools return structured objects derived from this JSON contract, not raw JSON strings. `verify_imports` is a pre-edit prediction tool that checks planned imports against the current static graph, dependency declarations, and configured architecture boundaries.

## What Real Upstream Integration Would Need

- agreement on ownership and naming
- schema compatibility review
- a stable language-backend invocation protocol
- end-to-end tests from the upstream CLI
- documentation that distinguishes official integration from this standalone package
- release process alignment

Until that happens, fallow-py should describe itself as inspired by Fallow, not official Fallow.
