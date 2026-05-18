# Dogfood Evidence Status

**Status:** active dogfood window after the `fallow-py` public rename.

This page is the durable, public pointer for what happens before Phase B/C work resumes.
It prevents the current plan from living only in chat context.

## Current State

- Canonical repo name: `pdurlej/fallow-py`.
- Current alpha: `fallow-py 0.3.0a3` and `fallow-py-mcp 0.1.0a3`.
- Phase B/C engineering issues remain open but paused by ADR 0008.
- DeepSeek audit triage is indexed in [Forgejo #35](https://git.pdurlej.com/pdurlej/fallow-py/issues/35) and summarized in [`docs/audits/deepseek-v4-pro-triage-2026-05-12.md`](audits/deepseek-v4-pro-triage-2026-05-12.md).
- Dogfood aggregation infrastructure is tracked in [Forgejo #29](https://git.pdurlej.com/pdurlej/fallow-py/issues/29).
  The current lightweight aggregator is [`scripts/dogfood/aggregate_evidence.py`](../scripts/dogfood/aggregate_evidence.py):
  it reads Forgejo Actions run metadata plus locally available `pyfallow-report.json`
  artifacts plus local dogfood logs and emits Markdown/JSON summaries for operator
  review. The Markdown summary starts with an Owner Action Board so the operator
  can scan what needs attention now.

## Evidence Gate

Phase B/C work resumes when the operator has enough real-world signal, not when a
calendar date passes. The current gate is:

- at least 100 fallow-py CI runs across integrated repositories,
- at least 20 meaningful dogfood log entries, and
- the operator's qualitative read that the evidence is enough to prioritize work.

## Immediate Next Work

1. Integrate fallow-py CI into operator-owned Python repositories.
2. Run the dogfood evidence aggregator from a trusted cron host, for example:

   ```bash
   python scripts/dogfood/aggregate_evidence.py \
     --repo pdurlej/fallow-py \
     --artifacts-dir pdurlej/fallow-py=/var/lib/fallow-py/dogfood/fallow-py \
     --dogfood-log /Users/pd/Developer/fallow-python/.codex/DOGFOOD-LOG.md \
     --output /var/lib/fallow-py/dogfood/weekly.md \
     --json-output /var/lib/fallow-py/dogfood/weekly.json
   ```

3. Log false positives, useful findings, missed findings, and workflow friction with [`docs/dogfood-log-template.md`](dogfood-log-template.md). The logs may stay gitignored; pass them to the aggregator with `--dogfood-log`.
4. Keep accepted DeepSeek follow-ups visible through Forgejo issues instead of re-litigating the raw audit.
5. Treat `fallow-ts` as a sibling project, not a reason to expand this Python analyzer before evidence arrives.

## Operator Action Items

- Decide when to publish the existing draft GitHub prerelease for `0.3.0a3`.
- Decide whether and when to upload `0.3.0a3` / `0.1.0a3` to TestPyPI.
