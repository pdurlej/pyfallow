# fallow-py Rules

## PY000 parse-error

- Category: `parser`
- Default severity: `error` - Stops normal commit or release flow unless fixed or explicitly waived.
- Precision: `very-high` - Usually deterministic for static inputs.
- Summary: A Python file could not be parsed.
- Why it matters: Other findings for that file are incomplete until syntax is fixed.
- Common false-positive surfaces:
  - Generated or version-specific syntax may be parsed with the wrong Python version.
- Agent action: Fix syntax first, then rerun fallow-py before acting on downstream findings.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY001 config-error

- Category: `config`
- Default severity: `error` - Stops normal commit or release flow unless fixed or explicitly waived.
- Precision: `very-high` - Usually deterministic for static inputs.
- Summary: fallow-py configuration is invalid or inconsistent.
- Why it matters: Bad configuration can invalidate every later analyzer decision.
- Common false-positive surfaces:
  - A config path may be correct on CI but missing in the local checkout.
- Agent action: Fix the config or run with the intended `--config` path; do not suppress this.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY010 unresolved-import

- Category: `imports`
- Default severity: `warning` - Useful structural signal; review before changing behavior.
- Precision: `high` - Strong static signal, still not runtime proof.
- Summary: A local import could not be resolved to an analyzed module.
- Why it matters: Agents often create broken imports while moving code; this catches that before commit.
- Common false-positive surfaces:
  - Optional imports, platform-specific modules, and generated modules may be intentionally unresolved.
- Agent action: Check the import target, source roots, package layout, and generated-code story before editing.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY011 dynamic-import

- Category: `imports`
- Default severity: `info` - Context or uncertainty signal; do not treat it as proof by itself.
- Precision: `medium` - Useful context with common dynamic-Python caveats.
- Summary: An import target is dynamic and cannot be fully resolved statically.
- Why it matters: Dynamic imports are where dead-code and dependency analysis become less certain.
- Common false-positive surfaces:
  - Plugin systems and framework loaders intentionally use dynamic import patterns.
- Agent action: Treat nearby unused-code findings as less certain and look for runtime/plugin registration.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY012 production-imports-test-code

- Category: `imports`
- Default severity: `warning` - Useful structural signal; review before changing behavior.
- Precision: `high` - Strong static signal, still not runtime proof.
- Summary: Production code imports a module that looks like test code.
- Why it matters: This can accidentally ship test helpers, fixtures, or heavyweight test dependencies.
- Common false-positive surfaces:
  - Some projects intentionally share test utilities with examples or local tooling.
- Agent action: Verify whether the importer is truly runtime code; move shared helpers out of tests if needed.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY020 circular-dependency

- Category: `architecture`
- Default severity: `warning` - Useful structural signal; review before changing behavior.
- Precision: `high` - Strong static signal, still not runtime proof.
- Summary: Local modules form an import cycle.
- Why it matters: Runtime cycles can produce partially initialized modules and fragile import ordering.
- Common false-positive surfaces:
  - Cycles that only involve `TYPE_CHECKING` imports may be harmless after review.
- Agent action: Break runtime cycles with dependency inversion, local imports, or type-only imports when appropriate.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY030 unused-module

- Category: `dead-code`
- Default severity: `warning` - Useful structural signal; review before changing behavior.
- Precision: `high` - Strong static signal, still not runtime proof.
- Summary: A module is not reachable from configured or inferred entrypoints.
- Why it matters: Unreachable modules are often abandoned code, but Python frameworks can hide entrypoints.
- Common false-positive surfaces:
  - Framework discovery, plugin loading, scripts, and public API modules may be missed.
- Agent action: Before deleting, verify entrypoints, packaging exports, dynamic imports, and tests.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY031 unused-symbol

- Category: `dead-code`
- Default severity: `info` - Context or uncertainty signal; do not treat it as proof by itself.
- Precision: `medium` - Useful context with common dynamic-Python caveats.
- Summary: A top-level function, class, or assignment is not referenced by analyzed code.
- Why it matters: Unused symbols are common agent leftovers and review noise.
- Common false-positive surfaces:
  - Public APIs, framework hooks, decorators, dynamic lookups, and docs examples can be real usage.
- Agent action: Use evidence flags before deleting; prefer `safe_to_remove` or targeted tests for removals.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY032 stale-suppression

