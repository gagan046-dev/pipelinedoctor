"""Content-addressed cache of eval results.

The key covers everything that can change an answer: the case, its log, the prompt (by hash), the
model list and the engine code fingerprint. Unchanged cases are therefore never re-sent to the
LLM, which is what keeps a PR eval run inside the free-tier request quota.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CACHE_SCHEMA = "1"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def case_key(
    *,
    case_yaml: bytes,
    log_sha256: str,
    prompt_id: str,
    prompt_version: str,
    prompt_sha256: str,
    models: list[str],
    engine_fingerprint: str,
) -> str:
    payload = {
        "schema": CACHE_SCHEMA,
        "case_yaml": case_yaml.replace(b"\r\n", b"\n").decode("utf-8"),
        "log_sha256": log_sha256,
        "prompt_id": prompt_id,
        "prompt_version": prompt_version,
        "prompt_sha256": prompt_sha256,
        "models": list(models),
        "engine_fingerprint": engine_fingerprint,
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class ResultCache:
    """Layout: <dir>/v1/diagnose/<key>.json and <dir>/v1/judge/<key>-<judge sha12>.json."""

    def __init__(self, directory: Path, read: bool = True) -> None:
        self.root = directory / "v1"
        self.read_enabled = read

    def _path(self, kind: str, name: str) -> Path:
        return self.root / kind / f"{name}.json"

    def _get(self, kind: str, name: str) -> dict[str, Any] | None:
        if not self.read_enabled:
            return None
        path = self._path(kind, name)
        if not path.is_file():
            return None
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("ignoring unreadable cache entry %s", path)
            return None
        return data

    def _put(self, kind: str, name: str, value: dict[str, Any]) -> None:
        path = self._path(kind, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, indent=1), encoding="utf-8")
        tmp.replace(path)

    def get_diagnosis(self, key: str) -> dict[str, Any] | None:
        return self._get("diagnose", key)

    def put_diagnosis(self, key: str, value: dict[str, Any]) -> None:
        self._put("diagnose", key, value)

    def get_judge(self, key: str, judge_sha: str) -> dict[str, Any] | None:
        return self._get("judge", f"{key}-{judge_sha[:12]}")

    def put_judge(self, key: str, judge_sha: str, value: dict[str, Any]) -> None:
        self._put("judge", f"{key}-{judge_sha[:12]}", value)
