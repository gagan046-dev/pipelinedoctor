"""A short hash of the engine code that influences diagnoses (part of the eval cache key).

If someone changes a rule, the excerpt logic or a schema, cached eval results become invalid
automatically, even when the prompt and models are unchanged. The versions of the libraries that
call the model and parse its answer are part of it too, so a Dependabot bump re-runs the evals.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from functools import lru_cache
from importlib import metadata, resources
from importlib.resources.abc import Traversable

FINGERPRINTED_DISTRIBUTIONS = ("litellm", "openai", "pydantic", "httpx")


def _python_files(node: Traversable, prefix: str) -> Iterator[tuple[str, Traversable]]:
    for child in node.iterdir():
        path = f"{prefix}/{child.name}"
        if child.is_dir():
            if child.name != "__pycache__":
                yield from _python_files(child, path)
        elif child.name.endswith(".py"):
            yield path, child


@lru_cache(maxsize=1)
def engine_fingerprint() -> str:
    root = resources.files("pipelinedoctor")
    files = dict(_python_files(root / "engine", "engine"))
    files["schemas.py"] = root / "schemas.py"
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode())
        digest.update(b"\0")
        digest.update(files[path].read_bytes().replace(b"\r\n", b"\n"))
        digest.update(b"\0")
    for name in FINGERPRINTED_DISTRIBUTIONS:
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            version = "absent"
        digest.update(f"{name}=={version}\0".encode())
    return digest.hexdigest()[:16]