- Category: `suppressions`
- Default severity: `info` - Context or uncertainty signal; do not treat it as proof by itself.
- Precision: `medium` - Useful context with common dynamic-Python caveats.
- Summary: A fallow-py suppression comment no longer matches a current finding.
- Why it matters: Stale suppressions hide nothing useful and make future findings harder to trust.
- Common false-positive surfaces:
  - A suppression may be waiting for a branch or generated file not present in this checkout.
- Agent action: Remove the stale suppression comment when the local report confirms it is unused.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY040 missing-runtime-dependency

- Category: `dependencies`
- Default severity: `error` - Stops normal commit or release flow unless fixed or explicitly waived.
- Precision: `very-high` - Usually deterministic for static inputs.
- Summary: Runtime code imports a third-party package that is not declared as a runtime dependency.
- Why it matters: This is a common 'works on my machine' failure for agents and CI.
- Common false-positive surfaces:
  - Import-name to distribution-name mapping can be ambiguous for some packages.
- Agent action: Declare the dependency, remove the import, or guard it as optional with explicit behavior.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY043 missing-type-dependency

- Category: `dependencies`
- Default severity: `info` - Context or uncertainty signal; do not treat it as proof by itself.
- Precision: `medium` - Useful context with common dynamic-Python caveats.
- Summary: Type-checking-only code imports an undeclared third-party package.
- Why it matters: Type-only imports can still break type checking or editor workflows.
- Common false-positive surfaces:
  - Projects may intentionally omit optional type stubs from runtime install profiles.
- Agent action: Add the package to a type/dev dependency group or guard the type usage more explicitly.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY044 missing-test-dependency

- Category: `dependencies`
- Default severity: `info` - Context or uncertainty signal; do not treat it as proof by itself.
- Precision: `medium` - Useful context with common dynamic-Python caveats.
- Summary: Test code imports an undeclared third-party package.
- Why it matters: Tests may fail in clean CI even when runtime installation works.
- Common false-positive surfaces:
  - Some CI images preinstall common test tools outside project metadata.
- Agent action: Declare the dependency in the test/dev group or remove the import.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY045 dev-dependency-used-in-runtime

- Category: `dependencies`
- Default severity: `error` - Stops normal commit or release flow unless fixed or explicitly waived.
- Precision: `very-high` - Usually deterministic for static inputs.
- Summary: Runtime code imports a package declared only for development.
- Why it matters: A production install can fail even though local developer machines pass.
- Common false-positive surfaces:
  - Some projects intentionally ship with a combined dev/runtime environment.
- Agent action: Move the dependency to runtime dependencies or move the import out of runtime code.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY046 optional-dependency-used-in-runtime

- Category: `dependencies`
- Default severity: `warning` - Useful structural signal; review before changing behavior.
- Precision: `high` - Strong static signal, still not runtime proof.
- Summary: Runtime code imports an optional dependency without an obvious guard.
- Why it matters: Optional extras should not be required by the default runtime path.
- Common false-positive surfaces:
  - A higher-level entrypoint may guarantee the extra is installed.
- Agent action: Guard the import, document the extra, or move the dependency to the required runtime set.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY047 runtime-dependency-used-only-in-tests

- Category: `dependencies`
- Default severity: `warning` - Useful structural signal; review before changing behavior.
- Precision: `high` - Strong static signal, still not runtime proof.
- Summary: A runtime dependency appears to be imported only from tests.
- Why it matters: It may be safe to demote the dependency and shrink production installs.
- Common false-positive surfaces:
  - Dynamic runtime imports or generated code may use the dependency invisibly.
- Agent action: Review package metadata and runtime entrypoints before demoting.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY048 runtime-dependency-used-only-for-types

- Category: `dependencies`
- Default severity: `info` - Context or uncertainty signal; do not treat it as proof by itself.
- Precision: `medium` - Useful context with common dynamic-Python caveats.
- Summary: A runtime dependency appears to be used only for type-checking imports.
- Why it matters: It may not need to be installed in production.
- Common false-positive surfaces:
  - Some packages expose runtime side effects even when referenced in type-only code.
- Agent action: Consider moving it to type/dev dependencies after verifying runtime behavior.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY049 unused-runtime-dependency

- Category: `dependencies`
- Default severity: `warning` - Useful structural signal; review before changing behavior.
- Precision: `high` - Strong static signal, still not runtime proof.
- Summary: A declared runtime dependency was not imported by analyzed Python code.
- Why it matters: Unused dependencies increase install time, attack surface, and maintenance load.
- Common false-positive surfaces:
  - CLI plugins, package extras, subprocess usage, and non-Python assets may require it.
