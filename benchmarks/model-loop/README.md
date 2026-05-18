# Model Loop Benchmark Harness

This harness prepares internal evidence for the question:

> Does `fallow-py` feedback help a cheaper or lower-effort model produce a patch
> candidate that is as reviewable as a more expensive baseline?

It does **not** run public OSS pull-request attempts and it does **not** claim a
model-quality result by itself. It records attempts so later summaries can be
honest about successes, failures, cost, token use, and human/Codex intervention.

## What This Measures

Each run record compares one model on one small Python task under one condition:

- `model_only` - model receives the task and repository context, but no
  `fallow-py` feedback loop.
- `model_plus_fallow` - model receives the task plus controlled `fallow-py`
  output before or during iteration.

The intended comparison is not "which model wins". The intended comparison is:

- Did `fallow-py` reduce iterations?
- Did `fallow-py` reduce review burden?
- Did `fallow-py` prevent a plausible but unsafe patch?
- Did lower-effort or cheaper models become acceptable candidate generators?
- Did expensive models still benefit from deterministic feedback?

## Usage

Inspect configured tasks, models, prompt variants, and matrix size:

```bash
python benchmarks/model-loop/run.py list
```

Create deterministic plan files without invoking any model:

```bash
python benchmarks/model-loop/run.py plan \
  --task fixture-missing-runtime-dependency \
  --model gpt-55-mid \
  --prompt with-fallow-feedback \
  --condition model_plus_fallow \
  --output-dir /tmp/fallow-model-loop-plan
```

Write a blank run-record template:

```bash
python benchmarks/model-loop/run.py record-template \
  --task fixture-missing-runtime-dependency \
  --model gpt-55-mid \
  --prompt with-fallow-feedback \
  --condition model_plus_fallow \
  --output /tmp/fallow-model-loop-run.json
```

Validate completed run records:

```bash
python benchmarks/model-loop/run.py validate benchmarks/model-loop/examples/run-records
```

Aggregate completed run records:

```bash
python benchmarks/model-loop/run.py aggregate \
  --records benchmarks/model-loop/examples/run-records \
  --output /tmp/fallow-model-loop-summary.md \
  --json-output /tmp/fallow-model-loop-summary.json
```

## Evidence Rules

- Treat every selected task as part of the denominator.
- Record failures, rejected patches, containment aborts, and no-PR decisions.
- Use a dedicated provider key and sterile execution environment when running
  real model attempts.
- Do not expose secrets in prompts, logs, run records, or model output.
- Do not credit GLM/GPT/Claude for a fix that Codex or a human materially wrote.
- Do not open a public PR from this harness. A human owner decides separately.

## Intervention Labels

- `model_only_patch` - model produced the candidate patch; reviewer did not edit.
- `model_patch_codex_review_only` - model produced patch; Codex only reviewed or
  requested another model iteration.
- `model_patch_human_mechanical_edit` - model produced patch; human/Codex made
  mechanical edits such as formatting or typo fix.
- `codex_or_human_authored_fix` - not counted as model success.

## Decisions

- `opened_pr` - patch was safe enough for the human owner to open a PR.
- `safe_no_pr` - patch/result was useful but no PR was appropriate.
- `rejected_bad_patch` - candidate patch was technically or behaviorally wrong.
- `tooling_failure` - harness/provider/tool failed before a usable candidate.
- `aborted_policy` - repository/community policy stopped the attempt.
- `aborted_containment` - sandbox/secret/tooling boundary stopped the attempt.

## Public Claims

Safe internal phrasing:

> We are collecting controlled evidence on whether deterministic `fallow-py`
> feedback improves candidate patches from lower-cost or lower-effort models.

Unsafe phrasing before enough records:

> Cheap models produce frontier-quality fixes with `fallow-py`.

The second sentence needs real denominator-backed evidence. This harness exists
so we do not accidentally market a cherry-picked anecdote.

