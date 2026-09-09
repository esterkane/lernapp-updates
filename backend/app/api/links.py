"""Editable bookmarks per learning workspace; included in profile exports/backups."""

from copy import deepcopy

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, HttpUrl, field_validator
from sqlalchemy import select

from app.core.db import db_session
from app.core.workspaces import current_workspace
from app.db.base import Learner, new_id

router = APIRouter(prefix="/links", tags=["links"])
DEFAULTS = [
    {
        "id": "testdaf",
        "title": "Fit für den TestDaF",
        "url": "https://www.testdaf.de/fit-fuer-testdaf/?id=1",
        "description": "Kurzer Lückentest zur Orientierung für die TestDaF-Vorbereitung.",
    },
    {
        "id": "pruefungstrainer",
        "title": "DeutschAkademie – Prüfungstrainer",
        "url": "https://www.deutschakademie.de/online-deutschkurs/pruefungstrainer/",
        "description": "Online-Übungen zur Vorbereitung auf Deutschprüfungen.",
    },
    {
        "id": "grammatik",
        "title": "DeutschAkademie – Dativ und Akkusativ",
        "url": "https://www.deutschakademie.de/online-deutschkurs/deutsche-grammatik/vier-faelle-deutsch/dativ-akkusativ-uebungen/",
        "description": "Grammatikübungen zu Dativ und Akkusativ.",
    },
    {
        "id": "vhs",
        "title": "VHS Augsburg – Deutschkurse",
        "url": "https://www.vhs-augsburg.de/kurse?q=deutsch&themenwelt=sprachen&thema=deutsch-integration-482-CAT-KAT200&page=2",
        "description": "Deutschkurse bei der Volkshochschule Augsburg suchen.",
    },
]


class LinkInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    url: HttpUrl = Field(max_length=2048)
    description: str = Field(default="", max_length=400)

    @field_validator("title", "description", mode="before")
    @classmethod
    def trim(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value

    @field_validator("url")
    @classmethod
    def public_url(cls, value: HttpUrl) -> HttpUrl:
        if value.username or value.password:
            raise ValueError("Bitte einen Link ohne eingebettete Zugangsdaten verwenden.")
        return value


def _items(row: Learner) -> list[dict[str, str]]:
    return deepcopy((row.profile or {}).get("external_links", DEFAULTS))


@router.get("")
def listing() -> list[dict[str, str]]:
    with db_session() as db:
        row = db.get(Learner, current_workspace())
        if row is None:
            raise HTTPException(404, "Lernbereich nicht gefunden.")
        return _items(row)


def _change(link_id: str | None, body: LinkInput | None) -> list[dict[str, str]]:
    with db_session() as db:
        row = db.execute(
            select(Learner).where(Learner.id == current_workspace()).with_for_update()
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "Lernbereich nicht gefunden.")
        items = _items(row)
        if link_id is not None and not any(item["id"] == link_id for item in items):
            raise HTTPException(404, "Link nicht gefunden.")
        if body is not None:
            candidate = body.model_dump(mode="json")
            canonical = str(HttpUrl(candidate["url"]))
            if any(str(HttpUrl(item["url"])) == canonical and item["id"] != link_id for item in items):
                raise HTTPException(409, "Dieser Link ist bereits gespeichert.")
            if link_id is None:
                if len(items) >= 200:
                    raise HTTPException(400, "Es können bis zu 200 Links gespeichert werden.")
                items.append({"id": new_id(), **candidate})
            else:
                items = [{"id": link_id, **candidate} if item["id"] == link_id else item for item in items]
        else:
            items = [item for item in items if item["id"] != link_id]
        row.profile = {**(row.profile or {}), "external_links": items}
        return items


@router.post("", status_code=201)
def create(body: LinkInput) -> list[dict[str, str]]:
    return _change(None, body)


@router.put("/{link_id}")
def update(link_id: str, body: LinkInput) -> list[dict[str, str]]:
    return _change(link_id, body)


@router.delete("/{link_id}")
def remove(link_id: str) -> list[dict[str, str]]:
    return _change(link_id, None)
