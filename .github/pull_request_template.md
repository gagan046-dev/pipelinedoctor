<!--
PR title = squash commit message = release-please input. Use Conventional Commits:
  feat(engine): ...   fix(console): ...   ci: ...   docs: ...   chore(deps): ...
-->

## What and why

<!-- One or two sentences. Link the issue if there is one: "Closes #12". -->

## How to verify

<!-- Commands or Actions runs a reviewer can check, e.g. `uv run pytest -k redact`. -->

## Checklist

- [ ] Title follows Conventional Commits (`pr-title` check)
- [ ] Tests added or updated; `ci-ok` is green
- [ ] Prompt wording changed? Then it is a **new** file `prompts/diagnose.vN.md` (published prompts are immutable) and `eval-gate` is green
- [ ] New third-party action? Pinned to a full commit SHA with a `# vX.Y.Z` comment (`workflow-policy` check)
- [ ] No secrets, tokens or real logs with credentials in the diff (`secret-scan` check)
