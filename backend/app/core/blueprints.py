"""Static exam blueprints (ADR-0005). Loaded + validated at startup; never LLM-generated.

CLI: ``python -m app.core.blueprints --check``
"""

from __future__ import annotations

import logging
import sys
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from app.core.paths import repo_root

log = logging.getLogger(__name__)

Skill = Literal["lesen", "hoeren", "schreiben", "sprechen"]


class BlueprintTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_type: str
    language_functions: list[str] | None = None
    description: str | None = None
    n_items: int | None = None
    preparation_seconds: int | None = None
    response_seconds: int | None = None


class ScoreScale(BaseModel):
    min: int
    max: int


class Blueprint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    skill: Skill
    format: Literal["digital", "paper"]
    source_url: str
    version: str
    verified_on: str | None = None
    total_time_seconds: int
    n_tasks: int
    n_items: int | None = None
    input_modality: Literal["text", "audio", "text_and_graphic", "text_audio_or_graphic"]
    output_modality: Literal["selection", "typed_text", "recorded_speech", "handwritten"]
    scoring: Literal["deterministic", "rubric"]
    rubric_ref: str | None = None
    score_scale: ScoreScale
    tasks: list[BlueprintTask] = []
    language_functions: list[str] = []

    @model_validator(mode="after")
    def _rules(self) -> Blueprint:
        if self.scoring == "rubric" and not self.rubric_ref:
            raise ValueError(f"{self.id}: rubric_ref required when scoring == rubric")
        if self.scoring == "deterministic" and self.rubric_ref:
            raise ValueError(f"{self.id}: rubric_ref must be null for deterministic scoring")
        if self.n_items is not None and self.tasks and all(t.n_items is not None for t in self.tasks):
            total = sum(t.n_items or 0 for t in self.tasks)
            if len(self.tasks) == self.n_tasks and total != self.n_items:
                raise ValueError(f"{self.id}: sum(tasks.n_items)={total} != n_items={self.n_items}")
        if self.rubric_ref and self.rubric_ref not in ("schreiben_v1", "sprechen_v1"):
            raise ValueError(f"{self.id}: unknown rubric_ref {self.rubric_ref}")
        return self

    @property
    def is_verified(self) -> bool:
        return self.verified_on is not None

    @property
    def task_types(self) -> list[str]:
        return [t.task_type for t in self.tasks]

    def task(self, task_type: str) -> BlueprintTask | None:
        return next((t for t in self.tasks if t.task_type == task_type), None)

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.model_dump(mode="json"), allow_unicode=True, sort_keys=False)


def blueprints_dir() -> Path:
    return repo_root() / "blueprints"


def _files() -> list[Path]:
    return sorted(p for p in blueprints_dir().rglob("*.yaml") if not p.name.startswith("."))


@lru_cache(maxsize=2)
def _load(signature: tuple[tuple[str, float], ...]) -> dict[str, Blueprint]:
    out: dict[str, Blueprint] = {}
    for p in _files():
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        bp = Blueprint.model_validate(raw)
        if bp.id in out:
            raise ValueError(f"duplicate blueprint id {bp.id} in {p}")
        out[bp.id] = bp
    return out


def load_blueprints() -> dict[str, Blueprint]:
    sig = tuple((str(p), p.stat().st_mtime) for p in _files())
    return _load(sig)


def get_blueprint(blueprint_id: str) -> Blueprint:
    bps = load_blueprints()
    if blueprint_id not in bps:
        raise KeyError(f"unknown blueprint {blueprint_id}")
    return bps[blueprint_id]


def unverified() -> list[str]:
    return [b.id for b in load_blueprints().values() if not b.is_verified]


def warn_unverified() -> None:
    u = unverified()
    if u:
        log.warning("Unverified blueprints (verified_on is null): %s — check against source_url", ", ".join(u))


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    try:
        bps = load_blueprints()
    except Exception as exc:  # noqa: BLE001
        print(f"INVALID: {exc}")
        return 1
    for b in bps.values():
        status = f"verified {b.verified_on}" if b.is_verified else "UNVERIFIED"
        print(f"{b.id:32s} {b.skill:9s} {b.format:8s} tasks={len(b.tasks)}/{b.n_tasks} {status}")
    if "--check" in args:
        print(f"{len(bps)} blueprints valid; {len(unverified())} unverified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