- Agent action: Verify metadata, dynamic loading, and external integrations before removing.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY050 duplicate-code

- Category: `duplication`
- Default severity: `warning` - Useful structural signal; review before changing behavior.
- Precision: `high` - Strong static signal, still not runtime proof.
- Summary: Similar token windows appear in multiple locations.
- Why it matters: Duplication can make agent edits inconsistent across copies.
- Common false-positive surfaces:
  - Generated files and intentionally parallel tests can duplicate code on purpose.
- Agent action: Refactor only when shared behavior is real; otherwise leave or exclude generated paths.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY060 high-cyclomatic-complexity

- Category: `health`
- Default severity: `warning` - Useful structural signal; review before changing behavior.
- Precision: `high` - Strong static signal, still not runtime proof.
- Summary: A function has many independent control-flow branches.
- Why it matters: Complex branch structure is harder for agents and reviewers to modify safely.
- Common false-positive surfaces:
  - State machines and parsers can be complex for valid reasons.
- Agent action: Prefer characterization tests before refactoring; do not block release on this alone.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY061 high-cognitive-complexity

- Category: `health`
- Default severity: `warning` - Useful structural signal; review before changing behavior.
- Precision: `high` - Strong static signal, still not runtime proof.
- Summary: A function is structurally hard to follow by a lightweight cognitive approximation.
- Why it matters: Nested control flow increases the chance of plausible but wrong agent edits.
- Common false-positive surfaces:
  - The metric is approximate and may over-penalize clear defensive code.
- Agent action: Use it as refactor context, not proof that code is wrong.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY062 large-function

- Category: `health`
- Default severity: `info` - Context or uncertainty signal; do not treat it as proof by itself.
- Precision: `medium` - Useful context with common dynamic-Python caveats.
- Summary: A function exceeds the configured line threshold.
- Why it matters: Large functions tend to hide multiple responsibilities and weak test seams.
- Common false-positive surfaces:
  - Generated functions and declarative tables can be large without being risky.
- Agent action: Split only when behavior boundaries are clear and tests cover the movement.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY063 large-file

- Category: `health`
- Default severity: `info` - Context or uncertainty signal; do not treat it as proof by itself.
- Precision: `medium` - Useful context with common dynamic-Python caveats.
- Summary: A file exceeds the configured line threshold.
- Why it matters: Large files slow agent orientation and increase accidental edit scope.
- Common false-positive surfaces:
  - Schema fixtures, generated files, and test data can be large intentionally.
- Agent action: Use as navigation context; do not refactor solely to satisfy the threshold.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY070 boundary-violation

- Category: `architecture`
- Default severity: `warning` - Useful structural signal; review before changing behavior.
- Precision: `high` - Strong static signal, still not runtime proof.
- Summary: A configured architecture boundary was crossed.
- Why it matters: Boundary rules encode local design constraints that generic linters do not know.
- Common false-positive surfaces:
  - The boundary config may be too broad or a migration may temporarily cross layers.
- Agent action: Fix the import direction, adjust the boundary config, or record a deliberate exception.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY080 framework-entrypoint-detected

- Category: `frameworks`
- Default severity: `info` - Context or uncertainty signal; do not treat it as proof by itself.
- Precision: `medium` - Useful context with common dynamic-Python caveats.
- Summary: fallow-py detected a framework-managed entrypoint or hook.
- Why it matters: Framework hooks explain why static reachability may not see a runtime caller.
- Common false-positive surfaces:
  - Heuristics may recognize a framework pattern that is not active in this project.
- Agent action: Use this as confidence context for nearby dead-code findings, not as an edit request.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.

## PY090 risky-hotspot

- Category: `health`
- Default severity: `warning` - Useful structural signal; review before changing behavior.
- Precision: `high` - Strong static signal, still not runtime proof.
- Summary: A file combines multiple risk signals into one hotspot score.
- Why it matters: Files with overlapping complexity, dependency, and duplication signals deserve extra review.
- Common false-positive surfaces:
  - A file can score high because it is central and well-tested, not because it is wrong.
- Agent action: Route agent edits through narrower tests and review; do not rewrite the file wholesale.
- Action policy: Do not infer edit safety from the rule name alone. Use the finding's `--format agent-fix-plan` classification, confidence, evidence, and local tests.
