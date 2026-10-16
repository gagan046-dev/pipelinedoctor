"""Apply one REAL, deliberate defect to the demo so Demo CI fails and PipelineDoctor has
something to diagnose.

    python demo/break/apply.py <mode>            # edit files in place (refuses on main)
    python demo/break/apply.py --dry-run <mode>  # print the unified diff only
    python demo/break/apply.py --list            # list modes

Every mode is idempotent: running it twice reports "already applied" and changes nothing.
Standard library only, so it runs before any dependency is installed.
"""

from __future__ import annotations

import argparse
import difflib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PATCHES = Path(__file__).resolve().parent / "patches"
WORKFLOW = ".github/workflows/demo-ci.yml"
DOCKERFILE = "demo/Dockerfile"
BASE_IMAGE = (
    "FROM python:3.12.14-slim@sha256:"
    "f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f"
)


@dataclass(frozen=True)
class Replace:
    """Replace `old` with `new` in `path` (every occurrence)."""

    path: str
    old: str
    new: str


@dataclass(frozen=True)
class Create:
    """Create `path` with the content of patches/<template>."""

    path: str
    template: str


Change = Replace | Create

MODES: dict[str, tuple[str, list[Change]]] = {
    "test": (
        "apply_tax truncates instead of rounding half-up -> pytest assertion failure",
        [
            Replace(
                "demo/src/invoicer/totals.py",
                "from decimal import ROUND_HALF_UP, Decimal",
                "from decimal import ROUND_DOWN, Decimal",
            ),
            Replace(
                "demo/src/invoicer/totals.py",
                "return (subtotal * rate).quantize(CENT, rounding=ROUND_HALF_UP)",
                "return (subtotal * rate).quantize(CENT, rounding=ROUND_DOWN)",
            ),
        ],
    ),
    "lint": (
        "unused import and badly formatted code -> ruff check / ruff format --check fail",
        [
            Replace(
                "demo/src/invoicer/money.py",
                "from dataclasses import dataclass\n",
                "import os\nfrom dataclasses import dataclass\n",
            ),
            Replace(
                "demo/src/invoicer/money.py",
                'CENT = Decimal("0.01")',
                "CENT=Decimal( '0.01' )",
            ),
        ],
    ),
    "type": (
        "wrong return annotation -> mypy error",
        [
            Replace(
                "demo/src/invoicer/money.py",
                'def format_money(amount: Decimal, currency: str = "EUR") -> str:',
                'def format_money(amount: Decimal, currency: str = "EUR") -> int:',
            ),
        ],
    ),
    "dependency": (
        "pin typing-extensions==4.1.1 -> pip ResolutionImpossible (psycopg needs >=4.6)",
        [
            Replace(
                "demo/requirements.txt",
                "typing-extensions==4.16.0",
                "typing-extensions==4.1.1",
            ),
        ],
    ),
    "missing-module": (
        "import of a package that is not declared anywhere -> ModuleNotFoundError",
        [
            Replace(
                "demo/src/invoicer/money.py",
                "from typing_extensions import override\n",
                "import humanize\nfrom typing_extensions import override\n",
            ),
            Replace(
                "demo/src/invoicer/money.py",
                '    return f"{currency} {to_cents(amount):,.2f}"',
                '    return f"{currency} {humanize.intcomma(to_cents(amount))}"',
            ),
        ],
    ),
    "conflict": (
        "unresolved merge-conflict markers in totals.py -> SyntaxError everywhere",
        [
            Replace(
                "demo/src/invoicer/totals.py",
                "    return (subtotal * rate).quantize(CENT, rounding=ROUND_HALF_UP)\n",
                "<<<<<<< HEAD\n"
                "    return (subtotal * rate).quantize(CENT, rounding=ROUND_HALF_UP)\n"
                "=======\n"
                "    return to_cents(subtotal) * rate\n"
                ">>>>>>> feature/faster-tax\n",
            ),
        ],
    ),
    "docker": (
        "Dockerfile COPYs a path that does not exist -> docker build fails",
        [
            Replace(
                DOCKERFILE,
                "# pd-break-anchor: docker\n",
                "# pd-break-anchor: docker\nCOPY config/ ./config/\n",
            ),
        ],
    ),
    "docker-base": (
        "base image tag that does not exist -> docker build fails resolving FROM",
        [Replace(DOCKERFILE, BASE_IMAGE, "FROM python:3.12.99-slim")],
    ),
    "timeout": (
        "integration test sleeps 400 s -> step timeout-minutes: 3 cancels it",
        [Create("demo/tests/test_slow_integration.py", "timeout/test_slow_integration.py.txt")],
    ),
    "oom": (
        "unit test allocates 1 GiB -> container with --memory=256m is OOM-killed (exit 137)",
        [Create("demo/tests/test_memory_hog.py", "oom/test_memory_hog.py.txt")],
    ),
    "db": (
        "integration tests point at port 5433 -> connection refused",
        [
            Replace(
                WORKFLOW,
                '          # pd-break-anchor: db\n          DEMO_DB_PORT: "5432"\n',
                '          # pd-break-anchor: db\n          DEMO_DB_PORT: "5433"\n',
            ),
        ],
    ),
    "permission": (
        "step creates a label with a read-only GITHUB_TOKEN -> HTTP 403",
        [
            Replace(
                WORKFLOW,
                "        # pd-break-anchor: permission\n"
                '        run: gh api "repos/${GITHUB_REPOSITORY}" --jq .full_name\n',
                "        # pd-break-anchor: permission\n"
                '        run: gh api -X POST "repos/${GITHUB_REPOSITORY}/labels"'
                " -f name=pd-demo-break -f color=b3261e\n",
            ),
        ],
    ),
    "secret": (
        "publish step requires DEMO_PUBLISH_TOKEN, which is not configured",
        [
            Replace(
                WORKFLOW,
                "        run: python demo/scripts/publish_dry_run.py --optional-token\n",
                "        run: python demo/scripts/publish_dry_run.py\n",
            ),
        ],
    ),
    "yaml": (
        "invalid YAML in the workflow -> run fails at startup, no jobs at all",
        [
            Replace(
                WORKFLOW,
                "      # pd-break-anchor: yaml\n",
                "      # pd-break-anchor: yaml\n"
                "      - name: Broken on purpose\n"
                "        run: [echo\n",
            ),
        ],
    ),
}


