# fallow-py CI Templates

These templates run fallow-py as a platform-neutral cleanup gate for Python repositories. They use `--since` for pull requests and merge requests, emit `agent-fix-plan`, render the same Markdown comment everywhere, and upload the JSON report as an artifact.

Copy the full `examples/ci/` directory into your repository, then copy the platform template into the platform-specific workflow location.

Before copying CI into a new repository, run:

```bash
fallow-py doctor --root .
```

The command is read-only. It confirms the config file, source roots, inferred or
configured entrypoints, and Git diff availability before the CI gate is enabled.

## Forgejo Actions

Forgejo is listed first because fallow-py is designed for self-hosted git from day one.

1. Copy `examples/ci/` into your repository.
2. Copy `examples/ci/forgejo-actions.yml` to `.forgejo/workflows/fallow-py.yml`.
3. Use a Node-capable runner label such as `ubuntu-22.04`; do not combine
   `container: python:*` with Node-based actions such as checkout.
4. Treat PR comments as optional. The default template uploads artifacts and
   fails after the report is written; add a trusted comment token only if your
   Forgejo security model allows PR comments.

The Forgejo template uses a Node-capable runner label, checks out full history for
`--since`, installs `fallow-py`, uploads artifacts, then fails the job only after
the report step.

## GitHub Actions

1. Copy `examples/ci/` into your repository.
2. Copy `examples/ci/github-actions.yml` to `.github/workflows/fallow-py.yml`.
3. Keep `pull-requests: write` permission if you want PR comments.

The GitHub template uses the same comment renderer as Forgejo and uploads `pyfallow-report.json`, `pyfallow-comment.md`, and `pyfallow-exit-code.txt`.

## GitLab CI

1. Copy `examples/ci/` into your repository.
2. Append `examples/ci/gitlab-ci.yml` to `.gitlab-ci.yml` or include it from your CI configuration.
3. Set `PYFALLOW_GITLAB_TOKEN` if merge-request comments should be posted.

GitLab's native Code Quality report format is different from fallow-py's agent plan. This template uploads fallow-py artifacts and posts a Markdown MR comment; a native Code Quality adapter is deferred until the report mapping is implemented.

## Comment Format

All templates can call `render_pyfallow_comment.py`, so artifacts and optional
PR/MR comments use the same structure:

```markdown
## fallow-py analysis

**5 findings on this change** (3 auto-fixable, 1 review needed, 1 blocking, 0 manual only):

### Blocking (1)
- `src/orders.py:12` - `missing-runtime-dependency` (high) - Imported third-party package is not declared

### Review needed (1)
- `src/billing.py:88` - `unused-symbol` `format_amount` (medium) - Function defined but not referenced

### Auto-fixable (3)
- `src/api.py:7` - `unused-symbol` `_helper` (high) - Private helper is unused

[View full report](pyfallow-report.json)
```

## Notes

- Keep `fetch-depth: 0` or platform equivalent. `--since` needs enough Git history to compare against the base commit.
- For very large repositories, prefer PR/MR diff analysis over full-repository gates.
- If a repository has no Python changes in a PR, fallow-py should produce an empty plan and the comment says no findings matched the change.
- Treat fallow-py as complementary to ruff, mypy, vulture, CodeQL, and dependency scanners. It provides project graph and agent-fix-plan context rather than replacing those tools.
