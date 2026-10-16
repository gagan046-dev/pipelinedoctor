"""Pretend to publish the demo package. Needs DEMO_PUBLISH_TOKEN unless --optional-token.

Used by the `ops` job of Demo CI; the `secret` break mode drops --optional-token so the step
fails exactly like a workflow whose secret was never configured.
"""

from __future__ import annotations

import argparse
import os
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run publish of the demo package.")
    parser.add_argument(
        "--optional-token",
        action="store_true",
        help="succeed even when DEMO_PUBLISH_TOKEN is empty",
    )
    args = parser.parse_args(argv)
    token = os.environ.get("DEMO_PUBLISH_TOKEN", "").strip()
    if not token and not args.optional_token:
        print("Error: DEMO_PUBLISH_TOKEN is not set", file=sys.stderr)
        return 1
    mode = "with token" if token else "without token"
    print(f"dry-run publish OK ({mode})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
