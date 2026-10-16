"""Parse and *verify* model output. The model is never trusted about what the log says."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pipelinedoctor.engine.preprocess import Excerpt
from pipelinedoctor.schemas import EvidenceItem, LLMDiagnosis, LLMEvidence

MAX_EXHIBITS = 5
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_LINE_PREFIX_RE = re.compile(r"^\s*\d+\s*\|\s?")
_WS_RE = re.compile(r"\s+")
_TAG_RE = re.compile(r"<!--.*?-->|</?[A-Za-z][^>]*>", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Return the first JSON object in `text` (models without JSON mode like to add prose)."""
    stripped = _FENCE_RE.sub("", text.strip())
    try:
        value = json.loads(stripped)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    while start != -1:
        end = _balanced_end(stripped, start)
        if end is not None:
            try:
                value = json.loads(stripped[start : end + 1])
            except json.JSONDecodeError:
                value = None
            if isinstance(value, dict):
                return value
        start = stripped.find("{", start + 1)
    raise ValueError("no JSON object found in model output")


def _balanced_end(text: str, start: int) -> int | None:
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return None


def to_llm_diagnosis(text: str) -> LLMDiagnosis:
    """Raises ValueError (pydantic's ValidationError is one) when the answer is unusable."""
    return LLMDiagnosis.model_validate(extract_json(text))


def clean_text(text: str, limit: int) -> str:
    """Strip HTML tags/comments and cap the length of model-written text."""
    text = _TAG_RE.sub("", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def normalise_ws(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


@dataclass
class EvidenceCheck:
    items: list[EvidenceItem] = field(default_factory=list)
    cited: int = 0
    valid: int = 0
    warnings: list[str] = field(default_factory=list)


def _clean_quote(quote: str) -> str:
    quote = _LINE_PREFIX_RE.sub("", quote.strip())
    for suffix in ("...", "…"):
        quote = quote.removesuffix(suffix)
    return normalise_ws(quote.strip("`\"' "))


def validate_evidence(evidence: list[LLMEvidence], excerpt: Excerpt) -> EvidenceCheck:
    """Keep only citations whose line exists in the excerpt AND whose quote is on that line.

    The report then shows the *real* (redacted) log line, never the model's quote, so a model
    that invents or "improves" a line cannot put words into the log.
    """
    check = EvidenceCheck(cited=len(evidence))
    seen: set[int] = set()
    for item in evidence:
        line_text = excerpt.get(item.line)
        quote = _clean_quote(item.quote)
        if line_text is None:
            check.warnings.append(f"dropped evidence: line {item.line} is not in the excerpt")
            continue
        if len(quote) < 3 or quote not in normalise_ws(line_text):
            check.warnings.append(f"dropped evidence: quote does not match line {item.line}")
            continue
        check.valid += 1
        if item.line in seen or len(check.items) >= MAX_EXHIBITS:
            continue
        seen.add(item.line)
        check.items.append(
            EvidenceItem(
                exhibit=chr(ord("A") + len(check.items)),
                line=item.line,
                text=line_text,
                why=item.why.strip()[:400],
            )
        )
    return check
