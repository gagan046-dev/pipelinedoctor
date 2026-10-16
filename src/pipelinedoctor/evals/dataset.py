"""Load eval cases from evals/cases/<id>/{case.yaml,log.txt}."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from pipelinedoctor.schemas import EvalCase


class DatasetError(Exception):
    pass


@dataclass(frozen=True)
class LoadedCase:
    case: EvalCase
    log_text: str
    case_yaml: bytes  # LF-normalised, part of the cache key
    log_sha256: str

    @property
    def id(self) -> str:
        return self.case.id


def _lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def load_case(directory: Path) -> LoadedCase:
    case_bytes = _lf((directory / "case.yaml").read_bytes())
    log_bytes = _lf((directory / "log.txt").read_bytes())
    case = EvalCase.model_validate(yaml.safe_load(case_bytes.decode("utf-8")))
    if case.id != directory.name:
        raise DatasetError(f"{directory}: case id {case.id!r} must equal the directory name")
    if "all" not in case.subsets:
        raise DatasetError(f"{directory}: every case must be in subset 'all'")
    return LoadedCase(
        case=case,
        log_text=log_bytes.decode("utf-8"),
        case_yaml=case_bytes,
        log_sha256=hashlib.sha256(log_bytes).hexdigest(),
    )


def load_cases(directory: Path, subset: Literal["pr", "all"] = "all") -> list[LoadedCase]:
    if not directory.is_dir():
        raise DatasetError(f"cases directory not found: {directory}")
    cases = [
        load_case(d)
        for d in sorted(directory.iterdir())
        if d.is_dir() and (d / "case.yaml").is_file()
    ]
    return [c for c in cases if subset in c.case.subsets]