def read(rel: str) -> str:
    # newline="" keeps the file's own line endings so we never rewrite a whole file.
    with (REPO / rel).open(encoding="utf-8", newline="") as fh:
        return fh.read()


def plan(mode: str) -> tuple[dict[str, tuple[str | None, str]], list[str]]:
    """Return {path: (before or None, after)} and human-readable notes. Raises on drift."""
    files: dict[str, tuple[str | None, str]] = {}
    notes: list[str] = []
    for change in MODES[mode][1]:
        if isinstance(change, Create):
            content = (PATCHES / change.template).read_text(encoding="utf-8")
            target = REPO / change.path
            if target.exists():
                if target.read_text(encoding="utf-8") != content:
                    raise SystemExit(f"{change.path} exists with different content; refusing")
                notes.append(f"{change.path}: already present")
                continue
            files[change.path] = (None, content)
            continue

        before = files[change.path][1] if change.path in files else read(change.path)
        original = files[change.path][0] if change.path in files else before
        # Anchors are written with LF; match a CRLF checkout too (core.autocrlf=true).
        eol = "\r\n" if "\r\n" in before else "\n"
        old, new = change.old.replace("\n", eol), change.new.replace("\n", eol)
        if old in before:
            files[change.path] = (original, before.replace(old, new))
        elif new in before:
            notes.append(f"{change.path}: already applied")
        else:
            raise SystemExit(
                f"{change.path}: anchor text not found - has the file been reformatted?\n"
                f"  expected: {change.old!r}"
            )
    return files, notes


def current_branch() -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Break the demo on purpose.")
    parser.add_argument("mode", nargs="?", choices=sorted(MODES))
    parser.add_argument("--dry-run", action="store_true", help="print the diff, change nothing")
    parser.add_argument("--list", action="store_true", help="list modes and exit")
    args = parser.parse_args(argv)

    if args.list or not args.mode:
        for name in sorted(MODES):
            print(f"{name:15} {MODES[name][0]}")
        return 0

    files, notes = plan(args.mode)
    for note in notes:
        print(note)
    if not files:
        print(f"mode '{args.mode}' is already applied - nothing to do")
        return 0

    if args.dry_run:
        for rel, (before, after) in files.items():
            diff = difflib.unified_diff(
                (before or "").splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile="/dev/null" if before is None else f"a/{rel}",
                tofile=f"b/{rel}",
            )
            sys.stdout.writelines(diff)
        return 0

    branch = current_branch()
    if branch in {"main", "master", "HEAD", ""}:
        print(f"refusing to break the demo on branch '{branch or 'unknown'}'.", file=sys.stderr)
        print("create a break/* branch first (demo/scripts/break.ps1 does this).", file=sys.stderr)
        return 1

    for rel, (_, after) in files.items():
        target = REPO / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8", newline="") as fh:
            fh.write(after)
        print(f"modified {rel}")
    print(f"applied '{args.mode}': {MODES[args.mode][0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
