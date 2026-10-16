"""Deterministic, regex-based failure classifier.

Two jobs:
1. A *hint* for the LLM (non-binding pre-classification).
2. The *fallback* diagnosis when no LLM is available (no key, rate limit, outage).

Rules are tried against every excerpt line. The winning rule is the one with the highest
confidence; ties go to the rule whose last match is closest to the end of the log.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pipelinedoctor.engine.preprocess import Excerpt
from pipelinedoctor.schemas import Category, PreClassification


@dataclass(frozen=True)
class Rule:
    id: str
    category: Category
    patterns: tuple[re.Pattern[str], ...]
    confidence: float
    title: str
    cause: str
    fix: str
    verification: str
    command: str | None = None


def _rule(
    id: str,
    category: Category,
    patterns: list[str],
    confidence: float,
    title: str,
    cause: str,
    fix: str,
    verification: str,
    command: str | None = None,
) -> Rule:
    return Rule(
        id=id,
        category=category,
        patterns=tuple(re.compile(p) for p in patterns),
        confidence=confidence,
        title=title,
        cause=cause,
        fix=fix,
        verification=verification,
        command=command,
    )


C = Category
RULES: list[Rule] = [
    # --- dependency
    _rule(
        "npm-eresolve",
        C.DEPENDENCY,
        [r"npm (?:ERR!|error) code ERESOLVE", r"ERESOLVE (?:unable to resolve|could not resolve)"],
        0.9,
        "npm could not resolve the dependency tree (ERESOLVE)",
        "Two packages require incompatible versions of a shared peer dependency, so npm refuses "
        "to build the dependency tree.",
        "Align the conflicting peer dependency versions in package.json (upgrade the package that "
        "pins the old peer) instead of forcing the install with --force or --legacy-peer-deps.",
        "Run `npm ci` locally and in CI; it completes without ERESOLVE.",
        "npm ls --all",
    ),
    _rule(
        "pip-resolution",
        C.DEPENDENCY,
        [
            r"ResolutionImpossible",
            r"conflicting dependencies",
            r"No matching distribution found for",
            r"Could not find a version that satisfies",
        ],
        0.88,
        "pip could not resolve a consistent set of package versions",
        "The pinned requirements contain versions that cannot be installed together (or a pinned "
        "version does not exist for this Python).",
        "Relax or update the conflicting pin so that every requirement can be satisfied, then "
        "regenerate the lock/requirements file.",
        "`pip install -r requirements.txt` succeeds in a clean virtualenv.",
        "python -m pip install -r requirements.txt",
    ),
    _rule(
        "python-module-not-found",
        C.DEPENDENCY,
        [r"ModuleNotFoundError: No module named", r"ImportError: No module named"],
        0.82,
        "A Python module is imported but not installed",
        "The code imports a package that is not declared in the project dependencies, so it is "
        "missing from the CI environment.",
        "Add the missing package to the declared dependencies (pyproject.toml / requirements) or "
        "remove the import.",
        "The import succeeds in a fresh environment created only from the declared dependencies.",
    ),
    _rule(
        "lockfile-outdated",
        C.DEPENDENCY,
        [
            r"poetry\.lock .*(?:not consistent|out of date)",
            r"pyproject\.toml changed significantly since poetry\.lock",
            r"[Ll]ock ?file .*needs to be updated",
            r"uv\.lock.*needs to be updated",
            r"npm (?:ERR!|error) `npm ci` can only install packages when your package\.json and "
            r"package-lock\.json",
        ],
        0.86,
        "The lock file is out of date with the project manifest",
        "The manifest was changed without regenerating the lock file, and the frozen install in "
        "CI refuses to modify it.",
        "Regenerate the lock file locally (poetry lock / uv lock / npm install) and commit it.",
        "The frozen install (`poetry install --sync`, `uv sync --frozen`, `npm ci`) passes.",
        "poetry lock",
    ),
    # --- test
    _rule(
        "pytest-assertion",
        C.TEST,
        [
            r"^E\s+assert\b",
            r"^E\s+AssertionError",
            r"^E\s+(?:Expected|Actual)\b",
            r"AssertionError: assert ",
        ],
        0.8,
        "A test assertion failed",
        "The code under test returned a value different from what the test expects.",
        "Inspect the failing assertion and fix the code (or the test, if the expectation is "
        "outdated). Reproduce locally with the single failing test.",
        "Re-run the failing test locally and then the CI test job; both pass.",
        "python -m pytest -x -q",
    ),
    _rule(
        "pytest-fixture-error",
        C.TEST,
        [r"fixture '[^']+' not found", r"^_+ ERROR at setup of ", r"ERROR at setup of"],
        0.8,
        "A pytest fixture failed or is missing",
        "A test requests a fixture that does not exist or whose setup raised an error, so the "
        "test errors before running.",
        "Define the missing fixture in conftest.py (or fix the fixture name / its setup code).",
        "`pytest --fixtures` lists the fixture and the test passes.",
        "python -m pytest --fixtures -q",
    ),
    _rule(
        "jest-snapshot",
        C.TEST,
        [r"Snapshot .* mismatched", r"\d+ snapshots? failed", r"toMatchSnapshot"],
        0.8,
        "A Jest snapshot no longer matches the rendered output",
        "The component output changed, but the stored snapshot was not updated (or the change "
        "is an unintended regression).",
        "Review the snapshot diff; if the change is intended, update the snapshot with "
        "`npx jest -u` and commit it, otherwise fix the component.",
        "`npx jest` passes with no obsolete or failed snapshots.",
        "npx jest -u",
    ),
    _rule(
        "jest-assertion",
        C.TEST,
        [
            r"expect\(received\)\.",
            "\N{BLACK CIRCLE} .+ \N{SINGLE RIGHT-POINTING ANGLE QUOTATION MARK} .+",
        ],
        0.7,
        "A JavaScript test failed",
        "A Jest/Vitest expectation did not hold.",
        "Run the failing test locally and fix the code or the outdated expectation.",
        "The test suite passes locally and in CI.",
        "npx jest",
    ),
    _rule(
        "test-failed-generic",
        C.TEST,
        [r"^FAILED [\w/\\.-]+::", r"=+ .*\d+ failed", r"Tests:\s+\d+ failed"],
        0.5,
        "One or more tests failed",
        "The test runner reported failing tests.",
        "Open the first failing test in the log, reproduce it locally and fix the cause.",
        "The test job passes.",
    ),
    # --- flaky
    _rule(
        "network-timeout",
        C.FLAKY,
        [
            r"ReadTimeout",
            r"ConnectTimeout",
            r"ETIMEDOUT",
            r"ECONNRESET",
            r"[Rr]ead timed out",
            r"socket hang up",
            r"TimeoutError: timed out",
        ],
        0.65,
        "A network call timed out (likely flaky)",
        "A request to an external service did not answer in time. Such failures are usually "
        "transient and unrelated to the change.",
        "Re-run the failed job; if it recurs, add retries with backoff or mock the external "
        "service in tests.",
        "A re-run passes; the test is stable over several runs.",
        "gh run rerun <run-id> --failed",
    ),
    _rule(
        "browser-test-timeout",
        C.FLAKY,
        [
            r"Timeout \d+ms exceeded",
            r"Test timeout of \d+ms exceeded",
            r"waiting for (?:locator|selector)",
        ],
        0.68,
        "A browser test timed out waiting for the page (likely flaky)",
        "The end-to-end test waited for an element or navigation that did not happen within the "
        "timeout; often a race or slow CI machine.",
        "Use web-first assertions/auto-waiting instead of fixed waits, and enable retries for "
        "end-to-end tests.",
        "The test passes repeatedly (e.g. `--repeat-each 5`).",
        "npx playwright test --repeat-each 5",
    ),
    _rule(
        "step-timeout",
        C.FLAKY,
        [
            r"has timed out after \d+ minutes",
            r"exceeded the maximum execution time",
            r"The operation was canceled\.",
        ],
        0.6,
        "A step or job hit its timeout",
        "The step ran longer than its `timeout-minutes`, so the runner cancelled it. A hanging "
        "test or an unreachable service is the usual reason.",
        "Find what the step was waiting for (last lines before the cancellation); fix the hang "
        "or raise the timeout only if the work is legitimately slower.",
        "The step completes well within its timeout on re-run.",
    ),
    # --- lint
    _rule(
        "ruff-lint",
        C.LINT,
        [
            r"^[\w./\\-]+\.py:\d+:\d+: [A-Z]{1,3}\d{3,4}\b",
            # ruff's "full" format: `F401 [*] message`, the message starts with a capital or a
            # backtick. Not just "letters+digits + space": "SHA256 digest of ..." from
            # actions/upload-artifact matched that and turned a test failure into "lint".
            r"^[A-Z]{1,3}\d{3,4} (?:\[\*\] )?[A-Z`]",
            r"^\s*--> [\w./\\-]+\.py:\d+:\d+$",
            r"\[\*\] \d+ fixable with the `--fix` option",
        ],
        0.85,
        "The linter (ruff) reported violations",
        "The changed code violates the configured lint rules.",
        "Run `ruff check --fix` locally, fix the remaining findings by hand and commit.",
        "`ruff check .` exits 0.",
        "ruff check --fix .",
    ),
    _rule(
        "ruff-format",
        C.LINT,
        [r"^Would reformat: ", r"\d+ files? would be reformatted"],
        0.88,
        "Code is not formatted (ruff format --check)",
        "Some files differ from the formatter output, and CI checks formatting.",
        "Run `ruff format` locally and commit the result.",
        "`ruff format --check .` exits 0.",
        "ruff format .",
    ),
    _rule(
        "eslint",
        C.LINT,
        [r"^\s+\d+:\d+\s+error\s+.+\s{2,}[@\w/-]+$", r"✖ \d+ problems? \(\d+ errors?"],
        0.85,
        "ESLint reported errors",
        "The changed JavaScript/TypeScript code violates ESLint rules.",
        "Run `npx eslint . --fix` and fix the remaining errors by hand.",
        "`npx eslint .` exits 0.",
        "npx eslint . --fix",
    ),
    _rule(
        "mypy",
        C.LINT,
        [r"\.py:\d+: error: .+\[[a-z-]+\]$", r"Found \d+ errors? in \d+ files? \(checked"],
        0.86,
        "The type checker (mypy) reported errors",
        "The code's type annotations are inconsistent with how values are used.",
        "Fix the reported annotations or the code using them; avoid blanket `# type: ignore`.",
        "`mypy src` reports no errors.",
        "mypy src",
    ),
    # --- build
    _rule(
        "tsc-error",
        C.BUILD,
        [r"error TS\d{4}:"],
        0.86,
        "TypeScript compilation failed",
        "The TypeScript compiler found type or syntax errors, so the build cannot produce output.",
        "Fix the reported TS errors (types, missing exports, wrong imports).",
        "`npx tsc --noEmit` exits 0.",
        "npx tsc --noEmit",
    ),
    _rule(
        "merge-conflict-markers",
        C.BUILD,
        [r"^\s*(?:E\s+)?<<<<<<< ", r"^\s*(?:E\s+)?>>>>>>> ", r"Merge conflict marker encountered"],
        0.9,
        "Unresolved merge-conflict markers were committed",
        "A file still contains `<<<<<<<`/`=======`/`>>>>>>>` markers from a merge, which is a "
        "syntax error.",
        "Resolve the conflict in the file, remove all markers, and add a pre-commit check "
        "(e.g. `check-merge-conflict`).",
        "`git grep -n '^<<<<<<<'` finds nothing and the build passes.",
        "git grep -n -E '^(<<<<<<<|>>>>>>>)'",
    ),
    _rule(
        "wheel-build-failed",
        C.BUILD,
        [
            r"Failed building wheel for",
            r"Failed to build installable wheels",
            r"error: command '[^']*(?:gcc|cc|clang)' failed",
            r"subprocess-exited-with-error",
        ],
        0.8,
        "Building a Python package from source failed",
        "pip had to compile a package from source (no wheel for this Python/OS) and the native "
        "build failed, usually because system headers or a compiler are missing.",
        "Use a version that ships wheels for this Python, or install the build dependencies "
        "(e.g. `apt-get install libpq-dev build-essential`) before pip install.",
        "pip installs the package from a wheel or builds it successfully.",
    ),
    # --- docker
    _rule(
        "docker-copy-not-found",
        C.DOCKER,
        [
            r"failed to compute cache key: .*not found",
            r"COPY failed",
            r'failed to calculate checksum of ref .*"/[^"]+": not found',
        ],
        0.9,
        "A Dockerfile COPY refers to a file that is not in the build context",
        "The path used in COPY/ADD does not exist in the build context (wrong path, or excluded "
        "by .dockerignore).",
        "Fix the COPY source path relative to the build context, or remove it from .dockerignore.",
        "`docker build` completes the COPY step.",
        "docker build .",
    ),
    _rule(
        "docker-base-image",
        C.DOCKER,
        [
            r"failed to resolve source metadata for",
            r"manifest unknown",
            r"manifest for .+ not found",
            r"pull access denied for .+ repository does not exist",
        ],
        0.88,
        "The Dockerfile base image (FROM) cannot be pulled",
        "The image tag in FROM does not exist (typo or unreleased version) or is not accessible.",
        "Use an existing tag (check the registry), ideally pinned by digest.",
        "`docker pull <image>` succeeds and the build passes.",
        "docker buildx imagetools inspect <image>",
    ),
    # --- secret / permission
    _rule(
        "registry-push-denied",
        C.SECRET_PERMISSION,
        [
            r"denied: (?:permission_denied|installation not allowed|requested access)",
            r"unauthorized: authentication required",
            r"write_package",
        ],
        0.88,
        "Pushing the image to the registry was denied",
        "The workflow token lacks `packages: write` (or the package is not linked to this repo).",
        "Add `permissions: packages: write` to the job and log in with "
        "`${{ secrets.GITHUB_TOKEN }}`; "
        "for an existing package grant the repository access in package settings.",
        "The push step succeeds.",
    ),
    _rule(
        "github-token-403",
        C.SECRET_PERMISSION,
        [
            r"Resource not accessible by integration",
            r"HTTP 403",
            r"403 Forbidden",
            r"Permission to .+ denied to github-actions\[bot\]",
        ],
        0.88,
        "The GITHUB_TOKEN is missing a permission (HTTP 403)",
        "The job's `permissions:` block does not grant the scope that this API call needs.",
        "Grant the minimal extra scope to this job only (e.g. `issues: write`), not "
        "workflow-wide write-all.",
        "The API call returns 2xx on re-run.",
    ),
    _rule(
        "missing-secret",
        C.SECRET_PERMISSION,
        [
            r"\b[A-Z][A-Z0-9_]*(?:TOKEN|SECRET|KEY|PASSWORD)\b.*"
            r"(?:is not set|is empty|not provided|must be set|is required)",
            r"Input required and not supplied",
            r"secret .* (?:is not set|not found)",
        ],
        0.85,
        "A required secret is empty or not configured",
        "The workflow reads a secret that is not defined for this repository/environment (or is "
        "not passed to forks), so it is empty.",
        "Create the secret with `gh secret set NAME` (or make the step skip cleanly when the "
        "secret is absent, e.g. on fork PRs).",
        "`gh secret list` shows the secret and the step passes.",
        "gh secret list",
    ),
    _rule(
        "registry-auth-401",
        C.SECRET_PERMISSION,
        [
            r"npm (?:ERR!|error) code E401",
            r"Unable to authenticate",
            r"401 Unauthorized",
            r"code E403",
        ],
        0.86,
        "Authentication to the package registry failed",
        "The registry token is missing, expired or not valid for this registry.",
        "Rotate the token, store it as a secret and reference it via NODE_AUTH_TOKEN / .npmrc; "
        "never print it in logs.",
        "`npm whoami --registry <url>` succeeds in CI.",
    ),
    # --- infrastructure
    _rule(
        "oom-killed",
        C.INFRASTRUCTURE,
        [
            r"exit code 137",
            r"\bKilled\b",
            r"OOMKilled",
            r"[Oo]ut of memory",
            r"JavaScript heap out of memory",
            r"\bMemoryError\b",
        ],
        0.85,
        "The process was killed for running out of memory (exit 137)",
        "The process exceeded the memory available to it (runner or container limit) and the "
        "kernel OOM killer terminated it.",
        "Reduce memory usage (smaller batches/fixtures, fewer workers) or raise the container "
        "memory limit / use a larger runner.",
        "The job passes and peak memory stays below the limit.",
    ),
    _rule(
        "disk-full",
        C.INFRASTRUCTURE,
        [r"No space left on device", r"\bENOSPC\b", r"running out of disk space"],
        0.9,
        "The runner ran out of disk space",
        "The job filled the runner's disk (large images, caches or build artifacts).",
        "Free space before the heavy step (remove unused toolchains, `docker system prune`) or "
        "reduce what is written.",
        "`df -h` shows free space and the job passes.",
        "df -h",
    ),
    _rule(
        "rate-limit-429",
        C.INFRASTRUCTURE,
        [
            r"\b429\b.*Too Many Requests",
            r"toomanyrequests",
            r"[Rr]ate limit exceeded",
            r"HTTP Error 429",
            r"API rate limit",
        ],
        0.86,
        "A registry or API rate-limited the job (HTTP 429)",
        "Too many requests were made to an external service (often anonymous Docker Hub pulls).",
        "Authenticate to the registry, cache/mirror images, and retry with backoff.",
        "A re-run after the limit resets passes.",
    ),
    _rule(
        "connection-refused",
        C.INFRASTRUCTURE,
        [
            r"[Cc]onnection refused",
            r"ECONNREFUSED",
            r"could not connect to server",
            r"connection to server at .+ failed",
        ],
        0.85,
        "A service the job needs refused the connection",
        "The test connects to a host/port where nothing is listening (service container not "
        "ready, wrong port or wrong host name).",
        "Check the service container's port mapping and health check, and that the test uses the "
        "same host/port.",
        "The integration tests connect and pass.",
    ),
    # --- config
    _rule(
        "workflow-yaml",
        C.CONFIG,
        [
            r"Invalid workflow file",
            r"yaml: line \d+:",
            r"while (?:parsing|scanning) a",
            r"mapping values are not allowed",
            r"The workflow is not valid",
        ],
        0.9,
        "The workflow YAML is invalid",
        "GitHub could not parse the workflow file, so no job could start.",
        "Fix the YAML at the reported line (indentation / quoting) and validate it locally "
        "with actionlint before pushing.",
        "`actionlint` passes and the workflow starts.",
        "actionlint",
    ),
    _rule(
        "runtime-version-mismatch",
        C.CONFIG,
        [
            r"EBADENGINE",
            r"Unsupported engine",
            r"The engine \"node\" is incompatible",
            r"requires a different Python",
            r"requires-python",
        ],
        0.84,
        "The runtime version in CI does not match what the project requires",
        "The project declares a runtime range (engines / requires-python) that the version set "
        "up in the workflow does not satisfy.",
        "Align the version in the workflow (setup-node / setup-python) with the project's "
        "declared range, ideally from a single source (.nvmrc / .python-version).",
        "The install step runs without engine/version errors.",
    ),
]

RULES_BY_ID = {rule.id: rule for rule in RULES}


@dataclass
class RuleMatch:
    rule: Rule
    lines: list[int] = field(default_factory=list)

    @property
    def last_line(self) -> int:
        return self.lines[-1] if self.lines else 0


def match_rules(excerpt: Excerpt) -> list[RuleMatch]:
    matches = []
    for rule in RULES:
        hit_lines = [
            n for n in excerpt.numbers if any(p.search(excerpt.lines[n]) for p in rule.patterns)
        ]
        if hit_lines:
            matches.append(RuleMatch(rule=rule, lines=hit_lines))
    matches.sort(key=lambda m: (m.rule.confidence, m.last_line), reverse=True)
    return matches


def best_match(excerpt: Excerpt) -> RuleMatch | None:
    matches = match_rules(excerpt)
    return matches[0] if matches else None


def preclassify(excerpt: Excerpt) -> PreClassification | None:
    match = best_match(excerpt)
    if match is None:
        return None
    return PreClassification(
        category=match.rule.category, rule_id=match.rule.id, confidence=match.rule.confidence
    )
