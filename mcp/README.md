# fallow-py-mcp

MCP server package for `fallow-py`.

Install locally from this repository:

```bash
python -m pip install -e ../
python -m pip install -e .
fallow-py-mcp --root /path/to/repo
```

The `--root` value is also the default MCP sandbox. Tool calls that pass an
explicit `root` outside that directory are rejected. You can override the
sandbox boundary with `FALLOW_PY_MCP_SANDBOX_ROOT` when a supervisor launches the
server from a different working directory.

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

The core `fallow-py` package remains stdlib-only. MCP dependencies live in this integration package.

Tools:

- `analyze_diff`: diff-aware fallow-py findings with `agent-fix-plan` classification
- `agent_context`: compact project map for coding agents
- `explain_finding`: deterministic remediation guidance
- `safe_to_remove`: conservative removal classification by fingerprint
- `verify_imports`: pre-edit prediction for planned imports

`analyze_diff` returns the same grouped policy shape as CLI `--format agent-fix-plan`:

```json
{
  "blocking": [],
  "decision_needed": [],
  "auto_safe": [],
  "findings": []
}
```

Agents should prefer the grouped fields (`blocking`, `decision_needed`, `auto_safe`).
The flat `findings` list is kept for backward compatibility and contains the same findings in grouped
order.

`safe_to_remove` returns a structured result so agents cannot confuse stale evidence with a current
finding:

```json
{
  "classifications": {
    "<fingerprint>": {
      "fingerprint": "<fingerprint>",
      "decision": "decision_needed",
      "rationale": "Fingerprint was not found in the current analysis; treat it as stale or unknown evidence and do not remove code from it.",
      "trade_offs": [
        "Refresh analysis: safest when the fingerprint may come from an old report.",
        "Do not delete: unknown fingerprints are never auto-safe removal evidence."
      ],
      "recognized": false
    }
  },
  "unrecognized": ["<fingerprint>"]
}
```

Only current, recognized findings can be deletion candidates. Unrecognized fingerprints are stale or
unknown evidence and must never drive code removal.
