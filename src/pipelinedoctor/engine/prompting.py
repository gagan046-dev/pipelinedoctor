"""Versioned prompt files (prompts/<id>.<version>.md) with front matter and system/user sections."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from string import Template

import yaml

_SECTION_RE = re.compile(r"^## (system|user)[ \t]*$", re.MULTILINE)
# Editable installs (uv sync) do not apply hatch's force-include, so fall back to the repo copy.
_REPO_PROMPTS = Path(__file__).resolve().parents[3] / "prompts"


class PromptError(Exception):
    pass


@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    description: str
    system: str
    user: str
    sha256: str

    @property
    def name(self) -> str:
        return f"{self.id}.{self.version}"

    def render(self, **values: str) -> tuple[str, str]:
        """Fill the user template. A missing placeholder is a bug, so `substitute` (strict)."""
        try:
            return self.system, Template(self.user).substitute(values)
        except KeyError as exc:
            raise PromptError(f"prompt {self.name} needs placeholder {exc}") from exc


def _read_prompt_bytes(filename: str) -> bytes:
    packaged = resources.files("pipelinedoctor") / "prompts" / filename
    if packaged.is_file():
        return packaged.read_bytes()
    local = _REPO_PROMPTS / filename
    if local.is_file():
        return local.read_bytes()
    raise PromptError(f"prompt file {filename} not found")


def parse_prompt(data: bytes, filename: str = "<prompt>") -> Prompt:
    # Hash LF-normalised bytes so a Windows checkout (CRLF) yields the same sha as Linux.
    normalised = data.replace(b"\r\n", b"\n")
    text = normalised.decode("utf-8")
    if not text.startswith("---\n"):
        raise PromptError(f"{filename}: missing front matter")
    _, front, body = text.split("---\n", 2)
    meta = yaml.safe_load(front) or {}
    parts = _SECTION_RE.split(body)
    sections = {parts[i]: parts[i + 1].strip("\n") for i in range(1, len(parts) - 1, 2)}
    if set(sections) != {"system", "user"}:
        raise PromptError(f"{filename}: needs exactly one '## system' and one '## user' section")
    return Prompt(
        id=str(meta.get("id", "")),
        version=str(meta.get("version", "")),
        description=str(meta.get("description", "")),
        system=sections["system"],
        user=sections["user"],
        sha256=hashlib.sha256(normalised).hexdigest(),
    )


def load_prompt(prompt_id: str, version: str) -> Prompt:
    if not re.fullmatch(r"[a-z]+", prompt_id) or not re.fullmatch(r"v\d+", version):
        raise PromptError(f"invalid prompt reference {prompt_id}.{version}")
    filename = f"{prompt_id}.{version}.md"
    prompt = parse_prompt(_read_prompt_bytes(filename), filename)
    if (prompt.id, prompt.version) != (prompt_id, version):
        raise PromptError(f"{filename}: front matter says {prompt.id}.{prompt.version}")
    return prompt
