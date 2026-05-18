# Dogfood-log template

**Status:** template. Copy this file into your project's working-notes directory (fallow-py uses `.codex/DOGFOOD-LOG.md`, gitignored, as its convention) when you start a dogfood window.

**Why this is a template, not a live log:** the active dogfood log lives outside git because the operator and agents add raw, half-formed observations they wouldn't want preserved as repo history. The template here gives you the shape; the content is yours.

**Recommended window close condition:** evidence-bounded, not calendar-bounded. ADR 0008 uses at least 100 fallow-py CI runs across integrated repos, at least 20 meaningful dogfood log entries, and the operator's qualitative read as the loose threshold for triage.

**Purpose:** capture concrete findings from real day-to-day work with fallow-py integrated into projects you actually edit. After the window closes, log content drives the next sprint's ticket prioritization. Without this log, polish becomes imagination-based ("we should add X" with no evidence vs "we saw FP on SQLAlchemy 4 times this week, refine the framework heuristic").

**How to use:**
- Add an entry whenever fallow-py does something notable: catches a real bug, raises a false positive, surfaces something subtle, frustrates the operator, surprises an agent
- Each entry is short (3-5 lines). Don't perfect — capture-then-classify
- Don't worry about format consistency. Patterns emerge from raw observations, not from forcing a schema upfront
- One entry per event. If fallow-py flags 5 things on one PR, that's potentially 5 entries (only the surprising ones, not the routine green CI runs)
- Keep the `### ... — \`[CATEGORY]\` Title`, `**Repo:**`, and `**fallow-py rule(s):**` lines when possible. The evidence aggregator reads those fields from gitignored logs via `--dogfood-log`.

**Categories** (use them when they fit, ignore when they don't):

- `[TP]` — true positive: fallow-py caught a real structural problem, agent or operator was about to ship slop
- `[FP]` — false positive: fallow-py flagged something that's actually fine
- `[FN]` — false negative: structural problem that landed and fallow-py didn't catch
- `[FRICTION]` — UX issue: install pain, slow run, confusing output, hard to read
- `[SURPRISE]` — unexpected behavior, neither clearly right nor wrong
- `[WIN]` — moment where operator or agent visibly benefited (deserves a story)
- `[META]` — observation about the dogfood process itself
- `[MODEL]` — model behavior during a controlled agent run: drift, obedience, hallucination, useful patch, or refusal

For low-cost model experiments, capture the denominator. Every selected task should end as one of: `opened_pr`, `safe_no_pr`, `rejected_bad_patch`, `aborted_policy`, `aborted_containment`, or `tooling_failure`. Do not log only successful PRs.

---

## Entries (newest first)

<!--
Entry template — copy and fill:

### YYYY-MM-DD HH:MM — `[CATEGORY]` Short title

**Repo:** owner/<repo>
**PR / commit:** <link or sha>
**fallow-py rule(s):** PY0XX, PY0YY
**What happened:** 1-3 sentences

**Surprising part:** what made this entry-worthy (vs routine)

**Implication for next sprint:** what tickets this adds / refines / invalidates
-->

<!--
Low-cost model attempt template — copy and fill:

### YYYY-MM-DD HH:MM — `[MODEL]` <repo>/<issue> with <model>

**Repo / SHA:** owner/repo @ <full sha>
**Task / issue:** <link or local fixture>
**Model path:** <provider/model/version>
**Containment:** sterile HOME? opencode --pure? MCP disabled? shell denied?
**fallow-py before:** <command + summary>
**Behavioral test before:** <command + failing/passing summary>
**Model intervention:** GLM-only / GLM + Codex review / Codex-authored
**Outcome:** opened_pr / safe_no_pr / rejected_bad_patch / aborted_policy / aborted_containment / tooling_failure
**fallow-py after:** <command + summary>
**Tests after:** <command + summary>
**Notes:** where the model followed instructions, drifted, invented facts, or needed a guardrail
-->

### Example entry — `[META]` Window opens

**Repo:** owner/your-project
**PR / commit:** <link to first PR>
**fallow-py rule(s):** N/A
**What happened:** Started using fallow-py in CI. First PR merged with green fallow-py gate.

**Surprising part:** N/A — meta entry to mark the window opened.

**Implication for next sprint:** evidence collection begins. After window closes, this log drives ticket prioritization.

---

<!-- Add entries above this line -->

## End-of-window analysis (fill when the evidence threshold is met)

When the window closes, the orchestrator (or operator + Codex) reads through this log and produces:

1. **Pattern summary** — which categories dominate (TP / FP / FN / FRICTION)? Which rules surface most often?
2. **Phase B re-prioritization** — which tickets in `.codex/MASTER/PHASE-B/` are validated by evidence (keep), invalidated (drop), or need refinement (rewrite)?
3. **Phase C re-prioritization** — same for Show HN polish. Maybe README ordering is fine but FAQ "Why not ruff plugin" needs different framing based on what people actually misunderstood.
4. **New tickets** — entries flagged `[FN]` (missed real bugs) likely become new tickets. Entries flagged `[FRICTION]` likely become C-phase or new D-phase work.

The output of this analysis becomes Codex master prompts for the next sprint. Until that analysis happens, **do not run Phase B/C from `.codex/MASTER/` blindly** — that would defeat the purpose of waiting for evidence.

---

*Maintained collaboratively. Operator's entries take precedence on operational claims; agents add entries from their session perspective. The log is append-only in spirit (don't rewrite past entries even if you later disagree — add a follow-up entry instead).*
