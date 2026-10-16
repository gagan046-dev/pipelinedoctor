"""Workflow policy linter for this repository (run by the `workflow-policy` required check).

actionlint checks that workflows are *valid*; this script checks that they follow *our rules*:

  R1  top-level `permissions: {}` and an explicit `permissions` block on every job
  R2  every third-party `uses:` pinned to a 40-hex commit SHA with a `# vX.Y.Z` comment
  R3  `self-hosted` runners only in deploy.yml/production and rollback.yml/rollback, which need
      `environment: production` and must not be reachable from PR / comment / workflow_run events
  R4  no `pull_request_target` trigger anywhere
  R5  every job with steps has `timeout-minutes`
  R6  no `${{ github.event.* }}` / `${{ github.head_ref }}` inside `run:` (pass them via env:)
  R7  `actions/checkout` sets `persist-credentials: false` (except nightly-evals.yml/record)
  R8  workflow_run workflows never check out the triggering run's head (PR code)

Usage:
  uv run --frozen python scripts/check_workflows.py            # .github/workflows + .github/actions
  uv run --frozen python scripts/check_workflows.py FILE ...   # specific files

Exit code 0 = clean, 1 = violations (printed as `file:line: [Rn] message`).
Only the standard library and PyYAML are used.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

SHA_PIN = re.compile(r"^[\w.-]+/[\w.-]+(/[\w./-]+)?@[0-9a-f]{40}$")
VERSION_COMMENT = re.compile(r"#\s*v\d+\.\d+\.\d+")
UNSAFE_EXPR = re.compile(r"\$\{\{[^}]*\b(github\.event\.|github\.head_ref\b)")
HEAD_REF_EXPR = re.compile(r"workflow_run\.(head_sha|head_branch|head_commit|head_repository)")

SELF_HOSTED_ALLOWED = {("deploy.yml", "production"), ("rollback.yml", "rollback")}
PUSHING_CHECKOUT_ALLOWED = {("nightly-evals.yml", "record")}
FORBIDDEN_FOR_SELF_HOSTED = {"pull_request", "pull_request_target", "issue_comment", "workflow_run"}


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule}] {self.message}"


# ---------------------------------------------------------------------------
# Small helpers over PyYAML *nodes*: unlike plain yaml.safe_load they keep line numbers,
# and they keep "on" as the string "on" (YAML 1.1 would turn a bare `on` key into True).
# ---------------------------------------------------------------------------


def line_of(node: Node) -> int:
    return int(node.start_mark.line) + 1


def get(node: Node | None, key: str) -> Node | None:
    if not isinstance(node, MappingNode):
        return None
    for k, v in node.value:
        if isinstance(k, ScalarNode) and k.value == key and isinstance(v, Node):
            return v
    return None


def items(node: Node | None) -> list[tuple[str, Node]]:
    if not isinstance(node, MappingNode):
        return []
    return [(k.value, v) for k, v in node.value if isinstance(k, ScalarNode)]


def scalar(node: Node | None) -> str | None:
    return node.value if isinstance(node, ScalarNode) else None


def as_text(node: Node | None) -> str:
    """Flatten a node to text (good enough for substring checks such as runs-on labels)."""
    if node is None:
        return ""
    if isinstance(node, ScalarNode):
        return str(node.value)
    if isinstance(node, SequenceNode):
        return " ".join(as_text(n) for n in node.value)
    if isinstance(node, MappingNode):
        return " ".join(f"{k} {as_text(v)}" for k, v in items(node))
    return ""


def trigger_names(on_node: Node | None) -> set[str]:
    if isinstance(on_node, ScalarNode):
        return {on_node.value}
    if isinstance(on_node, SequenceNode):
        return {str(n.value) for n in on_node.value if isinstance(n, ScalarNode)}
    return {k for k, _ in items(on_node)}


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_uses(path: str, uses_node: Node, lines: list[str], out: list[Violation]) -> None:
    ref = scalar(uses_node) or ""
    if ref.startswith("./"):
        return  # local action or reusable workflow from this repository
    lineno = line_of(uses_node)
    if ref.startswith("docker://"):
        if "@sha256:" not in ref:
            out.append(Violation(path, lineno, "R2", f"docker action not pinned by digest: {ref}"))
        return
    if not SHA_PIN.match(ref):
        out.append(Violation(path, lineno, "R2", f"not pinned to a full commit SHA: {ref}"))
        return
    if not VERSION_COMMENT.search(lines[lineno - 1]):
        out.append(Violation(path, lineno, "R2", f"missing '# vX.Y.Z' comment after {ref}"))


def check_steps(
    path: str, name: str, job_id: str, steps: Node | None, lines: list[str], out: list[Violation]
) -> None:
    if not isinstance(steps, SequenceNode):
        return
    for step in steps.value:
        uses = get(step, "uses")
        if uses is not None:
            check_uses(path, uses, lines, out)
            ref = scalar(uses) or ""
            if ref.startswith("actions/checkout@"):
                check_checkout(path, name, job_id, step, out)
        run = get(step, "run")
        if isinstance(run, ScalarNode) and UNSAFE_EXPR.search(run.value):
            out.append(
                Violation(
                    path,
                    line_of(run),
                    "R6",
                    f"job '{job_id}': event data interpolated into run: - pass it via env: instead",
                )
            )


def check_checkout(path: str, name: str, job_id: str, step: Node, out: list[Violation]) -> None:
    with_node = get(step, "with")
    persist = (scalar(get(with_node, "persist-credentials")) or "").lower()
    if persist != "false" and (name, job_id) not in PUSHING_CHECKOUT_ALLOWED:
        out.append(
            Violation(
                path,
                line_of(step),
                "R7",
                f"job '{job_id}': actions/checkout must set persist-credentials: false",
            )
        )
    if (scalar(get(with_node, "allow-unsafe-pr-checkout")) or "").lower() == "true":
        message = f"job '{job_id}': allow-unsafe-pr-checkout is banned"
        out.append(Violation(path, line_of(step), "R8", message))


def check_workflow(path: Path, display: str | None = None) -> list[Violation]:
    shown = display or path.as_posix()
    name = path.name
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[Violation] = []

    def add(line: int, rule: str, message: str) -> None:
        out.append(Violation(shown, line, rule, message))

    try:
        root = yaml.compose(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        add(int(mark.line) + 1 if mark is not None else 1, "YAML", f"cannot parse: {exc}")
        return out
    if not isinstance(root, MappingNode):
        add(1, "YAML", "workflow must be a mapping")
        return out

    on_node = get(root, "on")
    triggers = trigger_names(on_node)
    if "pull_request_target" in triggers:
        add(line_of(on_node or root), "R4", "pull_request_target is banned")

    perms = get(root, "permissions")
    if perms is None:
        add(1, "R1", "missing top-level 'permissions: {}'")
    elif not (isinstance(perms, MappingNode) and not perms.value):
        add(line_of(perms), "R1", "top-level permissions must be '{}'")

    for job_id, job in items(get(root, "jobs")):
        jline = line_of(job)
        if get(job, "permissions") is None:
            add(jline, "R1", f"job '{job_id}' has no permissions block")

        reusable = get(job, "uses")
        if reusable is not None:
            # Jobs that call a reusable workflow cannot set timeout-minutes; the callee's jobs do.
            check_uses(shown, reusable, lines, out)
        elif get(job, "timeout-minutes") is None:
            add(jline, "R5", f"job '{job_id}' has no timeout-minutes")

        runs_on = as_text(get(job, "runs-on"))
        if "self-hosted" in runs_on or "pd-prod" in runs_on:
            if (name, job_id) not in SELF_HOSTED_ALLOWED:
                add(jline, "R3", f"job '{job_id}' may not use a self-hosted runner")
            env_node = get(job, "environment")
            env_name = scalar(env_node) or scalar(get(env_node, "name"))
            if env_name != "production":
                add(jline, "R3", f"self-hosted job '{job_id}' needs environment: production")
            reachable = sorted(triggers & FORBIDDEN_FOR_SELF_HOSTED)
            if reachable:
                add(jline, "R3", f"self-hosted job '{job_id}' reachable via {', '.join(reachable)}")

        steps = get(job, "steps")
        check_steps(shown, name, job_id, steps, lines, out)

        if "workflow_run" in triggers and isinstance(steps, SequenceNode):
            for step in steps.value:
                if (scalar(get(step, "uses")) or "").startswith("actions/checkout@"):
                    ref = as_text(get(get(step, "with"), "ref"))
                    if HEAD_REF_EXPR.search(ref):
                        add(line_of(step), "R8", f"job '{job_id}': workflow_run checks out PR code")
    return out


def check_composite_action(path: Path, display: str | None = None) -> list[Violation]:
    """Composite actions only get R2 (pinning) and R6 (no event data in run:)."""
    shown = display or path.as_posix()
    text = path.read_text(encoding="utf-8")
    try:
        root = yaml.compose(text)
    except yaml.YAMLError as exc:
        return [Violation(shown, 1, "YAML", f"cannot parse: {exc}")]
    out: list[Violation] = []
    steps = get(get(root, "runs"), "steps")
    check_steps(shown, path.name, "(composite)", steps, text.splitlines(), out)
    return out


def default_targets(repo: Path) -> list[Path]:
    workflows = sorted((repo / ".github" / "workflows").glob("*.y*ml"))
    actions = sorted((repo / ".github" / "actions").glob("*/action.y*ml"))
    return workflows + actions


def check_path(path: Path) -> list[Violation]:
    try:
        display = path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        display = path.as_posix()
    if path.parent.parent.name == "actions" and path.stem == "action":
        return check_composite_action(path, display)
    return check_workflow(path, display)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    repo = Path(__file__).resolve().parent.parent
    targets = [Path(a) for a in args] if args else default_targets(repo)
    violations: list[Violation] = []
    for target in targets:
        violations.extend(check_path(target))
    for v in violations:
        print(v)
    print(f"checked {len(targets)} file(s): {len(violations)} violation(s)")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
