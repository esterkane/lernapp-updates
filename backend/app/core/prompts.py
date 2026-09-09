"""Versioned prompt loader (ADR-0012): prompts/<name>.v<semver>.md with YAML front-matter."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from packaging.version import Version
from pydantic import BaseModel

from app.core.models import load_models_config
from app.core.paths import repo_root

_FILE_RE = re.compile(r"^(?P<name>[a-z0-9_]+)\.v(?P<version>\d+\.\d+\.\d+)\.md$")
_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


class Prompt(BaseModel):
    name: str
    version: str
    tier: str | None
    schema_name: str | None
    changelog: str
    body: str
    path: str

    @property
    def placeholders(self) -> set[str]:
        return set(_PLACEHOLDER_RE.findall(self.body))

    def render(self, **values: Any) -> str:
        missing = self.placeholders - set(values)
        if missing:
            raise KeyError(f"prompt {self.name} v{self.version}: missing placeholders {sorted(missing)}")

        def sub(m: re.Match[str]) -> str:
            v = values[m.group(1)]
            return v if isinstance(v, str) else _to_text(v)

        return _PLACEHOLDER_RE.sub(sub, self.body)


def _to_text(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, (list, dict)):
        import json

        return json.dumps(v, ensure_ascii=False, indent=None)
    return str(v)


def prompts_dir() -> Path:
    return repo_root() / "prompts"


def _parse(path: Path) -> Prompt:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        raise ValueError(f"prompt file without front-matter: {path}")
    meta = yaml.safe_load(m.group(1)) or {}
    fm = _FILE_RE.match(path.name)
    if not fm:
        raise ValueError(f"bad prompt filename: {path.name}")
    version = str(meta.get("version", fm.group("version")))
    if version != fm.group("version"):
        raise ValueError(f"{path.name}: front-matter version {version} != filename version")
    return Prompt(
        name=str(meta.get("name", fm.group("name"))),
        version=version,
        tier=meta.get("tier"),
        schema_name=meta.get("schema"),
        changelog=str(meta.get("changelog", "")),
        body=m.group(2).strip("\n"),
        path=str(path),
    )


def list_versions(name: str) -> list[Prompt]:
    files = [p for p in prompts_dir().glob(f"{name}.v*.md") if _FILE_RE.match(p.name)]
    return sorted((_parse(p) for p in files), key=lambda p: Version(p.version))


@lru_cache(maxsize=64)
def _load_cached(name: str, pinned: str | None, dir_mtime: float) -> Prompt:
    versions = list_versions(name)
    if not versions:
        raise FileNotFoundError(f"no prompt named '{name}' in {prompts_dir()}")
    if pinned:
        for p in versions:
            if p.version == pinned:
                return p
        raise FileNotFoundError(f"prompt '{name}' pinned to {pinned} but that version does not exist")
    return versions[-1]


def load_prompt(name: str, version: str | None = None) -> Prompt:
    pinned = version or load_models_config().prompt_pins.get(name)
    return _load_cached(name, pinned, prompts_dir().stat().st_mtime)


def all_prompt_names() -> list[str]:
    names = {m.group("name") for p in prompts_dir().glob("*.md") if (m := _FILE_RE.match(p.name))}
    return sorted(names)
